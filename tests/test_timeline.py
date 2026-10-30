import math
import pytest
from video_editor.timeline import build_timeline, output_to_source, source_to_output
from video_editor.export.subtitles import cues, srt


def test_micro_cut_is_not_resurrected():
    timeline = build_timeline(20, [{'start': 10, 'end': 10.03}])
    assert len(timeline['keeps']) == 2
    assert timeline['duration'] == pytest.approx(19.97)
    assert output_to_source(timeline, 10) == pytest.approx(10.03)
    assert source_to_output(timeline, 10.015) == 10


def test_retained_short_pause_is_not_merged():
    timeline = build_timeline(5, [dict(start=1, end=2), dict(start=2.03, end=3)])
    assert len(timeline['keeps']) == 3
    assert timeline['keeps'][1]['source_end'] - timeline['keeps'][1]['source_start'] == pytest.approx(.03)


@pytest.mark.parametrize('start,end', [(-1, 2), (2, 2), (3, 2), (0, 21), (math.nan, 1), (0, math.inf)])
def test_invalid_cut_rejected(start, end):
    with pytest.raises(ValueError):
        build_timeline(20, [dict(start=start, end=end)])


def test_subtitles_preserve_kept_pauses_and_clip_at_cuts(jobs, ready):
    job = ready()
    transcript = jobs.read(job['job_id'], 'transcript.json')
    timeline = build_timeline(20, [dict(start=3, end=8)])
    result = cues(transcript, timeline)
    assert result[-1]['start'] == 10
    assert result[-1]['end'] == 11
    assert '00:00:10,000 --> 00:00:11,000' in srt(transcript, timeline)
    timeline = build_timeline(20, [dict(start=1.25, end=1.75)])
    result = cues(transcript, timeline)
    assert result[0]['start'] == 1
    assert result[0]['end'] == 1.25
    assert result[1]['start'] == 1.25
    assert result[1]['end'] == 1.5


def test_frame_alignment_is_part_of_preview_contract():
    timeline = build_timeline(20, [dict(start=10, end=10.03)], 60)
    assert timeline['cuts'] == [[10, pytest.approx(10 + 2 / 60)]]
