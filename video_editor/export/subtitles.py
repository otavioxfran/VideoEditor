from video_editor.timeline import project_interval


def timestamp(value):
    millis = max(0, round(value * 1000))
    seconds, ms = divmod(millis, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}'


def cues(transcript, timeline):
    output = []
    for segment in transcript['segments']:
        words = segment['words']
        if not words:
            # Segment-only legacy ASR: split cues by retained interval, never invent word timing.
            for start, end in project_interval(timeline, segment['start'], segment['end']):
                output.append(dict(start=start, end=end, text=segment['text']))
            continue
        for keep in timeline['keeps']:
            kept = [w for w in words if w['start'] < keep['source_end'] and w['end'] > keep['source_start']]
            # Short readable groups; retained pauses remain real output-time gaps.
            group = []
            for word in kept:
                if group and (word['end'] - group[0]['start'] > 5 or
                              word['start'] - group[-1]['end'] > .7 or len(group) >= 12):
                    output.append(_cue(group, keep))
                    group = []
                group.append(word)
                if word['text'].endswith(('.', '?', '!')):
                    output.append(_cue(group, keep))
                    group = []
            if group:
                output.append(_cue(group, keep))
    return sorted(output, key=lambda c: c['start'])


def _cue(words, keep):
    offset = keep['output_start'] - keep['source_start']
    return dict(start=max(words[0]['start'], keep['source_start']) + offset,
                end=min(words[-1]['end'], keep['source_end']) + offset,
                text=' '.join(w['text'] for w in words))


def srt(transcript, timeline):
    return '\n\n'.join(f'{i}\n{timestamp(c["start"])} --> {timestamp(c["end"])}\n{c["text"]}'
                       for i, c in enumerate(cues(transcript, timeline), 1)) + '\n'
