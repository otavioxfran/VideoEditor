import json
import subprocess

import pytest

from video_editor import media
from video_editor.finishing import audio, models
from video_editor.finishing import render as finishing_render


pytestmark = pytest.mark.slow

def _ffmpeg(*args):
    subprocess.run(
        [media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error', *args],
        check=True,
    )


def _speech(path, duration=5, target=-26):
    _ffmpeg(
        '-f', 'lavfi', '-i', f'sine=frequency=220:duration={duration}',
        '-af', f'loudnorm=I={target}:TP=-2:LRA=7',
        '-ar', '48000', '-ac', '2', str(path),
    )
    return path


def _noise(path, level):
    _ffmpeg(
        '-f', 'lavfi', '-i', 'anoisesrc=color=white:duration=2',
        '-af', f'volume={level}dB', '-ar', '48000', '-ac', '2', str(path),
    )
    return path


def _muted(path):
    _ffmpeg(
        '-f', 'lavfi', '-i', 'sine=frequency=220:duration=3',
        '-af', 'pan=stereo|c0=c0|c1=0*c0,volume=-12dB',
        '-ar', '48000', str(path),
    )
    return path


def _digital_silence(path):
    _ffmpeg(
        '-f', 'lavfi', '-i', 'sine=frequency=220:duration=2',
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo:d=2',
        '-filter_complex',
        '[0:a]volume=-5dB,pan=stereo|c0=c0|c1=c0[a];[a][1:a]concat=n=2:v=0:a=1[out]',
        '-map', '[out]', '-ar', '48000', str(path),
    )
    return path


def _video(path):
    _ffmpeg(
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=3',
        '-f', 'lavfi', '-i', 'sine=frequency=220:duration=3',
        '-filter_complex', '[1:a]loudnorm=I=-26:TP=-2:LRA=7[a]',
        '-map', '0:v', '-map', '[a]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-ar', '48000', '-shortest', str(path),
    )
    return path


def _streams(path):
    data = json.loads(media.run([
        'ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json',
        str(path),
    ]))
    return [
        {
            'type': stream['codec_type'],
            'codec': stream.get('codec_name'),
            'frames': stream.get('nb_read_frames'),
            'bitrate': stream.get('bit_rate'),
            'duration': stream.get('duration'),
        }
        for stream in data['streams']
    ]


def test_analyze_synthetic_speech(tmp_path):
    result = audio.analyze(_speech(tmp_path / 'speech.wav'))
    assert result['integrated_lufs'] == pytest.approx(-26, abs=1.5)
    assert result['true_peak_dbtp'] is not None
    assert result['true_peak_dbtp'] <= -1
    assert result['channels']['mono'] is True
    assert result['bands']['low_dbfs'] is not None
    assert 'LUFS' in result['report']
    assert 'low_loudness' in {item['code'] for item in result['diagnostics']}


def test_master_reaches_target_and_copies_video(tmp_path):
    source = _video(tmp_path / 'source.mp4')
    output = tmp_path / 'mastered.mp4'
    before_streams = _streams(source)
    result = audio.master(source, output)
    assert result['after']['integrated_lufs'] == pytest.approx(-14, abs=1)
    assert result['after']['true_peak_dbtp'] <= -1
    after_streams = _streams(output)
    before_video = next(item for item in before_streams if item['type'] == 'video')
    after_video = next(item for item in after_streams if item['type'] == 'video')
    assert after_video['codec'] == before_video['codec']
    assert after_video['bitrate'] == before_video['bitrate']
    assert after_video['frames'] == before_video['frames']
    assert result['after']['duration'] == pytest.approx(result['before']['duration'], abs=.05)
    assert any('loudnorm' in item['filter'] for item in result['filters'])


def test_master_duplicates_muted_channel(tmp_path):
    source = _muted(tmp_path / 'muted.wav')
    before = audio.analyze(source)
    assert before['channels']['quiet'] == [2]
    output = tmp_path / 'muted.m4a'
    result = audio.master(source, output)
    values = [item['rms_dbfs'] for item in result['after']['channels']['values']]
    assert all(value is not None for value in values)
    assert abs(values[0] - values[1]) < 1
    assert any(item['filter'].startswith('pan=') for item in result['filters'])


def test_denoise_is_automatic_for_high_noise_only(tmp_path):
    high = audio.analyze(_noise(tmp_path / 'high.wav', -45))
    low = audio.analyze(_noise(tmp_path / 'low.wav', -70))
    assert high['background_noise_dbfs'] > -55
    assert low['background_noise_dbfs'] < -55
    high_master = audio.master(high['path'], tmp_path / 'high.m4a')
    low_master = audio.master(low['path'], tmp_path / 'low.m4a')
    assert any('afftdn' in item['filter'] for item in high_master['filters'])
    assert not any('afftdn' in item['filter'] for item in low_master['filters'])


def test_digital_silence_is_not_counted_as_noise(tmp_path):
    result = audio.analyze(_digital_silence(tmp_path / 'digital.wav'))
    assert result['background_noise_dbfs'] is None
    assert 'noise' not in {item['code'] for item in result['diagnostics']}


def test_plan_audio_key_is_loaded(tmp_path):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({
        'audio': {'target_lufs': -15, 'true_peak': -1.5, 'lra': 6, 'denoise': 'off'}
    }), encoding='utf-8')
    plan = models.load_plan(path)
    assert plan.audio['target_lufs'] == -15


def test_render_masters_when_plan_has_audio(tmp_path):
    source = _video(tmp_path / 'source.mp4')
    result = finishing_render.render(
        source,
        models.Plan(audio={'target_lufs': -14, 'denoise': 'off'}),
        tmp_path / 'render.mp4',
        work_dir=tmp_path / 'parts',
        encoder='libx264',
    )
    assert result['audio'] is not None
    assert result['audio']['after']['integrated_lufs'] == pytest.approx(-14, abs=1)
