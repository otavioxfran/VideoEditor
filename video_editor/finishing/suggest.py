"""Deterministic finishing suggestions with human decisions and a final plan."""
import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

from video_editor import media, storage
from video_editor.analysis import glossary
from video_editor.editing import require_revision, view
from video_editor.finishing import models
from video_editor.settings import BASE_DIR
from video_editor.timeline import project_interval

LIBRARY_PATH = BASE_DIR / 'config' / 'finishing_library.local.json'
EXAMPLE_LIBRARY = BASE_DIR / 'config' / 'finishing_library.example.json'
HAZARDS_DIR = BASE_DIR / 'config' / 'screen_hazards.local'
COOLDOWN = 60.0
MIN_SECONDS = 1.2
MAX_SECONDS = 4.0
MAX_SEQUENCE = 1.5
MIN_HAZARD = 1.0
SAMPLE_FPS = 4
HASH_DISTANCE = 20


def _normalize(text):
    value = unicodedata.normalize('NFKD', str(text or '').casefold())
    value = ''.join(character for character in value if not unicodedata.combining(character))
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def _stable_id(kind, identity, start):
    raw = f'{kind}|{identity}|{round(float(start), 2):.2f}'
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def load_library(path=None):
    path = Path(path) if path else LIBRARY_PATH
    if not path.is_file():
        path = EXAMPLE_LIBRARY
    if not path.is_file():
        raise FileNotFoundError('Biblioteca de acabamento não encontrada.')
    value = storage.read(path)
    assets = []
    for index, item in enumerate(value.get('assets') or []):
        if not isinstance(item, dict):
            raise ValueError(f'Asset {index} inválido.')
        if item.get('rejected') or item.get('needs_confirmation'):
            continue
        terms = [term for term in item.get('terms') or [] if _normalize(term)]
        if not item.get('id') or not item.get('image'):
            raise ValueError(f'Asset {index} sem id ou imagem.')
        if item.get('kind') != 'broll' and not terms:
            raise ValueError(f'Asset {index} sem termos para menção.')
        image = Path(str(item['image']))
        if not image.is_absolute():
            image = (path.parent / image).resolve()
        assets.append(dict(
            item, terms=terms, image=str(image),
            mode=str(item.get('mode') or 'contain'),
            kind=str(item.get('kind') or 'logo').lower(),
            license=str(item.get('license') or ''),
        ))
    return dict(assets=assets, webcam_rect=value.get('webcam_rect') or None)


def _segments(view_value):
    return view_value['transcript'].get('segments') or []


def _word_start(timeline, word):
    pieces = list(project_interval(timeline, float(word['start']), float(word['end'])))
    if len(pieces) != 1:
        return None
    left, right = pieces[0]
    if abs((right - left) - (float(word['end']) - float(word['start']))) > 0.03:
        return None
    return left


def _segment_end(timeline, segment, duration):
    pieces = list(project_interval(timeline, float(segment['start']), float(segment['end'])))
    if not pieces:
        return None
    return min(duration, pieces[-1][1])


def _mention_matches(view_value, library):
    timeline, duration = view_value['timeline'], view_value['timeline']['duration']
    transcript = view_value['transcript']
    try:
        terms = glossary.user_terms()
        if terms:
            transcript, _ = glossary.correct(transcript, terms)
    except (OSError, ValueError):
        transcript = view_value['transcript']
    rows = [
        dict(segment=segment, word=word, text=_normalize(word.get('text', '')))
        for segment in transcript.get('segments') or []
        for word in segment.get('words') or []
    ]
    terms, maximum = {}, 1
    for asset in library['assets']:
        for term in asset['terms']:
            tokens = tuple(_normalize(term).split())
            if tokens:
                terms.setdefault(tokens, []).append(asset)
                maximum = max(maximum, len(tokens))
    matches, seen = [], set()
    for index, row in enumerate(rows):
        for size in range(1, maximum + 1):
            if index + size > len(rows):
                break
            phrase = tuple(item['text'] for item in rows[index:index + size])
            for asset in terms.get(phrase, []):
                key = (asset['id'], index, size)
                if key in seen:
                    continue
                seen.add(key)
                start = _word_start(timeline, row['word'])
                if start is None:
                    continue
                end = _segment_end(timeline, rows[index + size - 1]['segment'], duration)
                if end is None:
                    end = start
                matches.append(dict(
                    asset=asset, start=start, end=max(end, start),
                    segment_id=rows[index + size - 1]['segment']['id'],
                    row_start=index, row_end=index + size - 1,
                    term=' '.join(phrase),
                    words=[item['word'] for item in rows[index:index + size]],
                ))
    matches = _dedupe_matches(matches)
    matches.sort(key=lambda item: (item['start'], item['end'], item['asset']['id']))
    return matches


def _dedupe_matches(matches):
    result = []
    by_asset = {}
    for match in matches:
        by_asset.setdefault(match['asset']['id'], []).append(match)
    for values in by_asset.values():
        kept = []
        for match in sorted(
            values,
            key=lambda item: (-(item['row_end'] - item['row_start']), item['row_start']),
        ):
            if any(
                match['row_start'] <= other['row_end']
                and match['row_end'] >= other['row_start']
                for other in kept
            ):
                continue
            kept.append(match)
        result.extend(kept)
    return result


def _mention_items(view_value, library):
    duration = view_value['timeline']['duration']
    matches = _mention_matches(view_value, library)
    items = []
    for index, match in enumerate(matches):
        asset = match['asset']
        kind_asset = str(asset.get('kind') or 'logo')
        minimum = float(asset.get('min_seconds') or (2.0 if kind_asset == 'meme' else MIN_SECONDS))
        cooldown = float(asset.get('cooldown') or (20.0 if kind_asset == 'meme' else COOLDOWN))
        start = match['start']
        end = min(duration, match['end'], start + MAX_SECONDS)
        next_match = matches[index + 1] if index + 1 < len(matches) else None
        previous_match = matches[index - 1] if index else None
        sequence = (
            (next_match and next_match['start'] <= start + MAX_SECONDS)
            or (previous_match and start - previous_match['start'] <= MAX_SECONDS)
        )
        if sequence:
            end = min(end, start + MAX_SEQUENCE)
        if next_match and next_match['start'] > start:
            end = min(end, next_match['start'])
        needed = minimum if not sequence else min(minimum, MAX_SEQUENCE)
        if end < start + needed:
            limit = duration
            if next_match and next_match['start'] > start:
                limit = min(limit, next_match['start'])
            end = min(limit, start + needed)
        if end <= start:
            continue
        items.append(dict(
            id=_stable_id('overlay', asset['id'], start),
            kind='overlay', start=round(start, 3), end=round(end, 3),
            payload=dict(
                image=asset['image'], mode=asset['mode'],
                webcam_rect=asset.get('webcam_rect') or library.get('webcam_rect'),
            ),
            reason=f"Menção a '{match['term']}' na fala.",
            evidence=dict(asset_id=asset['id'], words=[w.get('text', '') for w in match['words']]),
            priority=40 if kind_asset == 'meme' else 60,
            status='pending', needs_text=False, asset_id=asset['id'],
            kind_asset=kind_asset, license=asset.get('license', ''),
            credit=asset.get('credit') or asset.get('license', ''),
            auto=bool(asset.get('auto')), source_url=asset.get('source_url'),
            max_per_video=asset.get('max_per_video'),
            min_seconds=minimum, cooldown=cooldown,
        ))
    last, meme_count, result = {}, {}, []
    for item in items:
        asset_id = item['asset_id']
        if asset_id in last and item['start'] - last[asset_id] < item['cooldown']:
            continue
        if item['kind_asset'] == 'meme':
            limit = int(item['max_per_video'] or 0)
            if limit and meme_count.get(asset_id, 0) >= limit:
                continue
            meme_count[asset_id] = meme_count.get(asset_id, 0) + 1
        last[asset_id] = item['start']
        result.append(item)
    return result


def _claim_items(jobs, job_id, timeline):
    path = jobs.path(job_id, 'editorial/brief.json')
    if not path.is_file():
        return []
    brief = storage.read(path)
    segments = {segment['id']: segment for segment in _segments(view(jobs, job_id))}
    duration = float(timeline['duration'])
    items = []
    for claim in brief.get('claims') or []:
        if claim.get('status') != 'CONTRADICTED' or not claim.get('human_confirmed'):
            continue
        segment = next((segments[sid] for sid in reversed(claim.get('segment_ids') or [])
                        if sid in segments), None)
        if segment is None:
            continue
        pieces = list(project_interval(timeline, segment['start'], segment['end']))
        if not pieces:
            continue
        start = max(0.0, min(duration, pieces[-1][1]))
        end = min(duration + MAX_SECONDS, start + MAX_SECONDS)
        high = str(claim.get('severity', '')).lower() == 'high'
        quote = str(claim.get('quote') or '').strip()
        evidence = (claim.get('evidence') or [{}])[0]
        excerpt = str(evidence.get('excerpt') or '').strip()
        text = (quote or claim.get('text') or 'Correção necessária').strip()
        kind = 'card' if high else 'banner'
        if kind == 'card':
            payload = dict(label='CORREÇÃO', lines=[text[:180]], note=excerpt[:180],
                           duration=MAX_SECONDS)
        else:
            payload = dict(first=text[:180], second=excerpt[:180])
        items.append(dict(
            id=_stable_id(kind, 'claim:' + str(claim.get('id')), start),
            kind=kind, start=round(start, 3), end=round(end, 3), payload=payload,
            reason='Claim contradita e confirmada por humano; revise o texto.',
            evidence=dict(claim_id=claim.get('id'), quote=text, excerpt=excerpt),
            priority=100 if high else 90, status='pending', needs_text=True,
            claim_id=claim.get('id'),
        ))
    return items


def _export_video(jobs, job_id, export_id=None, revision=None):
    def valid(directory):
        video = directory / 'video.mp4'
        record = directory / 'export.json'
        if not video.is_file() or not record.is_file():
            return None
        try:
            metadata = storage.read(record)
        except (OSError, ValueError):
            return None
        if metadata.get('state') != 'complete':
            return None
        if revision is None or int(metadata.get('revision', -1)) != int(revision):
            return None
        return video

    if export_id:
        directory = jobs.path(job_id, f'exports/{export_id}')
        video = valid(directory)
        return (video, export_id) if video else (None, None)
    exports = jobs.path(job_id, 'exports')
    if exports.is_dir():
        candidates = [
            ((directory / 'video.mp4').stat().st_mtime_ns, directory)
            for directory in exports.iterdir()
            if directory.is_dir() and valid(directory)
        ]
        if candidates:
            directory = max(candidates, key=lambda item: item[0])[1]
            return directory / 'video.mp4', directory.name
    return None, None


def _mask(gray, rect):
    if not rect:
        return gray
    height, width = gray.shape
    x, y, w, h = [float(value) for value in rect]
    left = max(0, min(width, round(x * width / 1920)))
    top = max(0, min(height, round(y * height / 1080)))
    right = max(left, min(width, round((x + w) * width / 1920)))
    bottom = max(top, min(height, round((y + h) * height / 1080)))
    result = gray.copy()
    result[top:bottom, left:right] = 0
    return result


def _dhash(gray):
    image = Image.fromarray(gray).resize((17, 16), Image.LANCZOS)
    data = np.asarray(image, dtype=np.int16)
    return (data[:, 1:] > data[:, :-1]).reshape(-1)


def _hazard_hashes(references, rect):
    result = {}
    for path in references:
        try:
            image = np.asarray(Image.open(path).convert('L').resize((320, 180), Image.LANCZOS))
        except (OSError, ValueError):
            continue
        result[path.name] = _dhash(_mask(image, rect))
    return result


def _samples(path):
    command = [
        media.binary('ffmpeg'), '-nostdin', '-v', 'error', '-i', str(path),
        '-vf', f'fps={SAMPLE_FPS},scale=320:180', '-pix_fmt', 'gray',
        '-f', 'rawvideo', '-',
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = 320 * 180
    index = 0
    try:
        while True:
            raw = process.stdout.read(size)
            if len(raw) < size:
                break
            yield index / SAMPLE_FPS, np.frombuffer(raw, dtype=np.uint8).reshape((180, 320))
            index += 1
    finally:
        if process.poll() is None:
            process.kill()
        error = process.stderr.read() if process.stderr else b''
        process.wait(timeout=30)
        if process.returncode:
            raise RuntimeError((error.decode('utf-8', 'replace') or 'ffmpeg falhou.')[-1000:])


def _screen_terms(jobs, job_id):
    path = jobs.path(job_id, 'visual/screen_terms.json')
    if not path.is_file():
        return []
    try:
        value = storage.read(path)
    except (OSError, ValueError):
        return []
    terms = value.get('terms') if isinstance(value, dict) else None
    return [str(term) for term in terms or [] if str(term).strip()]


def _merge_hazard_groups(groups):
    merged = []
    for group in groups:
        if merged and group[0][0] - (merged[-1][-1][0] + 1 / SAMPLE_FPS) < .5:
            merged[-1].extend(group)
        else:
            merged.append(group)
    return merged


def _hazard_items(jobs, job_id, video, library, hazards_dir=None):
    hazards = Path(hazards_dir or HAZARDS_DIR)
    references = sorted(
        [path for path in hazards.glob('*') if path.suffix.lower() in ('.png', '.jpg', '.jpeg')]
    ) if hazards.is_dir() else []
    if not references:
        return [], ''
    if not video:
        return [], 'Exporte a revisão atual para detectar telas indevidas'
    rect = library.get('webcam_rect')
    hashes = _hazard_hashes(references, rect)
    samples = []
    for time, gray in _samples(video):
        value = _dhash(_mask(gray, rect))
        matches = [name for name, reference in hashes.items()
                   if np.count_nonzero(value != reference) <= HASH_DISTANCE]
        samples.append((time, bool(matches), matches[0] if matches else None))
    groups, current = [], []
    for time, matched, name in samples:
        if matched:
            current.append((time, name))
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    merged = _merge_hazard_groups(groups)
    groups = merged
    broll = [
        asset for asset in library.get('assets') or []
        if str(asset.get('kind') or '').lower() == 'broll' and asset.get('image')
    ]
    broll_used = set()
    items = []
    for index, group in enumerate(groups):
        start = group[0][0]
        end = group[-1][0] + 1 / SAMPLE_FPS
        if end - start < MIN_HAZARD:
            continue
        clean = [
            time for time, matched, _ in samples
            if not matched and (time <= start or time >= end)
        ]
        if not clean:
            continue
        clean_time = min(clean, key=lambda time: abs(time - start))
        still_dir = jobs.path(job_id, 'finishing/stills')
        still_dir.mkdir(parents=True, exist_ok=True)
        still = still_dir / f'hazard_{index:03d}_{round(clean_time * 1000)}.jpg'
        try:
            media.run([
                'ffmpeg', '-nostdin', '-y', '-v', 'error', '-ss', str(clean_time),
                '-i', str(video), '-frames:v', '1', '-q:v', '2', str(still),
            ])
        except (OSError, RuntimeError):
            continue
        reference = group[0][1]
        screen_terms = _screen_terms(jobs, job_id)
        evidence = dict(reference=reference)
        if screen_terms:
            evidence['screen_terms'] = screen_terms
        long_cover = end - start > 4
        payload = dict(image=str(still), mode='fill', webcam_rect=rect)
        if long_cover:
            for asset in broll:
                if asset['id'] not in broll_used:
                    broll_used.add(asset['id'])
                    payload['alternatives'] = [asset['image']]
                    break
        reason = (
            'Trecho longo de tela indevida: troque por uma imagem ou B-roll.'
            if long_cover else f'Tela indevida semelhante a {reference}.'
        )
        items.append(dict(
            id=_stable_id('cover', 'hazard:' + str(reference), start),
            kind='cover', start=round(start, 3), end=round(end, 3),
            payload=payload, reason=reason, evidence=evidence, priority=80,
            status='pending', needs_text=False, license='', long=long_cover,
        ))
    return items, ''


def _resolve(items):
    ordered = sorted(
        items, key=lambda item: (-item.get('priority', 0), item['start'])
    )
    occupied, kept = [], []
    for item in ordered:
        if item.get('kind') == 'card':
            kept.append(item)
            continue
        if any(item['start'] < end and item['end'] > start for start, end in occupied):
            continue
        occupied.append((item['start'], item['end']))
        kept.append(item)
    return sorted(kept, key=lambda item: (item['start'], item['kind']))


def suggest(jobs, job_id, export_id=None, library_path=None, hazards_dir=None, expand_library=False):
    current = view(jobs, job_id)
    if expand_library:
        from video_editor.finishing import library as library_module
        library_module.expand(jobs, job_id, library_path=library_path)
    library = load_library(library_path)
    edit_revision = int(current['edit']['revision'])
    video, selected_export = _export_video(jobs, job_id, export_id, edit_revision)
    items = _mention_items(current, library)
    items.extend(_claim_items(jobs, job_id, current['timeline']))
    hazard_items, warning = _hazard_items(
        jobs, job_id, video, library, hazards_dir
    )
    items.extend(hazard_items)
    items = _resolve(items)
    path = jobs.path(job_id, 'finishing/suggestions.json')
    old = storage.read(path) if path.is_file() else dict(schema=1, revision=0, items=[])
    old_by_id = {item['id']: item for item in old.get('items') or []}
    merged = []
    for item in items:
        previous = old_by_id.get(item['id'])
        if previous and previous.get('status') == 'edited':
            merged.append(previous)
        elif previous:
            item['status'] = previous.get('status', 'pending')
            merged.append(item)
        else:
            merged.append(item)
    document = dict(
        schema=1, revision=int(old.get('revision', 0)) + 1, items=merged,
        export_id=selected_export, export_revision=edit_revision,
        edit_revision=edit_revision,
        export=dict(id=selected_export, revision=edit_revision),
    )
    if warning:
        document['warning'] = warning
    jobs.write(job_id, 'finishing/suggestions.json', document)
    return document


def load_suggestions(jobs, job_id):
    path = jobs.path(job_id, 'finishing/suggestions.json')
    if not path.is_file():
        return dict(schema=1, revision=0, items=[])
    value = storage.read(path)
    value.setdefault('schema', 1)
    value.setdefault('revision', 0)
    value.setdefault('items', [])
    return value


def decide(jobs, job_id, request):
    request = dict(request)
    with jobs.lock(job_id):
        document = load_suggestions(jobs, job_id)
        require_revision(document, int(request.get('expected_revision', -1)))
        item = next((value for value in document['items'] if value['id'] == request.get('item_id')), None)
        if item is None:
            raise ValueError('Sugestão inexistente.')
        action = str(request.get('action', '')).lower()
        if action == 'accept':
            item['status'] = 'accepted'
        elif action == 'reject':
            item['status'] = 'rejected'
            if item.get('auto') and item.get('asset_id'):
                try:
                    from video_editor.finishing import library
                    library.reject(LIBRARY_PATH, item['asset_id'])
                except (OSError, ValueError):
                    pass
        elif action == 'edit':
            payload = dict(request.get('payload') or {})
            item['payload'] = dict(item.get('payload') or {}, **payload)
            if request.get('start') is not None:
                item['start'] = round(float(request['start']), 3)
            if request.get('end') is not None:
                item['end'] = round(float(request['end']), 3)
            if request.get('needs_text') is not None:
                item['needs_text'] = bool(request['needs_text'])
            elif any(key in payload for key in ('first', 'second', 'lines', 'note')):
                item['needs_text'] = False
            if not 0 <= item['start'] < item['end']:
                raise ValueError('Tempos da sugestão inválidos.')
            item['status'] = 'edited'
        else:
            raise ValueError('Ação precisa ser accept, reject ou edit.')
        document['revision'] = int(document['revision']) + 1
        jobs.write(job_id, 'finishing/suggestions.json', document)
        return document


def _selected(document, accept_all=False):
    statuses = {'accepted', 'edited'}
    if accept_all:
        statuses |= {'pending'}
    return [
        item for item in document.get('items') or []
        if item.get('status') in statuses and not item.get('needs_text')
    ]


def build_plan(jobs, job_id, out=None, accept_all=False):
    document = load_suggestions(jobs, job_id)
    selected = _selected(document, accept_all)
    overlays, banners, cards = [], [], []
    for item in selected:
        payload = dict(item.get('payload') or {})
        if item['kind'] in ('overlay', 'cover'):
            overlays.append(dict(
                start=item['start'], end=item['end'],
                image=payload.get('image'), mode=payload.get('mode', 'contain'),
                webcam_rect=payload.get('webcam_rect'),
            ))
        elif item['kind'] == 'banner':
            banners.append(dict(
                start=item['start'], end=item['end'],
                first=payload.get('first', ''), second=payload.get('second', ''),
            ))
        elif item['kind'] == 'card':
            cards.append(dict(
                at=item['start'], duration=max(0.1, item['end'] - item['start']),
                label=payload.get('label', 'CORREÇÃO'),
                lines=payload.get('lines') or ['Correção'],
                note=payload.get('note', ''),
            ))
    plan = dict(overlays=overlays, banners=banners, cards=cards)
    path = Path(out) if out else jobs.path(job_id, 'finishing/plan.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write(path, plan)
    models.load_plan(path)
    return dict(path=str(path), plan=plan)


def _chapter_points(jobs, job_id, timeline):
    path = jobs.path(job_id, 'editorial/brief.json')
    if not path.is_file():
        return []
    brief = storage.read(path)
    segments = {segment['id']: segment for segment in _segments(view(jobs, job_id))}
    duration = float(timeline['duration'])
    points = []
    for chapter in brief.get('chapters') or []:
        starts = []
        for sid in chapter.get('segment_ids') or []:
            segment = segments.get(sid)
            if segment is None:
                continue
            pieces = list(project_interval(timeline, segment['start'], segment['end']))
            if pieces:
                starts.append(pieces[0][0])
        if starts:
            start = max(0.0, min(duration, min(starts)))
            points.append((start, str(chapter.get('title') or '').strip()))
    points.sort()
    if not points:
        return []
    if points[0][0] > 0:
        points.insert(0, (0.0, 'Introdução'))
    while len(points) > 1:
        changed = False
        for index in range(len(points) - 1):
            if points[index + 1][0] - points[index][0] < 10:
                points.pop(index + 1)
                changed = True
                break
        if not changed:
            break
    while len(points) > 1 and duration - points[-1][0] < 10:
        points.pop()
    return points


def _clock(seconds):
    seconds = max(0, int(round(seconds)))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def describe(jobs, job_id):
    current = view(jobs, job_id)
    brief_path = jobs.path(job_id, 'editorial/brief.json')
    brief = storage.read(brief_path) if brief_path.is_file() else {}
    suggestions = load_suggestions(jobs, job_id)
    chapters = _chapter_points(jobs, job_id, current['timeline'])
    titles = brief.get('titles') or []
    lines = []
    if titles:
        lines.extend(['Títulos sugeridos:', *[f'- {title}' for title in titles], ''])
    if brief.get('summary'):
        lines.extend(['Resumo:', str(brief['summary']), ''])
    if chapters:
        lines.append('Capítulos:')
        lines.extend(f'{_clock(time)} {title}' for time, title in chapters)
        lines.append('')
    credits = []
    for item in suggestions.get('items') or []:
        if item.get('status') not in ('accepted', 'edited'):
            continue
        credit = str(item.get('credit') or item.get('license') or '')
        if credit and credit not in credits:
            credits.append(credit)
    if credits:
        lines.extend(['Créditos:', *[f'- {credit}' for credit in credits], ''])
    if any(item.get('kind') in ('card', 'banner') and item.get('status') in ('accepted', 'edited')
           for item in suggestions.get('items') or []):
        lines.append('Nota: este vídeo contém cartelas/banners de correção.')
    text = '\n'.join(lines).strip() + '\n'
    path = jobs.path(job_id, 'descricao.txt')
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write_text(path, text)
    finishing_path = jobs.path(job_id, 'finishing/descricao.txt')
    finishing_path.parent.mkdir(parents=True, exist_ok=True)
    storage.write_text(finishing_path, text)
    return dict(path=str(path), text=text)
