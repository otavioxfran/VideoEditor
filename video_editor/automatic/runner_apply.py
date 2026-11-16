"""Applying automatic cuts and building the report."""
import copy

from video_editor import storage, takes
from video_editor.editing import load_edit, require_revision
from video_editor.timeline import build_timeline, digest, source_to_output
from .bounds import overlap
from .constants import NEW_CLASSES, POLICY_VERSION


def _prepare_edit(jobs, job_id, revision):
    _, latest, _ = load_edit(jobs, job_id)
    require_revision(latest, revision)
    mechanical_ids = {
        c.get("suggestion_id")
        for c in latest["cuts"]
        if c["origin"] in ("mechanical", "automatic")
    }
    before = {k: copy.deepcopy(latest[k]) for k in ("cuts", "markers", "protected")}
    human_edits = any(
        h["action"] in ("cut", "restore", "protect", "undo", "legacy_import")
        for h in latest["history"]
    )
    if not human_edits:
        latest["cuts"] = [
            c for c in latest["cuts"]
            if c["origin"] not in ("mechanical", "automatic")
        ]
    return latest, before, human_edits, mechanical_ids


def _apply_automatic_cuts(latest, suggestions, mechanical_ids, allowed,
                          evaluation_mode, human_edits, before):
    for s in sorted(suggestions, key=lambda s: s["kind"] not in takes.AUTO_KINDS):
        if (
            s.get("auto")
            and (
                s["id"] in mechanical_ids
                or s["id"].startswith("gap-")
                or (s["kind"] in takes.AUTO_KINDS and allowed(s["kind"]))
                or (s["kind"] in NEW_CLASSES and allowed(s["kind"]) and not human_edits)
            )
            and not human_edits
            and not any(overlap(s, c) for c in latest["cuts"])
        ):
            latest["cuts"].append(
                dict(
                    start=s["start"], end=s["end"], origin="automatic",
                    suggestion_id=s["id"], kind=s["kind"],
                    experimental=evaluation_mode,
                )
            )
    if latest["cuts"] != before["cuts"]:
        latest["undo"] = (latest["undo"] + [before])[-100:]
        latest["revision"] += 1
        latest["history"].append(
            dict(action="automatic_clean", at=storage.now(), policy=POLICY_VERSION)
        )


def _build_report(self, job, transcript, latest, timeline, suggestions,
                  notes_by_id, human_gate, provider, profile, voice_state,
                  context_report, sequence_audit, refinement, state):
    return dict(
        schema=1, policy=POLICY_VERSION, policy_hash=self.policy_hash,
        revision=latest["revision"], transcript_hash=digest(transcript),
        provider=provider, applied=len(latest["cuts"]),
        uncertain=sum(not s["auto"] for s in suggestions),
        source_seconds=job["media"]["duration"], output_seconds=timeline["duration"],
        removed_seconds=job["media"]["duration"] - timeline["duration"],
        human_validation=human_gate["state"], suggestions=suggestions,
        notes=list(notes_by_id.values()), mode=state["options"].get("mode", "standard"),
        background_profile=profile, background_voice=voice_state,
        contextual=context_report, sequence_audit=sequence_audit,
        contextual_refinement=refinement,
        coherence="strict_replacements_preserve_original_order",
        performance=dict(preparation=job.get("timings"), asr=transcript.get("actual_config")),
    )


def _applied_cuts(job, transcript, latest, suggestions, timeline):
    applied = []
    words = [w for seg in transcript["segments"] for w in seg["words"]]
    for cut in latest["cuts"]:
        suggestion = next(
            (s for s in suggestions if s["id"] == cut.get("suggestion_id")), None
        )
        if not suggestion or cut["origin"] != "automatic":
            continue
        start, end = build_timeline(
            job["media"]["duration"], [cut], job["media"]["fps"]
        )["cuts"][0]
        applied.append(
            dict(
                id=suggestion["id"], kind=suggestion["kind"], start=start, end=end,
                join_output_seconds=source_to_output(timeline, start),
                reason=suggestion["reason"],
                before=" ".join(w["text"] for w in [w for w in words if w["end"] <= start][-8:]),
                after=" ".join(w["text"] for w in [w for w in words if w["start"] >= end][:8]),
                replacement=suggestion["evaluation"].get("editorial", {}),
                intact=[start, end] == build_timeline(
                    job["media"]["duration"], [suggestion], job["media"]["fps"]
                )["cuts"][0],
            )
        )
    return applied


def _finish_report(report, applied, state, provider, evaluation_mode):
    report["applied_cuts"] = applied
    report["result"] = "cuts_applied" if applied else "no_safe_cuts"
    report["message"] = (
        "Edição experimental pronta para avaliação."
        if applied and evaluation_mode
        else ("Cortes aplicados." if applied else "Nenhum corte seguro encontrado")
    )
    if state["options"]["send_text"] and provider != "complete":
        report["message"] = (
            "Assistência editorial indisponível; limpeza local concluída. "
            + report["message"]
        )
    report["contextual_applied"] = sum(
        c["kind"] == "contextual_restart" for c in applied
    )
    report["contextual_result"] = (
        "not_requested" if not state["options"]["send_text"]
        else "not_available" if provider != "complete"
        else "cuts_applied" if report["contextual_applied"]
        else "no_safe_restarts"
    )
    if report["contextual_result"] == "no_safe_restarts":
        report["message"] += " Nenhum reinício contextual seguro encontrado."
    report["editorial_delivery"] = (
        "context_checked" if provider == "complete" else "local_cleanup"
    )


def apply_cuts_and_report(
    self, job_id, job, transcript, revision, suggestions, notes_by_id,
    human_gate, allowed, provider, profile, voice_state, context_report,
    sequence_audit, refinement, state, evaluation_mode,
):
    jobs = self.jobs
    with jobs.lock(job_id):
        jobs.validate(job_id)
        latest, before, human_edits, mechanical_ids = _prepare_edit(
            jobs, job_id, revision
        )
        _apply_automatic_cuts(
            latest, suggestions, mechanical_ids, allowed, evaluation_mode,
            human_edits, before,
        )
        timeline = build_timeline(
            job["media"]["duration"], latest["cuts"], job["media"]["fps"]
        )
        jobs.write(job_id, "edit.json", latest)
        jobs.write(job_id, "suggestions.json", suggestions)
        revision = latest["revision"]
        report = _build_report(
            self, job, transcript, latest, timeline, suggestions, notes_by_id,
            human_gate, provider, profile, voice_state, context_report,
            sequence_audit, refinement, state,
        )
        applied = _applied_cuts(job, transcript, latest, suggestions, timeline)
        _finish_report(report, applied, state, provider, evaluation_mode)
        jobs.write(job_id, "automatic_report.json", report)
        jobs.write(
            job_id,
            "automatic/checkpoint.json",
            dict(
                policy_hash=self.policy_hash,
                input_signature=state.get("input_signature"),
                revision=revision,
                edit_hash=digest(latest),
                transcript_hash=digest(transcript),
                report_hash=digest(report),
            ),
        )
    return revision
