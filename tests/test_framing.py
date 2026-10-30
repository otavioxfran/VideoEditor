from video_editor import framing
from video_editor.timeline import build_timeline

ON = dict(enabled=True, scale=1.1)


def _transcript(*words):
    return dict(segments=[dict(id='s1', words=[dict(id=f'w{i}', start=a, end=b) for i, (a, b) in enumerate(words)])])


def test_alternates_only_on_visible_cuts_and_holds_framing():
    words = _transcript((10.2, 10.5), (30.1, 30.4))
    cuts = [dict(start=5, end=5.4),      # short pause: barely visible, keep framing
            dict(start=10, end=10.6),    # removes speech, but the next shot is 0.9 s: no flicker
            dict(start=11.5, end=13),    # 1.5 s of dead air before a long shot: zoom in here
            dict(start=20, end=25),      # long dead air: zoom out
            dict(start=30, end=30.5)]    # removes speech: zoom in
    timeline = build_timeline(60, cuts, 30)
    assert framing.plan(timeline, words, ON) == [1.0, 1.0, 1.0, 1.1, 1.0, 1.1]


def test_disabled_or_single_keep_never_zooms():
    timeline = build_timeline(60, [dict(start=10, end=20)], 30)
    assert framing.plan(timeline, _transcript(), dict(enabled=False, scale=1.1)) == [1.0, 1.0]
    assert framing.plan(build_timeline(60, [], 30), _transcript(), ON) == [1.0]


def test_settings_clamp_scale_and_follow_profile():
    assert framing.settings(dict(punch_in=False))['enabled'] is False
    assert framing.settings(dict(punch_in_scale=3))['scale'] == framing.MAX_SCALE
    assert framing.settings(dict(punch_in_scale=.5))['scale'] == 1.0


def test_filter_crops_around_eye_height_and_restores_size():
    value = framing.video_filter(1.1, 1920, 1080)
    assert 'y=(ih-oh)*0.4' in value and 'scale=1920:1080' in value
    assert framing.video_filter(1.0, 1920, 1080) == '' and framing.video_filter(1.1, None, None) == ''
