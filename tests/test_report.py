import os
from pathlib import Path

import pytest

from video_editor import acceptance, report
from video_editor.channel_profile import load_profile, profile_path, save_profile
from video_editor.editing import mutate, RevisionConflict
from video_editor.timeline import digest
from video_editor.transcription import normalize


def test_tests_never_touch_the_real_channel_profile(jobs, ready):
    real = Path(__file__).resolve().parent.parent / 'config/channel_profile.local.json'
    before = real.read_bytes() if real.exists() else None
    job = ready()
    mutate(jobs, job['job_id'], dict(expected_revision=0, action='cut', start=3, end=8))
    assert profile_path() != real
    assert load_profile()['feedback_count'] == 1
    assert (real.read_bytes() if real.exists() else None) == before


def test_rejected_edit_does_not_train_profile(jobs, ready, monkeypatch):
    job = ready()
    mutate(jobs, job['job_id'], dict(expected_revision=0, action='cut', start=3, end=8))
    with pytest.raises(RevisionConflict):
        mutate(jobs, job['job_id'], dict(expected_revision=0, action='cut', start=9, end=10))
    original = jobs.write

    def failing(job_id, relative, value):
        if relative == 'edit.json':
            raise OSError('disco cheio')
        return original(job_id, relative, value)

    monkeypatch.setattr(jobs, 'write', failing)
    with pytest.raises(OSError):
        mutate(jobs, job['job_id'], dict(expected_revision=1, action='cut', start=9, end=10))
    assert load_profile()['feedback_count'] == 1


def test_policy_hash_follows_decision_thresholds_not_feedback_history():
    initial = acceptance.policy_hash()
    profile = load_profile()
    profile['manual_cuts'].append(dict(start=1, end=2, duration=1))
    profile['feedback_count'] += 1
    save_profile(profile)
    assert acceptance.policy_hash() == initial
    profile['min_silence_gap'] += .05
    save_profile(profile)
    assert acceptance.policy_hash() != initial


def test_profile_override_path_is_used(tmp_path):
    assert profile_path() == Path(os.environ['CHANNEL_PROFILE_PATH'])


def _word(text, start, end):
    return dict(word=text, start=start, end=end)


def test_report_measures_uniform_pauses_and_remaining_retakes(jobs, ready):
    take = 'ninguém mais quer ficar rico devagar'.split()
    first = [_word(t, 1 + i * .3, 1.25 + i * .3) for i, t in enumerate(take)]
    second = [_word(t, 10 + i * .3, 10.25 + i * .3) for i, t in enumerate(take + ['mano.'])]
    segments = [dict(start=1, end=first[-1]['end'], text=' '.join(take), words=first),
                dict(start=10, end=second[-1]['end'], text=' '.join(take) + ' mano.', words=second)]
    job = ready(duration=20, segments=segments)
    job_id = job['job_id']
    transcript = normalize(segments, job['source'])
    gap_start, gap_end = first[-1]['end'], second[0]['start']
    jobs.write(job_id, 'edit.json', dict(schema=1, revision=0, transcript_hash=digest(transcript),
               cuts=[dict(start=gap_start + .2, end=gap_end - .2, origin='automatic', kind='silence')],
               protected=[], markers=[], undo=[], history=[]))
    value = report.build(jobs, job_id)
    assert value['cuts']['by_kind'] == {'silence': 1}
    assert value['pauses']['count'] == 1
    assert value['pauses']['residual']['p50'] == pytest.approx(.4, abs=1 / 30 * 2)
    assert value['pauses']['sentence_final_intact'] == 0
    assert [r['first_id'] for r in value['remaining_retakes']] == ['s1']
    assert value['export'] is None
    assert 'Possíveis retakes ainda no vídeo: 1' in report.text(value)


def test_report_is_read_only(jobs, ready):
    job = ready()
    directory = jobs.directory(job['job_id'])
    before = {p: p.stat().st_mtime_ns for p in directory.rglob('*') if p.is_file()}
    report.text(report.build(jobs, job['job_id']))
    assert {p: p.stat().st_mtime_ns for p in directory.rglob('*') if p.is_file()} == before

def test_report_counts_adjacent_cuts_as_edit_decisions(jobs, ready):
    job = ready()
    transcript = jobs.read(job['job_id'], 'transcript.json')
    jobs.write(job['job_id'], 'edit.json', dict(schema=1, revision=0, transcript_hash=digest(transcript),
               cuts=[dict(start=3, end=4, origin='automatic', kind='silence'),
                     dict(start=4, end=5, origin='automatic', kind='stumble')],
               protected=[], markers=[], undo=[], history=[]))
    value = report.build(jobs, job['job_id'])
    assert value['cuts']['count'] == 2
    assert sum(value['cuts']['by_kind'].values()) == 2
    assert value['keeps']['count'] == 2
