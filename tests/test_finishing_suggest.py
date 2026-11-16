import json
import subprocess

import pytest

from video_editor import media
from video_editor.editing import RevisionConflict
from video_editor.finishing import models, suggest
from video_editor.timeline import digest


def _write_edit(jobs, job_id, cuts=()):
    transcript = jobs.read(job_id, 'transcript.json')
    jobs.write(job_id, 'edit.json', dict(
        schema=1, revision=0, transcript_hash=digest(transcript),
        cuts=list(cuts), protected=[], markers=[], undo=[], history=[],
    ))


def _library(tmp_path, assets, webcam_rect=None):
    image = tmp_path / 'asset.png'
    image.write_bytes(b'')
    value = []
    for asset in assets:
        item = dict(asset)
        item.setdefault('image', str(image))
        item.setdefault('mode', 'contain')
        value.append(item)
    path = tmp_path / 'library.json'
    path.write_text(json.dumps(dict(
        assets=value, webcam_rect=webcam_rect or [1240, 0, 680, 366]
    )), encoding='utf-8')
    return path


def _segments(*rows):
    return [
        dict(start=start, end=end, text=text, words=[
            dict(word=word, start=wstart, end=wend)
            for word, wstart, wend in words
        ])
        for start, end, text, words in rows
    ]


def test_mention_removed_by_cut_is_ignored(jobs, ready, tmp_path):
    info = ready(duration=20, segments=_segments(
        (1, 3, 'Produto X é bom.', [
            ('Produto', 1, 1.4), ('X', 1.4, 1.8), ('é', 1.8, 2), ('bom.', 2.2, 3)
        ])
    ))
    _write_edit(jobs, info['job_id'], [dict(start=0, end=2.1)])
    library = _library(tmp_path, [dict(id='x', terms=['produto x'], mode='badge')])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert value['items'] == []


def test_mention_uses_output_time_after_cut_and_builds_plan(jobs, ready, tmp_path):
    info = ready(duration=20, segments=_segments(
        (2, 5, 'Produto X é bom.', [
            ('Produto', 2, 2.4), ('X', 2.4, 2.8),
            ('é', 2.8, 3), ('bom.', 4, 5),
        ])
    ))
    _write_edit(jobs, info['job_id'], [dict(start=0, end=1)])
    library = _library(tmp_path, [dict(
        id='x', terms=['produto x'], mode='badge', license='CC BY'
    )])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert len(value['items']) == 1
    item = value['items'][0]
    assert item['kind'] == 'overlay'
    assert item['start'] == pytest.approx(1)
    assert item['end'] <= item['start'] + 4
    accepted = suggest.decide(jobs, info['job_id'], dict(
        expected_revision=value['revision'], item_id=item['id'], action='accept'
    ))
    assert accepted['items'][0]['status'] == 'accepted'
    plan = suggest.build_plan(jobs, info['job_id'])
    loaded = models.load_plan(plan['path'])
    assert loaded.overlays[0].start == pytest.approx(1)


def test_five_logo_sequence_has_no_overlap_and_short_items(jobs, ready, tmp_path):
    words = []
    for index in range(5):
        start = 2 + index * 2
        words.append((f'logo{index}', start, start + .6))
    segments = _segments((2, 20, ' '.join(word for word, _, _ in words), words))
    info = ready(duration=20, segments=segments)
    assets = [dict(id=f'l{index}', terms=[f'logo{index}'], mode='badge')
              for index in range(5)]
    library = _library(tmp_path, assets)
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    items = sorted(value['items'], key=lambda item: item['start'])
    assert len(items) == 5
    for left, right in zip(items, items[1:]):
        assert left['end'] <= right['start']
    assert all(item['end'] - item['start'] <= 1.5 for item in items)


def test_cooldown_and_meme_limit(jobs, ready, tmp_path):
    words = [
        ('meme', 2, 2.5), ('meme', 70, 70.5), ('meme', 140, 140.5),
    ]
    info = ready(duration=200, segments=_segments(
        (2, 150, 'meme meme meme', words)
    ))
    library = _library(tmp_path, [dict(
        id='m', terms=['meme'], mode='contain', kind='meme', max_per_video=2
    )])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert len(value['items']) == 2


def test_contradicted_claims_need_text_and_can_be_edited(jobs, ready, tmp_path):
    info = ready(duration=20)
    transcript = jobs.read(info['job_id'], 'transcript.json')
    segments = transcript['segments']
    brief = dict(
        claims=[
            dict(id='c1', text='A', quote='A', segment_ids=[segments[0]['id']],
                 status='CONTRADICTED', human_confirmed=True),
            dict(id='c2', text='B', quote='B', segment_ids=[segments[-1]['id']],
                 status='CONTRADICTED', human_confirmed=True, severity='high'),
        ],
        chapters=[], titles=['Título'], summary='Resumo',
    )
    jobs.write(info['job_id'], 'editorial/brief.json', brief)
    library = _library(tmp_path, [])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    claims = [item for item in value['items'] if item['claim_id']]
    assert {item['kind'] for item in claims} == {'banner', 'card'}
    assert all(item['needs_text'] for item in claims)
    plan = suggest.build_plan(jobs, info['job_id'], accept_all=True)
    assert plan['plan']['banners'] == [] and plan['plan']['cards'] == []
    item = claims[0]
    edited = suggest.decide(jobs, info['job_id'], dict(
        expected_revision=value['revision'], item_id=item['id'], action='edit',
        payload=dict(first='Correção revisada', second='Fonte'),
    ))
    assert edited['items'][0]['needs_text'] is False
    result = suggest.build_plan(jobs, info['job_id'], accept_all=True)
    assert len(result['plan']['banners']) + len(result['plan']['cards']) == 1


def test_stale_revision_is_rejected(jobs, ready, tmp_path):
    info = ready(duration=20, segments=_segments(
        (2, 4, 'Produto X.', [('Produto', 2, 2.5), ('X', 2.5, 3)])
    ))
    library = _library(tmp_path, [dict(id='x', terms=['produto x'], mode='contain')])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    with pytest.raises(RevisionConflict):
        suggest.decide(jobs, info['job_id'], dict(
            expected_revision=value['revision'] - 1,
            item_id=value['items'][0]['id'], action='accept'
        ))


def _hazard_video(jobs, job_id):
    export = jobs.path(job_id, 'exports/exp/video.mp4')
    export.parent.mkdir(parents=True, exist_ok=True)
    (export.parent / 'export.json').write_text(
        json.dumps(dict(render_id='exp', state='complete', revision=0)),
        encoding='utf-8',
    )
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=1',
        '-f', 'lavfi', '-i', 'color=blue:size=320x180:rate=30:duration=2',
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[out]',
        '-map', '[out]', '-r', '30', str(export),
    ], check=True)
    return export


def test_screen_hazard_generates_cover_with_clean_still(jobs, ready, tmp_path):
    info = ready(duration=10)
    video = _hazard_video(jobs, info['job_id'])
    reference = tmp_path / 'hazards' / 'screen.png'
    reference.parent.mkdir()
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-ss', '0.2', '-i', str(video), '-frames:v', '1', str(reference),
    ], check=True)
    library = _library(tmp_path, [], webcam_rect=None)
    value = suggest.suggest(
        jobs, info['job_id'], export_id='exp', library_path=str(library),
        hazards_dir=str(reference.parent),
    )
    covers = [item for item in value['items'] if item['kind'] == 'cover']
    assert len(covers) == 1
    assert covers[0]['start'] == pytest.approx(0)
    assert covers[0]['end'] >= 1
    assert covers[0]['payload']['mode'] == 'fill'
    assert covers[0]['payload']['image']
    assert __import__('pathlib').Path(covers[0]['payload']['image']).is_file()


def test_describe_starts_at_zero_merges_short_chapters_and_credits(jobs, ready, tmp_path):
    info = ready(duration=60, segments=_segments(
        (1, 2, 'Produto X é bom.', [('Produto', 1, 1.4), ('X', 1.4, 1.8)]),
        (30, 31, 'Fim.', [('Fim.', 30, 31)]),
    ))
    transcript = jobs.read(info['job_id'], 'transcript.json')
    ids = [segment['id'] for segment in transcript['segments']]
    jobs.write(info['job_id'], 'editorial/brief.json', dict(
        titles=['Um título'], summary='Resumo',
        chapters=[
            dict(title='Abertura', segment_ids=[ids[0]]),
            dict(title='Curto', segment_ids=[ids[0]]),
            dict(title='Fim', segment_ids=[ids[-1]]),
        ],
    ))
    library = _library(tmp_path, [dict(
        id='x', terms=['produto x'], mode='contain', license='CC BY 4.0'
    )])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert value['items']
    for item in list(value['items']):
        value = suggest.decide(jobs, info['job_id'], dict(
            expected_revision=value['revision'], item_id=item['id'], action='accept'
        ))
    text = suggest.describe(jobs, info['job_id'])['text']
    assert '00:00' in text
    assert 'CC BY 4.0' in text
    assert 'Um título' in text
    assert 'Curto' not in text
    assert text.count('\n') < 30


def test_regenerate_preserves_decisions(jobs, ready, tmp_path):
    info = ready(duration=20, segments=_segments(
        (2, 4, 'Produto X.', [('Produto', 2, 2.5), ('X', 2.5, 3)])
    ))
    library = _library(tmp_path, [dict(id='x', terms=['produto x'], mode='contain')])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    item = value['items'][0]
    item_id = item['id']
    value = suggest.decide(jobs, info['job_id'], dict(
        expected_revision=value['revision'], item_id=item_id, action='accept'
    ))
    again = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert next(value for value in again['items'] if value['id'] == item_id)['status'] == 'accepted'
    assert again['revision'] > value['revision']


def test_overlapping_subphrase_keeps_longest(jobs, ready, tmp_path):
    info = ready(duration=20, segments=_segments(
        (2, 10, 'ludwig von mises', [
            ('ludwig', 2, 2.4), ('von', 2.4, 2.8), ('mises', 2.8, 3.2)
        ])
    ))
    library = _library(tmp_path, [dict(
        id='mises', terms=['mises', 'ludwig von mises'], mode='badge'
    )])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert len(value['items']) == 1
    assert value['items'][0]['end'] - value['items'][0]['start'] >= 1.2


def test_meme_cooldown_and_minimum_duration(jobs, ready, tmp_path):
    info = ready(duration=80, segments=_segments(
        (2, 70, 'meme meme', [('meme', 2, 2.4), ('meme', 55, 55.4)])
    ))
    library = _library(tmp_path, [dict(
        id='m', terms=['meme'], mode='contain', kind='meme',
        max_per_video=2, cooldown=20, min_seconds=2.0,
    )])
    value = suggest.suggest(jobs, info['job_id'], library_path=str(library),
                            hazards_dir=str(tmp_path / 'hazards'))
    assert len(value['items']) == 2
    assert all(item['end'] - item['start'] >= 2 for item in value['items'])


def test_long_cover_is_flagged_and_has_broll_alternative(jobs, ready, tmp_path):
    info = ready(duration=10)
    export = jobs.path(info['job_id'], 'exports/exp/video.mp4')
    export.parent.mkdir(parents=True, exist_ok=True)
    (export.parent / 'export.json').write_text(
        json.dumps(dict(render_id='exp', state='complete', revision=0)),
        encoding='utf-8',
    )
    reference = tmp_path / 'hazards' / 'screen.png'
    reference.parent.mkdir()
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=0.2',
        '-frames:v', '1', str(reference),
    ], check=True)
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-loop', '1', '-framerate', '30', '-t', '5', '-i', str(reference),
        '-f', 'lavfi', '-i', 'color=blue:size=320x180:rate=30:duration=2',
        '-filter_complex', '[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]concat=n=2:v=1:a=0[out]',
        '-map', '[out]', '-r', '30', str(export),
    ], check=True)
    library = _library(tmp_path, [dict(
        id='broll', mode='contain', kind='broll'
    )], webcam_rect=None)
    value = suggest.suggest(
        jobs, info['job_id'], export_id='exp', library_path=str(library),
        hazards_dir=str(reference.parent),
    )
    cover = next(item for item in value['items'] if item['kind'] == 'cover')
    assert cover['long'] is True
    assert value['export_id'] == 'exp'
    assert value['export_revision'] == 0
    assert cover['end'] - cover['start'] > 4
    assert cover['reason'] == 'Trecho longo de tela indevida: troque por uma imagem ou B-roll.'
    assert cover['payload']['alternatives']


def test_card_does_not_occupy_overlay_timeline():
    card = dict(id='c', kind='card', start=2, end=6, priority=100)
    overlay = dict(id='o', kind='overlay', start=3, end=5, priority=60)
    banner = dict(id='b', kind='banner', start=7, end=8, priority=90)
    kept = suggest._resolve([card, overlay, banner])
    assert {item['id'] for item in kept} == {'c', 'o', 'b'}


def test_export_selection_uses_current_complete_revision(jobs, ready, tmp_path):
    info = ready(duration=20)
    job_id = info['job_id']
    for name, revision in (('old', 0), ('new', 1)):
        directory = jobs.path(job_id, f'exports/{name}')
        directory.mkdir(parents=True)
        (directory / 'video.mp4').write_bytes(b'x')
        (directory / 'export.json').write_text(
            json.dumps(dict(render_id=name, state='complete', revision=revision)),
            encoding='utf-8',
        )
    video, export_id = suggest._export_video(jobs, job_id, None, 1)
    assert export_id == 'new'
    assert video == jobs.path(job_id, 'exports/new/video.mp4')


def test_hazards_warn_without_current_export(jobs, ready, tmp_path):
    info = ready(duration=20)
    reference = tmp_path / 'hazards' / 'screen.png'
    reference.parent.mkdir()
    reference.write_bytes(b'not really a png')
    library = _library(tmp_path, [], webcam_rect=None)
    value = suggest.suggest(
        jobs, info['job_id'], library_path=str(library),
        hazards_dir=str(reference.parent),
    )
    assert value['warning'] == 'Exporte a revisão atual para detectar telas indevidas'
    assert not [item for item in value['items'] if item['kind'] == 'cover']


def test_nearby_hazard_groups_are_merged():
    groups = [[(0.0, 'a')], [(0.6, 'a')], [(2.0, 'a')]]
    merged = suggest._merge_hazard_groups(groups)
    assert len(merged) == 2
    assert len(merged[0]) == 2
