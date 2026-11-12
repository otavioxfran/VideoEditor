"""Read-only ledger of human decisions and a replay of the current cut policy."""
import json
from statistics import mean

from video_editor.settings import DATA_DIR

IGNORED_ACTIONS = {
    'automatic_clean',
    'automatic_refine',
    'evaluation_copy',
    'legacy_import',
}
DECISION_LABELS = {'cut': 'cut', 'restore': 'keep', 'protect': 'protect'}
REVIEW_KINDS = {
    'repeated_block',
    'visual_absence',
    'repeated_idea',
    'false_start',
    'contextual_restart',
    'background_voice',
}
OUTPUT_PATH = DATA_DIR / 'learning' / 'decisions.jsonl'


def _stack(history):
    stack = []
    for entry in history:
        if entry.get('action') == 'undo':
            if stack:
                stack.pop()
        else:
            stack.append(entry)
    return stack


def _words(transcript):
    words = [
        word
        for segment in transcript.get('segments', [])
        for word in segment.get('words', [])
    ]
    return sorted(words, key=lambda word: (word['start'], word['end']))


def _removed(words, start, end):
    return [word for word in words if word['end'] > start and word['start'] < end]


def _pauses(words, start, end):
    before = [word for word in words if word['end'] <= start]
    after = [word for word in words if word['start'] >= end]
    pause_before = round(start - max(word['end'] for word in before), 3) if before else None
    pause_after = round(min(word['start'] for word in after) - end, 3) if after else None
    return pause_before, pause_after


def _probabilities(removed):
    values = [
        float(word['probability'])
        for word in removed
        if word.get('probability') is not None
    ]
    return round(mean(values), 4) if values else None


def _evidence(suggestion):
    evidence = suggestion.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    detail = suggestion.get('detail')
    detail = detail if isinstance(detail, dict) else {}
    coverage = evidence.get('coverage', detail.get('coverage'))
    lost = evidence.get('lost', detail.get('lost'))
    lost_words = len(lost) if isinstance(lost, list) else 0
    visual = evidence.get('visual')
    visual = visual if isinstance(visual, dict) else {}
    absence = visual.get('absence_seconds', evidence.get('absence_seconds'))
    return coverage, lost_words, absence


def _example(job_id, duration, policy, words, entry, suggestion, label, kind=None, span=None):
    if suggestion is not None:
        start, end = float(suggestion['start']), float(suggestion['end'])
    elif span is not None:
        start, end = float(span[0]), float(span[1])
    else:
        start, end = float(entry['start']), float(entry['end'])
    removed = _removed(words, start, end)
    pause_before, pause_after = _pauses(words, start, end)
    coverage = lost_words = absence = None
    auto = False
    if suggestion is not None:
        kind = suggestion.get('kind', '')
        auto = bool(suggestion.get('auto'))
        coverage, lost_words, absence = _evidence(suggestion)
    kind = kind or 'manual'
    return dict(
        job_id=job_id,
        suggestion_id=(suggestion or {}).get('id') if suggestion else entry.get('suggestion_id'),
        kind=kind,
        auto=auto,
        label=label,
        overrode_auto=bool(auto and label == 'keep'),
        start=round(start, 3),
        end=round(end, 3),
        duration=round(end - start, 3),
        position=round(start / duration, 6) if duration else 0.0,
        removed_text=' '.join(word.get('text', '') for word in removed[:40]),
        removed_words=len(removed),
        mean_word_prob=_probabilities(removed),
        pause_before=pause_before,
        pause_after=pause_after,
        coverage=coverage,
        lost_words=lost_words,
        absence_seconds=absence,
        decided_at=entry.get('at'),
        policy=policy,
    )


def _intervals(entry, segments):
    identifiers = entry.get('segment_ids')
    if identifiers:
        return [
            (float(segments[identifier]['start']), float(segments[identifier]['end']))
            for identifier in identifiers
            if identifier in segments
        ]
    if entry.get('start') is not None and entry.get('end') is not None:
        return [(float(entry['start']), float(entry['end']))]
    return []


def _overlap(left, right, start, end):
    return max(0.0, min(right, end) - max(left, start))


def _overlaps_auto(suggestions, start, end):
    return any(
        _overlap(suggestion['start'], suggestion['end'], start, end) > 0
        for suggestion in suggestions
    )


def extract(jobs, job_id):
    job = jobs.info(job_id)
    if job.get('legacy'):
        return []
    duration = float(job['media']['duration'])
    edit = jobs.read(job_id, 'edit.json')
    transcript = jobs.read(job_id, 'transcript.json')
    suggestions = {
        suggestion['id']: suggestion
        for suggestion in jobs.read(job_id, 'suggestions.json')
    }
    policy = None
    try:
        policy = jobs.read(job_id, 'automatic_report.json').get('policy')
    except (OSError, ValueError, KeyError):
        pass
    words = _words(transcript)
    segments = {segment['id']: segment for segment in transcript.get('segments', [])}
    auto_suggestions = [item for item in suggestions.values() if item.get('auto')]
    examples, decisions, order = [], {}, {}
    for index, entry in enumerate(_stack(edit.get('history', []))):
        action = entry.get('action')
        if action in IGNORED_ACTIONS:
            continue
        sid = entry.get('suggestion_id')
        if sid and sid in suggestions and action in DECISION_LABELS:
            decisions[sid] = entry
            order[sid] = index
            continue
        if sid or action not in ('cut', 'restore', 'protect'):
            continue
        for start, end in _intervals(entry, segments):
            if action == 'cut':
                examples.append((index, _example(
                    job_id, duration, policy, words, entry, None, 'cut', span=(start, end)
                )))
            elif _overlaps_auto(auto_suggestions, start, end):
                examples.append((index, _example(
                    job_id, duration, policy, words, entry, None, 'keep',
                    kind='manual_keep', span=(start, end),
                )))
    for sid in sorted(decisions, key=lambda item: order[item]):
        entry = decisions[sid]
        examples.append((order[sid], _example(
            job_id,
            duration,
            policy,
            words,
            entry,
            suggestions[sid],
            DECISION_LABELS[entry['action']],
        )))
    return [example for _, example in sorted(examples, key=lambda item: item[0])]


def build(jobs):
    examples, jobs_read = [], 0
    for job in jobs.list():
        try:
            values = extract(jobs, job['job_id'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        examples.extend(values)
        jobs_read += 1
    path = OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False) + '\n')
    return dict(path=str(path), examples=len(examples), jobs=jobs_read)


def load(path=None):
    path = path or OUTPUT_PATH
    examples = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


CATEGORIES = (
    'auto_ok',
    'false_cuts',
    'asked_cut',
    'asked_keep',
    'hidden_cut',
    'hidden_keep',
    'missed',
    'kept_against_auto',
)


def _category(example):
    kind = example.get('kind')
    human = example.get('label') == 'cut'
    if kind == 'manual':
        return 'missed'
    if kind == 'manual_keep':
        return 'kept_against_auto'
    if example.get('auto'):
        return 'auto_ok' if human else 'false_cuts'
    if kind in REVIEW_KINDS:
        return 'asked_cut' if human else 'asked_keep'
    return 'hidden_cut' if human else 'hidden_keep'


def _metrics(examples):
    counts = {category: 0 for category in CATEGORIES}
    for example in examples:
        counts[_category(example)] += 1
    questions = counts['asked_cut'] + counts['asked_keep']
    return dict(
        examples=len(examples),
        **counts,
        errors=counts['false_cuts'] + counts['kept_against_auto'],
        omissions=counts['hidden_cut'] + counts['missed'],
        questions=questions,
        cut_rate_when_asked=(
            round(100 * counts['asked_cut'] / questions, 1) if questions else None
        ),
    )


def replay(examples):
    by_kind = {}
    for example in examples:
        by_kind.setdefault(example.get('kind', ''), []).append(example)
    return dict(
        total=_metrics(examples),
        by_kind={kind: _metrics(values) for kind, values in sorted(by_kind.items())},
    )


def text(value):
    rows = [('TOTAL', value['total'])] + list(value['by_kind'].items())
    lines = [
        f"{'Tipo':24} {'Exemplos':>8} {'Acertos auto':>12} {'Erros':>6} "
        f"{'Perguntas':>9} {'% cortou quando perguntado':>27} {'Omissões':>9}"
    ]
    for kind, metrics in rows:
        rate = (
            '—'
            if metrics['cut_rate_when_asked'] is None
            else f"{metrics['cut_rate_when_asked']:.1f}%"
        )
        lines.append(
            f"{kind:24} {metrics['examples']:>8} {metrics['auto_ok']:>12} "
            f"{metrics['errors']:>6} {metrics['questions']:>9} "
            f"{rate:>27} {metrics['omissions']:>9}"
        )
    return '\n'.join(lines)
