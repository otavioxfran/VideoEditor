"""Automated quality benchmark and self-learning tests.

The fixture keeps the timings of a real 10-minute recording; its content words were replaced
by pseudo-words (the same word always maps to the same pseudo-word, so retakes still repeat).
"""

import json
from pathlib import Path
import pytest

from video_editor.channel_profile import (
    load_profile,
    save_profile,
    record_feedback,
    detect_speech_gaps,
    DEFAULT_PROFILE,
)
from video_editor.cuts import find_candidates, tokens
from video_editor.timeline import build_timeline
from video_editor.automatic import overlap, safe_bounds

DATA_PATH = Path(__file__).parent / "data" / "sample_benchmark_transcript.json"


@pytest.fixture
def benchmark_transcript():
    assert DATA_PATH.exists(), f"Benchmark transcript not found at {DATA_PATH}"
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def test_dead_pause_detection_eliminates_over_180s_of_dead_air(benchmark_transcript):
    """The benchmark recording has ~3.4 minutes of dead pauses that must be removed."""
    profile = dict(DEFAULT_PROFILE, min_silence_gap=0.8, speech_margin_lead=0.22, speech_margin_tail=0.16)
    duration = 599.4
    gaps = detect_speech_gaps(benchmark_transcript, duration, profile)
    
    auto_gaps = [g for g in gaps if g["auto"]]
    total_removed = sum(g["end"] - g["start"] for g in auto_gaps)
    
    # Must remove at least 180 seconds (~3 minutes) of pure silence/hesitation
    assert len(auto_gaps) >= 40, f"Expected at least 40 dead pauses, got {len(auto_gaps)}"
    assert total_removed >= 180.0, f"Expected >= 180s removed, got {total_removed:.1f}s"
    
    # Must specifically cut the known gigantic dead air gaps
    has_40s_gap = any(g["start"] <= 264.0 and g["end"] >= 302.0 for g in auto_gaps)
    has_24s_gap = any(g["start"] <= 65.5 and g["end"] >= 88.0 for g in auto_gaps)
    has_10s_gap = any(g["start"] <= 41.5 and g["end"] >= 50.0 for g in auto_gaps)
    
    assert has_40s_gap, "The 40.7-second dead gap at 04:22 must be cut."
    assert has_24s_gap, "The 24.1-second dead gap at 01:04 must be cut."
    assert has_10s_gap, "The 9.7-second dead gap at 00:40 must be cut."


def test_word_safety_guarantee_zero_spoken_words_cut(benchmark_transcript):
    """Zero words from the transcript may ever be overlapped by dead pause cuts."""
    duration = 599.4
    gaps = detect_speech_gaps(benchmark_transcript, duration)
    auto_gaps = [g for g in gaps if g["auto"]]
    words = [w for s in benchmark_transcript["segments"] for w in s["words"]]
    
    for gap in auto_gaps:
        span = dict(start=gap["start"], end=gap["end"])
        for w in words:
            assert not overlap(span, w), (
                f"Cut {gap['id']} ({gap['start']}->{gap['end']}) collides with word '{w['text']}' ({w['start']}->{w['end']})"
            )


def test_retakes_detection_identifies_real_channel_stumbles(benchmark_transcript):
    """Finds repeated phrases within sentences and qualifies them for cutting."""
    cands = find_candidates(benchmark_transcript)
    retakes = [c for c in cands if c["kind"] == "retake"]
    assert len(retakes) >= 10, f"Expected at least 10 retake candidates, got {len(retakes)}"
    
    # Verify intra-retakes have valid removed words and replacements
    intra = [c for c in retakes if c["id"].startswith("intra-") and c.get("replacement_word_ids")]
    assert len(intra) >= 10
    
    # Check that major stumbles are present
    assert any(c["id"] == "intra-s58-3-6" for c in intra), "6.5s retake at s58 must be detected"
    assert any(c["id"] == "intra-s10-0-9" for c in intra), "3.0s retake at s10 must be detected"


def test_channel_profile_self_learning_from_studio_actions(tmp_path, monkeypatch):
    """The channel profile adapts when creator restores or manually cuts pauses."""
    prof_file = tmp_path / "channel_profile.json"
    monkeypatch.setattr("video_editor.channel_profile.profile_path", lambda: prof_file)
    
    # Starts with default min_silence_gap of 0.8s
    prof = load_profile()
    initial_gap = prof["min_silence_gap"]
    assert initial_gap == 0.8
    
    # Human restores a pause: system learns creator prefers more breathing room
    updated = record_feedback("restore", {"start": 10.0, "end": 10.8, "reason": "Pausa cômica"})
    assert updated["feedback_count"] == 1
    assert updated["min_silence_gap"] == 0.85
    assert len(updated["restored_intervals"]) == 1
    
    # Human manually cuts a short pause: system learns creator wants tighter cuts
    updated2 = record_feedback("cut", {"start": 20.0, "end": 20.6})
    assert updated2["feedback_count"] == 2
    assert updated2["min_silence_gap"] == 0.80
    assert len(updated2["manual_cuts"]) == 1


def test_empty_chair_and_long_absence_never_classified_as_expressive(benchmark_transcript):
    """A 24s pause where creator leaves the chair cannot be preserved as expressive, even with rising pitch."""
    from video_editor.channel_profile import detect_speech_gaps
    from video_editor.automatic import classify

    gaps = detect_speech_gaps(benchmark_transcript, 599.4)
    classified = classify(benchmark_transcript, gaps, 599.4, 30.0)
    gap_chair = next((c for c in classified if c["start"] <= 65.5 and c["end"] >= 88.0), None)
    assert gap_chair is not None, "24s gap where creator left the chair must be present"
    assert gap_chair["auto"] is True, f"Empty chair gap must be auto=True, got {gap_chair.get('auto')}"
    assert gap_chair["end"] - gap_chair["start"] >= 23.0

