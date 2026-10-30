"""Take selection: keep the last complete take, like an editor would.

Three levels, by how much proof the transcript gives:
- stumble (automatic): the same 3+ words said twice in a row; the first try goes.
- repeated_take (automatic): a later sentence restarts the same way, covers the earlier
  one's content and loses no number or negation; only silence or a restart cue sits
  between them. The earlier take goes.
- repeated_idea (suggestion only): the same idea said again with other content between,
  or with something lost. Never cut without a human decision.
- repeated_block (suggestion only): multi-sentence earlier attempts of the same opening.
  Each earlier block is reviewed against the last take, with the lost content listed.
"""
from difflib import SequenceMatcher

from video_editor.cuts import tokens

STOP = set(
    'a o as os um uma uns umas de do da dos das em no na nos nas por pra pro para com que e '
    'ou se ai mano cara tipo ne entao assim isso esse essa este esta aquele aquela ele ela eu '
    'voce gente ta ja mais muito ser foi era tem ter vai vou la aqui so tambem bem como quando '
    'seja quer dizer porque pois mas'.split())
NEGATIONS = {'nao', 'nunca', 'jamais', 'sem', 'nem'}
NUMBER_WORDS = set(
    'zero um uma dois duas tres quatro cinco seis sete oito nove dez onze doze treze quatorze '
    'quinze vinte trinta quarenta cinquenta sessenta setenta oitenta noventa cem cento mil '
    'milhao milhoes bilhao bilhoes metade dobro triplo'.split())
CUES = ('ou seja', 'quer dizer', 'na verdade', 'melhor', 'deixa eu', 'pera', 'nao pera', 'de novo',
        'vou repetir', 'deixa eu repetir', 'recomecando', 'corta', 'nao')
DANGLING = ('de', 'do', 'da', 'dos', 'das', 'com', 'para', 'pra', 'sobre', 'pelo', 'pela', 'que', 'o', 'a', 'e')

STUMBLE_MIN_WORDS = 3
STUMBLE_RUN_GAP = 4.0      # a speaker often pauses before redoing; longer is another thought
REFRAIN_MAX_WORDS = 4      # longer repeated windows are attempts, not a chant
REFRAIN_SPAN = 6           # windows scanned for a third occurrence of the same words
TAKE_WINDOW = 90           # seconds between takes
TAKE_COVERAGE = .8         # share of the earlier take's content the later take repeats
TAKE_MAX_LOST = 1          # content words of the earlier take the keeper may drop
IDEA_WINDOW = 60
IDEA_OVERLAP = .5
BLOCK_WINDOW = 120         # seconds between the first and the last attempt of a take block
BLOCK_GAP = 6.0            # a longer stop ends the take block before an aside
BLOCK_START_GAP = 2.5      # a continuation sentence starts without a take-sized pause
BLOCK_OPENING = 3          # minimum matching words at the start of two takes
BLOCK_MIN_CONTENT = 3      # the shared opening must carry real content, not "por exemplo"
BLOCK_MAX_SECONDS = 90     # a take block is a local attempt, not the rest of the video
BLOCK_LOST = 8             # lost content words shown in the review reason
MIN_CONTENT = 4
MIN_PROBABILITY = .5
BETWEEN_MAX_WORDS = 4
PAD = .04
BEFORE_KEPT = .1


def content(values):
    return [t for t in values if t not in STOP and len(t) > 1]


def anchors(values):
    return {t for t in values if t in NEGATIONS or t in NUMBER_WORDS or any(c.isdigit() for c in t)}


def _strip_cue(values):
    text = ' '.join(values)
    for cue in sorted(CUES, key=len, reverse=True):
        if text.startswith(cue + ' '):
            return values[len(cue.split()):], cue
    return values, None


def abandoned(segment):
    text = segment['text'].rstrip()
    tail = tokens(text)
    trailing_off = text.endswith(('...', '…')) and bool(tail) and tail[-1] in DANGLING
    return text.endswith('-') or trailing_off


def _confident(words):
    return bool(words) and sum(w['probability'] for w in words) / len(words) >= MIN_PROBABILITY


def _is_cue(segment):
    values = tokens(segment['text'])
    return len(values) <= BETWEEN_MAX_WORDS and (not values or ' '.join(values) in CUES or
                                                  all(v in STOP or v in ('pera', 'calma', 'perai') for v in values))


def _span(removed, kept, previous_end):
    """Cut from just before the removed words to just before the kept take starts."""
    start = max(previous_end, removed[0]['start'] - PAD)
    end = max(removed[-1]['end'] + PAD, kept[0]['start'] - BEFORE_KEPT)
    return start, min(end, kept[0]['start'] - .02)


def _periodic(window):
    return any(len(window) % p == 0 and window == window[:p] * (len(window) // p) for p in range(1, len(window)))


def _runs(transcript, gap=STUMBLE_RUN_GAP):
    """Continuous speech across ASR segment boundaries: a refrain can span segments."""
    words = sorted((w for s in transcript['segments'] for w in s['words']), key=lambda w: (w['start'], w['end']))
    runs = []
    for word in words:
        if runs and word['start'] - runs[-1][-1]['end'] <= gap:
            runs[-1].append(word)
        else:
            runs.append([word])
    return runs


def stumbles(transcript):
    result = []
    for words in _runs(transcript):
        norm = [' '.join(tokens(w['text'])) for w in words]
        i = 0
        while i < len(words):
            found = False
            for width in range(min(12, (len(words) - i) // 2), 1, -1):
                first, second = norm[i:i + width], norm[i + width:i + 2 * width]
                if first != second or not all(first):
                    continue
                repeats = 2
                while norm[i + repeats * width:i + (repeats + 1) * width] == first:
                    repeats += 1
                # A refrain broken by an aside ("da um like, ..., Joao, da um like") still counts.
                nearby = sum(norm[k:k + width] == first for k in range(max(0, i - REFRAIN_SPAN * width),
                                                                       min(len(norm), i + REFRAIN_SPAN * width)))
                removed, kept = words[i:i + width], words[i + width:i + 2 * width]
                values = [t for n in first for t in n.split()]
                # A short phrase said 3+ times, or a window that is itself a refrain, is emphasis or
                # a bit; a long sentence attempted three times is just more retakes.
                emphasis = (len(values) <= REFRAIN_MAX_WORDS and (repeats >= 3 or nearby >= 3)) or _periodic(first)
                automatic = (not emphasis and len(values) >= STUMBLE_MIN_WORDS and _confident(removed) and
                             min(w['probability'] for w in removed) >= .25)
                previous_end = words[i - 1]['end'] if i else max(0, words[0]['start'] - PAD)
                start, end = _span(removed, kept, previous_end)
                if end - start > .05:
                    result.append(dict(
                        id=f"stumble-{removed[0]['id']}", kind='stumble', start=round(start, 3), end=round(end, 3),
                        auto=automatic, removed_word_ids=[w['id'] for w in removed],
                        replacement_word_ids=[w['id'] for w in kept], evidence=[w['id'] for w in removed + kept],
                        reason=('Tropeço: o mesmo trecho dito duas vezes seguidas; fica a segunda tentativa.'
                                if automatic else 'Repetição pode ser ênfase ou piada; ouça antes de cortar.')))
                i += width * (repeats - 1 if emphasis else 1)
                found = True
                break
            if not found:
                i += 1
    return result


def _take_pair(first, second, between):
    """'take' when the later sentence is a complete retake, 'idea' when only similar, else None."""
    a, b = tokens(first['text']), tokens(second['text'])
    if len(content(a)) < 2 or len(b) < 3:
        return None, {}
    a_body, _ = _strip_cue(a)
    b_body, cue = _strip_cue(b)
    match = SequenceMatcher(None, a_body, b_body, autojunk=False).find_longest_match(0, len(a_body), 0, len(b_body))
    restart = match.size >= 3 and match.a <= 2 and match.b <= 2
    ca, cb = content(a), set(content(b))
    coverage = sum(t in cb for t in ca) / len(ca)
    lost = [t for t in dict.fromkeys(ca) if t not in cb]
    detail = dict(coverage=round(coverage, 2), lost=lost, cue=cue, restart=restart)
    # The keeper must say at least as much: a shorter retake would silently drop content.
    complete = len(set(cb)) >= len(set(ca)) and len(lost) <= TAKE_MAX_LOST
    if (restart and complete and coverage >= TAKE_COVERAGE and anchors(a) <= set(b) and not abandoned(second)
            and all(_is_cue(s) for s in between) and second['start'] - first['end'] <= TAKE_WINDOW
            and _confident(first['words']) and _confident(second['words'])):
        return 'take', detail
    overlap = len(set(ca) & cb) / max(1, len(set(ca) | cb))
    if len(set(ca)) >= MIN_CONTENT and (overlap >= IDEA_OVERLAP or (restart and coverage >= .6)):
        return 'idea', dict(detail, overlap=round(overlap, 2))
    return None, detail


def repeated_takes(transcript):
    segments = [s for s in transcript['segments'] if s['words']]
    result, removed = [], set()
    for i, first in enumerate(segments):
        if first['id'] in removed:
            continue
        for j in range(i + 1, min(len(segments), i + 6)):
            second = segments[j]
            if second['start'] - first['end'] > max(TAKE_WINDOW, IDEA_WINDOW):
                break
            between = segments[i + 1:j]
            kind, detail = _take_pair(first, second, between)
            if kind is None:
                continue
            if kind == 'idea' and second['start'] - first['end'] > IDEA_WINDOW:
                continue
            lost = ', '.join(detail['lost'][:6])
            a, b = content(tokens(first['text'])), content(tokens(second['text']))
            if kind == 'idea' and len(b) < len(a) and set(b) <= set(a):
                # The later one is the partial repeat: suggest dropping it, keep the complete one.
                following = segments[j + 1]['start'] if j + 1 < len(segments) else second['end'] + 1
                start = max(segments[j - 1]['end'], second['words'][0]['start'] - PAD)
                end = min(following, second['words'][-1]['end'] + PAD)
                cut_words, kept_id = second['words'], first['id']
                reason = 'A frase é repetida pela metade logo depois; a versão completa vem antes. Ouça e decida.'
            else:
                cut_words = first['words'] + ([w for s in between for w in s['words']] if kind == 'take' else [])
                previous_end = segments[i - 1]['end'] if i else 0
                start, end = _span(cut_words, second['words'], previous_end)
                if kind != 'take':
                    end = min(end, first['words'][-1]['end'] + PAD)
                kept_id = second['id']
                reason = ('Retake: a frase foi refeita logo depois e a nova versão cobre a anterior; fica a última.'
                          if kind == 'take' else
                          'A mesma ideia aparece de novo logo depois'
                          + (f'; a versão final não diz: {lost}.' if lost else '.') + ' Ouça e decida qual fica.')
            result.append(dict(
                id=f"{'take' if kind == 'take' else 'idea'}-{first['id']}-{second['id']}",
                kind='repeated_take' if kind == 'take' else 'repeated_idea',
                start=round(start, 3), end=round(end, 3), auto=kind == 'take',
                removed_word_ids=[w['id'] for w in cut_words],
                replacement_segment_ids=[kept_id], evidence=[first['id'], second['id']],
                detail=detail, reason=reason))
            if kind == 'take':
                removed.update([first['id'], *(s['id'] for s in between)])
            break
    return result


def _opening(first, second):
    a, b = tokens(first['text']), tokens(second['text'])
    if len(a) < BLOCK_OPENING or len(b) < BLOCK_OPENING:
        return 0
    a_body, _ = _strip_cue(a)
    b_body, _ = _strip_cue(b)
    match = SequenceMatcher(None, a_body, b_body, autojunk=False).find_longest_match(
        0, len(a_body), 0, len(b_body)
    )
    if match.size < BLOCK_OPENING or match.a > 2 or match.b > 2:
        return 0
    shared = content(a_body[match.a:match.a + match.size])
    return match.size if len(shared) >= BLOCK_MIN_CONTENT else 0


def _block_end(segments, start, limit):
    end = start + 1
    while end < limit:
        if segments[end]['start'] - segments[end - 1]['end'] > BLOCK_GAP:
            break
        if segments[end]['end'] - segments[start]['start'] > BLOCK_MAX_SECONDS:
            break
        end += 1
    return end


def _block_span(segments, start, end, previous_end, next_start):
    first_word = segments[start]['words'][0]
    last_word = segments[end - 1]['words'][-1]
    cut_start = max(previous_end, first_word['start'] - PAD)
    if next_start is not None and next_start - last_word['end'] <= BLOCK_GAP:
        cut_end = min(next_start - .02, max(last_word['end'] + PAD, next_start - BEFORE_KEPT))
    else:
        cut_end = last_word['end'] + PAD
    return cut_start, cut_end


def repeated_blocks(transcript):
    """Earlier multi-sentence attempts of the same take: review the whole block, keep the last."""
    segments = [s for s in transcript['segments'] if s['words']]
    result, used = [], set()
    for i, first in enumerate(segments):
        if first['id'] in used:
            continue
        if i and first['start'] - segments[i - 1]['end'] < BLOCK_START_GAP:
            continue
        group = [i]
        for j in range(i + 1, len(segments)):
            if segments[j]['start'] - first['end'] > BLOCK_WINDOW:
                break
            if _opening(first, segments[j]):
                group.append(j)
        if len(group) < 2:
            continue
        last_start = group[-1]
        last_end = _block_end(segments, last_start, len(segments))
        replacement = segments[last_start:last_end]
        if not replacement or not replacement[0]['words']:
            continue
        replacement_text = ' '.join(s['text'] for s in replacement)
        replacement_content = content(tokens(replacement_text))
        replacement_set = set(replacement_content)
        previous_end = segments[i - 1]['end'] if i else 0
        for position, start_index in enumerate(group[:-1]):
            next_index = group[position + 1]
            end_index = _block_end(segments, start_index, next_index)
            block = segments[start_index:end_index]
            if not block or not block[0]['words']:
                continue
            start, end = _block_span(
                segments, start_index, end_index, previous_end, segments[next_index]['start']
            )
            if position > 0:
                first_word = block[0]['words'][0]
                between = any(
                    word['end'] > previous_end and word['start'] < first_word['start']
                    for segment in segments
                    for word in segment['words']
                )
                if not between:
                    start = previous_end
            if end - start < .05:
                continue
            block_text = ' '.join(s['text'] for s in block)
            block_content = content(tokens(block_text))
            coverage = (
                sum(token in replacement_set for token in block_content) / len(block_content)
                if block_content
                else 1.0
            )
            lost = [token for token in dict.fromkeys(block_content) if token not in replacement_set]
            lost_text = ', '.join(lost[:BLOCK_LOST])
            result.append(
                dict(
                    id=f"block-{block[0]['id']}-{replacement[0]['id']}",
                    kind='repeated_block',
                    start=round(start, 3),
                    end=round(end, 3),
                    auto=False,
                    removed_word_ids=[word['id'] for segment in block for word in segment['words']],
                    replacement_segment_ids=[segment['id'] for segment in replacement],
                    evidence=dict(
                        takes=len(group),
                        position=position + 1,
                        coverage=round(coverage, 2),
                        lost=lost[:BLOCK_LOST],
                        block=dict(start=block[0]['start'], end=block[-1]['end'], text=block_text),
                        replacement=dict(
                            start=replacement[0]['start'],
                            end=replacement[-1]['end'],
                            text=replacement_text,
                        ),
                    ),
                    detail=dict(
                        coverage=round(coverage, 2),
                        lost=lost[:BLOCK_LOST],
                        takes=len(group),
                    ),
                    reason=(
                        f"Tomada repetida ({position + 1} de {len(group)}): a última tentativa fica."
                        + (
                            f" A versão final não diz: {lost_text}."
                            if lost_text
                            else " A versão final cobre o conteúdo."
                        )
                        + " Ouça e decida."
                    ),
                )
            )
            previous_end = end
        used.update(segments[index]['id'] for index in group)
    return result


AUTO_KINDS = ('stumble', 'repeated_take')
KINDS = AUTO_KINDS + ('repeated_idea', 'repeated_block')


def candidates(transcript):
    found = stumbles(transcript)
    claimed = {i for s in found for i in s['removed_word_ids']}
    ideas = [
        take for take in repeated_takes(transcript)
        if not set(take['removed_word_ids']) <= claimed
    ]
    blocks = repeated_blocks(transcript)
    if blocks:
        ideas = [
            idea for idea in ideas
            if not any(_overlap(idea, block) for block in blocks)
        ]
    return sorted(found + ideas + blocks, key=lambda suggestion: suggestion['start'])


def _overlap(a, b):
    return a['start'] < b['end'] and a['end'] > b['start']


def merge(found, proposals):
    """Older gated retake candidates that a take proposal already covers are redundant."""
    def covered(candidate):
        if candidate['kind'] not in ('retake', 'false_start'):
            return False
        return any(t['start'] <= candidate['start'] + .05 and candidate['end'] <= t['end'] + .05 or
                   set(candidate.get('removed_word_ids') or []) & set(t['removed_word_ids'])
                   for t in proposals)
    return [c for c in found if not covered(c)] + proposals


def guard(suggestions, transcript):
    """A take may go only while its replacement survives every other automatic cut."""
    words = {w['id']: w for s in transcript['segments'] for w in s['words']}
    segments = {s['id']: s for s in transcript['segments']}
    for take in suggestions:
        if not take.get('auto') or take['kind'] not in ('stumble', 'repeated_take'):
            continue
        kept = [words[i] for i in take.get('replacement_word_ids', []) if i in words]
        kept += [segments[i] for i in take.get('replacement_segment_ids', []) if i in segments]
        if any(other is not take and other.get('auto') and other['kind'] not in ('silence', 'adaptive_silence')
               and _overlap(other, item) for other in suggestions for item in kept):
            take.update(auto=False, reason='A tomada que ficaria também seria removida; ambas preservadas.')
    return suggestions
