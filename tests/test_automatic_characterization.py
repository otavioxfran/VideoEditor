import copy
import json
import os
from pathlib import Path

from video_editor.automatic import Automatic, classify, safe_bounds
from video_editor.transcription import normalize

SNAPSHOT_DIR = Path(__file__).parent / 'fixtures' / 'automatic_snapshot'


def transcript(rows):
    return normalize([
        dict(
            id=f's{index + 1}', start=start, end=end, text=text,
            words=[dict(word=word, start=left, end=right, probability=0.99)
                   for word, left, right in words],
        )
        for index, (start, end, text, words) in enumerate(rows)
    ], {})


def _snapshot(name, value):
    path = SNAPSHOT_DIR / f'{name}.json'
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if os.getenv('UPDATE_AUTOMATIC_SNAPSHOTS') == '1':
        path.write_text(encoded, encoding='utf-8')
        return
    assert path.is_file(), f'Snapshot ausente: {path}. Rode com UPDATE_AUTOMATIC_SNAPSHOTS=1.'
    expected = json.loads(path.read_text(encoding='utf-8'))
    assert value == expected


def _clean(value):
    if isinstance(value, dict):
        return {
            key: _clean(item) for key, item in value.items()
            if key not in {
                'timings', 'created_at', 'updated_at', 'completed_at', 'at',
                'input_signature', 'input_edit_hash', 'transcript_hash',
                'source_hash', 'policy_hash',
            }
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def test_classify_characterization():
    tr = transcript([
        (1, 2.2, 'Primeira frase completa.', [
            ('Primeira', 1, 1.5), ('frase', 1.5, 1.8), ('completa', 1.8, 2.2),
        ]),
        (4, 5.2, 'Segunda frase completa.', [
            ('Segunda', 4, 4.5), ('frase', 4.5, 4.8), ('completa', 4.8, 5.2),
        ]),
    ])
    candidate = dict(id='p', kind='silence', start=2.4, end=3.9, auto=True, evidence={})
    _snapshot('classify', copy.deepcopy(classify(tr, [candidate], 8, 30)))


def test_safe_bounds_characterization():
    tr = transcript([
        (1, 2.2, 'Primeira frase completa.', [
            ('Primeira', 1, 1.5), ('frase', 1.5, 1.8), ('completa', 1.8, 2.2),
        ]),
    ])
    candidate = dict(start=1.0, end=2.2, evidence=['s1'])
    assert safe_bounds(candidate, tr, 8, 30) is not None
    _snapshot('safe_bounds', {'accepted': safe_bounds(candidate, tr, 8, 30)})


def test_automatic_run_characterization(jobs, ready, tmp_path):
    from video_editor.worker import Worker

    tr = transcript([
        (1, 2.2, 'Primeira frase completa.', [
            ('Primeira', 1, 1.5), ('frase', 1.5, 1.8), ('completa', 1.8, 2.2),
        ]),
        (4, 5.2, 'Segunda frase completa.', [
            ('Segunda', 4, 4.5), ('frase', 4.5, 4.8), ('completa', 4.8, 5.2),
        ]),
    ])
    job = ready(duration=8, segments=tr['segments'])
    job_id = job['job_id']
    jobs.write(job_id, 'suggestions.json', [dict(
        id='pause', kind='silence', start=2.4, end=3.9, auto=True,
        evidence={}, reason='Pausa de caracterização.',
    )])
    worker = Worker()
    service = Automatic(jobs, worker, None, None, tmp_path / 'provider.json')
    service.request(job_id, dict(send_text=False, export=False), 0)
    worker.queue.join()
    worker.close()
    status = service.status(job_id)
    report = jobs.read(job_id, 'automatic_report.json')
    edit = jobs.read(job_id, 'edit.json')
    _snapshot('automatic_run', _clean({'status': status, 'report': report, 'edit': edit}))
