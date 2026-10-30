"""Recording-relative background evidence. Foreground sounds always stay review-only."""

import subprocess
import json
import wave
import numpy as np

from video_editor import media

RATE, WINDOW, HOP = 16000, 2048, 1024


def _features(audio):
    n = max(0, (len(audio) - WINDOW) // HOP + 1)
    levels, peaks, shapes, tones = [], [], [], []
    edges = np.unique(np.rint(np.geomspace(3, WINDOW // 2, 33)).astype(int))
    frequencies = np.fft.rfftfreq(WINDOW, 1 / RATE)
    for left in range(0, n, 512):
        indices = (
            np.arange(left, min(n, left + 512))[:, None] * HOP
            + np.arange(WINDOW)[None, :]
        )
        frames = audio[indices]
        levels.extend(
            20 * np.log10(np.maximum(np.sqrt(np.mean(frames**2, axis=1)), 1e-8))
        )
        peaks.extend(20 * np.log10(np.maximum(np.max(np.abs(frames), axis=1), 1e-8)))
        power = np.abs(np.fft.rfft(frames * np.hanning(WINDOW), axis=1)) ** 2
        bands = np.stack(
            [power[:, a:b].sum(axis=1) for a, b in zip(edges, edges[1:])], axis=1
        )
        shapes.extend(bands / np.maximum(bands.sum(axis=1, keepdims=True), 1e-20))
        high = power[:, frequencies >= 180]
        # A narrow foreground tone or musical harmonics are not room noise.
        top = np.sort(high, axis=1)[:, -9:].sum(axis=1)
        high_fraction = high.sum(axis=1) / np.maximum(
            power[:, frequencies >= 20].sum(axis=1), 1e-20
        )
        tones.extend(
            np.where(
                high_fraction >= 0.12, top / np.maximum(high.sum(axis=1), 1e-20), 0
            )
        )
    return dict(
        level=np.array(levels),
        peak=np.array(peaks),
        shape=np.array(shapes),
        tonal=np.array(tones),
        times=(np.arange(n) * HOP + WINDOW / 2) / RATE,
    )


def _channels(wav, native):
    if native:
        probe = subprocess.run(
            [
                media.binary("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels",
                "-of",
                "json",
                str(native),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode:
            raise ValueError("Checkpoint de áudio nativo indisponível.")
        count = int(json.loads(probe.stdout)["streams"][0]["channels"])
        result = subprocess.run(
            [
                media.binary("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(native),
                "-map",
                "0:a:0",
                "-ar",
                str(RATE),
                "-f",
                "f32le",
                "-",
            ],
            capture_output=True,
            timeout=180,
        )
        if result.returncode:
            raise ValueError("Não foi possível analisar os canais originais do áudio.")
        return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, count)
    with wave.open(str(wav), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (
            RATE,
            1,
            2,
        ):
            raise ValueError("Perfil de fundo requer PCM mono de 16 kHz.")
        return (
            np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(
                np.float32
            )
            / 32768
        )[:, None]


def analyze(transcript, wav, candidates, native=None):
    """Subdivide proposals around breaths/transients; never cut through a rejected window."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    def unavailable(reason):
        return [
            dict(
                c,
                reason="Perfil de fundo insuficiente; pausa preservada.",
                evidence=dict(c["evidence"], background_match=False),
            )
            for c in candidates
            if c["kind"] == "adaptive_silence"
        ], dict(state=reason)

    channels = _channels(wav, native)
    mono = channels.mean(axis=1)
    speech = get_speech_timestamps(
        mono,
        VadOptions(
            threshold=0.3,
            min_speech_duration_ms=0,
            min_silence_duration_ms=400,
            speech_pad_ms=220,
        ),
    )
    features = [_features(channels[:, index]) for index in range(channels.shape[1])]
    if not features or len(features[0]["times"]) < 10:
        return unavailable("insufficient_background")
    times = features[0]["times"]
    unspoken = np.ones(len(times), dtype=bool)
    margin = WINDOW / RATE / 2 + 0.22
    for segment in transcript["segments"]:
        for item in segment["words"] or [segment]:
            unspoken[
                (times >= item["start"] - margin) & (times <= item["end"] + margin)
            ] = False
    for item in speech:
        unspoken[
            (times >= item["start"] / RATE - WINDOW / RATE / 2)
            & (times <= item["end"] / RATE + WINDOW / RATE / 2)
        ] = False
    safe = unspoken.copy()
    rejection = np.full(len(times), "", dtype=object)
    references = []
    for feature in features:
        eligible = unspoken & (feature["tonal"] < 0.45)
        if eligible.sum() < 10:
            return unavailable("insufficient_background")
        floor = float(np.percentile(feature["level"][eligible], 25))
        quiet = eligible & (feature["level"] <= floor + 3)
        # Several local references accommodate AGC and different colored room noise.
        templates = []
        for second in range(0, int(times[-1]) + 1, 30):
            selected = quiet & (times >= second) & (times < second + 30)
            if selected.sum() >= 5:
                templates.append(np.median(feature["shape"][selected], axis=0))
        if not templates:
            templates = [np.median(feature["shape"][quiet], axis=0)]
        distance = np.min(
            np.stack(
                [
                    np.sum(np.abs(feature["shape"] - template), axis=1)
                    for template in templates
                ]
            ),
            axis=0,
        )
        reference_distance = distance[quiet]
        shape_limit = min(
            0.65, max(0.25, float(np.percentile(reference_distance, 95)) + 0.12)
        )
        crest = feature["peak"] - feature["level"]
        crest_limit = float(np.percentile(crest[quiet], 95)) + 4
        tonal = (feature["tonal"] >= 0.45) & (feature["level"] > -100)
        energy = feature["level"] > floor + 7
        transient = (crest > crest_limit) | (
            np.abs(np.diff(feature["level"], prepend=feature["level"][0])) > 8
        )
        mismatch = distance > shape_limit
        for mask, reason in [
            (mismatch, "som diferente do fundo"),
            (energy, "respiração ou som acima do fundo"),
            (transient, "transiente"),
            (tonal, "som tonal ou música"),
        ]:
            rejection[mask] = reason
        safe &= ~(tonal | energy | transient | mismatch)
        references.append(
            dict(
                floor_db=round(floor, 3),
                reference_frames=int(quiet.sum()),
                shape_limit=round(shape_limit, 3),
                crest_limit_db=round(crest_limit, 3),
            )
        )
    result = []
    for candidate in candidates:
        if candidate["kind"] != "adaptive_silence":
            continue
        # Existing evidence independently excludes native-channel speech and ASR words.
        evidence = candidate["evidence"]
        if not evidence.get("native_clear") or not evidence.get("vad_clear"):
            continue
        selected = np.flatnonzero(
            (times >= candidate["start"]) & (times <= candidate["end"])
        )
        passing = selected[safe[selected]]
        groups = (
            np.split(passing, np.flatnonzero(np.diff(passing) > 1) + 1)
            if len(passing)
            else []
        )
        emitted = False
        for group in groups:
            if not len(group):
                continue
            # Inset into verified windows; leave 180 ms around uncertain sound and breaths.
            left = max(candidate["start"], times[group[0]] + WINDOW / RATE / 2 + 0.18)
            right = min(candidate["end"], times[group[-1]] - WINDOW / RATE / 2 - 0.18)
            if right - left < 0.12:
                continue
            result.append(
                dict(
                    candidate,
                    id=candidate["id"] + f"-bg{len(result) + 1}",
                    start=float(left),
                    end=float(right),
                    reason="Pausa sem fala compatível com o fundo da gravação; emenda experimental.",
                    evidence=dict(
                        evidence,
                        background_match=True,
                        profile=references,
                        verified_window=dict(start=float(left), end=float(right)),
                        retained_sounds=sorted(
                            set(str(v) for v in rejection[selected] if v)
                        ),
                    ),
                )
            )
            emitted = True
        if not emitted:
            result.append(
                dict(
                    candidate,
                    reason="Som ou respiração sem margem segura; pausa preservada.",
                    evidence=dict(
                        evidence,
                        background_match=False,
                        rejected_sounds=sorted(
                            set(str(v) for v in rejection[selected] if v)
                        ),
                    ),
                )
            )
    return result, dict(
        state="measured",
        channels=len(features),
        references=references,
        speech_windows=len(speech),
        profiled_without_words=True,
    )
