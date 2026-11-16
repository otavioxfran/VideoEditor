from video_editor.automatic import Automatic
from video_editor.automatic import runner_validation


def _call(jobs, job, transcript, edit, suggestions, notes, monkeypatch, *, evaluation=False,
          context_report=None, protected=None, refine=None, safe=None, profile=None, gate=None,
          audit=None):
    service = Automatic(jobs, None, None, None, jobs.root / 'provider.json')
    monkeypatch.setattr(runner_validation.acceptance, 'gate', lambda jobs: gate or dict(
        enabled=False, state='gated', classes={'contextual_restart': {'enabled': True}}
    ))
    monkeypatch.setattr(runner_validation, 'load_profile', lambda: profile or {
        'auto_cut_takes': False, 'auto_cut_silence': False
    })
    monkeypatch.setattr(runner_validation, 'refine_visual', refine or (
        lambda *args, **kwargs: (args[2], dict(state='measured', abrupt=False, difference=0))
    ))
    if safe is not None:
        monkeypatch.setattr(runner_validation, 'safe_bounds', safe)
    monkeypatch.setattr(runner_validation.pacing, 'seal', lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_validation.takes, 'guard', lambda *args, **kwargs: None)
    if audit is not None:
        monkeypatch.setattr(runner_validation.contextual, 'audit', audit)
    return runner_validation.validate_and_refine(
        service, job['job_id'], job, transcript, edit, suggestions, notes,
        protected or [], context_report or {'state': 'disabled'}, evaluation, {},
        lambda *args, **kwargs: None,
    )


def test_validation_gates_visual_and_protected_branches(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    word = transcript['segments'][0]['words'][0]
    suggestions = [
        dict(id='take', kind='stumble', start=2, end=3, auto=True, evidence=[], reason='take', evaluation={}),
        dict(id='idea', kind='repeated_idea', start=4, end=5, auto=True, evidence=[], reason='idea', evaluation={}),
        dict(id='adaptive', kind='adaptive_silence', start=6, end=7, auto=True,
             evidence=dict(native_clear=True, vad_clear=True, background_match=True),
             reason='pause', evaluation={}),
        dict(id='context', kind='contextual_restart', start=8, end=9, auto=True,
             evidence=[], removed_word_ids=[word['id']], reason='context',
             evaluation=dict(editorial=dict(
                 action='remove', replacement_proven=True,
                 replacement_segment_ids=[transcript['segments'][1]['id']],
                 replacement_word_ids=[],
             ))),
    ]
    result = _call(
        jobs, job, transcript, edit, suggestions, {}, monkeypatch, protected=[],
        profile={'auto_cut_takes': False, 'auto_cut_silence': True},
        safe=lambda candidate, *args, **kwargs: dict(start=candidate['start'], end=candidate['end']),
    )
    assert result[0][0]['auto'] is False
    assert result[0][1]['auto'] is False
    assert result[0][3]['evaluation']['human_gate'] == 'gated'
    allowed = result[3]
    assert allowed('stumble') is False
    assert allowed('repeated_idea') is False
    assert allowed('adaptive_silence') is True
    assert allowed('contextual_restart') is True


def test_validation_covers_replacement_and_visual_failures(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    word = transcript['segments'][0]['words'][0]
    suggestions = [
        dict(id='repair', kind='correction', start=2, end=3, auto=True, evidence=[],
             removed_word_ids=[word['id']], reason='repair', evaluation={}),
        dict(id='visual', kind='silence', start=6, end=7, auto=True,
             evidence={}, reason='visual', evaluation={}),
        dict(id='abrupt', kind='silence', start=8, end=9, auto=True,
             evidence={}, reason='abrupt', evaluation={}),
    ]
    notes = {'repair': dict(action='remove', replacement_segment_ids=[transcript['segments'][1]['id']],
                            replacement_word_ids=[], replacement_proven=True)}

    def refine(source, info, candidate, *args, **kwargs):
        if candidate['id'] == 'visual':
            raise RuntimeError('visual unavailable')
        if candidate['id'] == 'abrupt':
            return candidate, dict(state='measured', abrupt=True, difference=.9)
        return candidate, dict(state='measured', abrupt=False, difference=0)

    _call(
        jobs, job, transcript, edit, suggestions, notes, monkeypatch, evaluation=True, refine=refine,
        safe=lambda candidate, *args, **kwargs: dict(start=candidate['start'], end=candidate['end']),
    )
    assert suggestions[0]['auto'] is True
    assert suggestions[1]['auto'] is True
    assert suggestions[2]['auto'] is False


def test_validation_covers_replacement_words_and_conflict(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    word = transcript['segments'][0]['words'][0]
    suggestions = [
        dict(id='repair', kind='correction', start=2, end=3, auto=True, evidence=[],
             removed_word_ids=[word['id']], reason='repair', evaluation={}),
        dict(id='other', kind='silence', start=1.2, end=1.8, auto=True,
             evidence={}, reason='other', evaluation={}),
    ]
    notes = {
        'repair': dict(action='remove', replacement_segment_ids=['s1'],
                       replacement_word_ids=[], replacement_proven=True),
        'other': dict(replacement_segment_ids=['s1'], replacement_word_ids=[], replacement_proven=True),
    }
    _call(
        jobs, job, transcript, edit, suggestions, notes, monkeypatch, evaluation=True,
        safe=lambda candidate, *args, **kwargs: dict(start=candidate['start'], end=candidate['end']),
    )
    assert suggestions[0]['auto'] is False


def test_validation_covers_editorial_protection_and_human_history(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    edit['history'] = [dict(action='cut')]
    suggestions = [
        dict(id='protected', kind='silence', start=1, end=2, auto=True,
             evidence={}, reason='protected', evaluation={}),
    ]
    _call(
        jobs, job, transcript, edit, suggestions, {}, monkeypatch,
        protected=[dict(start=1.2, end=1.7)],
    )
    assert suggestions[0]['auto'] is False


def test_validation_rejects_contextual_bounds(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    word = transcript['segments'][0]['words'][0]
    suggestion = dict(
        id='context', kind='contextual_restart', start=1, end=2, auto=True,
        evidence=[], removed_word_ids=[word['id']], reason='context',
        evaluation=dict(editorial=dict(
            action='remove', replacement_proven=True,
            replacement_segment_ids=[transcript['segments'][1]['id']],
            replacement_word_ids=[],
        )),
    )
    _call(
        jobs, job, transcript, edit, [suggestion], {}, monkeypatch,
        safe=lambda *args, **kwargs: None,
    )
    assert suggestion['reason'].endswith('preservado.')


def test_validation_covers_contextual_audit_instability(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    word = transcript['segments'][0]['words'][0]
    suggestion = dict(
        id='context', kind='contextual_restart', start=1, end=2, auto=True,
        evidence=[], removed_word_ids=[word['id']], reason='context',
        evaluation=dict(editorial=dict(
            action='remove', replacement_proven=True,
            replacement_segment_ids=[transcript['segments'][1]['id']],
            replacement_word_ids=[],
        )),
    )
    calls = []

    def audit(*args, **kwargs):
        calls.append(1)
        return dict(state='complete', restored=['x'])

    _call(
        jobs, job, transcript, edit, [suggestion], {}, monkeypatch, audit=audit,
        context_report={'state': 'complete'},
        gate=dict(enabled=False, state='gated', classes={'contextual_restart': {'enabled': True}}),
        safe=lambda candidate, *args, **kwargs: dict(start=candidate['start'], end=candidate['end']),
    )
    assert calls == [1, 1]
    assert suggestion['auto'] is False


def test_validation_covers_prior_cut_overlap(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    edit['cuts'] = [dict(start=15.2, end=15.5, origin='human')]
    suggestion = dict(
        id='repair', kind='correction', start=2, end=3, auto=True,
        evidence=[], removed_word_ids=[transcript['segments'][0]['words'][0]['id']],
        reason='repair', evaluation={},
    )
    notes = {'repair': dict(action='remove', replacement_segment_ids=['s2'],
                            replacement_word_ids=[], replacement_proven=True)}
    _call(
        jobs, job, transcript, edit, [suggestion], notes, monkeypatch, evaluation=True,
        safe=lambda candidate, *args, **kwargs: dict(start=candidate['start'], end=candidate['end']),
    )
    assert suggestion['auto'] is False


def test_validation_covers_human_history_without_protection(jobs, ready, monkeypatch):
    job = ready(duration=20)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    edit = jobs.read(job['job_id'], 'edit.json')
    edit['history'] = [dict(action='cut')]
    suggestion = dict(id='plain', kind='silence', start=6, end=7, auto=True,
                      evidence={}, reason='plain', evaluation={})
    _call(jobs, job, transcript, edit, [suggestion], {}, monkeypatch)
    assert suggestion['auto'] is False
