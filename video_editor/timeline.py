"""The only temporal contract used by preview, video and subtitles (seconds)."""
import hashlib
import json
import math


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def interval(start, end, duration):
    start, end = float(start), float(end)
    if not all(math.isfinite(x) for x in (start, end, duration)):
        raise ValueError("Timestamps precisam ser finitos.")
    if not 0 <= start < end <= duration:
        raise ValueError("Intervalo fora dos limites da fonte.")
    return start, end


def build_timeline(duration, cuts=(), fps=None):
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duração inválida.")
    ordered = sorted(interval(c['start'], c['end'], duration) for c in cuts)
    # Both streams and the preview share frame boundaries. No cumulative per-cut rounding.
    if fps is not None:
        fps = float(fps)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('FPS inválido.')
        ordered = [(max(0, math.floor(a * fps + 1e-8) / fps),
                    min(duration, math.ceil(b * fps - 1e-8) / fps)) for a, b in ordered]
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    keeps, source, output = [], 0.0, 0.0
    for start, end in merged + [[duration, duration]]:
        if start > source:
            length = start - source
            keeps.append(dict(source_start=source, source_end=start,
                              output_start=output, output_end=output + length))
            output += length
        source = end
    return dict(source_duration=duration, duration=output, keeps=keeps, cuts=merged)


def source_to_output(timeline, time):
    time = min(max(float(time), 0), timeline['source_duration'])
    for keep in timeline['keeps']:
        if time < keep['source_start']:
            return keep['output_start']
        if time <= keep['source_end']:
            return keep['output_start'] + time - keep['source_start']
    return timeline['duration']


def output_to_source(timeline, time):
    time = min(max(float(time), 0), timeline['duration'])
    for keep in timeline['keeps']:
        if time < keep['output_end']:
            return keep['source_start'] + time - keep['output_start']
    return timeline['keeps'][-1]['source_end'] if timeline['keeps'] else 0


def project_interval(timeline, start, end):
    """Clip at each cut boundary; never collapse retained pauses."""
    for keep in timeline['keeps']:
        left, right = max(start, keep['source_start']), min(end, keep['source_end'])
        if right > left:
            yield (keep['output_start'] + left - keep['source_start'],
                   keep['output_start'] + right - keep['source_start'])


def export_identity(job, transcript, timeline, config):
    return digest(dict(contract=2, source=job['source'], audio=job.get('render_audio'),transcript=digest(transcript),
                       keeps=timeline['keeps'], config=config))
