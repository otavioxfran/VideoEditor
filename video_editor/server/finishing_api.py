import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
import mimetypes
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

from video_editor import media, storage
from video_editor.editing import view
from video_editor.finishing import cards, layers, models, suggest
from video_editor.finishing import render as finishing_render


class GenerateRequest(BaseModel):
    export_id: str | None = None
    # Fetch free images for names in the video that have none yet, before matching.
    expand_library: bool = True


class DecideRequest(BaseModel):
    expected_revision: int
    item_id: str
    action: str
    payload: dict[str, Any] | None = None
    start: float | None = None
    end: float | None = None
    needs_text: bool | None = None


class PlanRequest(BaseModel):
    accept_all: bool = False


class LibraryRequest(BaseModel):
    dry_run: bool = False


_STATUS_LOCK = threading.Lock()
# Renders queued or running in this process. A status file that says otherwise was left
# by a Studio that closed mid-render and must not keep the panel waiting forever.
_ACTIVE_RENDERS = set()
_WARMING = set()
INTERRUPTED = 'Render interrompido: o Studio foi fechado durante o render. Gere o vídeo final de novo.'


def _status_path(jobs, job_id):
    return jobs.path(job_id, 'finishing/render.json')


def _read_status(jobs, job_id):
    with _STATUS_LOCK:
        path = _status_path(jobs, job_id)
        if not path.is_file():
            return dict(state='idle', done=0, total=0)
        status = storage.read(path)
        if status.get('state') in ('queued', 'running') and job_id not in _ACTIVE_RENDERS:
            status.update(state='failed', error=INTERRUPTED)
            jobs.write(job_id, 'finishing/render.json', status)
        return status


def _write_status(jobs, job_id, value):
    with _STATUS_LOCK:
        jobs.write(job_id, 'finishing/render.json', value)
    return value


def _item(doc, item_id):
    if not item_id or '/' in item_id or '\\' in item_id or '..' in item_id:
        raise FileNotFoundError('Sugestão inexistente.')
    item = next((value for value in doc.get('items') or [] if value.get('id') == item_id), None)
    if item is None:
        raise FileNotFoundError('Sugestão inexistente.')
    return item


def _allowed_file(jobs, job_id, raw):
    if not isinstance(raw, str) or not raw:
        raise FileNotFoundError('Imagem ausente.')
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise FileNotFoundError('Imagem fora das origens permitidas.')
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError('Imagem ausente.')
    resolved = path.resolve()
    stills = jobs.path(job_id, 'finishing/stills').resolve()
    if resolved.is_relative_to(stills):
        return resolved
    try:
        library = suggest.load_library()
    except (OSError, ValueError):
        library = dict(assets=[])
    if any(Path(asset['image']).resolve() == resolved for asset in library.get('assets') or []):
        return resolved
    raise FileNotFoundError('Imagem fora das origens permitidas.')


def _warm_previews(jobs, job_id, items):
    def one(item):
        try:
            _preview(jobs, job_id, item, None)
        except (OSError, RuntimeError, ValueError, FileNotFoundError):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(one, items[:60]))


def _image_response(path):
    media_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return FileResponse(path, media_type=media_type)


def _current_export(jobs, job_id):
    current = view(jobs, job_id)
    video, export_id = suggest._export_video(
        jobs, job_id, None, int(current['edit']['revision'])
    )
    if not video or not export_id:
        raise ValueError('Exporte a revisão atual para gerar sugestões.')
    return video, export_id, current


def _preview(jobs, job_id, item, time_value):
    video, _, _ = _current_export(jobs, job_id)
    duration = float(suggest.view(jobs, job_id)['timeline']['duration'])
    if time_value is None:
        time_value = (float(item['start']) + float(item['end'])) / 2
    time_value = max(0.0, min(duration, float(time_value)))
    preview_dir = jobs.path(job_id, 'finishing/previews')
    preview_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps(
        [item['id'], item.get('payload'), round(time_value, 3)],
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()[:20]
    target = preview_dir / f'{item["id"]}-{key}.jpg'
    if target.is_file():
        return target
    frame = preview_dir / f'{item["id"]}-{key}.frame.jpg'
    media.run([
        'ffmpeg', '-nostdin', '-y', '-v', 'error', '-ss', str(time_value),
        '-i', str(video), '-frames:v', '1', '-vf', 'scale=960:540', '-q:v', '2',
        str(frame),
    ])
    image = Image.open(frame).convert('RGBA').resize((960, 540), Image.LANCZOS)
    payload = item.get('payload') or {}
    kind = item.get('kind')
    if kind in ('overlay', 'cover'):
        _allowed_file(jobs, job_id, payload.get('image'))
        layer = layers.build_layer(payload)
    elif kind == 'banner':
        layer = cards.build_banner(models.Banner(
            float(item['start']), float(item['end']),
            str(payload.get('first') or ''), str(payload.get('second') or ''),
        ))
    elif kind == 'card':
        layer = cards.build_card(models.Card(
            float(item['start']), str(payload.get('label') or 'CORREÇÃO'),
            tuple(payload.get('lines') or ['Correção']),
            str(payload.get('note') or ''), max(.1, float(item['end']) - float(item['start'])),
        ))
    else:
        raise ValueError('Tipo de sugestão sem prévia.')
    image.alpha_composite(layer.convert('RGBA').resize((960, 540), Image.LANCZOS))
    image.convert('RGB').save(target, quality=88)
    frame.unlink(missing_ok=True)
    return target


def _render_execute(jobs, job_id, status):
    status.update(state='running', error=None)
    _write_status(jobs, job_id, status)
    try:
        plan = models.load_plan(jobs.path(job_id, status['plan']))
        output = jobs.path(job_id, status['video'])
        work = jobs.path(job_id, status['work'])
        def update(done, total):
            status.update(done=done, total=total)
            _write_status(jobs, job_id, status)
        result = finishing_render.render(
            jobs.path(job_id, status['export']), plan, output,
            work_dir=work, master_audio=True, progress=update, sound_seed=job_id,
        )
        srt_source = jobs.path(job_id, status['source_srt'])
        srt_target = jobs.path(job_id, status['srt'])
        if srt_source.is_file():
            models.apply_srt(srt_source, srt_target, plan.cards)
        else:
            storage.write_text(srt_target, '')
        description = suggest.describe(jobs, job_id)
        description_target = jobs.path(job_id, status['description'])
        storage.write_text(description_target, description['text'])
        status.update(
            state='complete', done=status.get('total', 0),
            duration=result['duration'], frames=result['frames'],
            completed_at=storage.now(), error=None,
        )
        _write_status(jobs, job_id, status)
        return status
    except Exception as error:
        status.update(state='failed', error=str(error)[:2000])
        _write_status(jobs, job_id, status)
        raise
    finally:
        _ACTIVE_RENDERS.discard(job_id)


def _start_warmup(jobs, job_id, items):
    """Previews are a convenience: warm them on a background thread of their own instead
    of the single worker, so exports, renders and preparation never wait behind them."""
    if os.getenv('VIDEO_EDITOR_PREVIEW_WARMUP', '1') == '0':
        return
    with _STATUS_LOCK:
        if job_id in _WARMING:
            return
        _WARMING.add(job_id)

    def run():
        try:
            _warm_previews(jobs, job_id, items)
        finally:
            with _STATUS_LOCK:
                _WARMING.discard(job_id)

    threading.Thread(target=run, name=f'finishing-previews-{job_id[:8]}', daemon=True).start()


def router(jobs, worker=None):
    api = APIRouter(prefix='/api/jobs/{job_id}/finishing')

    @api.get('/suggestions')
    def suggestions(job_id: str):
        return suggest.load_suggestions(jobs, job_id)

    @api.post('/suggestions/generate')
    def generate(job_id: str, request: GenerateRequest | None = None):
        request = request or GenerateRequest()
        value = suggest.suggest(
            jobs, job_id, request.export_id, expand_library=request.expand_library
        )
        if worker is not None and value.get('items'):
            _start_warmup(jobs, job_id, value['items'])
        return value

    @api.post('/suggestions/decide')
    def decide(job_id: str, request: DecideRequest):
        return suggest.decide(jobs, job_id, request.model_dump())

    @api.post('/plan')
    def plan(job_id: str, request: PlanRequest | None = None):
        return suggest.build_plan(
            jobs, job_id, accept_all=(request or PlanRequest()).accept_all
        )

    @api.post('/library/expand')
    def library_expand(job_id: str, request: LibraryRequest | None = None):
        from video_editor.finishing import library
        return library.expand(
            jobs, job_id, dry_run=(request or LibraryRequest()).dry_run
        )

    @api.get('/suggestions/{item_id}/image')
    def suggestion_image(job_id: str, item_id: str):
        item = _item(suggest.load_suggestions(jobs, job_id), item_id)
        return _image_response(_allowed_file(jobs, job_id, (item.get('payload') or {}).get('image')))

    @api.get('/suggestions/{item_id}/alternative/{index}/image')
    def alternative_image(job_id: str, item_id: str, index: int):
        item = _item(suggest.load_suggestions(jobs, job_id), item_id)
        alternatives = (item.get('payload') or {}).get('alternatives') or []
        if index < 0 or index >= len(alternatives):
            raise FileNotFoundError('Alternativa inexistente.')
        return _image_response(_allowed_file(jobs, job_id, alternatives[index]))

    @api.get('/suggestions/{item_id}/preview.jpg')
    def suggestion_preview(job_id: str, item_id: str, t: float | None = None):
        item = _item(suggest.load_suggestions(jobs, job_id), item_id)
        return FileResponse(_preview(jobs, job_id, item, t), media_type='image/jpeg')

    @api.post('/render', status_code=202)
    def request_render(job_id: str):
        current = view(jobs, job_id)
        document = suggest.load_suggestions(jobs, job_id)
        selected = [
            item for item in document.get('items') or []
            if item.get('status') in ('accepted', 'edited')
        ]
        if any(item.get('needs_text') for item in selected):
            raise ValueError('Edite o texto das correções aceitas antes de gerar o vídeo.')
        if not selected:
            raise ValueError('Aceite ao menos uma sugestão para gerar o vídeo final.')
        video, export_id = suggest._export_video(
            jobs, job_id, None, int(current['edit']['revision'])
        )
        if not video or not export_id:
            raise ValueError('Exporte a revisão atual para gerar o vídeo final.')
        suggest.build_plan(jobs, job_id)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:6]
        base = f'finishing/final/{stamp}'
        status = dict(
            state='queued', done=0, total=0, error=None,
            revision=int(current['edit']['revision']), export_id=export_id,
            created_at=storage.now(), plan='finishing/plan.json',
            export=str(Path(video).relative_to(jobs.directory(job_id))).replace('\\', '/'),
            source_srt=f'exports/{export_id}/subtitles.srt',
            video=f'{base}/video.mp4', srt=f'{base}/subtitles.srt',
            description=f'{base}/descricao.txt', work=f'{base}/work',
        )
        with _STATUS_LOCK:
            if job_id in _ACTIVE_RENDERS:
                raise ValueError('Já existe um vídeo final sendo gerado para este job.')
            _ACTIVE_RENDERS.add(job_id)
        try:
            jobs.path(job_id, f'{base}/work').mkdir(parents=True, exist_ok=True)
            _write_status(jobs, job_id, status)
        except Exception:
            _ACTIVE_RENDERS.discard(job_id)
            raise
        if worker is None:
            _render_execute(jobs, job_id, status)
        else:
            try:
                worker.submit(_render_execute, jobs, job_id, status)
            except Exception as error:
                _ACTIVE_RENDERS.discard(job_id)
                status.update(state='failed', error=str(error))
                _write_status(jobs, job_id, status)
                raise
        return status

    @api.get('/render')
    def render_status(job_id: str):
        return _read_status(jobs, job_id)

    def _result_path(job_id, key, expected):
        status = _read_status(jobs, job_id)
        if status.get('state') != 'complete':
            raise FileNotFoundError('Vídeo final ainda não está pronto.')
        relative = status.get(key)
        if not relative:
            raise FileNotFoundError('Resultado ausente.')
        path = jobs.path(job_id, relative).resolve()
        directory = jobs.path(job_id, 'finishing/final').resolve()
        if not path.is_relative_to(directory) or not path.is_file() or path.is_symlink():
            raise FileNotFoundError('Resultado ausente.')
        if expected and path.suffix.lower() != expected:
            raise FileNotFoundError('Resultado ausente.')
        return path

    @api.get('/render/video')
    def render_video(job_id: str):
        return FileResponse(_result_path(job_id, 'video', '.mp4'), media_type='video/mp4')

    @api.get('/render/srt')
    def render_srt(job_id: str):
        return FileResponse(_result_path(job_id, 'srt', '.srt'), media_type='application/x-subrip')

    @api.get('/render/description')
    def render_description(job_id: str):
        return FileResponse(_result_path(job_id, 'description', '.txt'), media_type='text/plain; charset=utf-8')

    return api
