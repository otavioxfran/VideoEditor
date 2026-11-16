"""Automatic orchestration class; public API is re-exported by the package."""
import time
from pathlib import Path
from video_editor import (
    storage, activity, acceptance, contextual, takes,
    visual_presence,
)
from video_editor.cuts import find_candidates
from video_editor.cut_policy import apply_policy
from video_editor.editing import load_edit, require_revision, RevisionConflict
from video_editor.timeline import digest
from video_editor.noise_profile import analyze as analyze_noise_profile
from .constants import ACTIVE_STATES, CONTEXT_SUFFIX
from .classify import classify
from .voice import background_voice_candidates
from .runner_context import resolve_contextual_and_editorial
from .runner_validation import validate_and_refine
from .runner_apply import apply_cuts_and_report
from .runner_run import run_automatic

class Automatic:
    def __init__(self, jobs, worker, pipeline, exports, config_path):
        self.jobs, self.worker, self.pipeline, self.exports = (
            jobs,
            worker,
            pipeline,
            exports,
        )
        self.config_path = Path(config_path)
    @property
    def policy_hash(self):
        # Live, not cached at startup: Studio edits can move the channel profile.
        return acceptance.policy_hash()
    def recover(self, job_id):
        """Only requests with current identities may resume without a user action."""
        state = self.status(job_id)
        if state["state"] not in ACTIVE_STATES + ("interrupted",) or not state.get(
            "input_signature"
        ):
            return False
        info = self.jobs.info(job_id)
        options = state["options"]
        signature = digest(
            dict(
                source=info["source"],
                config=info["config"],
                options=options,
                policy=self.policy_hash,
            )
        )
        if signature != state["input_signature"]:
            return False
        self.jobs.validate(job_id)
        revision = None
        if info["state"] == "ready":
            from video_editor.contextual import finish_refinement

            intent = finish_refinement(self.jobs, job_id)
            _, edit, tr = load_edit(self.jobs, job_id)
            revision = edit["revision"]
            checkpoint_path = self.jobs.path(job_id, "automatic/checkpoint.json")
            checkpoint = (
                self.jobs.read(job_id, "automatic/checkpoint.json")
                if checkpoint_path.exists()
                else {}
            )
            if (
                checkpoint.get("input_signature") == signature
                and checkpoint.get("policy_hash") == self.policy_hash
                and checkpoint.get("edit_hash") == digest(edit)
                and checkpoint.get("transcript_hash") == digest(tr)
                and checkpoint.get("report_hash")
                == digest(self.jobs.read(job_id, "automatic_report.json"))
            ):
                state["applied_revision"] = revision
            elif state.get("input_edit_hash") == digest(edit) or (
                intent
                and intent["before_edit_hash"] == state.get("input_edit_hash")
                and intent["after_edit_hash"] == digest(edit)
            ):
                state.pop("applied_revision", None)
            else:
                return False
        state["state"] = "interrupted"
        self.jobs.write(job_id, "automatic.json", state)
        self.request(job_id, options, revision)
        return True
    def create_evaluation(self, job_id, revision, send_text=False):
        from video_editor.automatic_evaluation import clone_prepared

        previous = self.status(job_id)
        if previous["state"] in ACTIVE_STATES:
            raise RevisionConflict("Aguarde a conclusão do processamento anterior.")
        child = clone_prepared(self.jobs, job_id, revision)
        if send_text:
            child = self.jobs.update(
                child["job_id"], name=child["name"] + CONTEXT_SUFFIX
            )
        self.request(
            child["job_id"],
            dict(send_text=send_text, export=True, mode="evaluation"),
            0,
        )
        return self.jobs.info(child["job_id"])
    def status(self, job_id):
        path = self.jobs.path(job_id, "automatic.json")
        result = (
            self.jobs.read(job_id, "automatic.json")
            if path.exists()
            else dict(state="not_requested")
        )
        report_path = self.jobs.path(job_id, "automatic_report.json")
        if report_path.exists():
            report = self.jobs.read(job_id, "automatic_report.json")
            result["report"] = report
            result["stale"] = (
                report.get("revision")
                != self.jobs.read(job_id, "edit.json")["revision"]
            )
            result["engine_stale"] = (
                report.get("policy_hash") != acceptance.policy_hash()
            )
            result["stale"] = result["stale"] or result["engine_stale"]
        return result
    def request(self, job_id, options, expected_revision=None):
        from video_editor.models import AutomaticOptions

        options = AutomaticOptions(**options).model_dump()
        with self.jobs.lock(job_id):
            self.jobs.validate(job_id)
            previous = self.status(job_id)
            if previous["state"] in ACTIVE_STATES:
                raise RevisionConflict("Edição automática já está em andamento.")
            info = self.jobs.info(job_id)
            if info.get("editing_mode", "standard") != options["mode"]:
                if info.get("evaluation_parent") or info["state"] == "ready":
                    raise ValueError(
                        "Crie um job separado para alterar o modo de edição."
                    )
                info["editing_mode"] = options["mode"]
                self.jobs.write(job_id, "job.json", info)
            if info["state"] in ("queued", "processing"):
                raise RevisionConflict("Preparação em andamento.")
            if info["state"] == "ready":
                require_revision(self.jobs.read(job_id, "edit.json"), expected_revision)
            resume = (
                previous.get("applied_revision")
                if previous["state"] in ("failed", "interrupted")
                else None
            )
            if previous.get("engine_stale"):
                resume = None
            if (
                info["state"] == "ready"
                and resume != self.jobs.read(job_id, "edit.json")["revision"]
            ):
                resume = None
            value = dict(
                state="queued",
                options=dict(options),
                created_at=storage.now(),
                resume_revision=resume,
                input_signature=digest(
                    dict(
                        source=info["source"],
                        config=info["config"],
                        options=options,
                        policy=self.policy_hash,
                    )
                ),
                input_edit_hash=digest(self.jobs.read(job_id, "edit.json"))
                if info["state"] == "ready"
                else None,
            )
            self.jobs.write(job_id, "automatic.json", value)
            if info["state"] != "ready":
                info.update(state="queued", stage="automatic_queued", error=None)
                self.jobs.write(job_id, "job.json", info)
        try:
            self.worker.submit(self.run, job_id)
        except Exception:
            self.jobs.write(
                job_id,
                "automatic.json",
                dict(
                    value, state="failed", error="Fila indisponível. Retome a edição."
                ),
            )
            if info["state"] != "ready":
                self.jobs.update(job_id, state="failed", error="Fila indisponível.")
            raise
        return value
    def _prepare_run(self, job_id, state, stage):
        jobs = self.jobs
        stage("preparing")
        if jobs.info(job_id)["state"] != "ready":
            self.pipeline.prepare(job_id)

        contextual.finish_refinement(jobs, job_id)
        job, edit, transcript = load_edit(jobs, job_id)
        refinement = dict(state="not_requested")
        refinement_wav = jobs.path(job_id, "audio/extracted_16k.wav")
        if (
            state["options"]["send_text"]
            and refinement_wav.exists()
            and state.get("resume_revision") is None
        ):
            stage("analyzing_takes")
            refinement = contextual.refine_job(
                jobs, job_id, transcript, edit, refinement_wav
            )
            job, edit, transcript = load_edit(jobs, job_id)
            state["input_edit_hash"] = digest(edit)
        if state.get("input_edit_hash") is None:
            state["input_edit_hash"] = digest(edit)
        revision = edit["revision"]
        resume = state.get("resume_revision")
        if resume is not None and resume != revision:
            raise RevisionConflict(
                "Edição humana mudou após a interrupção; preserve os ajustes e solicite novamente."
            )
        return job, edit, transcript, revision, refinement
    def _detect_candidates(self, job_id, job, edit, transcript):
        jobs = self.jobs
        wav = jobs.path(job_id, "audio/extracted_16k.wav")
        found = jobs.read(job_id, "suggestions.json")
        voice_state = dict(state="not_available")
        if wav.exists() and job.get("render_audio"):
            found = [s for s in found if s["kind"] == "legacy"] + apply_policy(
                transcript,
                find_candidates(transcript),
                job["media"]["duration"],
                job["media"]["fps"],
            )
            path = jobs.path(job_id, "audio/activity.json")
            if path.exists():
                found += activity.silence_candidates(
                    transcript,
                    jobs.read(job_id, "audio/activity.json"),
                    job["media"]["duration"],
                )
            found += activity.adaptive_for_job(
                jobs, job_id, transcript, job["media"]["duration"]
            )
            from video_editor.channel_profile import detect_speech_gaps
            found += detect_speech_gaps(transcript, job["media"]["duration"], wav=wav)
            found = takes.merge(found, takes.candidates(transcript))
            found += visual_presence.candidates(jobs, job_id, job, edit, transcript)
            voice_candidates, voice_state = background_voice_candidates(
                jobs, job_id, job, transcript, edit
            )
            found += voice_candidates
            found.sort(key=lambda s: (s["start"], s["end"]))
        profile = dict(state="not_available")
        if wav.exists() and job.get("render_audio"):
            proposals, profile = analyze_noise_profile(
                transcript, wav, found, job["render_audio"]["path"]
            )
            found = [
                s for s in found if s["kind"] != "adaptive_silence"
            ] + proposals
            jobs.write(job_id, "audio/background_profile.json", profile)
        suggestions = classify(
            transcript,
            found,
            job["media"]["duration"],
            job["media"]["fps"],
            wav if wav.exists() else None,
            edit["protected"],
        )
        return found, suggestions, profile, voice_state
    def _resolve_contextual_and_editorial(
        self, job_id, job, transcript, edit, found, suggestions, state, stage
    ):
        return resolve_contextual_and_editorial(
            self, job_id, job, transcript, edit, found, suggestions, state, stage
        )

    def _validate_and_refine(
        self, job_id, job, transcript, edit, suggestions, notes_by_id,
        editorial_protected, context_report, evaluation_mode, state, stage,
    ):
        return validate_and_refine(
            self, job_id, job, transcript, edit, suggestions, notes_by_id,
            editorial_protected, context_report, evaluation_mode, state, stage,
        )

    def _apply_cuts_and_report(
        self, job_id, job, transcript, revision, suggestions, notes_by_id,
        human_gate, allowed, provider, profile, voice_state, context_report,
        sequence_audit, refinement, state, evaluation_mode,
    ):
        return apply_cuts_and_report(
            self, job_id, job, transcript, revision, suggestions, notes_by_id,
            human_gate, allowed, provider, profile, voice_state, context_report,
            sequence_audit, refinement, state, evaluation_mode,
        )

    def _export_if_requested(self, job_id, revision, state, stage, timings):
        if state["options"]["export"]:
            stage("exporting")
            tick = time.monotonic()
            item = next(
                (
                    e
                    for e in self.exports.list(job_id)
                    if e["state"] == "complete" and e["status"] == "current"
                ),
                None,
            )
            if item:
                stage("exporting", render_id=item["render_id"], reused_export=True)
            else:
                item = self.exports.request(job_id, revision, enqueue=False)
                stage("exporting", render_id=item["render_id"], reused_export=False)
                self.exports.execute(job_id, item["render_id"])
            timings["export"] = time.monotonic() - tick
    def run(self, job_id):
        return run_automatic(self, job_id)
