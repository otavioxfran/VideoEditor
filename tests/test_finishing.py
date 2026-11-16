import io
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from video_editor import media
from video_editor.finishing import assets, capcut, layers, models
from video_editor.finishing import render as finishing_render


pytestmark = pytest.mark.slow

def _image(path):
    image = Image.new('RGBA', (200, 120), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((20, 10, 120, 110), fill=(255, 60, 120, 255))
    image.save(path)
    return path


def _opaque_image(path):
    image = Image.new('RGBA', (200, 120), (20, 220, 120, 255))
    ImageDraw.Draw(image).ellipse((20, 10, 120, 110), fill=(255, 60, 120, 255))
    image.save(path)
    return path


def test_every_layer_mode_has_the_webcam_hole(tmp_path):
    image = _image(tmp_path / 'image.png')
    rect = (1240, 0, 680, 384)
    for mode in models.MODES:
        layer = layers.build_layer(models.Overlay(1, 2, image, mode, rect))
        assert layer.size == (1920, 1080)
        assert layer.getpixel((1300, 100))[3] == 0


def test_upscale_is_limited_to_1_6():
    assert layers.fit_scale(100, 50, 2000, 1000) == pytest.approx(1.6)
    assert layers.fit_scale(400, 200, 200, 100) == pytest.approx(.5)


def test_time_mapping_with_two_cards():
    first = models.Card(38.86, 'CORREÇÃO', ('a',), '', 4)
    second = models.Card(575.31, 'ATENÇÃO', ('b',), '', 4)
    plan = models.Plan(cards=[first, second])
    assert models.map_time(10, plan.cards) == 10
    assert models.map_time(38.85, plan.cards) == 38.85
    assert models.map_time(38.86, plan.cards) == pytest.approx(42.86)
    assert models.map_time(100, plan.cards) == pytest.approx(104)
    assert models.map_time(575.31, plan.cards) == pytest.approx(583.31)
    assert models.map_end(100, plan.cards) == pytest.approx(104)
    assert models.card_start(first, plan.cards) == pytest.approx(38.86)
    assert models.card_start(second, plan.cards) == pytest.approx(579.31)
    assert models.final_duration(600, plan.cards) == pytest.approx(608)


def test_end_exactly_at_card_stays_before_it():
    cards = [models.Card(10, 'CORREÇÃO', ('a',), '', 4)]
    assert models.map_range(5, 10, cards) == pytest.approx((5, 10))
    assert models.map_range(10, 12, cards) == pytest.approx((14, 16))


def test_srt_is_shifted_by_cards():
    text = '1\n00:00:38,860 --> 00:00:39,500\nfala\n'
    shifted = models.shift_srt(text, [models.Card(38.86, 'CORREÇÃO', ('a',), '', 4)])
    assert '00:00:42,860 --> 00:00:43,500' in shifted


def test_overlapping_overlays_are_rejected(tmp_path):
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(
        json.dumps(
            {
                'overlays': [
                    {'start': 1, 'end': 4, 'mode': 'dark'},
                    {'start': 2, 'end': 5, 'mode': 'dark'},
                ]
            }
        ),
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='Overlays sobrepostos'):
        models.load_plan(plan_path)


def _base_video(path):
    subprocess.run(
        [
            media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=30:duration=3',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-shortest', str(path),
        ],
        check=True,
    )
    return path


def test_render_synthetic_three_seconds(tmp_path):
    base = _base_video(tmp_path / 'base.mp4')
    image = _image(tmp_path / 'image.png')
    plan = models.Plan(
        overlays=[models.Overlay(.5, 1.5, image, 'cover', (400, 0, 240, 150))]
    )
    output = tmp_path / 'out.mp4'
    result = finishing_render.render(
        base, plan, output, work_dir=tmp_path / 'parts', encoder='libx264'
    )
    assert result['frames'] == 90
    probe = media.probe(output)
    assert probe['duration'] == pytest.approx(3.0, abs=.02)
    assert probe['audio_codec'] == 'aac'


def _silent_video(path):
    subprocess.run(
        [
            media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=30:duration=3',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path),
        ],
        check=True,
    )
    return path


def test_render_silent_base_with_and_without_cards(tmp_path):
    base = _silent_video(tmp_path / 'silent.mp4')
    with_card = finishing_render.render(
        base,
        models.Plan(cards=[models.Card(1, 'CORREÇÃO', ('a',), '', 1)]),
        tmp_path / 'card.mp4', work_dir=tmp_path / 'card-parts', encoder='libx264',
    )
    assert with_card['frames'] == 120
    assert finishing_render._probe_audio(tmp_path / 'card.mp4') == {}
    without_card = finishing_render.render(
        base, models.Plan(), tmp_path / 'plain.mp4',
        work_dir=tmp_path / 'plain-parts', encoder='libx264',
    )
    assert without_card['frames'] == 90


def test_audio_is_cut_only_at_cards():
    pieces = finishing_render._audio_pieces(
        models.Plan(cards=[models.Card(10, 'CORREÇÃO', ('a',), '', 4)]), 20
    )
    assert [(kind, left, right) for kind, left, right, _ in pieces] == [
        ('base', 0, 10), ('card', 10, 10), ('base', 10, 20),
    ]


def _range(start, duration):
    return {
        'source_timerange': {'start': start, 'duration': duration},
        'target_timerange': {'start': start, 'duration': duration},
    }


def _template(root, audio=False):
    template = root / 'Template'
    (template / 'Timelines' / 'TESTDRAFT').mkdir(parents=True)
    base = {
        'id': 'b1',
        'material_id': 'm1',
        'extra_material_refs': [],
        'clip': {'scale': {'x': 1}, 'transform': {'x': 0, 'y': 0}},
        **_range(0, 20_000_000),
    }
    tracks = [
        {'id': 'base', 'type': 'video', 'flag': 0, 'segments': [base]},
        {
            'id': 'overlay',
            'type': 'video',
            'flag': 2,
            'segments': [
                {
                    'id': 's1',
                    'material_id': 'm1',
                    'extra_material_refs': [],
                    'clip': {'scale': {'x': 1}, 'transform': {'x': 0, 'y': 0}},
                    'target_timerange': {'start': 0, 'duration': 1_000_000},
                }
            ],
        },
    ]
    if audio:
        tracks.append({
            'id': 'audio',
            'type': 'audio',
            'flag': 0,
            'segments': [
                {
                    'id': 'a1',
                    'material_id': 'm1',
                    'extra_material_refs': [],
                    **_range(0, 20_000_000),
                }
            ],
        })
    draft = {
        'id': 'TESTDRAFT',
        'duration': 20_000_000,
        'materials': {
            'videos': [{'id': 'm1', 'path': 'x', 'width': 1920, 'height': 1080}],
            'texts': [],
        },
        'tracks': tracks,
    }
    text = json.dumps(draft)
    (template / 'draft_content.json').write_text(text, encoding='utf-8')
    (template / 'Timelines' / 'TESTDRAFT' / 'draft_content.json').write_text(
        text, encoding='utf-8'
    )
    (template / 'draft_meta_info.json').write_text(
        json.dumps(
            {'draft_id': 'TESTDRAFT', 'draft_name': 'old', 'draft_fold_path': 'old'}
        ),
        encoding='utf-8',
    )
    return template


def _export(tmp_path, plan, name='copy', audio=False):
    template = _template(tmp_path, audio=audio)
    result = capcut.export_draft(
        template, plan, target=tmp_path / name, name=name
    )
    target = Path(result['target'])
    return template, result, json.loads(
        (target / 'draft_content.json').read_text(encoding='utf-8')
    )


def _segments_by_kind(draft):
    materials = {item['id']: item for item in draft['materials'].get('videos', [])}
    result = {}
    for track in draft['tracks']:
        for segment in track.get('segments', []):
            material = materials.get(segment.get('material_id'), {})
            name = str(material.get('material_name', ''))
            if '_' not in name:
                continue
            result.setdefault(name.split('_')[0], []).append((track, segment))
    return result


def test_capcut_copy_keeps_id_and_writes_both_copies(tmp_path):
    image = _image(tmp_path / 'image.png')
    plan = models.Plan(
        overlays=[models.Overlay(1, 2, image, 'cover', (1240, 0, 680, 384))],
        cards=[models.Card(2.5, 'CORREÇÃO', ('teste',), 'nota', 4)],
        banners=[models.Banner(3, 4, 'primeira', 'segunda')],
    )
    template, result, draft = _export(tmp_path, plan)
    assert draft['id'] == 'TESTDRAFT'
    assert (Path(result['target']) / 'Timelines' / 'TESTDRAFT' / 'draft_content.json').is_file()
    assert json.loads(
        (Path(result['target']) / 'draft_meta_info.json').read_text(encoding='utf-8')
    )['draft_id'] == 'TESTDRAFT'
    assert len(draft['tracks']) == 5
    original = json.loads((template / 'draft_content.json').read_text(encoding='utf-8'))
    assert len(original['tracks']) == 2


def _user_overlay_track(template):
    root = template / 'draft_content.json'
    draft = json.loads(root.read_text(encoding='utf-8'))
    track = next(item for item in draft['tracks'] if item['id'] == 'overlay')
    track['render_index'] = 7
    track['track_render_index'] = 7
    track['segments'] = [
        {
            'id': f'user-{index}',
            'material_id': 'm1',
            'extra_material_refs': [],
            'clip': {'scale': {'x': 1}, 'transform': {'x': 0, 'y': 0}},
            **_range(start, 2_000_000),
        }
        for index, start in enumerate((0, 12_000_000))
    ]
    text = json.dumps(draft)
    root.write_text(text, encoding='utf-8')
    (template / 'Timelines' / 'TESTDRAFT' / 'draft_content.json').write_text(
        text, encoding='utf-8'
    )
    return template


def test_capcut_preserves_user_overlay_track(tmp_path):
    template = _user_overlay_track(_template(tmp_path))
    image = _image(tmp_path / 'image.png')
    plan = models.Plan(
        overlays=[models.Overlay(1, 2, image, 'cover', None)],
        banners=[models.Banner(3, 4, 'a', 'b')],
        cards=[models.Card(10, 'CORREÇÃO', ('a',), '', 4)],
    )
    result = capcut.export_draft(template, plan, target=tmp_path / 'copy')
    draft = json.loads(
        (Path(result['target']) / 'draft_content.json').read_text(encoding='utf-8')
    )
    user = next(track for track in draft['tracks'] if track['id'] == 'overlay')
    assert len(user['segments']) == 2
    assert user['render_index'] == 7
    assert user['segments'][1]['target_timerange']['start'] == 16_000_000
    new_tracks = [
        track for track in draft['tracks']
        if track.get('render_index') in (2, 3, 4)
    ]
    assert sorted(track['render_index'] for track in new_tracks) == [2, 3, 4]


def test_capcut_card_shifts_all_template_tracks(tmp_path):
    _, _, draft = _export(
        tmp_path, models.Plan(cards=[models.Card(10, 'CORREÇÃO', ('a',), '', 4)]),
        audio=True,
    )
    for track in draft['tracks']:
        if track['id'] not in ('base', 'audio'):
            continue
        ranges = [
            (segment['target_timerange']['start'], segment['target_timerange']['duration'])
            for segment in track['segments']
        ]
        assert ranges == [(0, 10_000_000), (14_000_000, 10_000_000)]
        assert track['segments'][1]['source_timerange']['start'] == 10_000_000
    assert draft['duration'] == 24_000_000
    assert len(draft['tracks']) == 4


def test_capcut_overlay_crossing_card_is_split(tmp_path):
    image = _image(tmp_path / 'image.png')
    plan = models.Plan(
        overlays=[models.Overlay(5, 15, image, 'cover', None)],
        cards=[models.Card(10, 'CORREÇÃO', ('a',), '', 4)],
    )
    _, _, draft = _export(tmp_path, plan)
    overlays = _segments_by_kind(draft)['overlay']
    assert len(overlays) == 2
    ranges = [
        (segment['target_timerange']['start'], segment['target_timerange']['duration'])
        for _, segment in sorted(overlays, key=lambda item: item[1]['target_timerange']['start'])
    ]
    assert ranges == [(5_000_000, 5_000_000), (14_000_000, 5_000_000)]


def test_capcut_uses_three_finishing_tracks(tmp_path):
    image = _image(tmp_path / 'image.png')
    plan = models.Plan(
        overlays=[
            models.Overlay(1, 2, image, 'cover', None),
            models.Overlay(3, 4, image, 'cover', None),
        ],
        banners=[
            models.Banner(1, 2, 'a', 'b'),
            models.Banner(3, 4, 'c', 'd'),
        ],
        cards=[models.Card(5, 'CORREÇÃO', ('a',), '', 4)],
    )
    _, _, draft = _export(tmp_path, plan)
    groups = _segments_by_kind(draft)
    assert len(groups['overlay']) == 2
    assert len(groups['banner']) == 2
    assert len(groups['card']) == 1
    assert {kind: groups[kind][0][0]['render_index'] for kind in groups} == {
        'overlay': 2, 'banner': 3, 'card': 4,
    }


def test_capcut_refuses_locked_template(tmp_path):
    template = _template(tmp_path)
    (template / '.locked').write_text('', encoding='utf-8')
    with pytest.raises(RuntimeError, match='locked'):
        capcut.export_draft(template, models.Plan(), target=tmp_path / 'copy')


def test_cutout_opaque_image_calls_rembg(tmp_path, monkeypatch):
    image = _opaque_image(tmp_path / 'opaque.png')

    def fake_remove(data, session=None):
        result = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
        buffer = io.BytesIO()
        result.save(buffer, format='PNG')
        return buffer.getvalue()

    def fake_session(model):
        return object()

    module = types.ModuleType('rembg')
    module.new_session = fake_session
    module.remove = fake_remove
    monkeypatch.setitem(sys.modules, 'rembg', module)
    layer = layers.build_layer(models.Overlay(1, 2, image, 'cutout'))
    assert layers.cutout_module.cache_path(image).is_file()
    assert layer.size == (1920, 1080)


def test_badge_has_no_black_shadow(tmp_path):
    image = Image.new('RGBA', (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((50, 50, 150, 150), fill=(255, 0, 0, 255))
    path = tmp_path / 'logo.png'
    image.save(path)
    layer = layers.build_layer(models.Overlay(1, 2, path, 'badge'))
    alpha = np.asarray(layer.getchannel('A'))
    assert 160 not in set(alpha.ravel().tolist())


def test_card_fade_has_both_edges():
    fade = finishing_render._card_fade(4)
    assert 'fade=t=in' in fade and 'fade=t=out' in fade


def test_placement_follows_capcut_clip_transform():
    size, (cx, cy) = capcut.placement(
        (100, 50), {'scale': {'x': 1}, 'transform': {'x': .5, 'y': -.5}}
    )
    assert size == (1920, 960)
    assert (cx, cy) == pytest.approx((1440, 810))


def test_asset_license_filter():
    assert assets.license_ok('CC BY-SA 4.0', 900)
    assert assets.license_ok('Public domain', 900)
    assert not assets.license_ok('All rights reserved', 900)
    assert not assets.license_ok('CC BY 4.0', 500)
