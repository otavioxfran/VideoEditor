import os
import pytest
from video_editor import storage
from video_editor.editing import mutate
from video_editor.safety import destination


def test_raw_alias_and_write_escape_are_blocked(jobs, ready):
    job = ready()
    root = jobs.directory(job['job_id'])
    alias = root / 'alias.mp4'
    os.link(job['source']['path'], alias)
    with pytest.raises(PermissionError):
        jobs.path(job['job_id'], 'alias.mp4')
    with pytest.raises(PermissionError):
        jobs.write(job['job_id'], '../escape.json', {})
    with pytest.raises(PermissionError):
        destination(job['source']['path'], root)


def test_same_basename_is_never_substituted(jobs, ready):
    job = ready()
    source = jobs.source_dir / 'raw.mp4'
    source.rename(source.with_name('changed.mp4'))
    with pytest.raises(FileNotFoundError):
        jobs.validate(job['job_id'])
    source.write_bytes(b'new content')
    with pytest.raises(ValueError, match='Fonte alterada'):
        jobs.validate(job['job_id'])


def test_atomic_failure_preserves_state_and_raw(jobs, ready, monkeypatch):
    job = ready()
    source = jobs.source_dir / 'raw.mp4'
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    edit = jobs.read(job['job_id'], 'edit.json')
    def fail(*_):
        raise OSError('injected disk failure')
    monkeypatch.setattr('video_editor.core.atomic_io.os.replace', fail)
    with pytest.raises(OSError):
        mutate(jobs, job['job_id'], dict(expected_revision=0, action='cut', start=3, end=8))
    assert jobs.read(job['job_id'], 'edit.json') == edit
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
