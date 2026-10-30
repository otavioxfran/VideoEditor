"""Find candidates only. Similarity never authorizes a destructive operation."""
import re
import unicodedata
from difflib import SequenceMatcher

RESETS = (
    'corta essa parte', 'vou repetir', 'deixa eu repetir', 'vamos de novo',
    'corta isso', 'vou de novo', 'deixa eu recomecar', 'deixa eu voltar',
    'vamos recomecar', 'corta ai', 'corta aqui', 'recomecando'
)


def tokens(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    return re.findall(r'\w+', text)


def find_candidates(transcript):
    segments = transcript['segments']
    result = []
    for index, segment in enumerate(segments):
        normalized = ' '.join(tokens(segment['text']))
        if any(reset in normalized for reset in RESETS):
            isolated = normalized in RESETS
            result.append(dict(id='reset-' + segment['id'], start=segment['start'], end=segment['end'],
                               kind='reset', auto=False, isolated=isolated,
                               reason='Marcador de produção; revisar contexto.', evidence=[segment['id']]))
        # Intra-segment repeats are shown with word windows and kept by default.
        words = segment['words']
        normalized_words = [' '.join(tokens(w['text'])) for w in words]
        i = 0
        while i < len(words) - 3:
            matched = False
            for width in range(min(9, (len(words) - i) // 2), 1, -1):
                prefix = normalized_words[i:i + width]
                for j in range(i + width, min(len(words) - width + 1, i + width + 8)):
                    if prefix == normalized_words[j:j + width] and all(prefix):
                        result.append(dict(id=f'intra-{segment["id"]}-{i}-{j}', start=words[i]['start'],
                                           end=words[j - 1]['end'], kind='retake', auto=False,
                                           reason='Repetição dentro da fala; pode ser ênfase ou reinício.',
                                           removed_word_ids=[w['id'] for w in words[i:j]],
                                           replacement_word_ids=[w['id'] for w in words[j:j+width]],
                                           evidence=[w['id'] for w in words[i:j + width]]))
                        matched = True
                        i = j
                        break
                if matched:
                    break
            if not matched:
                i += 1
        if index + 1 < len(segments):
            other = segments[index + 1]
            a, b = tokens(segment['text']), tokens(other['text'])
            if a and b and other['start'] - segment['end'] <= 5 and SequenceMatcher(None, a, b).ratio() >= .55:
                result.append(dict(id='retake-' + segment['id'], start=segment['start'], end=segment['end'],
                                   kind='retake', auto=False, reason='Falas semelhantes; preservar até revisão humana.',
                                   evidence=[segment['id'], other['id']]))
            elif a and b and 1 <= len(a) <= 5 and other['start']-segment['end'] <= 5 and (
                    segment['text'].rstrip().endswith(('...', '…', '-')) or
                    normalized.startswith(('nao pera','pera deixa','deixa eu recomecar','pera ai','espera ai'))):
                result.append(dict(id='false-start-'+segment['id'],start=segment['start'],end=segment['end'],
                    kind='false_start',auto=False,reason='Tentativa possivelmente abandonada; preserve até avaliar a substituição.',
                    evidence=[segment['id'],other['id']]))
            if b and ' '.join(b).startswith(('quer dizer ', 'nao na verdade ', 'corrigindo ')):
                result.append(dict(id='correction-'+segment['id'],start=segment['start'],end=segment['end'],
                    kind='correction',auto=False,reason='Autocorreção: pode alterar números ou sentido; revisar a tomada correta.',
                    evidence=[segment['id'],other['id']]))
    # Nested matches describe the same restart; keep one complete proposal, without
    # joining distinct adjacent repetitions or expanding any authorized cut.
    retained=[]
    for candidate in sorted(result,key=lambda c: c['end']-c['start'],reverse=True):
        if candidate['id'].startswith('intra-') and any(
            r['id'].startswith('intra-') and candidate['start']>=r['start'] and candidate['end']<=r['end']
            for r in retained):
            continue
        retained.append(candidate)
    return sorted(retained,key=lambda c:(c['start'],c['end'],c['id']))
