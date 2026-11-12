import wave

import numpy as np
import pytest

from video_editor import voice_presence


def test_robust_centroid_ignores_outliers():
    base = np.tile(np.array([1.0, 0.0, 0.0], dtype=np.float32), (10, 1))
    base += np.array(
        [[0.01, 0.01, 0], [0.02, -0.01, 0], [-0.01, 0.02, 0]] * 3
        + [[0, 0, 0]],
        dtype=np.float32,
    )
    outliers = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    centroid = voice_presence.robust_centroid(np.vstack([base, outliers]))
    assert float(centroid @ np.array([1.0, 0.0, 0.0])) > .99
    assert float(centroid @ outliers[0]) < .2


def test_aggregate_merges_and_drops_short_runs():
    windows = [(0, 1), (1, 2), (2, 3), (5, 6), (6, 7), (10, 11)]
    scores = np.array([.1, .2, .8, .1, .1, .2])
    result = voice_presence.aggregate(windows, scores, threshold=.5)
    assert [(item['start'], item['end']) for item in result] == [(0, 2), (5, 7)]
    assert result[0]['indices'] == [0, 1]
    short = voice_presence.aggregate(windows[-1:], np.array([.1]), threshold=.5)
    assert short == []


def test_detect_flags_only_windows_below_the_threshold():
    windows = [(0, 1.5), (2, 3.5), (4, 5.5)]
    embeddings = np.array(
        [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32
    )
    levels = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    brightness = np.array([10.0, 10.0, 10.0], dtype=np.float32)
    value = voice_presence.detect(
        windows, embeddings, levels, brightness, None, threshold=.5
    )
    assert value['state'] == 'ok'
    assert len(value['intervals']) == 1
    assert (value['intervals'][0]['start'], value['intervals'][0]['end']) == (4, 5.5)
    assert value['intervals'][0]['score'] == pytest.approx(0.0, abs=1e-6)


def _write_wav(path, seconds=4):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * 16000 * seconds)


def _transcript():
    return dict(
        segments=[
            dict(
                id='s1',
                start=.2,
                end=4.0,
                text='fala',
                words=[
                    dict(id='s1-w1', word='fala', start=.2, end=2.0),
                    dict(id='s1-w2', word='mais', start=2.0, end=4.0),
                ],
            )
        ]
    )


def test_analyze_caches_ok_results(tmp_path):
    audio = tmp_path / 'audio.wav'
    _write_wav(audio)
    calls = []

    def extractor(audio_values, rate, windows):
        calls.append(len(windows))
        return (
            np.array([[1.0, 0.0, 0.0]] * len(windows), dtype=np.float32),
            np.array([10.0] * len(windows), dtype=np.float32),
            np.array([1.0] * len(windows), dtype=np.float32),
        )

    cache = tmp_path / 'voice' / 'voice_presence.json'
    first = voice_presence.analyze(
        audio, _transcript(), 4, dict(path='video.mp4'), cache_path=cache,
        extractor=extractor,
    )
    assert first['state'] == 'ok'
    assert cache.is_file()

    def fail(*args):
        raise AssertionError('cache não foi usado')

    second = voice_presence.analyze(
        audio, _transcript(), 4, dict(path='video.mp4'), cache_path=cache,
        extractor=fail,
    )
    assert second == first
    assert len(calls) == 1


def test_analyze_reports_unavailable_without_dependencies(monkeypatch):
    monkeypatch.setattr(voice_presence, 'available', lambda: False)
    value = voice_presence.analyze(
        'missing.wav', _transcript(), 4, dict(path='video.mp4')
    )
    assert value == dict(state='unavailable', reason='dependencies_missing')

def test_resolve_model_rejects_wrong_hash(tmp_path):
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'bad')
    assert voice_presence.resolve_model(path) == (None, 'model_hash')


def test_analyze_reports_model_hash(monkeypatch, tmp_path):
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'bad')
    monkeypatch.setattr(voice_presence, 'available', lambda: True)
    monkeypatch.setattr(voice_presence, 'model_path', lambda: path)
    value = voice_presence.analyze('missing.wav', _transcript(), 4, {})
    assert value == dict(state='unavailable', reason='model_hash')


def test_background_voice_candidates_from_intervals(jobs, ready, monkeypatch):
    from video_editor import automatic

    job = ready(
        duration=20,
        segments=[
            dict(start=1, end=3, text='fala mais', words=[
                dict(word='fala', start=1, end=2),
                dict(word='mais', start=2, end=3),
            ]),
            dict(start=8, end=10, text='depois', words=[
                dict(word='depois', start=8, end=10),
            ]),
        ],
    )
    job_id = job['job_id']
    monkeypatch.setattr(voice_presence, 'available', lambda: True)
    monkeypatch.setattr(
        voice_presence,
        'analyze',
        lambda *args, **kwargs: dict(
            state='ok',
            windows=12,
            reference_windows=5,
            threshold=.45,
            intervals=[
                dict(
                    start=1.0,
                    end=3.0,
                    score=.2,
                    evidence=dict(rms_ratio=.3, windows=2),
                )
            ],
        ),
    )
    found, state = automatic.background_voice_candidates(
        jobs, job_id, job, jobs.read(job_id, 'transcript.json')
    )
    assert state['state'] == 'ok'
    assert len(found) == 1
    suggestion = found[0]
    assert suggestion['kind'] == 'background_voice'
    assert suggestion['auto'] is False
    assert suggestion['start'] == 1.0
    assert suggestion['end'] == 3.0
    assert suggestion['evidence']['score'] == .2
    assert suggestion['evidence']['rms_ratio'] == .3
    assert suggestion['evidence']['speech'] == ['fala', 'mais']
    assert len(suggestion['removed_word_ids']) == 2
    job_info = jobs.info(job_id)
    cut_all = automatic.background_voice_candidates(
        jobs, job_id, job_info, jobs.read(job_id, 'transcript.json'),
        dict(cuts=[dict(start=0.5, end=3.5)]),
    )[0]
    assert cut_all == []
    partly = automatic.background_voice_candidates(
        jobs, job_id, job_info, jobs.read(job_id, 'transcript.json'),
        dict(cuts=[dict(start=0.5, end=1.5)]),
    )[0]
    assert [(s['start'], s['end']) for s in partly] == [(1.5, 3.0)]


def test_background_voice_unavailable_does_not_break(jobs, ready, monkeypatch):
    from video_editor import automatic

    job = ready(duration=20)
    monkeypatch.setattr(voice_presence, 'available', lambda: False)
    found, state = automatic.background_voice_candidates(
        jobs, job['job_id'], job, jobs.read(job['job_id'], 'transcript.json')
    )
    assert found == []
    assert state == dict(state='unavailable', reason='dependencies_missing')


def test_background_voice_failure_does_not_break(jobs, ready, monkeypatch):
    from video_editor import automatic

    job = ready(duration=20)
    monkeypatch.setattr(voice_presence, 'available', lambda: True)

    def fail(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(voice_presence, 'analyze', fail)
    found, state = automatic.background_voice_candidates(
        jobs, job['job_id'], job, jobs.read(job['job_id'], 'transcript.json')
    )
    assert found == []
    assert state['state'] == 'failed'

def test_short_windows_use_stricter_threshold():
    windows = [(0, 1.0), (1, 3.0)]
    kept_out = voice_presence.aggregate(windows, np.array([.35, .35]), threshold=.45)
    assert [(item['start'], item['end']) for item in kept_out] == [(1.0, 3.0)]
    included = voice_presence.aggregate(windows, np.array([.25, .25]), threshold=.45)
    assert [(item['start'], item['end']) for item in included] == [(0.0, 3.0)]
