import json
import re
from video_editor.timeline import digest, project_interval


def local_signals(transcript):
    signals = []
    for s in transcript['segments']:
        if re.search(r'\d|\b(?:mil|milhao|milhões|bilhões|por cento)\b',s['text'],re.I):
            signals.append(dict(segment_id=s['id'],text=s['text'],kind='numeric',status='NEEDS_VERIFICATION'))
        if re.search(r'[\w.+-]+@[\w.-]+\.\w+|\b\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}\b',s['text']):
            signals.append(dict(segment_id=s['id'],text=s['text'],kind='privacy',status='NEEDS_VERIFICATION'))
    return signals


def package(transcript, timeline, task='brief', brief=None, suggestions=()):
    removed = [s['id'] for s in transcript['segments']
               if not list(project_interval(timeline, s['start'], s['end']))]
    instruction = '''Analise apenas a transcrição fornecida. Não invente evidências nem verifique fatos pela memória.
Indique IDs existentes para cada afirmação ou sugestão. Não autorize cortes automaticamente.
Retorne SOMENTE JSON neste formato:
{"thesis":"tese atribuída ao vídeo", "summary":"resumo", "chapters":[{"title":"...","segment_ids":["s1"]}],
 "suggestions":[{"reason":"redundância/digressão/trecho fraco","segment_ids":["s1"]}],
 "claims":[{"id":"c1","text":"afirmação atribuída ao vídeo","segment_ids":["s1"],"quote":"trecho literal"}],
 "titles":["ideia de título"], "risks":[{"reason":"alegação sensível","segment_ids":["s1"]}]}
Inclua argumento, exemplos, números, datas, alegações sensíveis e orientação editorial.
Claims precisam de verificação humana. O briefing cobre o raw, inclusive trechos removidos.
Títulos devem usar somente o conteúdo mantido. Não apresente afirmações controversas como fatos confirmados.'''
    if task == 'titles':
        if brief is None:
            raise ValueError('Importe ou gere um briefing antes de solicitar títulos.')
        def usable(segment):
            projected=list(project_interval(timeline,segment['start'],segment['end']))
            if segment['words']:
                return bool(projected)
            return abs(sum(b-a for a,b in projected)-(segment['end']-segment['start']))<1e-6
        kept = [dict(id=s['id'], text=' '.join(w['text'] for w in s['words']
                           if list(project_interval(timeline,w['start'],w['end']))) if s['words'] else s['text'])
                for s in transcript['segments'] if usable(s)]
        return ('Gere ideias de títulos a partir deste briefing e APENAS do conteúdo mantido. '
                'Não apresente claims pendentes como fatos. Retorne JSON {"titles":["ideia"]}.\n' +
                json.dumps(dict(brief=brief, kept_content=kept),ensure_ascii=False))
    if task == 'retakes':
        candidates = [s for s in suggestions if s['kind'] in ('retake','reset') and not s['auto']][:20]
        ids = {i for s in candidates for i in s['evidence']}
        relevant = [s for s in transcript['segments'] if s['id'] in ids or any(w['id'] in ids for w in s['words'])]
        if not candidates:
            raise ValueError('Nenhum retake ambíguo para enviar.')
        return ('Explique ambiguidades; não autorize cortes. Retorne JSON '
                '{"retakes":[{"suggestion_id":"id existente","reason":"explicação"}]}.\n' +
                json.dumps(dict(candidates=candidates,segments=relevant),ensure_ascii=False))
    return instruction + '\nTarefa: ' + task + '\n' + json.dumps(dict(
        transcript_hash=digest(transcript), transcript=transcript, timeline=timeline,
        fully_removed_segment_ids=removed, local_signals=local_signals(transcript)), ensure_ascii=False, indent=2)
