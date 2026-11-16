"""Acoustic and visual context helpers."""
import subprocess
import wave

import numpy as np

from video_editor import media
from .bounds import safe_bounds

def audio_context(wav_path, start, end):
    """Small acoustic windows: preserve rising intonation and energetic boundaries."""
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()

        def samples(left, right):
            wav.setpos(min(wav.getnframes(), max(0, int(left * rate))))
            return (
                np.frombuffer(
                    wav.readframes(max(0, int((right - left) * rate))), dtype="<i2"
                ).astype(np.float32)
                / 32768
            )

        before = samples(max(0, start - 0.4), start)
        after = samples(end, end + 0.2)

    def pitch(values):
        if len(values) < 320 or float(np.sqrt(np.mean(values * values))) < 0.003:
            return None
        values = values - np.mean(values)
        corr = np.correlate(values[::4], values[::4], mode="full")[
            len(values[::4]) - 1 :
        ]
        low, high = int(rate / 4 / 350), min(int(rate / 4 / 70), len(corr))
        if high <= low or corr[0] <= 0:
            return None
        lag = low + int(np.argmax(corr[low:high]))
        return float(rate / 4 / lag) if corr[lag] / corr[0] > 0.5 else None

    mid = len(before) // 2
    p1, p2 = pitch(before[:mid]), pitch(before[mid:])
    return dict(
        rising_intonation=bool(p1 and p2 and p2 / p1 > 1.2),
        boundary_peak=float(
            max(
                np.max(np.abs(before)) if len(before) else 0,
                np.max(np.abs(after)) if len(after) else 0,
            )
        ),
    )


def _sample_frames(source, timestamp, origin, cache=None):
    """Sample 3 low-resolution frames at a specific timestamp, using an optional cache."""
    seek_time = max(0, timestamp + origin)
    cache_key = (str(source), round(seek_time, 4))
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    result = subprocess.run(
        [
            media.binary("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(seek_time),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "3",
            "-vf",
            "fps=25,scale=96:54",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode or not result.stdout or len(result.stdout) % (96 * 54 * 3):
        entry = (None, None)
    else:
        raw = result.stdout
        median = np.median(
            np.frombuffer(raw, dtype=np.uint8)
            .reshape(-1, 96 * 54 * 3)
            .astype(float),
            axis=0,
        )
        entry = (raw, median)

    if cache is not None:
        if len(cache) > 256:
            del cache[next(iter(cache))]
        cache[cache_key] = entry
    return entry


def visual_context(source, info, start, end, cache=None):
    """Compare short low-resolution sequences on both sides of the join."""
    origin = info.get("origin", 0) if info.get("timing_contract") == 2 else 0
    t_left = max(0, start - 0.12)
    t_right = min(info["duration"] - 0.04, end + 0.01)

    raw0, med0 = _sample_frames(source, t_left, origin, cache=cache)
    if raw0 is None:
        return dict(state="unavailable")

    raw1, med1 = _sample_frames(source, t_right, origin, cache=cache)
    if raw1 is None:
        return dict(state="unavailable")

    difference = float(np.mean(np.abs(med0 - med1)) / 255)
    return dict(
        state="measured",
        difference=difference,
        abrupt=difference > 0.35,
        sampled_frames=[len(raw0) // (96 * 54 * 3), len(raw1) // (96 * 54 * 3)],
    )


def _call_visual_context(source, info, start, end, cache=None):
    """Call the package-level visual_context so existing monkeypatches keep working."""
    from video_editor import automatic

    try:
        return automatic.visual_context(source, info, start, end, cache=cache)
    except TypeError:
        return automatic.visual_context(source, info, start, end)


def refine_visual(source, info, candidate, transcript, protected, cache=None):
    if cache is None:
        cache = {}
    original = _call_visual_context(source, info, candidate["start"], candidate["end"], cache=cache)
    if original.get("state") != "measured" or not original.get("abrupt"):
        return candidate, original
    best, evidence = candidate, original
    removed = {i for i in candidate.get("removed_word_ids", [])}
    target = [
        w for s in transcript["segments"] for w in s["words"] if w["id"] in removed
    ]
    frame = 1 / info["fps"]
    for left, right in ((frame, 0), (0, frame), (frame, frame), (2 * frame, 2 * frame)):
        trial = dict(
            candidate, start=candidate["start"] + left, end=candidate["end"] - right
        )
        if trial["start"] >= trial["end"] or any(
            w["start"] < trial["start"] or w["end"] > trial["end"] for w in target
        ):
            continue
        bounds = safe_bounds(
            trial, transcript, info["duration"], info["fps"], protected
        )
        if not bounds:
            continue
        trial.update(bounds)
        value = _call_visual_context(source, info, trial["start"], trial["end"], cache=cache)
        if (
            value.get("state") == "measured"
            and value["difference"] < evidence["difference"]
        ):
            best, evidence = trial, value
    evidence["boundary_refined"] = best != candidate
    return best, evidence
