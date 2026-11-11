import pytest

from video_editor import decision_ledger

SEGMENTS = [
    dict(
        start=1,
        end=3,
        text='fala aqui.',
        words=[
            dict(word='fala', start=1, end=2, probability=.9),
            dict(word='aqui.', start=2, end=3, probability=.7),
        ],
    )
]


def _suggestion(identifier='s1', **values):
    return dict(
        id=identifier,
        kind=values.pop('kind', 'repeated_block'),
        auto=values.pop('auto', False),
        start=values.pop('start', 1),
        end=values.pop('end', 3),
        reason='motivo',
        **values,
    )


def _job(jobs, ready, suggestions, history):
    job = ready(duration=20, segments=SEGMENTS)
    job_id = job['job_id']
    jobs.write(job_id, 'suggestions.json', suggestions)
    edit = jobs.read(job_id, 'edit.json')
    edit['history'] = history
    jobs.write(job_id, 'edit.json', edit)
    return job_id


def test_undo_cancels_the_decision(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [_suggestion()],
        [
            dict(action='cut', suggestion_id='s1', at='2026-01-01T00:00:00Z'),
            dict(action='undo', at='2026-01-01T00:00:01Z'),
        ],
    )
    assert decision_ledger.extract(jobs, job_id) == []


def test_restore_of_an_automatic_cut_is_a_keep_with_override(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [_suggestion(kind='repeated_take', auto=True, start=4, end=5)],
        [dict(action='restore', suggestion_id='s1', at='2026-01-01T00:00:00Z')],
    )
    example = decision_ledger.extract(jobs, job_id)[0]
    assert example['label'] == 'keep'
    assert example['auto'] is True
    assert example['overrode_auto'] is True


def test_manual_range_cut_becomes_a_manual_example(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [],
        [dict(action='cut', start=4, end=5, at='2026-01-01T00:00:00Z')],
    )
    example = decision_ledger.extract(jobs, job_id)[0]
    assert example['kind'] == 'manual'
    assert example['label'] == 'cut'
    assert example['suggestion_id'] is None
    assert example['start'] == 4
    assert example['end'] == 5
    assert example['duration'] == 1
    assert example['position'] == pytest.approx(.2)


def test_automatic_actions_and_untouched_suggestions_are_ignored(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [_suggestion(auto=True)],
        [
            dict(action='automatic_clean'),
            dict(action='automatic_refine'),
            dict(action='evaluation_copy'),
            dict(action='legacy_import'),
        ],
    )
    assert decision_ledger.extract(jobs, job_id) == []


def test_example_carries_transcript_and_evidence_fields(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [
            _suggestion(
                start=1,
                end=3,
                evidence=dict(coverage=.8, lost=['x', 'y']),
            ),
            _suggestion(
                identifier='s2',
                kind='visual_absence',
                start=10,
                end=10.5,
                evidence=dict(visual=dict(absence_seconds=2.5)),
            ),
        ],
        [
            dict(action='cut', suggestion_id='s1'),
            dict(action='cut', suggestion_id='s2'),
        ],
    )
    first, second = decision_ledger.extract(jobs, job_id)
    assert first['removed_text'] == 'fala aqui.'
    assert first['removed_words'] == 2
    assert first['mean_word_prob'] == pytest.approx(.8)
    assert first['coverage'] == .8
    assert first['lost_words'] == 2
    assert first['absence_seconds'] is None
    assert second['absence_seconds'] == 2.5
    assert second['pause_before'] == pytest.approx(7)


def test_manual_keep_by_interval_needs_an_automatic_cut_overlap(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [
            _suggestion('auto1', kind='repeated_take', auto=True, start=4, end=6),
            _suggestion('auto2', kind='repeated_take', auto=True, start=10, end=11),
        ],
        [
            dict(action='restore', start=4, end=6, at='2026-01-01T00:00:00Z'),
            dict(action='protect', start=10, end=11, at='2026-01-01T00:00:01Z'),
            dict(action='restore', start=14, end=15, at='2026-01-01T00:00:02Z'),
        ],
    )
    examples = decision_ledger.extract(jobs, job_id)
    assert [example['kind'] for example in examples] == ['manual_keep', 'manual_keep']
    assert [example['label'] for example in examples] == ['keep', 'keep']
    assert examples[0]['start'] == 4
    assert examples[1]['start'] == 10


def test_segment_ids_resolve_manual_cut_and_restore(jobs, ready):
    job_id = _job(
        jobs,
        ready,
        [_suggestion(kind='repeated_take', auto=True, start=1, end=3)],
        [
            dict(action='cut', segment_ids=['s1'], at='2026-01-01T00:00:00Z'),
            dict(action='restore', segment_ids=['s1'], at='2026-01-01T00:00:01Z'),
        ],
    )
    examples = decision_ledger.extract(jobs, job_id)
    assert [example['kind'] for example in examples] == ['manual', 'manual_keep']
    assert examples[0]['start'] == 1
    assert examples[0]['end'] == 3
    assert examples[1]['label'] == 'keep'
    assert examples[1]['start'] == 1


def test_replay_categories_are_exclusive_and_measured():
    examples = [
        dict(kind='silence', auto=True, label='cut'),
        dict(kind='silence', auto=True, label='keep'),
        dict(kind='repeated_block', auto=False, label='cut'),
        dict(kind='repeated_block', auto=False, label='keep'),
        dict(kind='silence', auto=False, label='cut'),
        dict(kind='silence', auto=False, label='protect'),
        dict(kind='manual', auto=False, label='cut'),
        dict(kind='manual_keep', auto=False, label='keep'),
    ]
    value = decision_ledger.replay(examples)
    total = value['total']
    assert total == dict(
        examples=8,
        auto_ok=1,
        false_cuts=1,
        asked_cut=1,
        asked_keep=1,
        hidden_cut=1,
        hidden_keep=1,
        missed=1,
        kept_against_auto=1,
        errors=2,
        omissions=2,
        questions=2,
        cut_rate_when_asked=50.0,
    )
    for metrics in [total, *value['by_kind'].values()]:
        assert sum(metrics[category] for category in decision_ledger.CATEGORIES) == metrics[
            'examples'
        ]
    assert value['by_kind']['repeated_block']['cut_rate_when_asked'] == 50.0
    assert value['by_kind']['silence']['cut_rate_when_asked'] is None
    assert value['by_kind']['silence']['errors'] == 1
    assert value['by_kind']['silence']['omissions'] == 1
    assert value['by_kind']['manual']['omissions'] == 1
    assert value['by_kind']['manual_keep']['errors'] == 1


def test_build_rewrites_the_ledger_idempotently(tmp_path, jobs, ready, monkeypatch):
    monkeypatch.setattr(
        decision_ledger, 'OUTPUT_PATH', tmp_path / 'learning' / 'decisions.jsonl'
    )
    _job(
        jobs,
        ready,
        [_suggestion()],
        [dict(action='cut', suggestion_id='s1', at='2026-01-01T00:00:00Z')],
    )
    first = decision_ledger.build(jobs)
    second = decision_ledger.build(jobs)
    assert first['examples'] == second['examples'] == 1
    assert len(decision_ledger.load(second['path'])) == 1
