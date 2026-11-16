"""Human-like pause pacing for dead-air cuts.

A fixed margin turns every pause into the same ~0.37 s and the edit sounds like a
metronome. Editors instead leave a residual pause that depends on context (hesitation,
end of sentence, punchline) and grows with how long the speaker really paused.
Cuts must also buy enough time to justify a jump cut, sit on the quietest point
nearby and never hide untranscribed sound.
"""
import hashlib
import math
import wave

import numpy as np

from video_editor.cuts import tokens

FRAME = .01
# (residual for a pause of the same length, log growth, ceiling) per context, in seconds.
PACE = dict(hesitation=(.22, .06, .5), sentence=(.45, .12, .95))
THOUGHT_BREAK = 2.5        # unpunctuated pauses this long end a thought, not a word
EXPRESSIVE_KEEP = 2.0     # doubtful expressive pauses up to this length stay intact
EXPRESSIVE_FLOOR = 1.0     # longer expressive pauses are shortened only down to this
EXPRESSIVE_JITTER = .12    # a beat after a question is rarely the same 1.000 s twice
JITTER = .08               # deterministic +/-8%, so equal contexts do not sound identical
MIN_SAVING = .35           # a jump cut must buy at least this much time
MIN_AFTER_PREVIOUS = .15   # decay of the previous word
MIN_BEFORE_NEXT = .12      # breath and onset of the next word
SNAP_WINDOW = .04
LEAD_SHARE = .6            # most of the residual stays after the previous word
VOICE_MIN = .12           # shorter VAD islands are clicks or breaths, not speech
VOICE_PAD = .25           # room kept around untranscribed speech inside a pause
MIN_KEEP = 1.5
THIN_SAVING = 1.0          # only small cuts are dropped to avoid choppy short keeps
SEAL_MAX = .35             # pure-silence slivers between cuts are swallowed, not heard
PAUSE_KINDS = ('silence', 'adaptive_silence')
CONTRAST = ('mas ', 'porem ', 'so que ', 'entao ', 'ou seja ')


def _jitter(seed, amplitude=JITTER):
    value = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xffffffff
    return (value * 2 - 1) * amplitude


def context(previous_words, following_words):
    """hesitation | sentence | expressive, from punctuation and sentence shape."""
    text = previous_words[-1]['text'].rstrip() if previous_words else ''
    if text.endswith(('?', '!', '...', '…')):
        return 'expressive'
    if text.endswith(('.', ':', ';')):
        sentence = []
        for word in reversed(previous_words[:-1]):
            if word['text'].rstrip().endswith(('.', '?', '!', '…', ':', ';')):
                break
            sentence.append(word)
        continuation = ' '.join(tokens(' '.join(w['text'] for w in following_words[:4]))) + ' '
        # A short closing line or a pause before a turn is timing, not dead air.
        if len(sentence) + 1 <= 3 or continuation.startswith(CONTRAST):
            return 'expressive'
        return 'sentence'
    return 'hesitation'


def target(gap, kind, seed):
    """Residual pause to keep from a pause of `gap` seconds."""
    if kind == 'expressive':
        if gap <= EXPRESSIVE_KEEP:
            return gap
        return min(gap, EXPRESSIVE_FLOOR * (1 + _jitter(seed, EXPRESSIVE_JITTER)))
    base, slope, ceiling = PACE[kind]
    value = min(ceiling, base + slope * math.log(max(gap, base) / base))
    return min(gap, value * (1 + _jitter(seed)))


class Levels:
    """10 ms RMS levels and local Silero VAD of the 16 kHz mono analysis WAV."""

    def __init__(self, path):
        with wave.open(str(path), 'rb') as handle:
            if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
                raise ValueError('Ritmo requer WAV PCM mono 16 kHz.')
            audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2').astype(np.float32) / 32768
        block = int(16000 * FRAME)
        frames = audio[:len(audio) // block * block].reshape(-1, block)
        self.db = 20 * np.log10(np.maximum(np.sqrt(np.mean(frames * frames, axis=1)), 1e-6))
        # Same local model Whisper used; speech it heard but ASR left without words matters.
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        self.speech = [(s['start'] / 16000, s['end'] / 16000) for s in get_speech_timestamps(
            audio, VadOptions(threshold=.3, min_speech_duration_ms=0, min_silence_duration_ms=400, speech_pad_ms=0))]

    def window(self, start, end):
        return self.db[max(0, int(start / FRAME)):max(0, int(math.ceil(end / FRAME)))]

    def voiced(self, start, end):
        """Untranscribed speech islands inside (start, end), padded and merged."""
        islands = []
        for left, right in self.speech:
            left, right = max(start, left), min(end, right)
            if right - left >= VOICE_MIN:
                left, right = max(start, left - VOICE_PAD), min(end, right + VOICE_PAD)
                if islands and left <= islands[-1][1]:
                    islands[-1][1] = max(islands[-1][1], right)
                else:
                    islands.append([left, right])
        return islands

    def quietest(self, around, low, high):
        """Quietest 10 ms frame near `around`, inside [low, high]."""
        low, high = max(low, around - SNAP_WINDOW), min(high, around + SNAP_WINDOW)
        values = self.window(low, high)
        if high <= low or not len(values):
            return around
        return min(high, max(low, (int(low / FRAME) + int(np.argmin(values)) + .5) * FRAME))


def _inward(start, end):
    # Round toward the inside: a cut never grows past its gap or the source end.
    return math.ceil(start * 1000) / 1000, math.floor(end * 1000) / 1000


def _candidate(identifier, start, end, reason, **evidence):
    start, end = _inward(start, end)
    return dict(id=identifier, start=start, end=end, kind='silence', auto=True,
                reason=reason, evidence=dict(paced=True, **evidence))


def speech_gaps(transcript, duration, profile, wav=None, intonation=None):
    """Dead-air proposals between timed words, paced like a human edit."""
    words = sorted((w for s in transcript.get('segments', []) for w in s.get('words', [])
                    if 'start' in w and 'end' in w), key=lambda w: (w['start'], w['end']))
    if not words:
        return []
    minimum = profile.get('min_silence_gap', .8)
    levels = Levels(wav) if wav else None
    result = []

    def add(candidate):
        """Append, splitting around untranscribed speech the VAD still hears."""
        if levels is None:
            candidate['evidence']['acoustic'] = 'not_measured'
            result.append(candidate)
            return
        islands = levels.voiced(candidate['start'], candidate['end'])
        candidate['evidence']['acoustic'] = 'vad_checked'
        if not islands:
            result.append(candidate)
            return
        edges = [candidate['start']] + [x for island in islands for x in island] + [candidate['end']]
        voiced = [[round(a, 3), round(b, 3)] for a, b in islands]
        for part, (start, end) in enumerate(zip(edges[::2], edges[1::2])):
            start, end = _inward(start, end)
            if end - start >= MIN_SAVING:
                piece = dict(candidate, id=f"{candidate['id']}-{part + 1}", start=start, end=end,
                             reason=candidate['reason'] + ' Fala não transcrita no meio foi preservada.',
                             evidence=dict(candidate['evidence'], untranscribed_speech=voiced))
                result.append(piece)

    if words[0]['start'] >= minimum:
        end = words[0]['start'] - max(MIN_BEFORE_NEXT, .3)
        if end > MIN_SAVING:
            add(_candidate('gap-start-1', 0, end, 'Pausa inicial antes da primeira fala.',
                           pause_duration=round(words[0]['start'], 3)))
    for index in range(len(words) - 1):
        before, after = words[index], words[index + 1]
        gap = after['start'] - before['end']
        if gap < minimum:
            continue
        kind = context(words[max(0, index - 30):index + 1], words[index + 1:index + 5])
        if kind == 'hesitation' and gap >= THOUGHT_BREAK:
            # ASR often drops punctuation; a long stop still ends a thought.
            kind = 'sentence'
        if kind != 'expressive' and gap <= EXPRESSIVE_KEEP and intonation and intonation(before['end'], after['start']):
            kind = 'expressive'
        keep = target(gap, kind, f"{before['end']:.3f}-{after['start']:.3f}")
        lead = max(MIN_AFTER_PREVIOUS, keep * LEAD_SHARE)
        tail = max(MIN_BEFORE_NEXT, keep - lead)
        start, end = before['end'] + lead, after['start'] - tail
        if levels is not None:
            start = levels.quietest(start, before['end'] + MIN_AFTER_PREVIOUS * .7, end - MIN_SAVING)
            end = levels.quietest(end, start + MIN_SAVING, after['start'] - MIN_BEFORE_NEXT * .7)
        if end - start < MIN_SAVING:
            continue
        evidence = dict(before_word=before['text'], after_word=after['text'], gap_seconds=round(gap, 3),
                        context=kind, residual_seconds=round(gap - (end - start), 3))
        reason = {
            'hesitation': f'Hesitação de {gap:.1f}s encurtada, mantendo a fala corrida.',
            'sentence': f'Pausa morta de {gap:.1f}s encurtada, mantendo respiro de fim de frase.',
            'expressive': f'Pausa longa de {gap:.1f}s encurtada só até {EXPRESSIVE_FLOOR:.0f}s; timing preservado.',
        }[kind]
        add(_candidate(f'gap-{len(result) + 1}', start, end, reason, **evidence))
    if duration - words[-1]['end'] >= minimum:
        start = words[-1]['end'] + .6
        if duration - start > MIN_SAVING:
            add(_candidate(f'gap-end-{len(result) + 1}', start, duration, 'Silêncio final após o término da fala.',
                           pause_duration=round(duration - words[-1]['end'], 3)))
    return result


def _overlap(a, b):
    return a['start'] < b['end'] and a['end'] > b['start']


def reconcile(suggestions):
    """Paced proposals own their pause: no other pause path may cut what pacing preserved."""
    paced = [s for s in suggestions if isinstance(s.get('evidence'), dict) and s['evidence'].get('paced')]
    for other in suggestions:
        if other in paced or other['kind'] not in PAUSE_KINDS or not other.get('auto'):
            continue
        owner = next((p for p in paced if _overlap(p, other)), None)
        if owner is not None:
            other.update(auto=False, reason='Pausa já tratada pelo ritmo natural; sem corte duplicado.'
                         if owner.get('auto') else owner['reason'])
    return suggestions


def _saving(cut):
    return cut['end'] - cut['start']


def thin(suggestions, minimum_keep=MIN_KEEP):
    """Avoid two jump cuts around a tiny kept phrase by dropping the smaller pause cut."""
    changed = True
    while changed:  # each pass disables one cut, so this terminates
        changed = False
        cuts = sorted((s for s in suggestions if s.get('auto') and s['kind'] in PAUSE_KINDS),
                      key=lambda s: s['start'])
        for a, b in zip(cuts, cuts[1:]):
            if b['start'] - a['end'] >= minimum_keep:
                continue
            weaker = min((a, b), key=_saving)
            # When both remove real dead air, restoring it is worse than a quick cut.
            if _saving(weaker) >= THIN_SAVING:
                continue
            weaker.update(auto=False, reason='Corte pequeno entre falas curtas; pausa mantida para evitar picote.')
            changed = True
            break
    return suggestions


def seal(suggestions, transcript, protected=(), maximum=SEAL_MAX):
    """Swallow a short pure-silence keep left between a pause cut and another cut.

    Two cuts that almost touch but leave 0.1-0.3 s of silence produce an audible
    stutter at the join. Nothing spoken may be inside the sliver, so extending the
    later cut back to the earlier boundary is safe.
    """
    cuts = sorted((s for s in suggestions if s.get('auto')), key=lambda s: s['start'])
    for left, right in zip(cuts, cuts[1:]):
        gap = right['start'] - left['end']
        if not 0 < gap <= maximum:
            continue
        if left['kind'] not in PAUSE_KINDS and right['kind'] not in PAUSE_KINDS:
            continue
        start, end = left['end'], right['start']
        if any(w['start'] < end and w['end'] > start
               for s in transcript.get('segments', []) for w in s.get('words', [])):
            continue
        if any(p['start'] < end and p['end'] > start for p in protected):
            continue
        right['start'] = left['end']
    return suggestions
