"""Automatic.run orchestration."""
import time
from video_editor import storage
from .constants import CONTEXT_SUFFIX, CONTEXT_UNAVAILABLE_SUFFIX
def run_automatic(self, job_id):
        jobs = self.jobs
        state = jobs.read(job_id, "automatic.json")
        evaluation_mode = state["options"].get("mode", "standard") == "evaluation"
        started = time.monotonic()
        timings = {}

        def stage(name, **extra):
            state.update(state=name, timings=dict(timings), **extra)
            jobs.write(job_id, "automatic.json", state)

        try:
            job, edit, transcript, revision, refinement = self._prepare_run(
                job_id, state, stage
            )
            resume = state.get("resume_revision")
            voice_state = dict(state="not_available")
            if resume is None:
                stage("analyzing")
                tick = time.monotonic()
                found, suggestions, profile, voice_state = self._detect_candidates(
                    job_id, job, edit, transcript
                )
                (
                    suggestions,
                    editorial_protected,
                    context_report,
                    notes_by_id,
                    provider,
                ) = self._resolve_contextual_and_editorial(
                    job_id, job, transcript, edit, found, suggestions, state, stage
                )
                (
                    suggestions,
                    human_gate,
                    sequence_audit,
                    allowed,
                ) = self._validate_and_refine(
                    job_id,
                    job,
                    transcript,
                    edit,
                    suggestions,
                    notes_by_id,
                    editorial_protected,
                    context_report,
                    evaluation_mode,
                    state,
                    stage,
                )
                if state["options"]["send_text"]:
                    provider = (
                        "complete"
                        if context_report["state"] == "complete"
                        and sequence_audit["state"] in ("complete", "not_needed")
                        else "unavailable_or_invalid"
                    )
                timings["analysis"] = time.monotonic() - tick
                revision = self._apply_cuts_and_report(
                    job_id,
                    job,
                    transcript,
                    revision,
                    suggestions,
                    notes_by_id,
                    human_gate,
                    allowed,
                    provider,
                    profile,
                    voice_state,
                    context_report,
                    sequence_audit,
                    refinement,
                    state,
                    evaluation_mode,
                )
                stage("analyzing", applied_revision=revision)
                if state["options"]["send_text"] and provider != "complete":
                    # The name must not claim context the provider never delivered.
                    name = jobs.info(job_id)["name"]
                    if name.endswith(CONTEXT_SUFFIX):
                        jobs.update(
                            job_id,
                            name=name[: -len(CONTEXT_SUFFIX)] + CONTEXT_UNAVAILABLE_SUFFIX,
                        )

            self._export_if_requested(job_id, revision, state, stage, timings)
            timings["total"] = time.monotonic() - started
            stage("complete", completed_at=storage.now())
        except Exception as error:
            stage("failed", error=str(error))
            raise
