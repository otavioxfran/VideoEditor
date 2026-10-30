from pathlib import Path

from fastapi import APIRouter, HTTPException
from video_editor.media import LocalFileResponse as FileResponse
from video_editor.models import CreateJob, AutomaticJob, AutomaticOptions, RevisionRequest, AcceptanceRequest, EvaluationRequest
from video_editor import acceptance, media
from video_editor.editing import decisions
from video_editor.safety import source_identity


REVIEW_KINDS = {
    'repeated_block',
    'visual_absence',
    'repeated_idea',
    'false_start',
    'contextual_restart',
}


def router(jobs, worker, pipeline, exports, automatic=None):
    api = APIRouter(prefix='/api')

    def pending_review(job_id):
        path = jobs.path(job_id, 'suggestions.json')
        if not path.exists():
            return 0
        try:
            items = jobs.read(job_id, 'suggestions.json')
        except (ValueError, OSError):
            return 0
        try:
            decided = decisions(jobs.read(job_id, 'edit.json'))
        except (ValueError, OSError):
            decided = {}
        return sum(
            1 for item in items
            if not item.get('auto') and item.get('kind') in REVIEW_KINDS and item.get('id') not in decided
        )

    def job_source(job_id, info):
        if info.get('legacy'):
            return source_identity(info['source']['path'])['path']
        return jobs.validate(job_id)

    def latest_export(job_id):
        directory = jobs.directory(job_id) / 'exports'
        if not directory.is_dir():
            return None
        latest = None
        for child in directory.iterdir():
            metadata = child / 'export.json'
            if not metadata.is_file():
                continue
            try:
                value = jobs.read(job_id, f'exports/{child.name}/export.json')
            except (OSError, ValueError):
                continue
            if value.get('state') != 'complete':
                continue
            if latest is None or (value.get('completed_at') or '') > (latest.get('completed_at') or ''):
                latest = value
        if latest is not None:
            latest['status'] = export_status(job_id, latest)
        return latest

    def mtime(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    status_cache = {}

    def export_status(job_id, metadata):
        # exports.get re-validates the source and rebuilds the identity (~0,3 s per job);
        # reuse it until the edit, transcript, export, source or punch-in preference changes.
        render_id = metadata.get('render_id')
        if not render_id:
            return 'stale'
        directory = jobs.directory(job_id)
        source = (jobs.info(job_id).get('source') or {}).get('path')
        signature = (
            tuple(mtime(directory / name) for name in (
                'job.json', 'edit.json', 'transcript.json', f'exports/{render_id}/export.json')),
            mtime(Path(source)) if source else None,
            repr(sorted(exports.current_config().items())),
        )
        cached = status_cache.get((job_id, render_id))
        if cached and cached[0] == signature:
            return cached[1]
        try:
            status = exports.get(job_id, render_id).get('status', 'stale')
        except (ValueError, OSError, KeyError):
            status = 'stale'
        status_cache[(job_id, render_id)] = (signature, status)
        return status

    def render_still(job_id, source, duration, timestamp, relative):
        path = jobs.path(job_id, relative)
        if path.is_file():
            return path
        at = max(0.0, min(float(timestamp), max(0.0, duration - .05)))
        path.parent.mkdir(parents=True, exist_ok=True)
        media.run([
            'ffmpeg', '-nostdin', '-y', '-v', 'error', '-ss', f'{at:.3f}',
            '-i', str(source), '-frames:v', '1', '-vf', 'scale=640:-2',
            '-q:v', '3', str(path),
        ])
        return path

    @api.get('/library')
    def library():
        groups = {}
        for job in jobs.list():
            key = (job.get('source') or {}).get('path') or job['job_id']
            groups.setdefault(key, []).append(job)
        result = []
        for versions in groups.values():
            best = max(
                versions,
                key=lambda item: (
                    not item.get('legacy', False),
                    item.get('state') == 'ready',
                    item.get('created_at') or '',
                ),
            )
            best['versions'] = len(versions)
            info = best.get('media') or {}
            best['duration'] = info.get('duration')
            best['model'] = (best.get('config') or {}).get('model')
            best['poster_url'] = f"/api/jobs/{best['job_id']}/poster"
            best['pending_review'] = 0 if best.get('legacy') else pending_review(best['job_id'])
            best['export'] = None if best.get('legacy') else latest_export(best['job_id'])
            result.append(best)
        result.sort(key=lambda item: item.get('created_at') or '', reverse=True)
        return dict(sources=jobs.sources(), source_dir=str(jobs.source_dir), jobs=result)

    @api.get('/jobs')
    def list_jobs():
        return jobs.list()

    @api.post('/jobs', status_code=202)
    def create(request: CreateJob):
        info = jobs.create(request.source_path, request.config.model_dump())
        return enqueue(info['job_id'])

    @api.post('/automatic-jobs',status_code=202)
    def automatic_job(request: AutomaticJob):
        config=request.config.model_dump()
        if config['model']=='small' and config['batch_size']==1:
            from video_editor.performance import selected_batch
            from video_editor.settings import BASE_DIR
            config['batch_size']=selected_batch(BASE_DIR/'config/asr_calibration.local.json')
        info=jobs.create(request.source_path,config)
        automatic.request(info['job_id'],request.automatic.model_dump())
        return jobs.info(info['job_id'])

    @api.get('/jobs/{job_id}/automatic')
    def get_automatic(job_id: str):
        return dict(automatic.status(job_id),acceptance=acceptance.gate(jobs))

    @api.post('/jobs/{job_id}/automatic/review')
    def review_automatic(job_id: str,request: AcceptanceRequest):
        return acceptance.record(jobs,job_id,request.model_dump())

    @api.post('/jobs/{job_id}/automatic',status_code=202)
    def start_automatic(job_id: str,request: RevisionRequest):
        # Explicit retry retains this job's text permission, never reads ambient credentials.
        previous=automatic.status(job_id)
        options=previous.get('options',AutomaticOptions().model_dump())
        return automatic.request(job_id,options,request.expected_revision)

    @api.post('/jobs/{job_id}/automatic/evaluation', status_code=202)
    def evaluation_job(job_id: str, request: EvaluationRequest):
        return automatic.create_evaluation(job_id, request.expected_revision, request.send_text)

    def enqueue(job_id):
        with jobs.lock(job_id):
            jobs.validate(job_id)
            info = jobs.info(job_id)
            from video_editor.automatic import ACTIVE_STATES
            if automatic and automatic.status(job_id)['state'] in ACTIVE_STATES:
                raise HTTPException(409,'Edição automática em andamento.')
            if info['state'] not in ('created', 'failed', 'interrupted'):
                raise HTTPException(409, 'Job já preparado ou em processamento.')
            info.update(state='queued', stage='queued', error=None)
            jobs.write(job_id, 'job.json', info)
        try:
            worker.submit(pipeline.prepare, job_id)
        except Exception as error:
            jobs.update(job_id, state='failed', error=str(error))
            raise
        return info

    @api.get('/jobs/{job_id}')
    def get_job(job_id: str):
        info = jobs.info(job_id)
        try:
            if not info.get('legacy'):
                jobs.validate(job_id)
            info['source_error'] = None
        except (OSError, ValueError) as error:
            info['source_error'] = str(error)
        return info

    @api.post('/jobs/{job_id}/prepare', status_code=202)
    def prepare(job_id: str):
        return enqueue(job_id)

    @api.post('/jobs/{job_id}/migrate')
    def migrate(job_id: str):
        return jobs.migrate(job_id)

    @api.get('/jobs/{job_id}/poster')
    def poster(job_id: str):
        info = jobs.info(job_id)
        source = job_source(job_id, info)
        duration = float((info.get('media') or {}).get('duration') or 0)
        at = duration * .08 if duration > 2 else 0
        path = render_still(job_id, source, duration, at, 'media/poster.jpg')
        return FileResponse(path, media_type='image/jpeg',
                            headers={'Cache-Control': 'public, max-age=86400'})

    @api.get('/jobs/{job_id}/frame')
    def frame(job_id: str, t: float = 0):
        info = jobs.info(job_id)
        source = job_source(job_id, info)
        duration = float((info.get('media') or {}).get('duration') or 0)
        at = max(0.0, min(t, duration))
        path = render_still(job_id, source, duration, at,
                            f'media/frames/{round(at * 1000)}.jpg')
        return FileResponse(path, media_type='image/jpeg',
                            headers={'Cache-Control': 'public, max-age=86400'})

    @api.get('/jobs/{job_id}/waveform')
    def waveform(job_id: str):
        path = jobs.path(job_id, 'audio/waveform.json')
        if path.is_file():
            return jobs.read(job_id, 'audio/waveform.json')
        wav = jobs.path(job_id, 'audio/extracted_16k.wav')
        if not wav.is_file():
            raise HTTPException(404, '?udio extra?do n?o encontrado.')
        import wave
        import numpy as np
        with wave.open(str(wav), 'rb') as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            audio = np.frombuffer(handle.readframes(frames), dtype='<i2').astype(np.float32) / 32768
        buckets = 1200
        chunk = max(1, len(audio) // buckets)
        values = audio[:chunk * buckets].reshape(-1, chunk)
        peaks = np.sqrt(np.mean(values * values, axis=1)) if len(values) else np.zeros(0)
        peak = float(peaks.max()) if len(peaks) else 0.0
        values_out = [round(float(item / peak), 4) if peak else 0.0 for item in peaks]
        value = dict(duration=round(frames / rate, 3), peaks=values_out)
        jobs.write(job_id, 'audio/waveform.json', value)
        return value

    @api.get('/jobs/{job_id}/media')
    def source_media(job_id: str):
        info = jobs.info(job_id)
        if info.get('legacy'):
            identity = source_identity(info['source']['path'])
            expected = info['source']
            if (identity['size'] != expected.get('size') or expected.get('mtime') is None or
                    abs(identity['mtime_ns'] / 1e9 - expected['mtime']) > .001):
                raise ValueError('Fonte legada alterada.')
            path = identity['path']
        else:
            path = jobs.validate(job_id)
            preview=info.get('preview')
            if preview:
                checkpoint=jobs.read(job_id,'media/checkpoint.json')
                if checkpoint['identity']!=preview['identity']:
                    raise ValueError('Proxy incompatível com a fonte.')
                path=jobs.path(job_id,preview['path'])
        return FileResponse(path, media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @api.get('/jobs/{job_id}/exports')
    def list_exports(job_id: str):
        return exports.list(job_id)

    @api.get('/jobs/{job_id}/exports/{render_id}')
    def get_export(job_id: str, render_id: str):
        return exports.get(job_id, render_id)

    @api.get('/jobs/{job_id}/exports/{render_id}/{filename}')
    def download(job_id: str, render_id: str, filename: str):
        if filename not in ('video.mp4', 'subtitles.srt'):
            raise HTTPException(404, 'Arquivo não encontrado.')
        metadata = exports.get(job_id, render_id)
        if metadata['state'] != 'complete' or metadata['status'] == 'missing':
            raise HTTPException(409, 'Exportação ainda não disponível.')
        path = jobs.path(job_id, f'exports/{render_id}/{filename}')
        return FileResponse(path, media_type='video/mp4' if filename.endswith('.mp4') else 'application/x-subrip',
                            filename=filename if filename.endswith('.srt') else None)

    @api.get('/jobs/{job_id}/historical/{filename}')
    def historical(job_id: str, filename: str):
        from pathlib import Path
        from video_editor.core.security import assert_safe_job_id
        assert_safe_job_id(filename)
        if Path(filename).suffix.lower() not in ('.mp4','.srt'):
            raise HTTPException(404,'Arquivo histórico não encontrado.')
        path=jobs.path(job_id,'output/'+filename)
        if not path.is_file():
            raise HTTPException(404,'Arquivo histórico não encontrado.')
        return FileResponse(path,filename=filename,headers={'X-Export-Compatibility':'unknown'})

    return api
