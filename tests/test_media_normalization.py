import math
import shutil
import struct
import subprocess
import wave
import pytest
from video_editor import media
from video_editor.export.renderer import DEFAULT_CONFIG, render
from video_editor.pipeline import Pipeline
from video_editor.models import PrepareConfig
from video_editor.timeline import build_timeline
from video_editor.transcription import normalize

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg requerido")


@pytest.fixture
def timed_source(jobs, request):
    kind = getattr(request, "param", "offset")
    audio = jobs.source_dir / "pulses.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(28000 * math.sin(2 * math.pi * 700 * i / 16000)))
                if any(
                    p <= i / 16000 < p + 0.12
                    for p in ((0.5, 2.5, 5, 5.8) if kind == "offset" else (0.5, 2.5, 5))
                )
                else b"\x00\x00"
                for i in range(6 * 16000)
            )
        )
    source = jobs.source_dir / ("offset.mov" if kind == "offset" else "variable.mkv")
    expression = "between(t,.5,.62)+between(t,2.5,2.62)+between(t,5,5.12)"
    if kind == "offset":
        expression += "+between(t,5.8,5.92)"
    flashes = f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{expression}'"
    if kind == "variable":
        flashes += ",select='not(mod(n,2))+between(t,2,3)'"
    args = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=160x90:r=30:d=6",
    ]
    if kind == "offset":
        args += ["-itsoffset", "0.25"]
    args += [
        "-i",
        str(audio),
        "-vf",
        flashes,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "pcm_s16le",
        "-fps_mode",
        "vfr",
        "-t",
        "6.25",
    ]
    if kind == "offset":
        args += ["-output_ts_offset", "7"]
    media.run([*args, str(source)])
    return source


def signals(path):
    frames = subprocess.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=30",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    on = [
        sum(frames[i : i + 14400]) / 14400 > 200 for i in range(0, len(frames), 14400)
    ]
    flashes = [i / 30 for i, v in enumerate(on) if v and (i == 0 or not on[i - 1])]
    audio = subprocess.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
    blocks = [
        max(abs(v) for v in samples[i : i + 160]) > 8000
        for i in range(0, len(samples), 160)
    ]
    sounds = [
        i / 100 for i, v in enumerate(blocks) if v and (i == 0 or not blocks[i - 1])
    ]
    return flashes, sounds


@pytest.mark.parametrize(
    "timed_source,batches,codec",
    [
        ("offset", 24, "libx264"),
        ("offset", 1, "libx264"),
        ("variable", 1, "libx264"),
        ("offset", 1, "h264_nvenc"),
    ],
    indirect=["timed_source"],
)
def test_proxy_and_export_preserve_source_clock(
    jobs, timed_source, monkeypatch, batches, codec
):
    info = media.probe(timed_source)
    assert media.needs_proxy(info, timed_source)
    before = (timed_source.read_bytes(), timed_source.stat().st_mtime_ns)
    job = jobs.create(timed_source, PrepareConfig(device="cpu").model_dump())
    jid = job["job_id"]

    def asr(path, config, source):
        times = (
            [0.75, 2.75, 5.25, 6.05] if timed_source.suffix == ".mov" else [0.5, 2.5, 5]
        )
        return normalize(
            [
                dict(
                    start=p,
                    end=p + 0.12,
                    text="Pulso",
                    words=[dict(word="Pulso", start=p, end=p + 0.12)],
                )
                for p in times
            ],
            source,
        ), config

    monkeypatch.setattr("video_editor.transcription.transcribe", asr)
    monkeypatch.setattr("video_editor.export.renderer.MAX_BATCH_KEEPS", batches)
    Pipeline(jobs).prepare(jid)
    prepared = jobs.info(jid)
    proxy = jobs.path(jid, prepared["preview"]["path"])
    pf, ps = signals(proxy)
    expected_count = 4 if timed_source.suffix == ".mov" else 3
    assert len(pf) == len(ps) == expected_count
    expected_offset = 0.25 if timed_source.suffix == ".mov" else 0
    assert [s - f for s, f in zip(ps, pf)] == pytest.approx(
        [expected_offset] * expected_count, abs=0.08
    )
    timeline = build_timeline(info["duration"], [dict(start=1, end=2)], info["fps"])
    snapshot = dict(
        source=job["source"],
        media=info,
        audio_source=prepared["render_audio"],
        timeline=timeline,
        config=dict(DEFAULT_CONFIG, video_codec=codec),
    )
    output = jobs.path(jid, "normalized-export.mp4")
    render(snapshot, output, jobs.directory(jid))
    ef, es = signals(output)
    assert len(ef) == len(es) == expected_count
    assert [s - f for s, f in zip(es, ef)] == pytest.approx(
        [expected_offset] * expected_count, abs=0.08
    )
    assert (timed_source.read_bytes(), timed_source.stat().st_mtime_ns) == before
    assert media.probe(output)["width"] == 160
