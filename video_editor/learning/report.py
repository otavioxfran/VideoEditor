"""Read-only edit quality metrics: how natural the current edit of a job sounds.

Nothing here writes job state. Numbers are heuristics for comparing engine versions,
not a substitute for listening to the export.
"""
import json
from difflib import SequenceMatcher
from statistics import median

from video_editor import media
from video_editor.cuts import tokens
from video_editor.editing import load_edit
from video_editor.timeline import build_timeline

LONG_PAUSE = 1.0
SHORT_KEEP = 1.5
RETAKE_WINDOW = 90
UNIFORM_TOLERANCE = .03


def _percentiles(values, points=(5, 50, 75, 95)):
    if not values:
        return {}
    ordered = sorted(values)
    return {f'p{p}': round(ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))], 3) for p in points}


def _removed(cuts, start, end):
    return sum(max(0, min(end, b) - max(start, a)) for a, b in cuts)


def pauses(words, cuts):
    """Original vs. residual length of every long pause between timed words."""
    result = []
    for before, after in zip(words, words[1:]):
        gap = after['start'] - before['end']
        if gap >= LONG_PAUSE:
            residual = gap - _removed(cuts, before['end'], after['start'])
            result.append(dict(original=gap, residual=residual,
                               sentence_end=before['text'].rstrip().endswith(('.', '?', '!', '...', '…'))))
    residuals = [p['residual'] for p in result]
    # Share of residual pauses equal (within tolerance) to the most common one.
    uniform = 0
    if residuals:
        anchor = median(residuals)
        uniform = sum(abs(r - anchor) <= UNIFORM_TOLERANCE for r in residuals) / len(residuals)
    sentence = [p for p in result if p['sentence_end']]
    return dict(count=len(result), original=_percentiles([p['original'] for p in result]),
                residual=_percentiles(residuals), uniform_share=round(uniform, 3),
                sentence_final=len(sentence),
                sentence_final_intact=sum(abs(p['original'] - p['residual']) < .01 for p in sentence))


def remaining_retakes(segments, cuts):
    """Kept sentence pairs that restart with the same words: likely repeated takes."""
    def kept(segment):
        length = segment['end'] - segment['start']
        return length > 0 and _removed(cuts, segment['start'], segment['end']) / length < .5

    found = []
    for index, first in enumerate(segments):
        a = tokens(first['text'])
        if len(a) < 4 or not kept(first):
            continue
        for second in segments[index + 1:index + 6]:
            if second['start'] - first['end'] > RETAKE_WINDOW:
                break
            b = tokens(second['text'])
            if len(b) < 4 or not kept(second):
                continue
            match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
            if match.size >= 4 and match.a <= 2 and match.b <= 2:
                found.append(dict(first_id=first['id'], second_id=second['id'],
                                  start=round(first['start'], 2),
                                  gap=round(second['start'] - first['end'], 2),
                                  text=' '.join(a[:12])))
                break
    return found


def _bitrate(path):
    try:
        value = json.loads(media.run(['ffprobe', '-v', 'error', '-show_entries', 'format=bit_rate',
                                      '-of', 'json', str(path)]))['format'].get('bit_rate')
        return int(value) if value else None
    except (OSError, RuntimeError, ValueError, KeyError):
        return None


def build(jobs, job_id, exports=None, probe=True):
    job, edit, transcript = load_edit(jobs, job_id)
    duration, fps = job['media']['duration'], job['media'].get('fps')
    timeline = build_timeline(duration, edit['cuts'], fps)
    cuts = timeline['cuts']
    words = sorted((w for s in transcript['segments'] for w in s['words']),
                   key=lambda w: (w['start'], w['end']))
    suggestions = {s['id']: s for s in jobs.read(job_id, 'suggestions.json')}
    kinds, origins = {}, {}
    for cut in edit['cuts']:
        kind = cut.get('kind') or suggestions.get(cut.get('suggestion_id'), {}).get('kind') or 'manual'
        kinds[kind] = kinds.get(kind, 0) + 1
        origins[cut['origin']] = origins.get(cut['origin'], 0) + 1
    # Edit decisions (not merged timeline spans) so the count matches by_kind/by_origin.
    lengths = [c['end'] - c['start'] for c in edit['cuts']]
    keeps = [k['source_end'] - k['source_start'] for k in timeline['keeps']]
    removed = duration - timeline['duration']
    value = dict(
        job_id=job_id, name=job.get('name'), mode=job.get('editing_mode', 'standard'),
        revision=edit['revision'], source_seconds=round(duration, 3),
        output_seconds=round(timeline['duration'], 3), removed_seconds=round(removed, 3),
        removed_share=round(removed / duration, 4) if duration else 0,
        cuts=dict(count=len(edit['cuts']), per_minute=round(len(edit['cuts']) / (duration / 60), 2) if duration else 0,
                  by_kind=kinds, by_origin=origins,
                  median_seconds=round(median(lengths), 3) if lengths else 0,
                  max_seconds=round(max(lengths), 3) if lengths else 0),
        keeps=dict(count=len(keeps), shorter_than_1_5s=sum(k < SHORT_KEEP for k in keeps)),
        pauses=pauses(words, cuts),
        remaining_retakes=remaining_retakes(transcript['segments'], cuts),
        export=None)
    path = jobs.path(job_id, 'automatic_report.json')
    if path.exists():
        report = jobs.read(job_id, 'automatic_report.json')
        value['automatic'] = dict(provider=report.get('provider'), message=report.get('message'),
                                  report_revision=report.get('revision'))
    if exports is not None:
        done = [e for e in exports.list(job_id) if e.get('state') == 'complete']
        if done:
            latest = max(done, key=lambda e: e.get('completed_at') or '')
            output = jobs.path(job_id, f"exports/{latest['render_id']}/video.mp4")
            value['export'] = dict(render_id=latest['render_id'], status=latest.get('status'),
                                   revision=latest.get('revision'), codec=latest.get('actual_codec'),
                                   bitrate=_bitrate(output) if probe and output.exists() else None,
                                   source_bitrate=_bitrate(job['source']['path']) if probe else None)
    return value


def _minutes(seconds):
    return f'{seconds / 60:.1f} min'


def _mbps(bits):
    return f'{bits / 1e6:.1f} Mbps' if bits else '?'


def text(value):
    p, c, k = value['pauses'], value['cuts'], value['keeps']
    lines = [
        f"{value['name']}  ({value['job_id']}, revisão {value['revision']}, modo {value['mode']})",
        f"Duração: {_minutes(value['source_seconds'])} → {_minutes(value['output_seconds'])}"
        f"  (removido {value['removed_seconds']:.0f} s, {value['removed_share'] * 100:.0f}%)",
        f"Cortes: {c['count']}  ({c['per_minute']}/min; mediana {c['median_seconds']} s; maior {c['max_seconds']} s)",
        f"  por tipo: {c['by_kind'] or '-'}   por origem: {c['by_origin'] or '-'}",
        f"Trechos mantidos: {k['count']}  (com menos de {SHORT_KEEP} s: {k['shorter_than_1_5s']})",
        f"Pausas ≥ {LONG_PAUSE} s: {p['count']}",
        f"  original: {p['original'] or '-'}",
        f"  após o corte: {p['residual'] or '-'}",
        f"  pausas iguais entre si (±{int(UNIFORM_TOLERANCE * 1000)} ms): {p['uniform_share'] * 100:.0f}%",
        f"  fim de frase intactas: {p['sentence_final_intact']} de {p['sentence_final']}",
        f"Possíveis retakes ainda no vídeo: {len(value['remaining_retakes'])}",
    ]
    for item in value['remaining_retakes'][:10]:
        lines.append(f"  {item['start']:>7.1f} s  (+{item['gap']} s)  {item['text']}")
    if value.get('automatic'):
        lines.append(f"Automático: provider={value['automatic']['provider']}  {value['automatic']['message']}")
    export = value.get('export')
    if export:
        lines.append(f"Export {export['render_id'][:8]} ({export['status']}, rev {export['revision']}, "
                     f"{export['codec']}): {_mbps(export['bitrate'])}  | raw: {_mbps(export['source_bitrate'])}")
    else:
        lines.append('Export: nenhum concluído.')
    return '\n'.join(lines)
