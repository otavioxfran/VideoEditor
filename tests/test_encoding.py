import subprocess
from pathlib import Path
from types import SimpleNamespace

from video_editor import encoding, media
from video_editor.export.renderer import DEFAULT_CONFIG
from video_editor.finishing import models
from video_editor.finishing import render as finishing_render


def test_shared_encoder_args_have_quality_cap_and_cpu_pixel_format():
    info = dict(width=1920, height=1080, fps=30)
    nvenc = encoding.encoder_args('h264_nvenc', DEFAULT_CONFIG, info, cpu_pix_fmt=True)
    assert nvenc[nvenc.index('-cq') + 1] == '21'
    assert nvenc[nvenc.index('-maxrate') + 1] == '10000000'
    assert nvenc[nvenc.index('-bufsize') + 1] == '20000000'
    assert nvenc[nvenc.index('-pix_fmt') + 1] == 'yuv420p'
    x264 = encoding.encoder_args('libx264', DEFAULT_CONFIG, info)
    assert x264[x264.index('-crf') + 1] == '20'


def test_encoder_probe_is_cached(monkeypatch):
    encoding._CACHE.clear()
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(encoding.subprocess, 'run', run)
    assert encoding.encoder_available('h264_nvenc') is True
    assert encoding.encoder_available('h264_nvenc') is True
    assert len(calls) == 1


def _base(path):
    subprocess.run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-shortest', str(path),
    ], check=True)
    return path


def test_finishing_falls_back_to_x264_on_gpu_failure_without_redoing_first_piece(
    tmp_path, monkeypatch
):
    base = _base(tmp_path / 'base.mp4')
    plan = models.Plan(cards=[models.Card(1, 'CORREÇÃO', ('teste',), '', .5)])
    calls = []
    nvenc = 0

    def run(command):
        nonlocal nvenc
        calls.append(command)
        if 'h264_nvenc' in command:
            nvenc += 1
            if nvenc == 2:
                raise RuntimeError('CUDA driver failure')
        Path(command[-1]).write_bytes(b'x')

    monkeypatch.setattr(finishing_render, '_run', run)
    result = finishing_render.render(
        base, plan, tmp_path / 'out.mp4', work_dir=tmp_path / 'parts',
        encoder='h264_nvenc',
    )
    assert result['encoder'] == 'libx264'
    assert result['encoder_fallback'] is True
    assert nvenc == 2
    assert len([command for command in calls if 'h264_nvenc' in command]) == 2


def test_exact_timescale_makes_every_frame_a_whole_number_of_ticks():
    from fractions import Fraction

    from video_editor.encoding import exact_timescale

    assert exact_timescale(60) == 15360
    assert exact_timescale(30) == 7680
    assert exact_timescale(60000 / 1001) == 60000
    assert exact_timescale(30000 / 1001) == 30000
    for fps in (24, 25, 30, 50, 60, 24000 / 1001, 30000 / 1001, 60000 / 1001):
        rate = Fraction(fps).limit_denominator(1001)
        assert (exact_timescale(fps) / rate).denominator == 1
