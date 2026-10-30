"""Learned channel profile and dynamic preferences adapted from editing actions.

The profile is local, per-machine state (gitignored), never a tracked repository file.
"""

import copy
import json
import os
import threading
from pathlib import Path
from video_editor import storage
from video_editor.settings import BASE_DIR

DEFAULT_PROFILE = {
    "version": 1,
    "channel": "Meu canal",
    "cadence_pause_seconds": 0.55,
    "min_silence_gap": 0.8,
    "max_expressive_pause": 1.8,
    "noise_floor_db": -53.6,
    "speech_margin_lead": 0.22,
    "speech_margin_tail": 0.16,
    "min_word_prob": 0.45,
    "auto_cut_silence": True,
    "auto_cut_retakes": True,
    # Approved by the creator on 2026-10-21: stumbles and clear retakes (takes.py) may be
    # cut without review; repeated ideas and digressions stay suggestions.
    "auto_cut_takes": True,
    # Alternate a slight zoom at visible jump cuts (framing.py); render-only, not cut policy.
    "punch_in": False,
    "punch_in_scale": 1.1,
    "feedback_count": 0,
    "restored_intervals": [],
    "manual_cuts": [],
    "protected_phrases": [],
}


# Only these keys change cut decisions; feedback logs are history, not policy.
DECISION_KEYS = (
    "cadence_pause_seconds",
    "min_silence_gap",
    "max_expressive_pause",
    "noise_floor_db",
    "speech_margin_lead",
    "speech_margin_tail",
    "min_word_prob",
    "auto_cut_silence",
    "auto_cut_retakes",
    "auto_cut_takes",
)


def profile_path() -> Path:
    override = os.getenv("CHANNEL_PROFILE_PATH")
    return Path(override) if override else BASE_DIR / "config" / "channel_profile.local.json"


def decision_parameters(profile: dict | None = None) -> dict:
    profile = load_profile() if profile is None else profile
    return {key: profile.get(key) for key in DECISION_KEYS}


def load_profile() -> dict:
    path = profile_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            merged = copy.deepcopy(DEFAULT_PROFILE)
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(DEFAULT_PROFILE)


def save_profile(profile: dict) -> None:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write(path, profile)


_feedback_lock = threading.Lock()


def record_feedback(action: str, details: dict) -> dict:
    """Learn from human interactions (restore, cut, protect)."""
    # Studio requests run in a thread pool; serialize the read-modify-write.
    with _feedback_lock:
        return _record_feedback(action, details)


def _record_feedback(action: str, details: dict) -> dict:
    prof = load_profile()
    prof["feedback_count"] = prof.get("feedback_count", 0) + 1

    if action == "restore":
        duration = details.get("end", 0) - details.get("start", 0)
        prof.setdefault("restored_intervals", []).append({
            "start": details.get("start"),
            "end": details.get("end"),
            "duration": round(duration, 3),
            "reason": details.get("reason", ""),
        })
        if details.get("text"):
            prof.setdefault("protected_phrases", []).append(details["text"])
        if 0.5 <= duration <= 1.2:
            prof["min_silence_gap"] = min(1.5, round(prof["min_silence_gap"] + 0.05, 2))

    elif action == "cut":
        duration = details.get("end", 0) - details.get("start", 0)
        prof.setdefault("manual_cuts", []).append({
            "start": details.get("start"),
            "end": details.get("end"),
            "duration": round(duration, 3),
        })
        if 0.4 <= duration < prof["min_silence_gap"]:
            prof["min_silence_gap"] = max(0.6, round(prof["min_silence_gap"] - 0.05, 2))

    save_profile(prof)
    return prof


def detect_speech_gaps(transcript: dict, duration: float, profile: dict | None = None, wav=None) -> list:
    """Dead-air proposals between timed words, paced like a human edit (see pacing.py).

    With `wav`, every removed span is checked for untranscribed sound and cut points
    snap to the quietest nearby frame; without it the evidence says 'not_measured'.
    """
    from video_editor import pacing

    if profile is None:
        profile = load_profile()
    intonation = None
    if wav:
        from video_editor.automatic import audio_context

        def intonation(start, end):
            return audio_context(wav, start, end).get('rising_intonation')
    return pacing.speech_gaps(transcript, duration, profile, wav=wav, intonation=intonation)
