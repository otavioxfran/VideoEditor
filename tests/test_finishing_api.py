import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from video_editor import media
from video_editor.finishing import suggest
from video_editor.server.app import create_app


def _prepare(jobs, ready):
    info = ready(duration=2)
    job_id = info['job_id']
    export = jobs.path(job_id, 'exports/exp/video.mp4')
    export.parent.mkdir(parents=True)
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-shortest', str(export),
    ], check=True)
    (export.parent / 'export.json').write_text(
        json.dumps(dict(render_id='exp', state='complete', revision=0)),
        encoding='utf-8',
    )
    (export.parent / 'subtitles.srt').write_text(
        '1\n00:00:00,500 --> 00:00:01,500\nfala\n', encoding='utf-8'
    )
    still = jobs.path(job_id, 'finishing/stills/logo.png')
    still.parent.mkdir(parents=True)
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'color=red:size=200x120:duration=1',
        '-frames:v', '1', str(still),
    ], check=True)
    document = dict(
        schema=1, revision=1, export_id='exp', export_revision=0,
        items=[dict(
            id='item-1', kind='overlay', start=.3, end=1.3,
            payload=dict(image=str(still), mode='contain', webcam_rect=None),
            reason='Teste', evidence={}, priority=60, status='accepted',
            needs_text=False, asset_id='logo', license='Teste',
        )],
    )
    jobs.write(job_id, 'finishing/suggestions.json', document)
    suggest.build_plan(jobs, job_id)
    return job_id, still


@pytest.fixture
def client(jobs, ready):
    job_id, still = _prepare(jobs, ready)
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as value:
        yield value, job_id, still


def test_item_image_and_not_found(client, tmp_path):
    value, job_id, _ = client
    prefix = f'/api/jobs/{job_id}/finishing'
    response = value.get(f'{prefix}/suggestions/item-1/image')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('image/')
    assert value.get(f'{prefix}/suggestions/no-such/image').status_code == 404
    outside = tmp_path / 'secret.png'
    outside.write_bytes(b'x')
    document = value.get(f'{prefix}/suggestions').json()
    document['items'][0]['payload']['image'] = str(outside)
    value.app.state.jobs.write(job_id, 'finishing/suggestions.json', document)
    assert value.get(f'{prefix}/suggestions/item-1/image').status_code == 404
    document['items'][0]['payload']['image'] = str(tmp_path / '..' / 'secret.png')
    value.app.state.jobs.write(job_id, 'finishing/suggestions.json', document)
    assert value.get(f'{prefix}/suggestions/item-1/image').status_code == 404


def test_preview_is_cached(client):
    value, job_id, _ = client
    prefix = f'/api/jobs/{job_id}/finishing'
    first = value.get(f'{prefix}/suggestions/item-1/preview.jpg')
    assert first.status_code == 200
    assert first.headers['content-type'] == 'image/jpeg'
    cache = value.app.state.jobs.path(job_id, 'finishing/previews')
    files = list(cache.glob('item-1-*.jpg'))
    assert files
    before = files[0].stat().st_mtime_ns
    second = value.get(f'{prefix}/suggestions/item-1/preview.jpg')
    assert second.status_code == 200
    assert files[0].stat().st_mtime_ns == before


def test_render_refuses_needs_text(client):
    value, job_id, _ = client
    prefix = f'/api/jobs/{job_id}/finishing'
    document = value.get(f'{prefix}/suggestions').json()
    document['items'][0]['needs_text'] = True
    value.app.state.jobs.write(job_id, 'finishing/suggestions.json', document)
    response = value.post(f'{prefix}/render')
    assert response.status_code == 422


def test_render_completes_and_serves_result(client):
    value, job_id, _ = client
    prefix = f'/api/jobs/{job_id}/finishing'
    response = value.post(f'{prefix}/render')
    assert response.status_code == 202
    state = None
    for _ in range(180):
        state = value.get(f'{prefix}/render').json()
        if state['state'] in ('complete', 'failed'):
            break
        time.sleep(.2)
    assert state['state'] == 'complete', state
    assert state['frames'] > 0
    video = value.get(f'{prefix}/render/video')
    assert video.status_code == 200
    assert video.headers['content-type'] == 'video/mp4'
    assert value.get(f'{prefix}/render/srt').status_code == 200
    assert value.get(f'{prefix}/render/description').status_code == 200
