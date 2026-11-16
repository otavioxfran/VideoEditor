"""Validation and refinement stage for Automatic."""
import math
import subprocess

from video_editor.analysis import contextual, pacing, takes
from video_editor.learning import acceptance
from video_editor.learning.channel_profile import load_profile
from video_editor.cuts import tokens
from .bounds import overlap, safe_bounds
from .constants import NEW_CLASSES
from .context import refine_visual


def _allowed(kind, evaluation_mode, human_gate):
    if evaluation_mode:
        return True
    prof = load_profile()
    if kind in takes.AUTO_KINDS:
        return prof.get("auto_cut_takes", True)
    if kind == "repeated_idea":
        return False
    if kind == "adaptive_silence" and prof.get("auto_cut_silence", True):
        return True
    if kind == "contextual_restart":
        return human_gate.get("classes", {}).get(kind, {}).get("enabled", False)
    return human_gate.get("classes", {}).get(
        kind, {}
    ).get("enabled", human_gate["enabled"])


def _apply_take_gate(s, allowed):
    if s["kind"] in takes.KINDS and s.get("auto") and not allowed(s["kind"]):
        s.update(
            auto=False,
            reason=s["reason"] + " Corte automático de tomadas desativado no perfil.",
        )


def _apply_adaptive(s, allowed, transcript, job, edit):
    if s["kind"] != "adaptive_silence" or not allowed(s["kind"]):
        return
    proof = s["evidence"]
    evaluation = s["evaluation"]
    if (
        proof.get("native_clear")
        and proof.get("vad_clear")
        and proof.get("background_match")
        and not evaluation.get("expressive_pause")
        and not evaluation.get("rhythm_keep")
        and not (
            (s["end"] - s["start"] <= 2.0)
            and evaluation.get("acoustic_context", {}).get("rising_intonation")
        )
    ):
        bounds = safe_bounds(
            s, transcript, job["media"]["duration"], job["media"]["fps"],
            edit["protected"],
        )
        if bounds:
            s.update(bounds, auto=True)


def _apply_contextual(s, allowed, transcript, job, editorial_protected):
    if s["kind"] != "contextual_restart" or not allowed(s["kind"]):
        return
    removed_ids = set(s["removed_word_ids"])
    removed = [
        w for seg in transcript["segments"] for w in seg["words"]
        if w["id"] in removed_ids
    ]
    proposed = dict(
        s,
        start=max(0, removed[0]["start"] - 0.06),
        end=min(job["media"]["duration"], removed[-1]["end"] + 0.06),
    )
    bounds = safe_bounds(
        proposed, transcript, job["media"]["duration"], job["media"]["fps"],
        editorial_protected,
    )
    if bounds:
        s.update(bounds, auto=True)
    else:
        s["reason"] = (
            "Limite de frame toca fala ou proteção editorial; preservado."
        )


def _apply_editorial_target(s, transcript, job, editorial_protected):
    target = [
        x for x in transcript["segments"]
        if x["start"] == s["start"] and x["end"] == s["end"]
    ]
    if not (
        len(target) == 1
        and target[0]["words"]
        and all(w["probability"] >= 0.9 for w in target[0]["words"])
        and tokens(" ".join(w["text"] for w in target[0]["words"]))
        == tokens(target[0]["text"])
    ):
        return False
    proposed = dict(
        s,
        evidence=[target[0]["id"]],
        start=max(0, target[0]["words"][0]["start"] - 0.06),
        end=min(
            job["media"]["duration"], target[0]["words"][-1]["end"] + 0.06
        ),
    )
    bounds = safe_bounds(
        proposed, transcript, job["media"]["duration"], job["media"]["fps"],
        editorial_protected,
    )
    if bounds:
        s.update(bounds, auto=True)
    return True


def _apply_editorial_words(s, transcript, job, editorial_protected):
    if not s.get("removed_word_ids"):
        return
    words = {
        w["id"]: w for x in transcript["segments"] for w in x["words"]
    }
    removed = [words[i] for i in s["removed_word_ids"] if i in words]
    if not removed:
        return
    all_words = sorted(
        [w for x in transcript["segments"] for w in x["words"]],
        key=lambda w: w["start"],
    )
    first_idx = next(
        (i for i, w in enumerate(all_words) if w["id"] == removed[0]["id"]), None
    )
    last_idx = next(
        (i for i, w in enumerate(all_words) if w["id"] == removed[-1]["id"]), None
    )
    prev_end = (
        all_words[first_idx - 1]["end"]
        if first_idx is not None and first_idx > 0 else 0.0
    )
    next_start = (
        all_words[last_idx + 1]["start"]
        if last_idx is not None and last_idx + 1 < len(all_words)
        else job["media"]["duration"]
    )
    fps = job["media"]["fps"]
    st = math.ceil(max(prev_end, removed[0]["start"] - 0.04) * fps) / fps
    en = math.floor(min(next_start, removed[-1]["end"] + 0.04) * fps) / fps
    proposed = dict(
        s, evidence=s["removed_word_ids"],
        removed_word_ids=s["removed_word_ids"], start=st, end=en,
    )
    bounds = safe_bounds(
        proposed, transcript, job["media"]["duration"], job["media"]["fps"],
        editorial_protected,
    )
    if bounds:
        s.update(bounds, auto=True)


def _apply_editorial_replacement(s, decision, allowed, transcript, job, editorial_protected):
    if s["kind"] != "contextual_restart" and (
        allowed(s["kind"])
        and decision.get("action") == "remove"
        and decision.get("replacement_proven")
    ):
        if not _apply_editorial_target(s, transcript, job, editorial_protected):
            _apply_editorial_words(s, transcript, job, editorial_protected)


def _apply_new_class(s, notes_by_id, allowed, evaluation_mode, human_gate,
                     transcript, job, edit, editorial_protected):
    decision = notes_by_id.get(s["id"], {})
    if s["kind"] == "contextual_restart":
        decision = s["evaluation"]["editorial"]
        notes_by_id[s["id"]] = decision
    s["evaluation"]["human_gate"] = (
        "evaluation_only" if evaluation_mode else human_gate["state"]
    )
    _apply_adaptive(s, allowed, transcript, job, edit)
    _apply_contextual(s, allowed, transcript, job, editorial_protected)
    _apply_editorial_replacement(
        s, decision, allowed, transcript, job, editorial_protected
    )


def _guard_replacements(suggestions, notes_by_id, edit, transcript):
    for s in suggestions:
        if not (s.get("auto") and s["kind"] in (
            "retake", "false_start", "correction", "contextual_restart",
        )):
            continue
        note = notes_by_id[s["id"]]
        present = [x for x in transcript["segments"] if x["id"] in note["replacement_segment_ids"]]
        word_ids = note.get("replacement_word_ids", [])
        present.extend(
            w for x in transcript["segments"] for w in x["words"]
            if w["id"] in word_ids
        )
        if any(overlap(cut, replacement) for cut in edit["cuts"] for replacement in present):
            s.update(
                auto=False,
                reason="A edição anterior já remove parte da tomada substituta; conteúdo preservado.",
            )
            continue
        if any(
            overlap(other, r) for r in present for other in suggestions
            if other["id"] != s["id"] and other.get("auto")
        ):
            s.update(
                auto=False,
                reason="A tomada substituta também seria removida; conteúdo preservado.",
            )


def _guard_protected(suggestions, editorial_protected, edit):
    for s in suggestions:
        if s.get("auto") and any(overlap(s, p) for p in editorial_protected):
            s.update(
                auto=False,
                reason="Proteção editorial ou humana; trecho preservado.",
            )
    human_decisions = any(
        h["action"] in ("cut", "restore", "protect", "undo", "legacy_import")
        for h in edit["history"]
    )
    if human_decisions:
        for s in suggestions:
            if s.get("auto"):
                s.update(
                    auto=False,
                    reason="Decisões humanas preservadas; nenhuma reaplicação automática.",
                )
    return human_decisions


def _sequence_audit(self, job_id, job, transcript, suggestions, edit, context_report,
                    human_decisions, stage):
    fixed_baseline = (
        edit["cuts"] if human_decisions else [
            c for c in edit["cuts"]
            if c["origin"] not in ("automatic", "mechanical")
        ]
    )
    sequence_audit = dict(state="not_requested", restored=[])
    if context_report["state"] != "complete":
        return sequence_audit
    sequence_audit = contextual.audit(
        self.jobs, job_id, transcript, suggestions, fixed_baseline,
        self.config_path, context_report, stage,
    )
    if sequence_audit["state"] == "complete" and sequence_audit["restored"]:
        first_restored = sequence_audit["restored"]
        sequence_audit = contextual.audit(
            self.jobs, job_id, transcript, suggestions, fixed_baseline,
            self.config_path, context_report, stage,
        )
        sequence_audit["previously_restored"] = first_restored
        if sequence_audit["state"] == "complete" and sequence_audit["restored"]:
            for s in suggestions:
                if s["kind"] == "contextual_restart":
                    s.update(
                        auto=False,
                        reason="Conferência não estabilizou; reinício contextual preservado.",
                    )
            sequence_audit["state"] = "restored_to_local"
    return sequence_audit


def validate_and_refine(
    self, job_id, job, transcript, edit, suggestions, notes_by_id,
    editorial_protected, context_report, evaluation_mode, state, stage,
):
    human_gate = acceptance.gate(self.jobs)
    stage("validating")
    def allowed(kind):
        return _allowed(kind, evaluation_mode, human_gate)
    visual_cache = {}
    for s in suggestions:
        if s["id"] in notes_by_id:
            s["evaluation"]["editorial"] = notes_by_id[s["id"]]
        _apply_take_gate(s, allowed)
        if s["kind"] in NEW_CLASSES:
            _apply_new_class(
                s, notes_by_id, allowed, evaluation_mode, human_gate,
                transcript, job, edit, editorial_protected,
            )
        if s.get("auto"):
            # The real helper obtains the validated source from self.jobs.
            _apply_visual_with_source(
                self, s, job_id, job, transcript, editorial_protected, visual_cache
            )
    pacing.seal(pacing.thin(pacing.reconcile(suggestions)), transcript, editorial_protected)
    takes.guard(suggestions, transcript)
    _guard_replacements(suggestions, notes_by_id, edit, transcript)
    human_decisions = _guard_protected(suggestions, editorial_protected, edit)
    sequence_audit = _sequence_audit(
        self, job_id, job, transcript, suggestions, edit, context_report,
        human_decisions, stage,
    )
    return suggestions, human_gate, sequence_audit, allowed


def _apply_visual_with_source(self, suggestion, job_id, job, transcript,
                              editorial_protected, cache):
    try:
        refined, visual = refine_visual(
            self.jobs.validate(job_id), job["media"], suggestion, transcript,
            editorial_protected, cache=cache,
        )
        suggestion.update(start=refined["start"], end=refined["end"])
    except (RuntimeError, subprocess.TimeoutExpired):
        visual = dict(state="unavailable")
    suggestion["evaluation"]["visual"] = visual
    if visual.get("abrupt") or (
        suggestion["kind"] in NEW_CLASSES and visual["state"] != "measured"
    ):
        suggestion.update(
            auto=False,
            reason="Emenda visual abrupta; trecho preservado para revisão.",
        )
