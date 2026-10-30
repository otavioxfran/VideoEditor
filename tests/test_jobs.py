import pytest
from video_editor import storage
from video_editor.jobs import Jobs
from video_editor.editing import view


def test_legacy_read_pure_and_human_decisions_preserved(jobs, ready):
    created = ready()
    directory = jobs.root / 'legacy'
    directory.mkdir()
    source = created['source']
    storage.write(directory / 'source_info.json', dict(source_path=source['path'],
        source_size_bytes=source['size'], mtime=source['mtime_ns'] / 1e9))
    decisions = dict(metadata=dict(duration=20, fps=30), segments=[
        dict(id=1, start_time=1, end_time=2, transcript='Humano cortou.', status='CUT_AUTO'),
        dict(id=2, start_time=4, end_time=5, transcript='Automático antigo.', status='CUT_AUTO'),
        dict(id=3, start_time=7, end_time=8, transcript='Humano restaurou.', status='KEEP')],
        user_overrides={'1': 'CUT', '3': 'KEEP', 'silence_1': 'COMPRESS'},
        silences=[dict(id=1,start_time=10,end_time=14,cut_start_time=10.325,cut_end_time=13.675)])
    storage.write(directory / 'analysis/decisions.json', decisions)
    storage.write(directory / 'analysis/markers.json', [dict(source_time=15, rough_time=2, label='Antigo')])
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert jobs.info('legacy')['legacy']
    assert jobs.list()
    assert before == {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    jobs.migrate('legacy')
    new = view(jobs, 'legacy')
    assert len(new['edit']['cuts']) == 2
    assert all(c['origin'] == 'human' for c in new['edit']['cuts'])
    assert new['edit']['markers'][0]['source_time'] == 15
    assert any(c['start'] == 1 for c in new['edit']['cuts'])
    assert not any(c['start'] == 4 for c in new['edit']['cuts'])
    assert new['edit']['protected'] == [dict(start=7, end=8)]
    for path, content in before.items():
        assert (directory / path).read_bytes() == content
    assert jobs.migrate('legacy') == jobs.info('legacy')


def test_unique_jobs_same_second(jobs, ready):
    assert ready()['job_id'] != ready()['job_id']


def test_second_runtime_cannot_start_same_workspace(jobs):
    from video_editor.core.job_lock import JobLockTimeoutError
    with jobs.runtime_lock():
        with pytest.raises(JobLockTimeoutError):
            with jobs.runtime_lock():
                pytest.fail('Second instance acquired the same workspace.')


def test_missing_legacy_mtime_compares_actual_raw_and_copy(jobs, ready):
    created=ready()
    directory=jobs.root/'early';(directory/'analysis').mkdir(parents=True)
    copied=directory/'input_original.mp4'
    copied.write_bytes(__import__('pathlib').Path(created['source']['path']).read_bytes())
    storage.write(directory/'source_info.json',dict(source_path=created['source']['path'],
        source_size_bytes=created['source']['size'],copied_path=str(copied),copy_verified=True))
    storage.write(directory/'analysis/decisions.json',dict(metadata=dict(duration=20,fps=30),
        segments=[dict(id=1,start_time=1,end_time=2,transcript='Mantido.',status='KEEP')]))
    # Same size alone is not evidence; fail before committing the new format.
    original=copied.read_bytes();copied.write_bytes(b'x'*len(original))
    with pytest.raises(ValueError,match='diverge'):
        jobs.migrate('early')
    assert not (directory/'job.json').exists()
    copied.write_bytes(original)
    assert jobs.migrate('early')['source_verification']=='raw_copy_sha256'


@pytest.mark.parametrize('job_id',['..','../other','a/b','a\\b','C:raw','.', 'invalid space'])
def test_job_path_validation(jobs, job_id):
    with pytest.raises((ValueError, OSError)):
        jobs.directory(job_id)


def test_job_lock_cleans_up_memory_leak(jobs, ready):
    from video_editor.core.job_lock import _THREAD_LOCKS, _LOCK_REFS
    job = ready()
    jid = job['job_id']
    with jobs.lock(jid):
        assert jid in _THREAD_LOCKS
        assert _LOCK_REFS[jid] == 1
    assert jid not in _THREAD_LOCKS
    assert jid not in _LOCK_REFS


def test_worker_close_timeout_and_idempotent():
    from video_editor.worker import Worker, BusyError
    w = Worker()
    f = w.submit(lambda: 42)
    assert f.result() == 42
    w.close(timeout=1.0)
    w.close(timeout=1.0)  # idempotent
    with pytest.raises(BusyError):
        w.submit(lambda: 99)
