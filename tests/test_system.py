import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from video_editor.server.app import create_app


def _settings_in_subprocess(tmp_path, **env):
    code = (
        "import json; from video_editor import settings as s; "
        "print(json.dumps(dict(data=str(s.DATA_DIR), jobs=str(s.JOBS_DIR), installed=s.INSTALLED, "
        "config=str(s.LOCAL_CONFIG_DIR), persona=str(s.PERSONA_PATH), source=str(s.SOURCE_VIDEOS_DIR))))"
    )
    clean = {k: v for k, v in os.environ.items() if k not in ('SOURCE_VIDEOS_DIR', 'JOBS_DIR', 'VIDEOEDITOR_DATA_DIR')}
    clean.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), LOCALAPPDATA=str(tmp_path / 'local'),
                 XDG_DATA_HOME=str(tmp_path / 'local'), **env)
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=clean, check=True)
    return json.loads(result.stdout)


def test_installed_copy_writes_to_the_user_data_folder(tmp_path):
    value = _settings_in_subprocess(tmp_path, VIDEOEDITOR_INSTALLED='1')
    assert value['installed'] is True
    assert value['data'].startswith(str(tmp_path / 'local'))
    assert value['jobs'] == str((tmp_path / 'local').resolve() / ('VideoEditor' if os.name == 'nt' else 'videoeditor') / 'jobs')
    assert value['config'].startswith(value['data']) and value['persona'].startswith(value['data'])
    # Without a configured folder, an installed copy lists the user's own Videos folder.
    assert value['source'] == str((tmp_path / 'Videos').resolve())


def test_explicit_data_dir_wins(tmp_path):
    value = _settings_in_subprocess(tmp_path, VIDEOEDITOR_DATA_DIR=str(tmp_path / 'data'))
    assert value['data'] == str((tmp_path / 'data').resolve())
    assert value['jobs'] == str((tmp_path / 'data' / 'jobs').resolve())


def test_picked_video_outside_the_source_folder_can_be_cut(jobs, monkeypatch, tmp_path):
    outside = tmp_path / 'elsewhere' / 'gravação.mp4'
    outside.parent.mkdir()
    outside.write_bytes(b'isolated source fixture')
    monkeypatch.setattr('video_editor.picker.pick_file', lambda start=None: str(outside))
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        # Typed by hand, a path outside the folder is still refused.
        refused = client.post('/api/jobs', json=dict(source_path=str(outside)))
        assert refused.status_code == 403
        picked = client.post('/api/pick/video').json()
        assert picked['name'] == 'gravação.mp4'
        created = client.post('/api/jobs', json=dict(source_path=picked['path']))
        assert created.status_code == 202, created.text


def test_cancelled_pick_changes_nothing(jobs, monkeypatch):
    monkeypatch.setattr('video_editor.picker.pick_file', lambda start=None: None)
    monkeypatch.setattr('video_editor.picker.pick_folder', lambda start=None: None)
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        assert client.post('/api/pick/video').json() == dict(cancelled=True)
        assert client.post('/api/pick/folder').json() == dict(cancelled=True)


def test_picked_folder_becomes_the_library(jobs, monkeypatch, tmp_path):
    folder = tmp_path / 'minhas gravações'
    folder.mkdir()
    (folder / 'take.mov').write_bytes(b'x')
    monkeypatch.setattr('video_editor.picker.pick_folder', lambda start=None: str(folder))
    monkeypatch.setattr('video_editor.settings.USER_SETTINGS_PATH', tmp_path / 'studio.local.json')
    monkeypatch.setattr('video_editor.settings.LOCAL_CONFIG_DIR', tmp_path)
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        result = client.post('/api/pick/folder').json()
        assert result['source_dir'] == str(folder.resolve())
        library = client.get('/api/library').json()
        assert [s['name'] for s in library['sources']] == ['take.mov']
    assert json.loads((tmp_path / 'studio.local.json').read_text(encoding='utf-8'))['source_dir'] == str(folder.resolve())


def test_folder_that_contains_the_workspace_is_refused(jobs, monkeypatch):
    monkeypatch.setattr('video_editor.picker.pick_folder', lambda start=None: str(jobs.root.parent))
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        assert client.post('/api/pick/folder').status_code == 422


def test_system_status_recommends_a_light_model_without_gpu(jobs, monkeypatch):
    from video_editor import system
    monkeypatch.setattr(system, 'gpu', lambda: dict(ok=False, count=0, name=None))
    monkeypatch.setattr(system, 'ffmpeg', lambda: dict(ok=True, path='ffmpeg', version='7'))
    monkeypatch.setattr(system, 'model_ready', lambda model: False)
    started = []
    monkeypatch.setattr(system.downloads, 'start', lambda model: started.append(model) or True)
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        value = client.get('/api/system').json()
        assert value['model']['name'] == 'small' and value['ready'] is False
        assert client.post('/api/system/model', json={}).json()['model'] == 'small'
    assert started == ['small']
