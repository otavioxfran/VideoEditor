import re
from video_editor.timeline import digest


def _normalized(text):
    return re.sub(r'\s+', ' ', text).strip().casefold()


def validate(result, transcript, origin):
    if not isinstance(result, dict):
        raise ValueError('Briefing precisa ser JSON estruturado.')
    allowed = {'thesis', 'summary', 'chapters', 'suggestions', 'claims', 'titles', 'risks'}
    if set(result) - allowed:
        raise ValueError('Campos desconhecidos no briefing.')
    for field in ('thesis', 'summary'):
        if not isinstance(result.get(field), str) or len(result[field]) > 20000:
            raise ValueError(f'Campo obrigatório inválido: {field}.')
    segments = {s['id']: s for s in transcript['segments']}
    for field in ('chapters', 'suggestions', 'claims', 'risks'):
        values = result.get(field, [])
        if not isinstance(values, list) or len(values) > 500:
            raise ValueError('Lista editorial inválida.')
        for item in values:
            if not isinstance(item, dict):
                raise ValueError('Item editorial inválido.')
            ids = item.get('segment_ids')
            if not isinstance(ids, list) or not ids or not all(isinstance(s, str) and s in segments for s in ids):
                raise ValueError('Referência não existe na transcrição.')
            if field == 'claims':
                if not isinstance(item.get('text'), str) or not isinstance(item.get('quote'), str) or not item['quote'].strip():
                    raise ValueError('Claim exige texto e trecho literal.')
                evidence = _normalized(' '.join(segments[s]['text'] for s in ids))
                if _normalized(item['quote']) not in evidence:
                    raise ValueError('Trecho literal não aparece nos segmentos citados.')
                numbers = re.findall(r'\d+(?:[.,]\d+)*', item['text'])
                if any(n not in re.findall(r'\d+(?:[.,]\d+)*', item['quote']) for n in numbers):
                    raise ValueError('Números do claim não aparecem na evidência literal.')
            key = 'title' if field == 'chapters' else 'reason' if field in ('suggestions', 'risks') else 'text'
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('Item editorial sem conteúdo.')
    titles = result.get('titles', [])
    if not isinstance(titles, list) or len(titles) > 30 or not all(isinstance(t, str) and 0 < len(t) <= 250 for t in titles):
        raise ValueError('Títulos inválidos.')
    # Drop claimed verification/provenance/confidence, never trust imported conclusions.
    clean = dict(thesis=result['thesis'], summary=result['summary'], titles=titles)
    for field in ('chapters', 'suggestions', 'risks'):
        key = 'title' if field == 'chapters' else 'reason'
        clean[field] = [{key: i[key], 'segment_ids': i['segment_ids']} for i in result.get(field, [])]
    clean['claims'] = [dict(id='c' + str(i + 1), text=c['text'], quote=c['quote'], segment_ids=c['segment_ids'],
                            status='NEEDS_VERIFICATION', evidence=[], human_confirmed=False)
                       for i, c in enumerate(result.get('claims', []))]
    return dict(schema=1, revision=0, origin=origin, transcript_hash=digest(transcript), **clean)
