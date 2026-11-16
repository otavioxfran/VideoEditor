"""CapCut draft exporter: always a new copy, with matching draft/id and timelines."""
import copy
import json
import shutil
import uuid
from pathlib import Path

from video_editor.finishing import cards, layers, models

W, H = 1920, 1080
RENDER = {'overlay': 2, 'banner': 3, 'card': 4}


def placement(image_size, clip, canvas=(W, H)):
    width, height = image_size
    cw, ch = canvas
    scale = float((clip.get('scale') or {}).get('x', 1.0))
    fit = min(cw / width, ch / height) * scale
    size = (max(1, round(width * fit)), max(1, round(height * fit)))
    transform = clip.get('transform') or {}
    cx = cw / 2 + float(transform.get('x', 0.0)) * cw / 2
    cy = ch / 2 - float(transform.get('y', 0.0)) * ch / 2
    return size, (cx, cy)


def _new_id():
    return str(uuid.uuid4()).upper()


def _target(template, target, name):
    if target:
        target = Path(target)
        if (target / '.locked').exists():
            raise RuntimeError('CapCut está aberto neste destino (.locked).')
        if target.exists():
            raise FileExistsError(f'Destino já existe: {target}')
        return target
    base = template.parent / (name or f'{template.name} finishing')
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = base.with_name(f'{base.name} {counter}')
        counter += 1
    return candidate


def _timeline_id(draft, target):
    timelines = target / 'Timelines'
    current = str(draft.get('id') or '')
    if current and (timelines / current).is_dir():
        return current
    if timelines.is_dir():
        directories = [path for path in timelines.iterdir() if path.is_dir()]
        if len(directories) == 1:
            return directories[0].name
    return current or _new_id()


def _overlay_template(draft):
    for track in draft.get('tracks', []):
        if track.get('type') == 'video' and track.get('flag') == 2 and track.get('segments'):
            return track
    raise ValueError('Template do CapCut sem faixa de overlay (flag 2).')


def _material(draft, segment):
    for item in draft['materials'].get('videos', []):
        if item.get('id') == segment.get('material_id'):
            return item
    raise ValueError('Template do CapCut sem material de vídeo de apoio.')


def _extras(draft, segment):
    result = {}
    for reference in segment.get('extra_material_refs', []):
        for key, values in draft['materials'].items():
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and item.get('id') == reference:
                    result[reference] = (key, item)
    return result


def _new_track(template, render_index):
    track = copy.deepcopy({key: value for key, value in template.items() if key != 'segments'})
    track['id'] = _new_id()
    track['segments'] = []
    track['render_index'] = render_index
    track['track_render_index'] = render_index
    return track


def _segment(draft, template, material_template, extras, path, start, duration, name, render_index):
    material = copy.deepcopy(material_template)
    material.update(
        id=_new_id(), path=str(path).replace('\\', '/'), material_name=name,
        width=W, height=H, local_material_id='', origin_material_id='',
    )
    draft['materials']['videos'].append(material)
    refs = []
    for reference, (key, item) in extras.items():
        clone = copy.deepcopy(item)
        clone['id'] = _new_id()
        draft['materials'][key].append(clone)
        refs.append(clone['id'])
    segment = copy.deepcopy(template)
    segment.update(
        id=_new_id(), material_id=material['id'], extra_material_refs=refs,
        source_timerange=dict(start=0, duration=duration),
        target_timerange=dict(start=start, duration=duration),
        render_index=render_index, track_render_index=render_index,
    )
    segment['clip'] = dict(
        segment.get('clip') or {}, scale=dict(x=1.0, y=1.0),
        transform=dict(x=0.0, y=0.0),
    )
    segment['uniform_scale'] = dict(on=True, value=1.0)
    return segment


def _shift_template(draft, cards):
    """Insert card pauses into every existing template track."""
    card_list = sorted(cards, key=lambda item: item.at)
    if not card_list:
        return
    for card in card_list:
        insert = round(models.card_start(card, card_list) * 1_000_000)
        shift = round(card.duration * 1_000_000)
        for track in draft.get('tracks', []):
            segments = track.get('segments')
            if not segments:
                continue
            result = []
            for segment in segments:
                target = segment.get('target_timerange')
                if not isinstance(target, dict):
                    result.append(segment)
                    continue
                start = int(target.get('start', 0))
                duration = int(target.get('duration', 0))
                end = start + duration
                if end <= insert:
                    result.append(segment)
                elif start >= insert:
                    target['start'] = start + shift
                    result.append(segment)
                else:
                    left_duration = insert - start
                    right_duration = end - insert
                    left = copy.deepcopy(segment)
                    right = copy.deepcopy(segment)
                    right['id'] = _new_id()
                    left['target_timerange'].update(start=start, duration=left_duration)
                    right['target_timerange'].update(
                        start=insert + shift, duration=right_duration
                    )
                    left_source = left.get('source_timerange')
                    right_source = right.get('source_timerange')
                    if isinstance(left_source, dict):
                        left_source['duration'] = min(
                            int(left_source.get('duration', left_duration)), left_duration
                        )
                    if isinstance(right_source, dict):
                        source_start = int(right_source.get('start', 0))
                        source_duration = int(right_source.get('duration', right_duration))
                        advance = left_duration
                        speed = segment.get('speed')
                        if isinstance(speed, (int, float)) and speed > 0 and speed != 1:
                            advance = round(left_duration * float(speed))
                        right_source['start'] = source_start + advance
                        right_source['duration'] = max(0, source_duration - advance)
                    result.extend((left, right))
            track['segments'] = result
    total = sum(round(card.duration * 1_000_000) for card in card_list)
    old = draft.get('duration')
    if isinstance(old, (int, float)):
        draft['duration'] = int(old) + total
    else:
        ends = [
            int(segment['target_timerange'].get('start', 0))
            + int(segment['target_timerange'].get('duration', 0))
            for track in draft.get('tracks', [])
            for segment in track.get('segments', [])
            if isinstance(segment.get('target_timerange'), dict)
        ]
        draft['duration'] = (max(ends) if ends else 0)


def _spans(start, end, cards):
    points = [start]
    points.extend(sorted(card.at for card in cards if start < card.at < end))
    points.append(end)
    return zip(points, points[1:])


def export_draft(template, plan, target=None, name=None):
    template = Path(template)
    if not template.is_dir():
        raise FileNotFoundError(template)
    if (template / '.locked').exists():
        raise RuntimeError('CapCut está aberto neste template (.locked).')
    target = _target(template, target, name)
    if (target / '.locked').exists():
        raise RuntimeError('CapCut está aberto neste destino (.locked).')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        template, target,
        ignore=shutil.ignore_patterns('.locked', '*.tmp', '*.bak'),
    )
    content_path = target / 'draft_content.json'
    draft = json.loads(content_path.read_text(encoding='utf-8'))
    draft_id = _timeline_id(draft, target)
    draft['id'] = draft_id
    timeline_dir = target / 'Timelines' / draft_id
    timeline_dir.mkdir(parents=True, exist_ok=True)
    track_template = _overlay_template(draft)
    segment_template = copy.deepcopy(track_template['segments'][0])
    material_template = _material(draft, segment_template)
    extras = _extras(draft, segment_template)
    _shift_template(draft, plan.cards)
    layer_dir = target / 'finishing'
    layer_dir.mkdir(parents=True, exist_ok=True)
    groups = {'overlay': [], 'banner': [], 'card': []}
    for index, item in enumerate(plan.overlays):
        path = layer_dir / f'overlay_{index:03d}.png'
        layers.save_layer(item, path)
        for left, right in _spans(item.start, item.end, plan.cards):
            groups['overlay'].append((
                models.map_time(left, plan.cards),
                models.map_end(right, plan.cards),
                path, f'overlay_{index:03d}.png',
            ))
    for index, item in enumerate(plan.banners):
        path = layer_dir / f'banner_{index:03d}.png'
        cards.save_banner(item, path)
        for left, right in _spans(item.start, item.end, plan.cards):
            groups['banner'].append((
                models.map_time(left, plan.cards),
                models.map_end(right, plan.cards),
                path, f'banner_{index:03d}.png',
            ))
    for index, card in enumerate(plan.cards):
        path = layer_dir / f'card_{index:03d}.png'
        cards.save_card(card, path)
        start = models.card_start(card, plan.cards)
        groups['card'].append((
            start, start + card.duration, path, f'card_{index:03d}.png',
        ))
    timed = 0
    for kind in ('overlay', 'banner', 'card'):
        items = groups[kind]
        if not items:
            continue
        track = _new_track(track_template, RENDER[kind])
        draft['tracks'].append(track)
        for start, end, path, file_name in sorted(items, key=lambda item: item[0]):
            if end <= start:
                raise ValueError(f'Camada {kind}: duração inválida.')
            track['segments'].append(_segment(
                draft, segment_template, material_template, extras, path,
                round(start * 1_000_000), round((end - start) * 1_000_000),
                file_name, RENDER[kind],
            ))
            timed += 1
    text = json.dumps(draft, ensure_ascii=False)
    content_path.write_text(text, encoding='utf-8')
    (timeline_dir / 'draft_content.json').write_text(text, encoding='utf-8')
    for leftover in timeline_dir.glob('draft_content.json.bak'):
        leftover.unlink()
    meta_path = target / 'draft_meta_info.json'
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    else:
        meta = {}
    meta.update(
        draft_id=draft_id,
        draft_name=name or target.name,
        draft_fold_path=str(target).replace('\\', '/'),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return dict(target=str(target), draft_id=draft_id, segments=timed)
