import copy

import pytest
from fastapi.testclient import TestClient

from video_editor import productivity
from video_editor.editing import RevisionConflict, mutate
from video_editor.export.service import Exports
from video_editor.server.app import create_app
from video_editor.worker import Worker


@pytest.fixture
def timing(jobs, ready, monkeypatch):
    job = ready()
    jid = job['job_id']
    edit = jobs.read(jid, 'edit.json')
    edit['cuts'] = [dict(start=3, end=6, origin='automatic', suggestion_id='a'),
                    dict(start=10, end=12, origin='automatic', suggestion_id='b')]
    jobs.write(jid, 'edit.json', edit)
    jobs.write(jid, 'automatic_report.json', dict(revision=0,
        policy_hash=productivity.policy_hash(), transcript_hash=edit['transcript_hash'],
        applied_cuts=[dict(id='a', start=3, end=6), dict(id='b', start=10, end=12)]))
    exports = Exports(jobs, Worker(), lambda snapshot, output, root:
                      (output.write_bytes(b'isolated exported media fixture') and {}))
    render = exports.request(jid, 0, enqueue=False)
    exports.execute(jid, render['render_id'])
    jobs.write(jid, 'automatic.json', dict(state='complete', render_id=render['render_id']))
    clock = ['2026-10-07T00:00:00+00:00']
    monkeypatch.setattr(productivity.storage, 'now', lambda: clock[0])
    return jid, exports, clock


def command(jobs, exports, jid, action, state=None, **values):
    request = dict(action=action, expected_revision=jobs.read(jid, 'edit.json')['revision'], **values)
    if state:
        request.update(session_id=state['session']['id'], expected_session_revision=state['session']['revision'])
    return productivity.transition(jobs, exports, jid, request)


def test_elapsed_pause_resume_partial_restore_and_frozen_result(jobs, timing):
    jid, exports, clock = timing
    original = {name: jobs.path(jid, name).read_bytes() for name in
                ('job.json', 'automatic.json', 'automatic_report.json', 'transcript.json')}
    running = command(jobs, exports, jid, 'start', manual_minutes=10, manual_basis='estimated')
    clock[0] = '2026-10-07T00:02:00+00:00'
    paused = command(jobs, exports, jid, 'pause', running)
    clock[0] = '2026-10-07T00:12:00+00:00'
    assert productivity.status(jobs, jid)['session']['elapsed_seconds'] == 120
    resumed = command(jobs, exports, jid, 'resume', paused)
    mutate(jobs, jid, dict(expected_revision=0, action='restore', start=3, end=4))
    clock[0] = '2026-10-07T00:13:00+00:00'
    ended = command(jobs, exports, jid, 'finish', resumed)
    assert ended['session']['elapsed_seconds'] == 180
    assert ended['session']['minutes_saved'] == 7
    assert ended['session']['comparison'] == dict(intact_cuts=1, partially_restored=1,
        fully_restored=0, restored_seconds=1, current_revision=1)
    mutate(jobs, jid, dict(expected_revision=1, action='restore', start=3, end=12))
    assert productivity.status(jobs, jid)['session']['comparison'] == ended['session']['comparison']
    assert original == {name: jobs.path(jid, name).read_bytes() for name in original}
    assert not jobs.path(jid, 'human_acceptance.json').exists()


def test_status_is_read_only_and_no_baseline_never_invents_savings(jobs, timing):
    jid, exports, clock = timing
    assert productivity.status(jobs, jid)['state'] == 'not_started'
    assert not jobs.path(jid, 'productivity.json').exists()
    running = command(jobs, exports, jid, 'start')
    before = jobs.path(jid, 'productivity.json').read_bytes()
    clock[0] = '2026-10-07T00:03:00+00:00'
    assert productivity.status(jobs, jid)['session']['elapsed_seconds'] == 180
    assert jobs.path(jid, 'productivity.json').read_bytes() == before
    ended = command(jobs, exports, jid, 'finish', running)
    assert ended['session']['minutes_saved'] is None
    assert ended['session']['manual_minutes'] is None
    assert ended['session']['role'] == 'self_reported_timing'


def test_stale_tabs_and_invalid_transitions_are_non_destructive(jobs, timing):
    jid, exports, clock = timing
    running = command(jobs, exports, jid, 'start')
    with pytest.raises(RevisionConflict):
        command(jobs, exports, jid, 'start')
    paused = command(jobs, exports, jid, 'pause', running)
    before = jobs.path(jid, 'productivity.json').read_bytes()
    with pytest.raises(RevisionConflict):
        command(jobs, exports, jid, 'finish', running)
    with pytest.raises(RevisionConflict):
        command(jobs, exports, jid, 'pause', paused)
    assert jobs.path(jid, 'productivity.json').read_bytes() == before
    command(jobs, exports, jid, 'finish', paused)


def test_api_contract_rejects_bad_times_and_stale_source(jobs, timing):
    jid, exports, clock = timing
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        prefix = '/api/jobs/' + jid + '/productivity'
        assert client.get(prefix).json()['state'] == 'not_started'
        for minutes in (0, -1, 'NaN'):
            assert client.post(prefix, json=dict(action='start', expected_revision=0,
                                                 manual_minutes=minutes)).status_code == 422
        assert client.post(prefix, json=dict(action='pause', expected_revision=0)).status_code == 422
        result = client.post(prefix, json=dict(action='start', expected_revision=0))
        assert result.status_code == 200
        session = result.json()['session']
        assert client.post(prefix, json=dict(action='finish', expected_revision=99,
            session_id=session['id'], expected_session_revision=0)).status_code == 409
        report = jobs.read(jid, 'automatic_report.json')
        report['revision'] = 999
        jobs.write(jid, 'automatic_report.json', report)
        clock[0] = '2026-10-07T00:01:00+00:00'
        # A session keeps its initial report; restoring edits and regenerating the
        # report during review must not substitute the initial comparison silently.
        result = client.post(prefix, json=dict(action='finish', expected_revision=0,
            session_id=session['id'], expected_session_revision=0))
        assert result.status_code == 200 and result.json()['session']['elapsed_seconds'] == 60
        assert client.post(prefix, json=dict(action='start', expected_revision=0)).status_code == 422


def test_timing_preserves_same_cut_reapplied_by_human(jobs, timing):
    jid, exports, clock = timing
    running = command(jobs, exports, jid, 'start')
    edit = jobs.read(jid, 'edit.json')
    original = copy.deepcopy(edit)
    edit['cuts'] = [dict(c, origin='human', suggestion_id=None) for c in edit['cuts']]
    edit['revision'] += 1
    jobs.write(jid, 'edit.json', edit)
    result = command(jobs, exports, jid, 'finish', running)
    assert result['session']['comparison']['intact_cuts'] == len(original['cuts'])
    assert result['session']['comparison']['restored_seconds'] == 0
