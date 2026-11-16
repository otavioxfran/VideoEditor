"""Local visual absence analysis for fixed-camera talking-head footage.

Face detection alone mistakes a side profile for an empty chair. A no-face sample
is marked absent only when it matches an empty background reference taken from a
long acoustic silence. Results are cached per source and job.
"""
import subprocess
from pathlib import Path
from statistics import median

import numpy as np

from video_editor import media, storage
from video_editor.settings import BASE_DIR
from video_editor.timeline import build_timeline, digest

VERSION = 2
SAMPLE_FPS = 1.0
WIDTH = 320
HEIGHT = 180
FRAME_BYTES = WIDTH * HEIGHT * 3
FACE_THRESHOLD = .7
EMPTY_MAD = 20.0
MIN_ABSENCE = 3.0
STATIC_MOTION = 4.0
NEIGHBOR_SECONDS = 2.0
MIN_SUGGESTION = .5
MAX_EXTEND = 2.0
PAD = .4
ROI_WIDTH = 2.5
ROI_HEIGHT = 3.0
ROI_DROP = .5
NO_FACE_MIN = 3.0
MODEL_PATH = BASE_DIR / "config" / "models" / "face_detection_yunet_2023mar.onnx"


def _frames(source, detector, cv2):
    command = [
        media.binary("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-vf",
        f"fps={SAMPLE_FPS},scale={WIDTH}:{HEIGHT}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "-",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    previous = None
    index = 0
    try:
        while True:
            raw = process.stdout.read(FRAME_BYTES)
            if len(raw) < FRAME_BYTES:
                break
            image = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, faces = detector.detect(image)
            count = 0 if faces is None else len(faces)
            score = 0.0 if not count else max(float(face[-1]) for face in faces)
            box = None
            if count:
                best_face = max(faces, key=lambda face: float(face[-1]))
                box = tuple(round(float(value), 1) for value in best_face[0:4])
            motion = (
                0.0
                if previous is None
                else float(np.mean(np.abs(gray.astype(np.float32) - previous)))
            )
            yield index / SAMPLE_FPS, count, score, motion, gray, box
            previous = gray.astype(np.float32)
            index += 1
    finally:
        if process.poll() is None:
            process.kill()
        stderr = process.stderr.read() if process.stderr else b""
        process.wait(timeout=30)
        if process.returncode:
            raise RuntimeError(
                (stderr.decode("utf-8", "replace") or "ffmpeg visual falhou.")[-1000:]
            )


def runs(samples, min_seconds=MIN_ABSENCE, fps=SAMPLE_FPS):
    result, current = [], []

    def flush(end):
        if not current:
            return
        start = current[0]["t"]
        seconds = end - start
        if seconds >= min_seconds:
            motions = [sample.get("roi_motion", 0.0) for sample in current]
            if median(motions) > STATIC_MOTION:
                return
            values = [sample["mad"] for sample in current]
            result.append(
                dict(
                    start=round(start, 3),
                    end=round(end, 3),
                    seconds=round(seconds, 3),
                    samples=len(current),
                    mad=round(float(median(values)), 2),
                )
            )

    for sample in samples:
        if sample.get("empty"):
            current.append(sample)
        elif current:
            flush(sample["t"])
            current = []
    if current:
        flush(samples[-1]["t"] + 1 / fps)
    return result


def no_face_runs(samples, min_seconds=NO_FACE_MIN, fps=SAMPLE_FPS):
    result, current = [], []

    def flush(end):
        if not current:
            return
        start = current[0]["t"]
        seconds = end - start
        if seconds >= min_seconds:
            result.append(
                dict(start=round(start, 3), end=round(end, 3), seconds=round(seconds, 3))
            )

    for sample in samples:
        if sample.get("faces"):
            if current:
                flush(sample["t"])
                current = []
        else:
            current.append(sample)
    if current:
        flush(samples[-1]["t"] + 1 / fps)
    return result


def _face_roi(boxes):
    boxes = [box for box in boxes if box]
    if not boxes:
        return [0.0, 0.0, float(WIDTH), float(HEIGHT)]
    x = median(float(box[0]) for box in boxes)
    y = median(float(box[1]) for box in boxes)
    width = median(float(box[2]) for box in boxes)
    height = median(float(box[3]) for box in boxes)
    if width <= 0 or height <= 0:
        return [0.0, 0.0, float(WIDTH), float(HEIGHT)]
    center_x = x + width / 2
    center_y = y + height / 2 + ROI_DROP * height
    left = max(0.0, center_x - ROI_WIDTH * width / 2)
    top = max(0.0, center_y - ROI_HEIGHT * height / 2)
    right = min(float(WIDTH), center_x + ROI_WIDTH * width / 2)
    bottom = min(float(HEIGHT), center_y + ROI_HEIGHT * height / 2)
    return [round(left, 1), round(top, 1), round(right, 1), round(bottom, 1)]


def _roi_mad(gray, reference, roi):
    left, top, right, bottom = (int(round(value)) for value in roi)
    left = max(0, min(WIDTH, left))
    top = max(0, min(HEIGHT, top))
    right = max(left + 1, min(WIDTH, right))
    bottom = max(top + 1, min(HEIGHT, bottom))
    window = reference[top:bottom, left:right].astype(np.float32)
    sample = gray[top:bottom, left:right].astype(np.float32)
    return float(np.mean(np.abs(sample - window)))


def _cache(cache_path, value):
    if cache_path is None:
        return
    try:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        storage.write(path, value)
    except OSError:
        pass


def analyze(source, identity, duration, silences=(), cache_path=None):
    silences = list(silences)
    silences_hash = digest(silences)
    if cache_path is not None:
        path = Path(cache_path)
        if path.is_file():
            try:
                value = storage.read(path)
            except (ValueError, OSError):
                value = {}
            if (
                value.get("version") == VERSION
                and value.get("identity") == identity
                and value.get("silences_hash") == silences_hash
                and abs(float(value.get("duration", 0)) - duration) < 1e-6
            ):
                return value
    try:
        import cv2
    except ImportError:
        return dict(state="unavailable", reason="opencv_missing")
    if not MODEL_PATH.is_file():
        return dict(state="unavailable", reason="model_missing")
    detector = cv2.FaceDetectorYN_create(
        str(MODEL_PATH),
        "",
        (WIDTH, HEIGHT),
        score_threshold=FACE_THRESHOLD,
        nms_threshold=.3,
        top_k=5000,
    )
    try:
        samples = [
            dict(
                t=t,
                faces=faces,
                score=round(score, 3),
                motion=round(motion, 2),
                gray=gray,
                box=box,
            )
            for t, faces, score, motion, gray, box in _frames(source, detector, cv2)
        ]
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(state="failed", reason=str(error)[:300])
    if not samples:
        return dict(state="failed", reason="no_frames")

    long_silences = [
        silence
        for silence in silences
        if float(silence.get("end", 0)) - float(silence.get("start", 0)) >= 2.0
    ]
    value = _evaluate(samples, long_silences, duration, identity, silences_hash)
    _cache(cache_path, value)
    return value


def _evaluate(samples, long_silences, duration, identity, silences_hash):
    roi = _face_roi([sample.get("box") for sample in samples])
    previous = None
    for sample in samples:
        motion = (
            0.0
            if previous is None
            else _roi_mad(sample["gray"], previous["gray"].astype(np.float32), roi)
        )
        sample["roi_motion"] = round(motion, 2)
        previous = sample
    face_share = round(sum(1 for sample in samples if sample["faces"]) / len(samples), 3)
    no_face = no_face_runs(samples)
    best = None
    for sample in samples:
        if sample["faces"] or sample["roi_motion"] > STATIC_MOTION:
            continue
        neighbors = [
            other
            for other in samples
            if other is not sample
            and not other["faces"]
            and abs(other["t"] - sample["t"]) <= NEIGHBOR_SECONDS
            and other["roi_motion"] <= STATIC_MOTION
        ]
        if len(neighbors) < 2:
            continue
        for silence in long_silences:
            left, right = float(silence["start"]), float(silence["end"])
            if left <= sample["t"] <= right:
                score = (sample["roi_motion"], abs(sample["t"] - (left + right) / 2))
                if best is None or score < best[0]:
                    best = (score, sample)
                break
    if best is None:
        return dict(
            state="insufficient_reference",
            version=VERSION,
            identity=identity,
            silences_hash=silences_hash,
            duration=round(duration, 3),
            sample_fps=SAMPLE_FPS,
            reason="no_empty_reference",
            roi=roi,
            face_share=face_share,
            no_face_runs=no_face,
        )

    reference = best[1]["gray"].astype(np.float32)
    present = []
    for sample in samples:
        mad = _roi_mad(sample["gray"], reference, roi)
        sample["mad"] = round(mad, 2)
        if sample["faces"]:
            present.append(mad)
        sample["empty"] = bool(sample["faces"] == 0 and mad <= EMPTY_MAD)
    if len(present) < 3 or median(present) < EMPTY_MAD * 1.5:
        return dict(
            state="insufficient_separation",
            version=VERSION,
            identity=identity,
            silences_hash=silences_hash,
            duration=round(duration, 3),
            sample_fps=SAMPLE_FPS,
            reference_t=best[1]["t"],
            reason="empty_reference_too_similar_to_speaker",
            roi=roi,
            face_share=face_share,
            no_face_runs=no_face,
        )

    return dict(
        state="ok",
        version=VERSION,
        identity=identity,
        silences_hash=silences_hash,
        duration=round(duration, 3),
        sample_fps=SAMPLE_FPS,
        reference_t=best[1]["t"],
        roi=roi,
        absence_runs=runs(samples),
        samples=[{k: v for k, v in sample.items() if k != "gray"} for sample in samples],
    )


def candidates(jobs, job_id, job, edit, transcript):
    duration = job["media"]["duration"]
    words = sorted(
        (word for segment in transcript["segments"] for word in segment["words"]),
        key=lambda word: (word["start"], word["end"]),
    )
    gaps = [
        dict(start=before["end"], end=after["start"])
        for before, after in zip(words, words[1:])
        if after["start"] - before["end"] >= 2.0
    ]
    if words and words[0]["start"] >= 2.0:
        gaps.append(dict(start=0, end=words[0]["start"]))
    if words and duration - words[-1]["end"] >= 2.0:
        gaps.append(dict(start=words[-1]["end"], end=duration))
    try:
        activity = jobs.path(job_id, "audio/activity.json")
        references = (jobs.read(job_id, "audio/activity.json") if activity.exists() else []) + gaps
        presence = analyze(
            job["source"]["path"],
            digest(job["source"]),
            duration,
            references,
            jobs.path(job_id, "visual/presence.json"),
        )
    except Exception:
        return []
    if presence.get("state") != "ok":
        return []

    timeline = build_timeline(duration, edit["cuts"], job["media"].get("fps"))
    keeps = timeline["keeps"]
    result, seen = [], set()
    for run in presence.get("absence_runs", []):
        start = max(0.0, float(run["start"]) - PAD)
        end = min(duration, float(run["end"]) + PAD)
        for keep in keeps:
            left = max(start, keep["source_start"])
            right = min(end, keep["source_end"])
            if right - left < MIN_SUGGESTION:
                continue
            inside = [
                word for word in words if word["end"] > left and word["start"] < right
            ]
            if inside:
                cut_start = max(keep["source_start"], min(word["start"] for word in inside) - .06)
                cut_end = min(keep["source_end"], max(word["end"] for word in inside) + .06)
            else:
                cut_start, cut_end = left, right
            before = [
                word
                for word in words
                if word["end"] > keep["source_start"] and word["start"] < cut_start
            ]
            after = [
                word
                for word in words
                if word["start"] < keep["source_end"] and word["end"] > cut_end
            ]
            if not before and cut_start - keep["source_start"] <= MAX_EXTEND:
                cut_start = keep["source_start"]
            if not after and keep["source_end"] - cut_end <= MAX_EXTEND:
                cut_end = keep["source_end"]
            if cut_end - cut_start < MIN_SUGGESTION:
                continue
            key = (round(cut_start, 2), round(cut_end, 2))
            if key in seen:
                continue
            seen.add(key)
            removed = [
                word
                for word in words
                if word["end"] > cut_start and word["start"] < cut_end
            ]
            reason = (
                f"Sem rosto por {run['seconds']:.1f}s (provável cadeira vazia); "
                "fala preservada para revisão."
                if removed
                else f"Sem rosto por {run['seconds']:.1f}s; trecho vazio preservado para revisão."
            )
            result.append(
                dict(
                    id=f"visual-{len(result) + 1}",
                    kind="visual_absence",
                    start=round(cut_start, 3),
                    end=round(cut_end, 3),
                    auto=False,
                    removed_word_ids=[word["id"] for word in removed],
                    evidence=dict(
                        visual=dict(
                            reference_t=presence.get("reference_t"),
                            absence_seconds=run["seconds"],
                            mad=run["mad"],
                        ),
                        source_span=[run["start"], run["end"]],
                        kept_span=[keep["source_start"], keep["source_end"]],
                        speech=[word["text"] for word in removed][:20],
                    ),
                    reason=reason,
                )
            )
    return sorted(result, key=lambda item: item["start"])
