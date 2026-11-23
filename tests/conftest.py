import pytest
from video_editor.jobs import Jobs
from video_editor.models import PrepareConfig
from video_editor.timeline import digest
from video_editor.transcription import normalize


@pytest.fixture
def jobs(tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    return Jobs(tmp_path / 'jobs', sources)


@pytest.fixture
def ready(jobs):
    def create(name='raw.mp4', duration=20, segments=None):
        source = jobs.source_dir / name
        if not source.exists():
            source.write_bytes(b'isolated source fixture')
        job = jobs.create(source, PrepareConfig().model_dump())
        info = jobs.update(job['job_id'], state='ready', stage='ready',
                           media=dict(duration=duration, fps=30, video_codec='h264', audio_codec='aac'))
        transcript = normalize(segments or [dict(start=1, end=2, text='Primeira fala.',
            words=[dict(word='Primeira', start=1, end=1.5), dict(word='fala.', start=1.5, end=2)]),
            dict(start=15, end=16, text='Última fala.', words=[dict(word='Última', start=15, end=15.5),
             dict(word='fala.', start=15.5, end=16)])], job['source'])
        jobs.write(job['job_id'], 'transcript.json', transcript)
        jobs.write(job['job_id'], 'suggestions.json', [])
        jobs.write(job['job_id'], 'edit.json', dict(schema=1, revision=0, transcript_hash=digest(transcript),
                   cuts=[], protected=[], markers=[], undo=[], history=[]))
        return info
    return create


@pytest.fixture(autouse=True)
def isolated_channel_profile(tmp_path, monkeypatch):
    # Studio edits learn into the profile; tests must never train the real one.
    monkeypatch.setenv('CHANNEL_PROFILE_PATH', str(tmp_path / 'channel_profile.local.json'))


@pytest.fixture(autouse=True)
def isolated_finishing(tmp_path, monkeypatch):
    # Never read or grow the user's real image library, and never fetch images after an
    # automatic export: tests stay offline.
    from video_editor.analysis import glossary
    from video_editor.finishing import suggest
    monkeypatch.setattr(suggest, 'LIBRARY_PATH', tmp_path / 'finishing_library.local.json')
    monkeypatch.setattr(glossary, 'USER_GLOSSARY', tmp_path / 'glossary.local.json')
    monkeypatch.setenv('VIDEO_EDITOR_AUTO_FINISHING', '0')


@pytest.fixture(autouse=True)
def cleanup_whisper():
    yield
    try:
        from video_editor.transcription import release_model
        release_model()
    except Exception:
        pass

