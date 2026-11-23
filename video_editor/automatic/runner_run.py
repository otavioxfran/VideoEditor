"""Automatic.run orchestration."""
import logging
import os
import threading
import time

from video_editor import storage
from .constants import CONTEXT_SUFFIX, CONTEXT_UNAVAILABLE_SUFFIX


def _detect_stage(self, job_id, job, edit, transcript, stage):
    stage("analyzing")
    tick = time.monotonic()
    found, suggestions, profile, voice_state = self._detect_candidates(
        job_id, job, edit, transcript
    )
    return found, suggestions, profile, voice_state, tick


def _resolve_validate_stage(self, job_id, job, transcript, edit, found,
                            suggestions, state, stage, evaluation_mode):
    (
        suggestions,
        editorial_protected,
        context_report,
        notes_by_id,
        provider,
    ) = self._resolve_contextual_and_editorial(
        job_id, job, transcript, edit, found, suggestions, state, stage
    )
    suggestions, human_gate, sequence_audit, allowed = self._validate_and_refine(
        job_id, job, transcript, edit, suggestions, notes_by_id,
        editorial_protected, context_report, evaluation_mode, state, stage,
    )
    if state["options"]["send_text"]:
        provider = (
            "complete"
            if context_report["state"] == "complete"
            and sequence_audit["state"] in ("complete", "not_needed")
            else "unavailable_or_invalid"
        )
    return suggestions, human_gate, sequence_audit, allowed, context_report, notes_by_id, provider


def _rename_unavailable_context(self, job_id):
    name = self.jobs.info(job_id)["name"]
    if name.endswith(CONTEXT_SUFFIX):
        self.jobs.update(
            job_id, name=name[: -len(CONTEXT_SUFFIX)] + CONTEXT_UNAVAILABLE_SUFFIX
        )


def _apply_stage(self, job_id, job, transcript, revision, suggestions,
                 notes_by_id, human_gate, allowed, provider, profile,
                 voice_state, context_report, sequence_audit, refinement,
                 state, evaluation_mode, stage, timings, tick):
    timings["analysis"] = time.monotonic() - tick
    revision = self._apply_cuts_and_report(
        job_id, job, transcript, revision, suggestions, notes_by_id,
        human_gate, allowed, provider, profile, voice_state, context_report,
        sequence_audit, refinement, state, evaluation_mode,
    )
    stage("analyzing", applied_revision=revision)
    if state["options"]["send_text"] and provider != "complete":
        _rename_unavailable_context(self, job_id)
    return revision


def _prepare_finishing(jobs, job_id):
    """Have the finishing panel ready when the user opens the video: suggestions with new
    free images, plus previews, generated on a background thread after the export."""
    if os.getenv('VIDEO_EDITOR_AUTO_FINISHING', '1') == '0':
        return

    def run():
        try:
            from video_editor.finishing import suggest
            from video_editor.server.finishing_api import _start_warmup
            document = suggest.suggest(jobs, job_id, expand_library=True)
            if document.get('items'):
                _start_warmup(jobs, job_id, document['items'])
        except Exception:
            logging.getLogger(__name__).warning(
                'Sugestões de acabamento não foram geradas após o export', exc_info=True)

    threading.Thread(target=run, name=f'finishing-prepare-{job_id[:8]}', daemon=True).start()


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
        voice_state = dict(state="not_available")
        if state.get("resume_revision") is None:
            found, suggestions, profile, voice_state, tick = _detect_stage(
                self, job_id, job, edit, transcript, stage
            )
            (
                suggestions, human_gate, sequence_audit, allowed,
                context_report, notes_by_id, provider,
            ) = _resolve_validate_stage(
                self, job_id, job, transcript, edit, found, suggestions,
                state, stage, evaluation_mode,
            )
            revision = _apply_stage(
                self, job_id, job, transcript, revision, suggestions,
                notes_by_id, human_gate, allowed, provider, profile,
                voice_state, context_report, sequence_audit, refinement,
                state, evaluation_mode, stage, timings, tick,
            )
        self._export_if_requested(job_id, revision, state, stage, timings)
        timings["total"] = time.monotonic() - started
        stage("complete", completed_at=storage.now())
        if state["options"]["export"] and not evaluation_mode:
            _prepare_finishing(jobs, job_id)
    except Exception as error:
        stage("failed", error=str(error))
        raise
