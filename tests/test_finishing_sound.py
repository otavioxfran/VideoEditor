import json
import subprocess

import pytest

from video_editor import media
from video_editor.finishing import models, sound


def _plan(tmp_path, data):
    path = tmp_path / 'plano.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return models.load_plan(path)


def _overlay(start, mode='contain', sound_name=None):
    item = dict(start=start, end=start + 0.25, image='x.png', mode=mode)
    if sound_name is not None:
        item['sound'] = sound_name
    return item


def test_events_follow_layer_kind_and_keep_a_gap(tmp_path):
    plan = _plan(tmp_path, dict(overlays=[
        _overlay(1, sound_name='boom'),
        _overlay(1.3, mode='badge'),          # too close to the boom: dropped
        _overlay(3, mode='badge'),
        _overlay(6, mode='fill'),             # full-frame cover: silent
        _overlay(8, sound_name='none'),
    ], banners=[dict(start=10, end=12, first='a', second='b')],
       cards=[dict(at=5, label='CORREÇÃO', lines=['x'])]))
    # The card at 5 s shifts everything after it by 4 s.
    assert sound.events(plan) == [(1.0, 'boom'), (3.0, 'pop'), (5.0, 'whoosh'), (14.0, 'pop')]


def test_music_is_chosen_from_the_folder_and_stays_stable(tmp_path):
    folder = tmp_path / 'music'
    assert sound.music_file('auto', 'job-1', folder) is None
    folder.mkdir()
    for name in ('a.mp3', 'b.wav', 'notas.txt'):
        (folder / name).write_bytes(b'x')
    first = sound.music_file('auto', 'job-1', folder)
    assert first.suffix in ('.mp3', '.wav')
    assert sound.music_file('auto', 'job-1', folder) == first
    assert sound.music_file('none', 'job-1', folder) is None


def test_plans_without_sound_are_left_untouched(tmp_path):
    plan = _plan(tmp_path, dict(overlays=[_overlay(1)]))
    assert sound.mix(tmp_path / 'in.mp4', tmp_path / 'out.mp4', plan, 3, tmp_path) is None


@pytest.mark.slow
def test_mix_adds_effects_and_ducked_music_without_touching_video(tmp_path):
    video = tmp_path / 'in.mp4'
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=4',
        '-f', 'lavfi', '-i', 'sine=frequency=220:duration=4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(video),
    ], check=True)
    music = tmp_path / 'music'
    music.mkdir()
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', str(music / 'faixa.wav'),
    ], check=True)
    plan = _plan(tmp_path, dict(overlays=[_overlay(1, mode='badge'), _overlay(2.5, sound_name='boom')],
                                sound=dict(effects=True, music='auto')))
    out = tmp_path / 'out.mp4'
    result = sound.mix(video, out, plan, 4.0, tmp_path / 'work', seed='j', music_dir=music)
    assert result['effects'] == 2 and result['music'].endswith('faixa.wav')
    probe = json.loads(media.run([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(out),
    ]))['streams']
    kinds = {stream['codec_type']: stream for stream in probe}
    assert kinds['video']['codec_name'] == 'h264' and kinds['audio']['codec_name'] == 'aac'
    assert float(kinds['audio']['duration']) == pytest.approx(4.0, abs=0.1)
    # The synthesized effects were generated once and reused.
    assert sorted(p.name for p in (tmp_path / 'work' / 'sfx').iterdir()) == ['boom.wav', 'pop.wav']
