import threading
import time
import pytest
from fastapi.testclient import TestClient
from video_editor.server.app import create_app
from video_editor.editing import mutate
from video_editor.export.service import Exports
from video_editor.worker import Worker


def test_media_stops_reading_abandoned_range(tmp_path):
    import asyncio
    from video_editor.media import LocalFileResponse

    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x' * (8 * 1024 * 1024))

    async def exercise(disconnect_immediately=False):
        disconnected = asyncio.Event()
        if disconnect_immediately:
            disconnected.set()
        sent = []
        starts = []

        async def receive():
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == 'http.response.start':
                starts.append(message['status'])
            if message['type'] == 'http.response.body':
                sent.append(len(message['body']))
                disconnected.set()
                await asyncio.sleep(0)

        scope = {'type': 'http', 'method': 'GET',
                 'headers': [(b'range', b'bytes=0-')], 'extensions': {}}
        await asyncio.wait_for(LocalFileResponse(source)(scope, receive, send), 3)
        assert starts == [206], 'Middleware must receive response headers even on an early disconnect'
        # Permit an in-flight chunk, but never drain the abandoned source.
        assert len(sent) <= 2
        if not disconnect_immediately:
            assert len(sent) >= 1

    asyncio.run(exercise())
    asyncio.run(exercise(True))


def test_server_reads_and_ranges_while_worker_is_busy(jobs, ready):
    job = ready()
    started, release = threading.Event(), threading.Event()
    class BlockingPipeline:
        def prepare(self, job_id):
            started.set()
            assert release.wait(10)
    app = create_app(jobs.root, jobs.source_dir, pipeline=BlockingPipeline(), recover=False)
    with TestClient(app) as client:
        response = client.post('/api/jobs', json={'source_path': job['source']['path']})
        assert response.status_code == 202
        assert started.wait(3)
        try:
            begin = time.monotonic()
            assert client.get('/api/jobs/' + job['job_id'] + '/edit').status_code == 200
            assert time.monotonic() - begin < 2
            prefix = '/api/jobs/' + job['job_id']
            media = client.get(prefix + '/media', headers={'Range': 'bytes=-4'})
            assert media.status_code == 206
            assert media.content == b'ture'
            assert client.get(prefix + '/media', headers={'Range':'bytes=9999-'}).status_code == 416
        finally:
            release.set()


def test_revision_mandatory_and_queries_pure(jobs, ready, tmp_path):
    job = ready()
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        # Windows correctly prevents reading a byte held by the runtime's OS lock.
        before = {str(p): p.read_bytes() for p in jobs.root.rglob('*') if p.is_file() and p.name!='.job.lock'}
        prefix = '/api/jobs/' + job['job_id']
        for url in ('/api/library', prefix, prefix+'/edit', prefix+'/exports', prefix+'/editorial', prefix+'/editorial/prompt'):
            assert client.get(url).status_code == 200
        assert before == {str(p): p.read_bytes() for p in jobs.root.rglob('*') if p.is_file() and p.name!='.job.lock'}
        assert client.post(prefix+'/edit',json={'action':'cut','start':3,'end':8}).status_code == 422
        assert client.post(prefix+'/edit',json={'expected_revision':0,'action':'invalid'}).status_code == 422
        payload = {'expected_revision':0,'action':'cut','start':3,'end':8}
        assert client.post(prefix+'/edit',json=payload).status_code == 200
        assert client.post(prefix+'/edit',json=payload).status_code == 409
        assert client.post(prefix+'/edit', json=dict(payload, expected_revision=1),
                           headers={'Origin':'https://outside.example'}).status_code == 403
        assert client.post(prefix+'/edit', json=dict(payload, expected_revision=1),
                           headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
        assert client.post(prefix+'/edit', json=dict(payload, expected_revision=1),
                           headers={'Referer':'https://outside.example/evil'}).status_code == 403
        external_raw = tmp_path / 'external.mp4'
        external_raw.write_bytes(b'raw')
        assert client.post('/api/jobs', json={'source_path': str(external_raw)}).status_code == 403
        assert client.get('/api/project').status_code == 404
        assert client.get('/static/blind_review.html').status_code == 404


def test_snapshot_race_and_two_real_export_requests(jobs, ready):
    job = ready()
    started, release = threading.Event(), threading.Event()
    captured = []
    def renderer(snapshot, output, root):
        captured.append(snapshot)
        started.set()
        assert release.wait(10)
        output.write_bytes(b'isolated renderer fixture')
        return {'actual_codec':'test'}
    worker = Worker()
    exports = Exports(jobs, worker, renderer)
    first = exports.request(job['job_id'], 0)
    assert started.wait(3)
    second = exports.request(job['job_id'], 0)
    assert first['render_id'] != second['render_id']
    mutate(jobs, job['job_id'], dict(expected_revision=0,action='cut',start=3,end=8))
    release.set()
    worker.queue.join()
    worker.close()
    assert len(captured) == 2
    assert all(s['revision'] == 0 and s['timeline']['duration'] == 20 for s in captured)
    assert exports.get(job['job_id'], first['render_id'])['status'] == 'stale'
    mutate(jobs, job['job_id'], dict(expected_revision=1, action='undo'))
    assert Exports(jobs, Worker(), renderer).get(job['job_id'], first['render_id'])['status'] == 'current'


def test_historical_download_is_labeled_and_not_current(jobs,ready):
    job=ready()
    jid=job['job_id']
    output=jobs.directory(jid)/'output'
    output.mkdir()
    (output/'output_roughcut_v1.mp4').write_bytes(b'legacy media fixture')
    app=create_app(jobs.root,jobs.source_dir,recover=False)
    with TestClient(app) as client:
        prefix='/api/jobs/'+jid
        assert client.get(prefix+'/exports').json()[0]['status']=='historical'
        response=client.get(prefix+'/historical/output_roughcut_v1.mp4')
        assert response.content==b'legacy media fixture'
        assert response.headers['X-Export-Compatibility']=='unknown'
        assert client.get(prefix+'/historical/secret.json').status_code==404


def test_recovery_marks_incomplete_work_without_publishing_partials(jobs,ready):
    job=ready()
    jid=job['job_id']
    jobs.update(jid,state='processing',stage='transcription')
    jobs.write(jid,'exports/pending/export.json',dict(render_id='pending',state='rendering',identity='unknown'))
    jobs.write(jid,'editorial/status.json',dict(state='queued'))
    app=create_app(jobs.root,jobs.source_dir)
    with TestClient(app) as client:
        prefix='/api/jobs/'+jid
        assert client.get(prefix).json()['state']=='interrupted'
        assert client.get(prefix+'/exports/pending').json()['state']=='failed'
        assert client.get(prefix+'/exports/pending/video.mp4').status_code==409
        assert client.get(prefix+'/editorial').json()['assistance']['state']=='manual'


@pytest.mark.parametrize('field', ['timeline', 'media'])
def test_modified_snapshot_is_rejected_before_renderer(jobs,ready,field):
    job=ready()
    jid=job['job_id']
    class QueuedWorker:
        def submit(self,*args):pass
    called=[]
    exports=Exports(jobs,QueuedWorker(),lambda *args:called.append(args))
    item=exports.request(jid,0)
    rid=item['render_id']
    snapshot=jobs.read(jid,f'exports/{rid}/snapshot.json')
    if field == 'timeline':
        snapshot['timeline']['keeps'][0]['source_end']=10
    else:
        snapshot['media']['fps']=15
    jobs.write(jid,f'exports/{rid}/snapshot.json',snapshot)
    with pytest.raises(ValueError,match='Snapshot'):
        exports.execute(jid,rid)
    assert not called
    assert exports.get(jid,rid)['state']=='failed'


def test_library_exposes_pending_review_duration_and_poster(jobs, ready):
    job = ready()
    jid = job["job_id"]
    jobs.write(jid, "suggestions.json", [
        dict(id="v", kind="visual_absence", auto=False, start=1, end=2, reason="x"),
        dict(id="b", kind="repeated_block", auto=False, start=3, end=4, reason="x"),
        dict(id="r", kind="retake", auto=False, start=5, end=6, reason="x"),
        dict(id="s", kind="silence", auto=True, start=7, end=8, reason="x"),
    ])
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        library = client.get("/api/library").json()
        item = next(entry for entry in library["jobs"] if entry["job_id"] == jid)
        assert item["pending_review"] == 2
        assert item["duration"] == 20
        assert item["model"] == "large-v3-turbo"
        assert item["poster_url"] == f"/api/jobs/{jid}/poster"
        assert item["export"] is None
        assert client.get(f"/api/jobs/{jid}/waveform").status_code == 404
        client.post(f"/api/jobs/{jid}/edit", json=dict(expected_revision=0, action="restore", suggestion_id="v"))
        library = client.get("/api/library").json()
        item = next(entry for entry in library["jobs"] if entry["job_id"] == jid)
        assert item["pending_review"] == 1


def test_ui_assets_are_revalidated(jobs):
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        for path in ("/", "/editor.html", "/assets/review-card.js", "/assets/style.css"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
