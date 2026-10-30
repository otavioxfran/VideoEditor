import pytest

from video_editor import visual_presence


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
    assert [(run["start"], run["end"]) for run in result] == [(1.0, 3.0), (4.0, 7.0)]
    assert result[0]["mad"] == 12.5


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
