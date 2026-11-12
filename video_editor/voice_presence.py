"""Speaker-embedding voice presence: flags speech that is not the creator."""
import hashlib
import urllib.request
import wave
from pathlib import Path

import numpy as np

from video_editor import storage
from video_editor.settings import DATA_DIR
from video_editor.timeline import digest

VERSION = 3
SAMPLE_RATE = 16000
WINDOW_TARGET = 2.8
WINDOW_MIN = .8
WINDOW_MAX = 3.0
SHORT_WINDOW = 1.5
SHORT_THRESHOLD = .30
THRESHOLD = .45
MIN_INTERVAL = 1.5
MERGE_GAP = .75
RMS_PERCENTILE = 75
MODEL_NAME = 'wespeaker_resnet34_lm.onnx'
MODEL_REVISION = '0ae88dcaf48cacdf741275d6d1a8101f45eee220'
MODEL_SHA256 = '7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068'
MODEL_URL = (
    'https://huggingface.co/hbredin/wespeaker-voxceleb-resnet34-LM/'
    'resolve/' + MODEL_REVISION + '/speaker-embedding.onnx'
)
MODEL_MIN_BYTES = 10 * 1024 * 1024


def model_path():
    return DATA_DIR / 'config' / 'models' / 'voice' / MODEL_NAME


def available():
    try:
        import kaldi_native_fbank  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _sha256(path):
    result = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _download_model(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=120) as response, open(
            temporary, 'wb'
        ) as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        if temporary.stat().st_size < MODEL_MIN_BYTES:
            temporary.unlink(missing_ok=True)
            return 'model_missing'
        if _sha256(temporary) != MODEL_SHA256:
            temporary.unlink(missing_ok=True)
            return 'model_hash'
        temporary.replace(path)
    except (OSError, urllib.error.URLError):
        temporary.unlink(missing_ok=True)
        return 'model_missing'
    return 'ok'


def resolve_model(path=None):
    path = Path(path) if path else model_path()
    if path.is_file():
        return (path, 'ok') if _sha256(path) == MODEL_SHA256 else (None, 'model_hash')
    if not available():
        return None, 'dependencies_missing'
    status = _download_model(path)
    return (path, 'ok') if status == 'ok' else (None, status)


def ensure_model(path=None):
    resolved, _ = resolve_model(path)
    return resolved


def _read_wav(path):
    with wave.open(str(path), 'rb') as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2')
    if rate != SAMPLE_RATE or width != 2 or channels != 1:
        raise ValueError('Áudio precisa ser mono 16 kHz PCM.')
    return audio.astype(np.float32) / 32768, rate


def _words(transcript):
    return sorted(
        (
            dict(start=float(word['start']), end=float(word['end']))
            for segment in transcript.get('segments', [])
            for word in segment.get('words', [])
        ),
        key=lambda word: (word['start'], word['end']),
    )


def windows_for(transcript, target=WINDOW_TARGET, low=WINDOW_MIN, high=WINDOW_MAX):
    values, current = [], []
    for word in _words(transcript):
        if current and word['end'] - current[0]['start'] > high:
            if current[-1]['end'] - current[0]['start'] >= low:
                values.append(current)
            current = []
        current.append(word)
        if word['end'] - current[0]['start'] >= target:
            values.append(current)
            current = []
    if current and current[-1]['end'] - current[0]['start'] >= low:
        values.append(current)
    return [
        (group[0]['start'], group[-1]['end'])
        for group in values
        if group[-1]['end'] - group[0]['start'] >= low
    ]


def _fbank(samples, rate):
    import kaldi_native_fbank as knf

    options = knf.FbankOptions()
    options.frame_opts.samp_freq = rate
    options.frame_opts.dither = 0
    options.frame_opts.window_type = 'povey'
    options.mel_opts.num_bins = 80
    options.energy_floor = 0
    extractor = knf.OnlineFbank(options)
    extractor.accept_waveform(rate, (samples * 32768).tolist())
    extractor.input_finished()
    features = np.asarray(
        [extractor.get_frame(index) for index in range(extractor.num_frames_ready)],
        dtype=np.float32,
    )
    return features - features.mean(axis=0, keepdims=True)


def _brightness(features):
    power = np.exp(features)
    weights = power.mean(axis=0)
    return float(np.average(np.arange(len(weights)), weights=weights + 1e-9))


def _extractor(path):
    import onnxruntime

    session = onnxruntime.InferenceSession(
        str(path), providers=['CPUExecutionProvider']
    )
    input_name = session.get_inputs()[0].name

    def extract(audio, rate, windows):
        embeddings, brightness, levels = [], [], []
        for start, end in windows:
            samples = audio[int(start * rate):int(end * rate)]
            levels.append(float(np.sqrt((samples ** 2).mean() + 1e-12)))
            features = _fbank(samples, rate)
            embeddings.append(
                session.run(None, {input_name: features[None, :, :]})[0][0]
            )
            brightness.append(_brightness(features))
        return (
            np.asarray(embeddings, dtype=np.float32),
            np.asarray(brightness, dtype=np.float32),
            np.asarray(levels, dtype=np.float32),
        )

    return extract


def _normalized(values):
    values = np.asarray(values, dtype=np.float32)
    return values / (np.linalg.norm(values, axis=-1, keepdims=True) + 1e-9)


def robust_centroid(values):
    values = _normalized(values)
    if not len(values):
        raise ValueError('Sem embeddings para a referência.')
    centroid = np.median(values, axis=0)
    centroid = centroid / (np.linalg.norm(centroid) + 1e-9)
    keep = values
    while len(keep) > max(5, int(len(values) * .6)):
        scores = keep @ centroid
        order = np.argsort(scores)
        drop = max(1, int(round(len(keep) * .2)))
        keep = keep[order[drop:]]
        centroid = keep.mean(axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-9)
    return centroid


def _face_share(windows, presence):
    samples = (presence or {}).get('samples') or []
    if (presence or {}).get('state') != 'ok' or not samples:
        return None
    faces = {round(float(sample['t']), 3): sample['faces'] for sample in samples}
    result = []
    for start, end in windows:
        keys = [key for key in faces if start - .5 <= key <= end + .5]
        result.append(
            sum(faces[key] > 0 for key in keys) / len(keys) if keys else 0.0
        )
    return np.asarray(result, dtype=np.float32)


def aggregate(windows, scores, threshold=THRESHOLD, short_threshold=SHORT_THRESHOLD, min_seconds=MIN_INTERVAL):
    runs, current = [], []
    for index, (start, end) in enumerate(windows):
        limit = short_threshold if end - start < SHORT_WINDOW else threshold
        if scores[index] < limit:
            if current and start - windows[current[-1]][1] > MERGE_GAP:
                runs.append(current)
                current = []
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    intervals = []
    for indices in runs:
        start = windows[indices[0]][0]
        end = max(windows[index][1] for index in indices)
        if end - start < min_seconds:
            continue
        intervals.append(
            dict(start=round(start, 3), end=round(end, 3), indices=indices)
        )
    return intervals


def _histogram(scores):
    edges = [-.2 + .1 * index for index in range(13)]
    result = []
    for left, right in zip(edges, edges[1:]):
        if right == edges[-1]:
            count = int(((scores >= left) & (scores <= right)).sum())
        else:
            count = int(((scores >= left) & (scores < right)).sum())
        result.append(dict(start=round(left, 1), end=round(right, 1), count=count))
    return result


def detect(windows, embeddings, levels, brightness, faces=None, threshold=THRESHOLD, short_threshold=SHORT_THRESHOLD):
    embeddings = _normalized(embeddings)
    levels = np.asarray(levels, dtype=np.float32)
    candidates = np.where(levels >= np.percentile(levels, RMS_PERCENTILE))[0]
    if faces is not None:
        trusted = [index for index in candidates if faces[index] >= .5]
        if len(trusted) >= 8:
            candidates = np.asarray(trusted)
    if not len(candidates):
        return dict(state='failed', reason='no_reference_windows')
    reference = robust_centroid(embeddings[candidates])
    scores = embeddings @ reference
    median_level = float(np.median(levels[candidates])) or 1e-9
    ratios = levels / median_level
    intervals = aggregate(windows, scores, threshold, short_threshold)
    for interval in intervals:
        indices = interval.pop('indices')
        interval['score'] = round(float(np.mean(scores[indices])), 3)
        interval['evidence'] = dict(
            windows=len(indices),
            mean_score=round(float(np.mean(scores[indices])), 3),
            min_score=round(float(np.min(scores[indices])), 3),
            rms_ratio=round(float(np.mean(ratios[indices])), 3),
            brightness=round(float(np.mean(brightness[indices])), 2),
            face_share=(
                round(float(np.mean(faces[indices])), 3)
                if faces is not None
                else None
            ),
        )
    samples = [
        dict(
            start=round(float(start), 3),
            end=round(float(end), 3),
            score=round(float(score), 3),
            rms_ratio=round(float(ratio), 3),
            brightness=round(float(bright), 2),
            face_share=(
                round(float(face), 3) if faces is not None else None
            ),
        )
        for (start, end), score, ratio, bright, face in zip(
            windows,
            scores,
            ratios,
            brightness,
            faces if faces is not None else [None] * len(windows),
        )
    ]
    return dict(
        state='ok',
        version=VERSION,
        threshold=threshold,
        reference_windows=len(candidates),
        windows=len(windows),
        median_rms=round(median_level, 5),
        scores=samples,
        intervals=intervals,
        histogram=_histogram(scores),
    )


def _cache_key(source, duration, threshold, transcript):
    timing = digest(
        [
            [round(word['start'], 3), round(word['end'], 3)]
            for word in _words(transcript)
        ]
    )
    return dict(
        version=VERSION,
        threshold=threshold,
        short_threshold=SHORT_THRESHOLD,
        duration=round(float(duration), 3),
        source=digest(source),
        timing=timing,
        model=MODEL_NAME,
        window=[WINDOW_TARGET, WINDOW_MIN, WINDOW_MAX],
    )


def analyze(
    audio_path,
    transcript,
    duration,
    source,
    presence=None,
    cache_path=None,
    extractor=None,
    threshold=THRESHOLD,
):
    key = _cache_key(source, duration, threshold, transcript)
    if cache_path is not None:
        path = Path(cache_path)
        if path.is_file():
            try:
                value = storage.read(path)
            except (ValueError, OSError):
                value = {}
            if value.get('key') == key:
                return value
    if extractor is None:
        if not available():
            return dict(state='unavailable', reason='dependencies_missing')
        path, reason = resolve_model()
        if path is None:
            return dict(state='unavailable', reason=reason)
        extractor = _extractor(path)
    try:
        audio, rate = _read_wav(audio_path)
    except (OSError, ValueError, wave.Error) as error:
        return dict(state='failed', reason=str(error)[:300])
    windows = windows_for(transcript)
    if not windows:
        return dict(state='ok', version=VERSION, threshold=threshold, windows=0,
                    reference_windows=0, intervals=[], scores=[], histogram=[])
    try:
        embeddings, brightness, levels = extractor(audio, rate, windows)
    except (OSError, RuntimeError, ValueError) as error:
        return dict(state='failed', reason=str(error)[:300])
    faces = _face_share(windows, presence)
    value = detect(windows, embeddings, levels, brightness, faces, threshold)
    if value.get('state') == 'ok':
        value['key'] = key
        value['duration'] = round(float(duration), 3)
        if cache_path is not None:
            try:
                path = Path(cache_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                storage.write(path, value)
            except OSError:
                pass
    return value


def text(value):
    if value.get('state') != 'ok':
        return f"Detector indisponível: {value.get('state')} ({value.get('reason', '')})"
    lines = [
        f"Estado: ok; janelas: {value['windows']}; referência: {value['reference_windows']}; "
        f"limiar: {value['threshold']}",
        'Trechos suspeitos (tempo na origem):',
    ]
    if not value['intervals']:
        lines.append('  (nenhum)')
    for interval in value['intervals']:
        evidence = interval['evidence']
        lines.append(
            f"  {interval['start']:.1f}-{interval['end']:.1f}s "
            f"similaridade média {interval['score']:.3f}; "
            f"rms relativo {evidence['rms_ratio']:.2f}; "
            f"janelas {evidence['windows']}"
        )
    lines.append('Histograma de similaridade:')
    for item in value['histogram']:
        if item['count']:
            lines.append(
                f"  {item['start']:+.1f}..{item['end']:+.1f}: "
                f"{'#' * min(60, item['count'])} {item['count']}"
            )
    return '\n'.join(lines)
