from concurrent.futures import ThreadPoolExecutor
import pytest
from video_editor.editing import mutate, view, RevisionConflict
from video_editor.timeline import digest


def test_real_concurrent_edit_one_wins(jobs, ready):
    job = ready()
    request = dict(expected_revision=0, action='cut', start=3, end=8)
    def write():
        try:
            mutate(jobs, job['job_id'], request)
            return 'ok'
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == ['conflict', 'ok']
    assert view(jobs, job['job_id'])['edit']['revision'] == 1


def test_two_jobs_markers_undo_and_partial_restore(jobs, ready):
    a, b = ready(), ready('another.mp4')
    aid, bid = a['job_id'], b['job_id']
    mutate(jobs, aid, dict(expected_revision=0, action='cut', start=3, end=8))
    mutate(jobs, aid, dict(expected_revision=1, action='marker', source_time=15, label='Fim'))
    assert view(jobs, aid)['markers'][0]['output_time'] == 10
    mutate(jobs, aid, dict(expected_revision=2, action='restore', start=5, end=6))
    assert view(jobs, aid)['timeline']['cuts'] == [[3, 5], [6, 8]]
    mutate(jobs, aid, dict(expected_revision=3, action='undo'))
    assert view(jobs, aid)['timeline']['cuts'] == [[3, 8]]
    assert view(jobs, bid)['edit']['revision'] == 0
    assert not view(jobs, bid)['markers']


def test_changed_transcript_cannot_reapply_edit(jobs, ready):
    job = ready()
    transcript = jobs.read(job['job_id'], 'transcript.json')
    transcript['segments'][0]['text'] = 'Outra fala'
    jobs.write(job['job_id'], 'transcript.json', transcript)
    with pytest.raises(ValueError, match='Transcrição alterada'):
        mutate(jobs, job['job_id'], dict(expected_revision=0, action='cut', start=3, end=8))


def test_cut_seals_short_silent_gap_between_neighbor_cuts(jobs, ready):
    jid = ready()['job_id']
    mutate(jobs, jid, dict(expected_revision=0, action='cut', start=3, end=8))
    mutate(jobs, jid, dict(expected_revision=1, action='cut', start=8.3, end=9))
    assert view(jobs, jid)['timeline']['cuts'] == [[3, 9]]


def test_cut_keeps_gap_that_has_speech(jobs, ready):
    segments = [
        dict(start=1, end=2, text='Primeira.', words=[dict(word='Primeira.', start=1, end=2)]),
        dict(start=8.05, end=8.25, text='fala', words=[dict(word='fala', start=8.05, end=8.25)]),
        dict(start=15, end=16, text='Última.', words=[dict(word='Última.', start=15, end=16)]),
    ]
    jid = ready(segments=segments)['job_id']
    mutate(jobs, jid, dict(expected_revision=0, action='cut', start=3, end=8))
    mutate(jobs, jid, dict(expected_revision=1, action='cut', start=8.3, end=9))
    assert view(jobs, jid)['timeline']['cuts'] == [[3, 8], [8.3, 9]]


def test_cut_keeps_protected_and_long_gaps(jobs, ready):
    jid = ready()['job_id']
    mutate(jobs, jid, dict(expected_revision=0, action='protect', start=8, end=8.3))
    mutate(jobs, jid, dict(expected_revision=1, action='cut', start=3, end=8))
    mutate(jobs, jid, dict(expected_revision=2, action='cut', start=8.3, end=9))
    mutate(jobs, jid, dict(expected_revision=3, action='cut', start=10, end=11))
    assert view(jobs, jid)['timeline']['cuts'] == [[3, 8], [8.3, 9], [10, 11]]


def test_decisions_follow_history_and_undo(jobs, ready):
    jid = ready()['job_id']
    jobs.write(jid, 'suggestions.json', [
        dict(id='b', kind='repeated_block', auto=False, start=3, end=4, reason='x'),
        dict(id='v', kind='visual_absence', auto=False, start=6, end=8, reason='x'),
    ])
    mutate(jobs, jid, dict(expected_revision=0, action='cut', suggestion_id='b'))
    mutate(jobs, jid, dict(expected_revision=1, action='restore', suggestion_id='v'))
    assert view(jobs, jid)['decisions'] == {'b': 'applied', 'v': 'kept'}
    mutate(jobs, jid, dict(expected_revision=2, action='undo'))
    assert view(jobs, jid)['decisions'] == {'b': 'applied'}
    mutate(jobs, jid, dict(expected_revision=3, action='restore', suggestion_id='b'))
    assert view(jobs, jid)['decisions'] == {'b': 'kept'}
