import numpy as np
import pytest

from video_editor import visual_presence


BOX = (220.0, 10.0, 70.0, 70.0)


def _frame(person):
    image = np.full(
        (visual_presence.HEIGHT, visual_presence.WIDTH), 100, dtype=np.uint8
    )
    image[:, 205:] = person
    return image


def _samples(entries):
    return [
        dict(
            t=t,
            faces=faces,
            motion=0.0,
            score=0.99 if faces else 0.0,
            gray=_frame(value),
            box=BOX if faces else None,
        )
        for t, faces, value in entries
    ]


def test_runs_ignore_short_gaps_and_measure_long_absence():
    samples = [
        dict(t=0, empty=False, mad=40),
        dict(t=1, empty=True, mad=12),
        dict(t=2, empty=True, mad=13),
        dict(t=3, empty=False, mad=40),
        dict(t=4, empty=True, mad=12),
        dict(t=5, empty=True, mad=12),
        dict(t=6, empty=True, mad=12),
        dict(t=7, empty=False, mad=40),
        dict(t=8, empty=True, mad=12),
        dict(t=9, empty=False, mad=40),
    ]
    result = visual_presence.runs(samples)
    assert [(run["start"], run["end"]) for run in result] == [(4.0, 7.0)]
    assert result[0]["mad"] == 12.0


def test_runs_ignore_two_second_empty_gap():
    samples = [
        dict(t=t, empty=2 <= t <= 3, mad=10, roi_motion=1.0) for t in range(6)
    ]
    assert visual_presence.runs(samples) == []


def test_face_roi_expands_around_face_and_clips_to_frame():
    assert visual_presence._face_roi([BOX]) == pytest.approx(
        [167.5, 0.0, 320.0, 180.0]
    )
    assert visual_presence._face_roi([]) == [0.0, 0.0, 320.0, 180.0]


def test_roi_detects_absence_when_only_the_person_region_changes():
    samples = _samples(
        [
            (0, 1, 220),
            (1, 1, 220),
            (2, 0, 120),
            (3, 0, 120),
            (4, 0, 120),
            (5, 0, 120),
            (6, 1, 220),
            (7, 1, 220),
        ]
    )
    value = visual_presence._evaluate(
        samples, [dict(start=1.5, end=5.5)], 8, "identity", "hash"
    )
    assert value["state"] == "ok"
    assert value["roi"] == [167.5, 0.0, 320.0, 180.0]
    assert value["reference_t"] == 3
    assert [(run["start"], run["end"]) for run in value["absence_runs"]] == [
        (2.0, 6.0)
    ]


def test_static_empty_chair_is_absence():
    samples = _samples(
        [
            (0, 1, 220),
            (1, 1, 220),
            (2, 0, 120),
            (3, 0, 120),
            (4, 0, 120),
            (5, 0, 120),
            (6, 1, 220),
            (7, 1, 220),
        ]
    )
    value = visual_presence._evaluate(
        samples, [dict(start=1.5, end=5.5)], 8, "identity", "hash"
    )
    assert value["state"] == "ok"
    assert value["absence_runs"][0]["seconds"] == 4.0


def test_moving_head_down_without_face_is_not_absence():
    samples = _samples(
        [
            (0, 1, 220),
            (1, 1, 220),
            (2, 0, 120),
            (3, 0, 120),
            (4, 0, 120),
            (5, 0, 120),
            (6, 0, 122),
            (7, 0, 132),
            (8, 0, 122),
            (9, 0, 132),
            (10, 0, 122),
            (11, 0, 132),
            (12, 1, 220),
            (13, 1, 220),
        ]
    )
    value = visual_presence._evaluate(
        samples, [dict(start=1.5, end=5.5)], 14, "identity", "hash"
    )
    assert value["state"] == "ok"
    assert value["reference_t"] == 3
    assert value["absence_runs"] == []


def test_insufficient_separation_reports_face_share_and_no_face_runs():
    samples = _samples(
        [
            (0, 1, 150),
            (1, 1, 150),
            (2, 0, 120),
            (3, 0, 120),
            (4, 0, 120),
            (5, 0, 120),
            (6, 1, 150),
            (7, 1, 150),
            (8, 0, 120),
            (9, 0, 120),
            (10, 0, 120),
        ]
    )
    value = visual_presence._evaluate(
        samples, [dict(start=1.5, end=5.5)], 11, "identity", "hash"
    )
    assert value["state"] == "insufficient_separation"
    assert value["face_share"] == 0.364
    assert [(run["start"], run["end"], run["seconds"]) for run in value["no_face_runs"]] == [
        (2.0, 6.0, 4.0),
        (8.0, 11.0, 3.0),
    ]


def _job_with_words(ready, words):
    segments = [
        dict(
            start=start,
            end=end,
            text=text,
            words=[dict(word=text, start=start, end=end)],
        )
        for text, start, end in words
    ]
    return ready(duration=20, segments=segments)


def test_visual_absence_snaps_to_words_and_stays_review_only(jobs, ready, monkeypatch):
    job = _job_with_words(ready, [("fala", 5, 6), ("depois", 8, 9)])
    job_id = job["job_id"]
    info = jobs.info(job_id)
    edit = jobs.read(job_id, "edit.json")
    transcript = jobs.read(job_id, "transcript.json")
    monkeypatch.setattr(
        visual_presence,
        "analyze",
        lambda *args, **kwargs: dict(
            state="ok",
            absence_runs=[
                dict(start=4.5, end=7.0, seconds=2.5, reference_t=4.0, mad=12.0)
            ],
        ),
    )
    result = visual_presence.candidates(jobs, job_id, info, edit, transcript)
    assert len(result) == 1
    suggestion = result[0]
    assert suggestion["kind"] == "visual_absence"
    assert suggestion["auto"] is False
    assert suggestion["start"] == pytest.approx(4.94)
    assert suggestion["end"] == pytest.approx(6.06)
    assert suggestion["removed_word_ids"] == ["s1-w1"]
    assert "Sem rosto" in suggestion["reason"]
    assert suggestion["evidence"]["speech"] == ["fala"]


def test_visual_absence_extends_over_silence_up_to_the_keep_boundary(
    jobs, ready, monkeypatch
):
    job = _job_with_words(ready, [("fala", 5, 6)])
    job_id = job["job_id"]
    info = jobs.info(job_id)
    edit = dict(jobs.read(job_id, "edit.json"), cuts=[dict(start=0, end=4)])
    transcript = jobs.read(job_id, "transcript.json")
    monkeypatch.setattr(
        visual_presence,
        "analyze",
        lambda *args, **kwargs: dict(
            state="ok",
            absence_runs=[
                dict(start=5.2, end=6.4, seconds=2.0, reference_t=5.0, mad=12.0)
            ],
        ),
    )
    result = visual_presence.candidates(jobs, job_id, info, edit, transcript)
    assert result[0]["start"] == pytest.approx(4.0)
    assert result[0]["end"] == pytest.approx(6.06)


def test_visual_absence_skips_a_span_already_cut(jobs, ready, monkeypatch):
    job = _job_with_words(ready, [("fala", 5, 6)])
    job_id = job["job_id"]
    info = jobs.info(job_id)
    edit = dict(jobs.read(job_id, "edit.json"), cuts=[dict(start=0, end=20)])
    transcript = jobs.read(job_id, "transcript.json")
    monkeypatch.setattr(
        visual_presence,
        "analyze",
        lambda *args, **kwargs: dict(
            state="ok",
            absence_runs=[
                dict(start=4.5, end=7.0, seconds=2.5, reference_t=4.0, mad=12.0)
            ],
        ),
    )
    assert visual_presence.candidates(jobs, job_id, info, edit, transcript) == []


def test_visual_absence_requires_a_confident_empty_reference(
    jobs, ready, monkeypatch
):
    job = _job_with_words(ready, [("fala", 5, 6)])
    job_id = job["job_id"]
    info = jobs.info(job_id)
    edit = jobs.read(job_id, "edit.json")
    transcript = jobs.read(job_id, "transcript.json")
    monkeypatch.setattr(
        visual_presence,
        "analyze",
        lambda *args, **kwargs: dict(state="insufficient_separation"),
    )
    assert visual_presence.candidates(jobs, job_id, info, edit, transcript) == []
