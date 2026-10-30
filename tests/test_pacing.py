import numpy as np
import pytest

from video_editor import pacing
from video_editor.channel_profile import DEFAULT_PROFILE
from video_editor.export.renderer import _graph, crossfade_halves
from video_editor.timeline import build_timeline


def _words(*items):
    return [dict(id=f'w{i}', text=text, start=start, end=end, probability=1)
            for i, (text, start, end) in enumerate(items)]


def _transcript(words):
    return dict(segments=[dict(id='s1', start=words[0]['start'], end=words[-1]['end'],
                               text=' '.join(w['text'] for w in words), words=words)])


def test_residual_pause_depends_on_context_and_length():
    assert pacing.target(1.5, 'hesitation', 'a') < pacing.target(1.5, 'sentence', 'a')
    short, long = pacing.target(1.2, 'sentence', 'x'), pacing.target(12, 'sentence', 'x')
    assert short < long <= pacing.PACE['sentence'][2] * (1 + pacing.JITTER)
    # Doubtful expressive timing is preserved; long ones shrink to a beat near 1 s.
    assert pacing.target(1.8, 'expressive', 'x') == 1.8
    beats = {round(pacing.target(9, 'expressive', seed), 3) for seed in 'abcdefgh'}
    assert len(beats) > 1
    assert all(pacing.EXPRESSIVE_FLOOR * (1 - pacing.EXPRESSIVE_JITTER) - 1e-9 <= v
               <= pacing.EXPRESSIVE_FLOOR * (1 + pacing.EXPRESSIVE_JITTER) + 1e-9 for v in beats)
    # Deterministic variation: same seed, same value; different seeds differ.
    assert pacing.target(3, 'sentence', 'x') == pacing.target(3, 'sentence', 'x')
    assert pacing.target(3, 'sentence', 'x') != pacing.target(3, 'sentence', 'y')


def test_context_reads_punctuation_short_lines_and_turns():
    words = _words(('isso', 0, .3), ('é', .3, .5), ('caro.', .5, .9), ('Mas', 3, 3.2))
    assert pacing.context(words[:3], words[3:]) == 'expressive'  # pause before a turn
    words = _words(('a', 0, .2), ('gente', .2, .5), ('trabalha', .5, .9), ('muito.', .9, 1.2), ('Hoje', 3, 3.2))
    assert pacing.context(words[:4], words[4:]) == 'sentence'
    assert pacing.context(_words(('Meu', 0, .2), ('Deus.', .2, .5)), []) == 'expressive'
    assert pacing.context(_words(('Sério?', 0, .5)), []) == 'expressive'
    assert pacing.context(_words(('e', 0, .2), ('aí', .2, .4)), []) == 'hesitation'


def test_speech_gaps_vary_residuals_and_keep_expressive_pauses():
    words = _words(('a', 0, .3), ('gente', .3, .6), ('trabalha', .6, 1), ('muito.', 1, 1.4),
                   ('Hoje', 6, 6.3), ('em', 6.3, 6.5), ('dia', 6.5, 6.8),
                   ('cansa', 8.3, 8.7), ('demais', 8.7, 9.1), ('sabe?', 9.1, 9.5),
                   ('Pois', 11, 11.3), ('e', 11.3, 11.5), ('isso', 11.5, 11.9))
    gaps = pacing.speech_gaps(_transcript(words), 12.5, DEFAULT_PROFILE)
    by_word = {g['evidence'].get('before_word'): g for g in gaps}
    assert by_word['muito.']['evidence']['context'] == 'sentence'
    assert by_word['dia']['evidence']['context'] == 'hesitation'
    assert by_word['muito.']['evidence']['residual_seconds'] > by_word['dia']['evidence']['residual_seconds']
    assert 'sabe?' not in by_word  # 1.5 s question pause stays whole
    for gap in gaps:
        assert not any(w['start'] < gap['end'] and w['end'] > gap['start'] for w in words)
        assert gap['end'] - gap['start'] >= pacing.MIN_SAVING
        assert gap['evidence']['acoustic'] == 'not_measured'


def test_unpunctuated_long_stop_is_a_thought_break():
    words = _words(('então', 0, .4), ('o', .4, .5), ('brasil', 10, 10.4))
    gap = pacing.speech_gaps(_transcript(words), 11, DEFAULT_PROFILE)[0]
    assert gap['evidence']['context'] == 'sentence'


LEVELS = pacing.Levels


def _levels(db, speech=()):
    levels = object.__new__(LEVELS)
    levels.db, levels.speech = np.array(db, dtype=float), list(speech)
    return levels


def test_untranscribed_speech_splits_the_cut(monkeypatch):
    words = _words(('fim.', 0, .5), ('Volta', 30, 30.4))
    monkeypatch.setattr(pacing, 'Levels', lambda path: _levels([-50] * 3200, [(14, 14.6), (20, 20.05)]))
    gaps = pacing.speech_gaps(_transcript(words), 31, DEFAULT_PROFILE, wav='fake.wav')
    assert len(gaps) == 2 and all(g['auto'] for g in gaps)
    first, second = gaps
    # The 0.6 s murmur stays with padding; the 50 ms click does not stop the cut.
    assert first['end'] == pytest.approx(14 - pacing.VOICE_PAD, abs=.002)
    assert second['start'] == pytest.approx(14.6 + pacing.VOICE_PAD, abs=.002)
    assert first['evidence']['untranscribed_speech'] == [[13.75, 14.85]]


def test_cut_points_snap_to_the_quietest_frame():
    db = [-30.0] * 300
    db[103] = -70.0
    assert _levels(db).quietest(1.0, 0, 3) == pytest.approx(1.035)
    assert _levels(db).quietest(1.0, 0, 1.02) <= 1.02  # never leaves the allowed window


def test_thin_drops_small_cut_around_short_phrase_but_keeps_dead_air():
    small = dict(id='a', kind='silence', auto=True, start=10, end=10.5)
    big = dict(id='b', kind='silence', auto=True, start=11.2, end=30)
    pacing.thin([small, big])
    assert small['auto'] is False and big['auto'] is True
    left = dict(id='c', kind='silence', auto=True, start=40, end=50)
    right = dict(id='d', kind='silence', auto=True, start=51, end=60)
    pacing.thin([left, right])
    assert left['auto'] and right['auto']


def test_seal_swallows_pure_silence_sliver_between_pause_and_take():
    words = _words(('fala', 0, .4), ('segunda', 2, 2.4))
    pause = dict(id='p', kind='silence', auto=True, start=.8, end=1.4)
    take = dict(id='t', kind='stumble', auto=True, start=1.7, end=2)
    pacing.seal([pause, take], _transcript(words))
    assert take['start'] == 1.4


def test_seal_keeps_speech_protected_span_and_long_breath():
    words = _words(('fala', 0, .4), ('a', 1.5, 1.6), ('segunda', 2.4, 2.8))
    left = dict(id='p', kind='silence', auto=True, start=.8, end=1.4)
    right = dict(id='t', kind='stumble', auto=True, start=1.7, end=2)
    pacing.seal([left, right], _transcript(words))
    assert right['start'] == 1.7  # a word lives inside the sliver

    words = _words(('fala', 0, .4), ('segunda', 2.4, 2.8))
    right = dict(id='t', kind='stumble', auto=True, start=1.7, end=2)
    pacing.seal([left, right], _transcript(words), [dict(start=1.45, end=1.6)])
    assert right['start'] == 1.7  # protected editorial beat

    right = dict(id='t', kind='stumble', auto=True, start=1.9, end=2.2)
    pacing.seal([left, right], _transcript(words))
    assert right['start'] == 1.9  # 0.5 s breath is intentional, not a sliver


def test_reconcile_gives_each_pause_one_owner():
    paced = dict(id='gap-1', kind='silence', auto=False, start=5, end=6, reason='preservada',
                 evidence=dict(paced=True))
    adaptive = dict(id='adaptive-1', kind='adaptive_silence', auto=True, start=5.2, end=5.8, evidence={})
    pacing.reconcile([paced, adaptive])
    assert adaptive['auto'] is False and adaptive['reason'] == 'preservada'


def test_crossfade_borrows_from_removed_span_and_keeps_length():
    timeline = build_timeline(10, [dict(start=2, end=2.01), dict(start=5, end=7)])
    halves = crossfade_halves(timeline['keeps'], .06)
    assert halves == pytest.approx([.005, .03])
    graph = _graph(timeline['keeps'], .015, crossfade_seconds=.06)
    assert graph.count('acrossfade=d=') == 2
    # Each audio segment is extended by exactly the halves of its joins.
    assert 'atrim=start=0.000000000:end=2.005000000' in graph
    assert 'atrim=start=2.005000000:end=5.030000000' in graph
    plain = _graph(timeline['keeps'], .015)
    assert 'acrossfade' not in plain and plain.count('afade=t=in') == 3
