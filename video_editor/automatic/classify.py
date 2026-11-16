"""Deterministic classification of automatic candidates."""
import copy
import re

from video_editor.cuts import tokens
from .bounds import safe_bounds
from .constants import NEW_CLASSES, POLICY_VERSION
from .context import audio_context

def classify(transcript, suggestions, duration, fps, wav=None, protected=()):
    """Preserve expressive pauses; novel removal classes remain human-gated."""
    segments = transcript["segments"]
    all_words = sorted(
        (w for s in segments for w in s["words"]), key=lambda w: (w["start"], w["end"])
    )
    output = copy.deepcopy(suggestions)
    gaps = [
        b["start"] - a["end"]
        for a, b in zip(all_words, all_words[1:])
        if 0.12 <= b["start"] - a["end"] <= 2.5
    ]
    rhythm = sorted(gaps)[len(gaps) // 2] if gaps else 0.65
    gap_events = [
        (b["start"], b["start"] - a["end"])
        for a, b in zip(all_words, all_words[1:])
        if 0.12 <= b["start"] - a["end"] <= 2.5
    ]
    for candidate in output:
        nearby_gaps = [
            gap
            for timestamp, gap in gap_events
            if candidate["start"] - 30 <= timestamp <= candidate["end"] + 30
        ]
        local_rhythm = (
            sorted(nearby_gaps)[len(nearby_gaps) // 2]
            if len(nearby_gaps) >= 3
            else rhythm
        )
        candidate["evaluation"] = dict(
            policy=POLICY_VERSION, local_pause_seconds=round(local_rhythm, 3)
        )
        paced = isinstance(candidate.get("evidence"), dict) and candidate["evidence"].get("paced")
        # Paced proposals already chose their residual pause by context (pacing.py).
        if candidate["kind"] in ("silence", "adaptive_silence") and not paced:
            left = [w for w in all_words if w["end"] <= candidate["start"]]
            right = [w for w in all_words if w["start"] >= candidate["end"]]
            previous = left[-1] if left else None
            following = right[0] if right else None
            preceding = " ".join(w["text"] for w in left[-12:])
            continuation = " ".join(w["text"] for w in right[:12])
            sentence = re.split(r"[.!?…]", preceding.rstrip(".!?…"))[-1]
            pause_dur = candidate["end"] - candidate["start"]
            expressive = (
                pause_dur <= 2.0
                and (
                    not previous
                    or not following
                    or previous["text"].rstrip().endswith(("?", "!", "...", "…"))
                    or (len(tokens(sentence)) <= 4)
                    or any(any(c.isdigit() for c in t) for t in tokens(preceding)[-6:])
                    or " ".join(tokens(continuation)).startswith(
                        ("mas ", "porem ", "so que ", "por exemplo ")
                    )
                )
            )
            features = (
                audio_context(wav, previous["end"], following["start"])
                if wav and previous and following
                else {}
            )
            candidate["evaluation"].update(
                expressive_pause=expressive, acoustic_context=features
            )
            if expressive or (pause_dur <= 2.0 and features.get("rising_intonation")):
                candidate.update(
                    auto=False,
                    reason="Pausa possivelmente expressiva; preservada para manter seu ritmo.",
                )
            elif candidate.get("auto") or candidate["kind"] == "adaptive_silence":
                # Retain at least the observed local rhythm, rather than one fixed pause everywhere.
                target = min(1.2, max(0.65, local_rhythm))
                extra = max(0, target - 0.65) / 2
                if candidate["end"] - candidate["start"] - 2 * extra < 0.08:
                    candidate.update(
                        auto=False,
                        reason="Esta pausa já acompanha o ritmo da fala; preservada.",
                    )
                    candidate["evaluation"]["rhythm_keep"] = True
                else:
                    candidate.update(
                        start=candidate["start"] + extra, end=candidate["end"] - extra
                    )
        if candidate["kind"] in NEW_CLASSES:
            candidate["auto"] = False
            candidate["evaluation"]["human_gate"] = "pending_five_videos"
        if candidate.get("auto"):
            bounds = safe_bounds(candidate, transcript, duration, fps, protected)
            if bounds:
                candidate.update(bounds)
            else:
                candidate.update(
                    auto=False,
                    reason="Limite de frame toca fala ou trecho protegido; preservado.",
                )
    return output
