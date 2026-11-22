"""FFmpeg-only voice analysis and mastering for the finishing pipeline."""
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np

from video_editor import media

DEFAULT_TARGET = {'target_lufs': -14.0, 'true_peak': -1.0, 'lra': 7.0}
SILENCE_THRESHOLD = -35
SILENCE_DURATION = .35
PAUSE_MARGIN = .05
NOISE_LIMIT = -55.0
DIGITAL_SILENCE = -90.0


def _ffmpeg(args):
    result = subprocess.run(
        [media.binary('ffmpeg'), '-nostdin', '-hide_banner', *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or 'ffmpeg falhou.')
    return result.stdout, result.stderr


def _probe(path):
    output = media.run([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json',
        str(path),
    ])
    data = json.loads(output)
    audio = next((stream for stream in data.get('streams', [])
                  if stream.get('codec_type') == 'audio'), None)
    if not audio:
        raise ValueError('O arquivo precisa ter uma faixa de áudio.')
    return {
        'duration': float(data.get('format', {}).get('duration') or audio.get('duration') or 0),
        'sample_rate': int(audio.get('sample_rate') or 48000),
        'channels': int(audio.get('channels') or 1),
        'codec': audio.get('codec_name'),
    }


def _number(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ('-inf', 'inf', 'nan'):
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _json_block(text):
    match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', text, re.S)
    return json.loads(match.group(0)) if match else {}


def _ebur128_summary(text):
    summary = text.rsplit('Summary:', 1)[-1]
    return {
        'input_i': (_metric(summary, 'I') or '').split()[0],
        'input_tp': (_metric(summary, 'Peak') or '').split()[0],
        'input_lra': (_metric(summary, 'LRA') or '').split()[0],
        'input_thresh': (_metric(summary, 'Threshold') or '').split()[0],
        'target_offset': '0',
    }


def _loudness(path):
    _, error = _ffmpeg([
        '-i', str(path), '-map', '0:a:0',
        '-af', 'loudnorm=I=-14:TP=-1:LRA=7:print_format=json',
        '-f', 'null', '-',
    ])
    data = _json_block(error)
    return {
        'integrated_lufs': _number(data.get('input_i')),
        'true_peak_dbtp': _number(data.get('input_tp')),
        'lra_lu': _number(data.get('input_lra')),
        'loudness_threshold': _number(data.get('input_thresh')),
        'loudnorm_offset': _number(data.get('target_offset')),
    }


def _astats_from_text(error):
    channels = []
    for match in re.finditer(r'Channel:\s*(\d+)(.*?)(?=Channel:\s*\d+|Overall)', error, re.S):
        block = match.group(2)
        channels.append({
            'channel': int(match.group(1)),
            'peak_dbfs': _number(_metric(block, 'Peak level dB')),
            'rms_dbfs': _number(_metric(block, 'RMS level dB')),
        })
    overall = error.split('Overall', 1)[1] if 'Overall' in error else error
    return {
        'channels': channels,
        'peak_dbfs': _number(_metric(overall, 'Peak level dB')),
        'rms_dbfs': _number(_metric(overall, 'RMS level dB')),
        'samples': int(_metric(overall, 'Number of samples') or 0),
    }


def _analyze_metrics(path, full=True, duration=0):
    bands = [
        ('low_dbfs', 'lowpass=f=120'),
        ('mid_dbfs', 'highpass=f=120,lowpass=f=4000'),
        ('high_dbfs', 'highpass=f=4000,lowpass=f=10000'),
        ('sibilance_dbfs', 'highpass=f=5000,lowpass=f=9000'),
    ] if full else []
    loudness_filter = (
        'loudnorm=I=-14:TP=-1:LRA=7:print_format=json'
        if duration and duration <= 10
        else 'ebur128=peak=true'
    )
    branches = [
        loudness_filter,
        'astats=metadata=0:reset=0',
    ]
    if full:
        branches.extend([
            f'silencedetect=noise={SILENCE_THRESHOLD}dB:d={SILENCE_DURATION}',
            *[extra + ',astats=metadata=0:reset=0' for _, extra in bands],
        ])
    count = len(branches)
    graph = [f'[0:a:0]asplit={count}' + ''.join(f'[a{i}]' for i in range(count))]
    for index, branch in enumerate(branches):
        graph.append(f'[a{index}]{branch}[o{index}]')
    args = ['-i', str(path), '-filter_complex', ';'.join(graph)]
    for index in range(count):
        args.extend(['-map', f'[o{index}]'])
    args.extend(['-f', 'null', '-'])
    _, error = _ffmpeg(args)
    loudness = _json_block(error)
    if not loudness:
        loudness = _ebur128_summary(error)
    stats_groups = {}
    for line in error.splitlines():
        match = re.match(r'\[Parsed_astats_(\d+) @ [^\]]+\](.*)', line)
        if not match:
            continue
        key = int(match.group(1))
        stats_groups[key] = stats_groups.get(key, '') + match.group(2) + '\n'
    stats_blocks = [stats_groups[key] for key in sorted(stats_groups)]
    overall = _astats_from_text(stats_blocks[0]) if stats_blocks else _astats_from_text(error)
    measured = {name: None for name, _ in bands}
    if full and len(stats_blocks) >= 1 + len(bands):
        for (name, _), block in zip(bands, stats_blocks[1:]):
            measured[name] = _astats_from_text(block)['rms_dbfs']
    starts = [float(value) for value in re.findall(r'silence_start:\s*([0-9.]+)', error)]
    ends = [float(value) for value in re.findall(r'silence_end:\s*([0-9.]+)', error)]
    return loudness, overall, starts, ends, measured


def _metric(block, name):
    match = re.search(rf'{re.escape(name)}:\s*([^\r\n]+)', block)
    return match.group(1).strip() if match else None


def _silence_intervals(path, duration):
    _, error = _ffmpeg([
        '-i', str(path), '-map', '0:a:0',
        '-af', f'silencedetect=noise={SILENCE_THRESHOLD}dB:d={SILENCE_DURATION}',
        '-f', 'null', '-',
    ])
    starts = [float(value) for value in re.findall(r'silence_start:\s*([0-9.]+)', error)]
    ends = [float(value) for value in re.findall(r'silence_end:\s*([0-9.]+)', error)]
    result = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else duration
        if end > start:
            result.append((start, end))
    return result


def _pcm_scan(path, pauses, sample_rate, channels):
    command = [
        media.binary('ffmpeg'), '-nostdin', '-v', 'error', '-i', str(path),
        '-map', '0:a:0', '-f', 'f32le', '-acodec', 'pcm_f32le', '-',
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sums = np.zeros(len(pauses), dtype=np.float64)
    counts = np.zeros(len(pauses), dtype=np.int64)
    clipped = 0
    frame_position = 0
    remainder = b''
    try:
        while True:
            chunk = process.stdout.read(1 << 20)
            if not chunk:
                break
            data = np.frombuffer(remainder + chunk, dtype='<f4')
            usable = data.size - data.size % channels
            if usable <= 0:
                remainder = remainder + chunk
                continue
            data = data[:usable]
            remainder = (remainder + chunk)[usable * 4:]
            frames = data.size // channels
            matrix = data.reshape(-1, channels)
            clipped += int(np.count_nonzero(np.abs(matrix) >= .999))
            block_end = frame_position + frames
            for index, (start, end) in enumerate(pauses):
                left = max(frame_position, round((start + PAUSE_MARGIN) * sample_rate))
                right = min(block_end, round((end - PAUSE_MARGIN) * sample_rate))
                if right > left:
                    part = matrix[left - frame_position:right - frame_position]
                    sums[index] += float(np.sum(part * part))
                    counts[index] += part.size
            frame_position = block_end
    finally:
        process.stdout.close()
        process.stderr.close()
        code = process.wait()
    if code:
        raise RuntimeError('Falha ao decodificar o áudio para medir pausas.')
    values = []
    for index, (start, end) in enumerate(pauses):
        if counts[index] == 0:
            continue
        rms = math.sqrt(sums[index] / counts[index])
        dbfs = 20 * math.log10(rms) if rms > 0 else None
        if dbfs is not None and dbfs > DIGITAL_SILENCE:
            values.append(dbfs)
    return {
        'clipped_samples': clipped,
        'background_noise_dbfs': float(np.median(values)) if values else None,
        'pause_count': len(values),
    }


def _diagnostics(result):
    items = []

    def add(code, message, action):
        items.append(dict(code=code, message=message, action=action))

    lufs = result['integrated_lufs']
    peak = result['true_peak_dbtp']
    lra = result['lra_lu']
    noise = result['background_noise_dbfs']
    channels = result['channels']
    bands = result['bands']
    if lufs is not None and lufs < -16:
        add('low_loudness', f'Áudio baixo para o YouTube ({lufs:.1f} LUFS; alvo -14).',
            'Aplicar masterização de voz.')
    if peak is not None and peak > -1:
        add('peak', f'Pico perto de clipar ({peak:.2f} dBTP).',
            'Reduzir picos com limiter.')
    if result['clipped_samples']:
        add('clipped', f'{result["clipped_samples"]} amostra(s) clipada(s).',
            'Aplicar compressor e limiter; conferir a origem.')
    if lra is not None and lra > 8:
        add('lra', f'Dinâmica alta para voz ({lra:.1f} LU).',
            'Comprimir a voz em direção a 5-8 LU.')
    if noise is not None and noise > NOISE_LIMIT:
        add('noise', f'Ruído de fundo nas pausas em {noise:.1f} dBFS.',
            'Aplicar redução de ruído conservadora com afftdn.')
    if bands['low_dbfs'] is not None and bands['high_dbfs'] is not None:
        if bands['low_dbfs'] > bands['high_dbfs'] + 3:
            add('rumble', 'Grave/ronco sobrando abaixo de 120 Hz.',
                'Aplicar passa-alta em 80 Hz.')
    if channels['quiet']:
        add('channel', 'Um canal está mudo ou mais de 10 dB abaixo do outro.',
            'Duplicar o canal ativo para os dois lados.')
    elif channels['mono']:
        add('mono', 'Áudio mono na prática (canais praticamente iguais).',
            'Pode manter os dois canais iguais; não há ganho de estéreo.')
    if bands['sibilance_dbfs'] is not None and bands['mid_dbfs'] is not None:
        if bands['sibilance_dbfs'] > bands['mid_dbfs'] - 15:
            add('sibilance', 'Sibilância alta em relação à banda média.',
                'Aplicar de-esser suave.')
    return items


def _format_band(value):
    return '—' if value is None else f'{value:.1f} dBFS'


def format_report(result):
    lines = [
        'Análise de áudio',
        f"- Loudness integrado: {_format_number(result['integrated_lufs'], 'LUFS')}",
        f"- True peak: {_format_number(result['true_peak_dbtp'], 'dBTP')}",
        f"- LRA: {_format_number(result['lra_lu'], 'LU')}",
        f"- Pico/RMS: {_format_number(result['peak_dbfs'], 'dBFS')} / "
        f"{_format_number(result['rms_dbfs'], 'dBFS')}",
        f"- Amostras clipadas: {'—' if result['clipped_samples'] is None else result['clipped_samples']}",
        f"- Ruído nas pausas: {_format_number(result['background_noise_dbfs'], 'dBFS')}",
        f"- Canais: {result['channels']['count']} "
        f"({'mono' if result['channels']['mono'] else 'estéreo'})",
        f"- Bandas: <120 Hz {_format_band(result['bands']['low_dbfs'])} | "
        f"120 Hz-4 kHz {_format_band(result['bands']['mid_dbfs'])} | "
        f"4-10 kHz {_format_band(result['bands']['high_dbfs'])} | "
        f"sibilância {_format_band(result['bands']['sibilance_dbfs'])}",
    ]
    if result['diagnostics']:
        lines.append('Diagnósticos:')
        lines.extend(f"- {item['message']} Ação: {item['action']}"
                     for item in result['diagnostics'])
    else:
        lines.append('Diagnósticos: nenhum problema relevante.')
    return '\n'.join(lines)


def report(result):
    return format_report(result)


def _format_number(value, unit):
    return '—' if value is None else f'{value:.1f} {unit}'


def analyze(path, full=True):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    info = _probe(path)
    loudness_data, stats, starts, ends, bands = _analyze_metrics(
        path, full=full, duration=info['duration']
    )
    loudness = {
        'integrated_lufs': _number(loudness_data.get('input_i')),
        'true_peak_dbtp': _number(loudness_data.get('input_tp')),
        'lra_lu': _number(loudness_data.get('input_lra')),
        'loudness_threshold': _number(loudness_data.get('input_thresh')),
        'loudnorm_offset': _number(loudness_data.get('target_offset')),
    }
    if full:
        pauses = []
        for index, start in enumerate(starts):
            end = ends[index] if index < len(ends) else info['duration']
            if end > start:
                pauses.append((start, end))
        scan = _pcm_scan(path, pauses, info['sample_rate'], info['channels'])
        bands = {
            name: bands.get(name) for name in (
                'low_dbfs', 'mid_dbfs', 'high_dbfs', 'sibilance_dbfs'
            )
        }
    else:
        scan = dict(clipped_samples=0, background_noise_dbfs=None, pause_count=0)
        bands = dict(low_dbfs=None, mid_dbfs=None, high_dbfs=None, sibilance_dbfs=None)
    channel_values = [item['rms_dbfs'] for item in stats['channels']]
    finite = [item for item in channel_values if item is not None]
    imbalance = (max(finite) - min(finite)) if finite and len(finite) == len(channel_values) else (
        120.0 if finite else None
    )
    quiet = []
    active = 0
    if finite:
        loudest = max(finite)
        quiet = [
            index + 1 for index, value in enumerate(channel_values)
            if value is None or value < loudest - 10
        ]
        active = max(range(len(channel_values)),
                     key=lambda index: channel_values[index]
                     if channel_values[index] is not None else -999) + 1
    mono = info['channels'] == 1
    if info['channels'] > 1 and len(finite) >= 2:
        mono = abs(finite[0] - finite[1]) < 1
    result = {
        'path': str(path),
        'duration': info['duration'],
        'sample_rate': info['sample_rate'],
        'channels': {
            'count': info['channels'],
            'mono': mono,
            'is_mono': mono,
            'quiet': quiet,
            'muted': quiet,
            'active': active,
            'imbalance_db': imbalance,
            'values': stats['channels'],
        },
        'integrated_lufs': loudness['integrated_lufs'],
        'lufs': loudness['integrated_lufs'],
        'true_peak_dbtp': loudness['true_peak_dbtp'],
        'true_peak': loudness['true_peak_dbtp'],
        'lra_lu': loudness['lra_lu'],
        'lra': loudness['lra_lu'],
        'loudness_threshold': loudness['loudness_threshold'],
        'loudnorm_offset': loudness['loudnorm_offset'],
        'peak_dbfs': stats['peak_dbfs'],
        'rms_dbfs': stats['rms_dbfs'],
        'samples': stats['samples'],
        'clipped_samples': scan['clipped_samples'],
        'background_noise_dbfs': scan['background_noise_dbfs'],
        'noise_floor_dbfs': scan['background_noise_dbfs'],
        'noise_floor': scan['background_noise_dbfs'],
        'pause_count': scan['pause_count'],
        'bands': dict(
            bands,
            low=bands['low_dbfs'], mid=bands['mid_dbfs'],
            high=bands['high_dbfs'], sibilance=bands['sibilance_dbfs'],
        ),
    }
    result['diagnostics'] = _diagnostics(result)
    result['report'] = format_report(result)
    return result


def _target(options):
    result = dict(DEFAULT_TARGET)
    options = options or {}
    for key, fallback in DEFAULT_TARGET.items():
        value = _number(options.get(key))
        if value is not None:
            result[key] = value
    return result


def master(input_path, output, profile='voz', options=None):
    input_path = Path(input_path)
    output = Path(output)
    options = dict(options or {})
    target = _target(options)
    info = _probe(input_path)
    before = analyze(input_path, full=info['duration'] <= 120)
    filters = []

    def add(name, reason):
        filters.append(dict(filter=name, reason=reason))

    channel = before['channels']
    if channel['quiet'] and channel['active']:
        source = channel['active'] - 1
        add(f'pan=stereo|c0=c{source}|c1=c{source}',
            'um canal mudo ou muito abaixo do outro')
    elif channel['count'] == 1:
        add('pan=stereo|c0=c0|c1=c0',
            'áudio mono; manter os dois canais idênticos')
    if profile == 'voz':
        add('highpass=f=80', 'remover grave/ronco abaixo de 80 Hz')

    mode = str(options.get('denoise', 'auto')).lower()
    noise = before['background_noise_dbfs']
    if mode == 'on' or (mode == 'auto' and noise is not None and noise > NOISE_LIMIT):
        nf = min(-20, max(-80, noise if noise is not None else -50))
        add(f'afftdn=nf={nf:.1f}:nr=12',
            'ruído de fundo acima de -55 dBFS; redução limitada a 12 dB')

    sibilance = before['bands']['sibilance_dbfs']
    mid = before['bands']['mid_dbfs']
    if sibilance is not None and mid is not None and sibilance > mid - 15:
        add('deesser=i=0.5', 'sibilância alta em relação à banda média')

    add('acompressor=threshold=0.0631:ratio=3:attack=10:release=150:makeup=2',
        'suavizar a dinâmica da voz')
    target_filter = (
        f"I={target['target_lufs']}:TP={target['true_peak']}:LRA={target['lra']}"
    )
    measure_chain = [item['filter'] for item in filters]
    _, measure_error = _ffmpeg([
        '-i', str(input_path), '-map', '0:a:0',
        '-af', ','.join([*measure_chain, 'ebur128=peak=true']),
        '-f', 'null', '-',
    ])
    measured = _ebur128_summary(measure_error)
    second = f'loudnorm={target_filter}'
    if info['duration'] > 120 and measured.get('input_i'):
        gain = target['target_lufs'] - float(measured['input_i'])
        if abs(gain) >= .05:
            add(f'volume={gain:.1f}dB', 'ajustar loudness sem segunda passagem')
    else:
        if measured.get('input_i'):
            second += (
                f":measured_I={measured.get('input_i', '')}"
                f":measured_TP={measured.get('input_tp', '')}"
                f":measured_LRA={measured.get('input_lra', '')}"
                f":measured_thresh={measured.get('input_thresh', '')}"
                f":offset={measured.get('target_offset', '')}:linear=true"
            )
        add(second, 'normalizar loudness para o alvo do YouTube')
    # -2 dBFS leaves room for inter-sample peaks and the AAC encoder's overshoot, so the file stays under -1 dBTP.
    add('alimiter=limit=0.794328:level=false',
        'teto de segurança abaixo de -1 dBTP, com folga para o AAC')
    # Measure the processed audio in the same pass that writes it: astats for levels and
    # ebur128 for loudness, true peak and LRA. The report must show measured values.
    chain = (','.join(item['filter'] for item in filters)
             + ',astats=metadata=0:reset=0,ebur128=peak=true')
    suffix = output.suffix.lower()
    codec = ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000']
    if suffix == '.wav':
        codec = ['-c:a', 'pcm_s16le', '-ar', '48000']
    args = ['-i', str(input_path), '-filter_complex', f'[0:a:0]{chain}[a]']
    if suffix in ('.mp4', '.mov', '.mkv'):
        args.extend(['-map', '0:v?', '-c:v', 'copy'])
    args.extend(['-map', '[a]', *codec])
    if before['duration'] > 0:
        # loudnorm and the limiter can leave a short tail; keep the input length.
        args.extend(['-t', f"{before['duration']:.6f}"])
    if suffix not in ('.wav',):
        args.extend(['-movflags', '+faststart'])
    _, error = _ffmpeg([*args, str(output)])
    stats_blocks = {}
    for line in error.splitlines():
        match = re.match(r'\[Parsed_astats_(\d+) @ [^\]]+\](.*)', line)
        if not match:
            continue
        key = int(match.group(1))
        stats_blocks[key] = stats_blocks.get(key, '') + match.group(2) + '\n'
    after_stats = _astats_from_text(
        next(iter(stats_blocks.values()), error)
    )
    loudness_after = _ebur128_summary(error)
    after = {
        **{key: before[key] for key in ('duration', 'sample_rate')},
        # Not re-measured after mastering (that would cost extra full passes); shown as "—"
        # instead of repeating the input's numbers as if they were the output's.
        'bands': dict(low_dbfs=None, mid_dbfs=None, high_dbfs=None, sibilance_dbfs=None,
                      low=None, mid=None, high=None, sibilance=None),
        'clipped_samples': None, 'background_noise_dbfs': None,
        'noise_floor_dbfs': None, 'noise_floor': None, 'pause_count': None,
        'path': str(output),
        'channels': {
            'count': before['channels']['count'],
            'mono': before['channels']['mono'],
            'is_mono': before['channels']['mono'],
            'quiet': before['channels']['quiet'],
            'muted': before['channels']['quiet'],
            'active': before['channels']['active'],
            'imbalance_db': before['channels']['imbalance_db'],
            'values': after_stats['channels'],
        },
        'integrated_lufs': _number(loudness_after.get('input_i')),
        'lufs': _number(loudness_after.get('input_i')),
        'true_peak_dbtp': _number(loudness_after.get('input_tp')),
        'true_peak': _number(loudness_after.get('input_tp')),
        'lra_lu': _number(loudness_after.get('input_lra')),
        'lra': _number(loudness_after.get('input_lra')),
        'loudness_threshold': before['loudness_threshold'],
        'loudnorm_offset': before['loudnorm_offset'],
        'peak_dbfs': after_stats['peak_dbfs'],
        'rms_dbfs': after_stats['rms_dbfs'],
        'samples': after_stats['samples'],
    }
    after['diagnostics'] = _diagnostics(after)
    after['report'] = format_report(after)
    return dict(
        before=before,
        after=after,
        filters=filters,
        applied=[item['filter'] for item in filters],
        output=str(output),
        before_report=before['report'],
        after_report=after['report'],
    )
