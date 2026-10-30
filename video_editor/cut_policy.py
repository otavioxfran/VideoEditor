"""Only an isolated production command with reliable word bounds can be auto-cut.

Incomplete-prefix restarts deliberately remain suggestions until human audio validation.
"""
from video_editor.cuts import tokens, RESETS
from video_editor.timeline import build_timeline


def apply_policy(transcript, candidates, duration, fps=None):
    segments = {s['id']: s for s in transcript['segments']}
    for candidate in candidates:
        candidate['auto'] = False
        if candidate['kind'] != 'reset' or not candidate.get('isolated'):
            continue
        segment = segments[candidate['evidence'][0]]
        words = segment['words']
        # Contradictions between recognized sentence and timed words invalidate evidence.
        if ' '.join(tokens(segment['text'])) not in RESETS or tokens(' '.join(w['text'] for w in words)) != tokens(segment['text']):
            continue
        if not words or any(w['probability'] < .9 or w['end'] <= w['start'] for w in words):
            continue
        start, end = max(0, words[0]['start'] - .06), min(duration, words[-1]['end'] + .06)
        if fps:
            start, end = build_timeline(duration, [dict(start=start, end=end)], fps)['cuts'][0]
        # Padding must never touch neighboring words.
        foreign = [w for s in transcript['segments'] if s['id'] != segment['id'] for w in s['words']]
        if any(w['start'] < end and w['end'] > start for w in foreign):
            continue
        candidate.update(start=start, end=end, auto=True,
                         reason='Comando de produção isolado, com palavras confiáveis e limites protegidos.')
    return candidates
