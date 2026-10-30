"""Editorial suggestions for padding and digressions. Suggestions only: never automatic cuts.

The model points at sentences by segment id; boundaries always come from timed words,
so a suggestion can never cut through a word or invent a timestamp. Results are cached
per transcript, provider and prompt version: opening the panel never spends quota.
"""
from pathlib import Path

from video_editor import storage
from video_editor.cuts import tokens
from video_editor.editorial import llm_client
from video_editor.settings import PERSONA_PATH
from video_editor.timeline import digest

PROMPT_VERSION = 3
TYPES = {
    'padding': 'Enchimento',
    'digression': 'Digressão',
    'repeated_idea': 'Ideia repetida',
    'lecture': 'Modo aula',
    'backstage': 'Bastidor',
}
RECOMMENDATIONS = {
    'padding': 'Cortar a promessa ou a muleta e entrar direto no ponto.',
    'digression': 'Ouvir: se não sustenta o argumento nem é piada que funciona, cortar.',
    'repeated_idea': 'Manter a versão mais afiada da ideia e cortar a outra.',
    'lecture': 'Encurtar a explicação ao mínimo que o público precisa para seguir.',
    'backstage': 'Fala de gravação, não do vídeo: cortar.',
}
MAX_SEGMENTS = 40
MAX_SECONDS = 180
MAX_SHARE = .3          # suggestions never add up to more than this share of the video
MAX_ISSUES = 25
CHUNK_CHARS = 60000
PAD = .04
BEFORE_KEPT = .1


def load_persona() -> str:
    persona_path = PERSONA_PATH
    if persona_path.exists():
        try:
            return persona_path.read_text(encoding="utf-8")[:10000]
        except OSError:
            pass
    return ""


def prompt(segments, persona):
    outline = '\n'.join(f"[{s['id']}] {s['start']:.1f}–{s['end']:.1f}: {s['text']}" for s in segments)
    return (
        "Você é o editor do canal descrito abaixo. Leia a transcrição de uma gravação bruta e aponte "
        "trechos que deixam o vídeo prolixo. Você só SUGERE; o criador decide cada corte.\n\n"
        f"PERSONA DO CANAL:\n{persona}\n\n"
        "Tipos:\n"
        "- padding: promessa ou muleta que não entrega nada (\"coisa que ninguém te contou\", \"que você nunca viu\", "
        "rodeios antes do ponto). Seja exigente na abertura: o gancho precisa chegar rápido à tese.\n"
        "- digression: desvio que não sustenta o argumento.\n"
        "- repeated_idea: a mesma afirmação reformulada em frases próximas sem acrescentar nada "
        "(\"X não cresce há 40 anos\" e logo depois \"X está estagnado há 40 anos\"). Aponte a versão mais fraca.\n"
        "- lecture: explicação longa demais, tom de aula.\n"
        "- backstage: fala sobre a gravação, ferramentas ou reclamações fora do vídeo "
        "(\"a repetição tá lenta\", \"esse GPT tá burro\").\n\n"
        "Regras:\n"
        "- Humor, punchline, callback, exemplo concreto e dado que sustenta o argumento são CONTEÚDO. "
        "Só aponte se for claramente repetitivo, e diga o risco.\n"
        "- Prefira poucas sugestões certeiras. No máximo 12 neste trecho.\n"
        "- Use somente IDs que existem. first_id e last_id podem ser iguais.\n\n"
        "Responda SOMENTE com JSON:\n"
        '{"issues":[{"first_id":"s12","last_id":"s14","type":"padding|digression|repeated_idea|lecture|backstage",'
        '"title":"título curto","why":"por que pesa no vídeo",'
        '"risk":"o que o vídeo PERDE se este trecho sair (uma piada, um dado, um contexto); vazio se nada",'
        '"confidence":0.0}]}\n\n'
        f"TRANSCRIÇÃO:\n{outline}")


def _chunks(segments):
    chunk, size = [], 0
    for segment in segments:
        line = len(segment['text']) + 24
        if chunk and size + line > CHUNK_CHARS:
            yield chunk
            chunk, size = [], 0
        chunk.append(segment)
        size += line
    if chunk:
        yield chunk


def _bounds(segments, first, last, duration):
    """From just before the first removed word to just before the next kept sentence."""
    removed = [w for s in segments[first:last + 1] for w in s['words']]
    if not removed:
        return None
    previous_end = segments[first - 1]['end'] if first else 0
    start = max(previous_end, removed[0]['start'] - PAD)
    following = next((s for s in segments[last + 1:] if s['words']), None)
    if following:
        end = min(following['words'][0]['start'] - .02, max(removed[-1]['end'] + PAD,
                                                           following['words'][0]['start'] - BEFORE_KEPT))
    else:
        end = min(duration, removed[-1]['end'] + PAD)
    return (round(start, 3), round(end, 3)) if end > start else None


def validate(result, segments, duration, origin='gemini'):
    """Keep only well-formed, id-anchored suggestions; numbers and size limits add risk or drop."""
    if not isinstance(result, dict) or not isinstance(result.get('issues'), list):
        raise ValueError('Resposta editorial inválida.')
    index = {s['id']: i for i, s in enumerate(segments)}
    issues = []
    for value in result['issues'][:MAX_ISSUES * 2]:
        if not isinstance(value, dict) or value.get('type') not in TYPES:
            continue
        first, last = index.get(value.get('first_id')), index.get(value.get('last_id'))
        if first is None or last is None or last < first or last - first + 1 > MAX_SEGMENTS:
            continue
        bounds = _bounds(segments, first, last, duration)
        if bounds is None or bounds[1] - bounds[0] > MAX_SECONDS:
            continue
        try:
            confidence = min(1.0, max(0.0, float(value.get('confidence', .5))))
        except (TypeError, ValueError):
            confidence = .5
        text = ' '.join(s['text'] for s in segments[first:last + 1])
        risk = str(value.get('risk') or '')[:400]
        if any(any(c.isdigit() for c in t) for t in tokens(text)):
            risk = (risk + ' · ' if risk else '') + 'Contém números ou dados: confira se não sustentam o argumento.'
        issues.append(dict(
            id=f"prolix-{segments[first]['id']}-{segments[last]['id']}", type=value['type'],
            title=str(value.get('title') or TYPES[value['type']])[:120],
            start=bounds[0], end=bounds[1], saved_seconds=round(bounds[1] - bounds[0], 1),
            issue=str(value.get('why') or '')[:600], risk=risk, confidence=round(confidence, 2),
            recommendation=RECOMMENDATIONS[value['type']], preview_quote=segments[first]['text'][:80],
            segment_ids=[s['id'] for s in segments[first:last + 1]], origin=origin))
    # No overlaps (most confident wins) and never more than MAX_SHARE of the video.
    kept, total = [], 0
    for issue in sorted(issues, key=lambda i: -i['confidence']):
        if any(issue['start'] < k['end'] and issue['end'] > k['start'] for k in kept):
            continue
        if total + issue['saved_seconds'] > duration * MAX_SHARE or len(kept) >= MAX_ISSUES:
            continue
        kept.append(issue)
        total += issue['saved_seconds']
    return sorted(kept, key=lambda i: i['start'])


def local_issues(transcript):
    """Without a provider, only measurable repetition is reported."""
    from video_editor import takes
    segments = [s for s in transcript['segments'] if s['words']]
    index = {s['id']: i for i, s in enumerate(segments)}
    result = []
    for idea in takes.repeated_takes(transcript):
        if idea['kind'] != 'repeated_idea':
            continue
        removed = [i for i in idea['evidence'] if i not in idea['replacement_segment_ids']]
        if removed and removed[0] in index:
            result.append(dict(first_id=removed[0], last_id=removed[0], type='repeated_idea',
                               title='Ideia repetida', why=idea['reason'], risk='', confidence=.6))
    duration = segments[-1]['end'] if segments else 0
    return validate(dict(issues=result), segments, duration, origin='local') if result else []


def _cache_path(jobs, job_id, transcript, config_path):
    key = digest(dict(transcript=digest(transcript), version=PROMPT_VERSION,
                      provider=llm_client.cache_signature(config_path) if config_path else 'none'))
    return jobs.path(job_id, f'editorial/prolixity/{key}.json')


def cached(jobs, job_id, transcript, config_path):
    path = _cache_path(jobs, job_id, transcript, config_path)
    return storage.read(path) if path.exists() else None


def analyze(jobs, job_id, transcript, duration, config_path):
    """Explicit request: may send text to the configured free provider, then caches."""
    path = _cache_path(jobs, job_id, transcript, config_path)
    if path.exists():
        return storage.read(path)
    segments = [s for s in transcript['segments'] if s['words']]
    issues, state = [], 'complete'
    try:
        persona = load_persona()
        for chunk in _chunks(segments):
            issues += validate(llm_client.generate(Path(config_path), prompt(chunk, persona)), segments, duration)
    except (llm_client.ManualFallback, ValueError):
        issues, state = local_issues(transcript), 'local_only'
    value = dict(schema=1, version=PROMPT_VERSION, state=state, transcript_hash=digest(transcript),
                 issues=sorted(issues, key=lambda i: i['start']))
    storage.write(path, value)
    return value
