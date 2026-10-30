import pytest
from fastapi.testclient import TestClient

from video_editor import storage
from video_editor.editorial import anti_prolixity, llm_client
from video_editor.server.app import create_app
from video_editor.transcription import normalize


def _segments():
    texts = ['O Brasil não cresce há 40 anos.', 'Coisa que ninguém nunca te contou no Instagram.',
             'Que você só vê bosta o dia inteiro.', 'A produtividade está parada desde 1980.',
             'Minha mãe é de 1980.']
    result, t = [], 1.0
    for text in texts:
        words = []
        for token in text.split():
            words.append(dict(word=token, start=round(t, 2), end=round(t + .3, 2)))
            t += .35
        result.append(dict(start=words[0]['start'], end=words[-1]['end'], text=text, words=words))
        t += 1.5
    return result


def test_validate_anchors_to_word_bounds_and_rejects_bad_references():
    transcript = normalize(_segments(), {})
    segments = transcript['segments']
    raw = dict(issues=[
        dict(first_id='s2', last_id='s3', type='padding', title='Promessa vazia', why='Rodeio.', confidence=.9),
        dict(first_id='s9', last_id='s9', type='padding', confidence=.9),        # unknown id
        dict(first_id='s3', last_id='s2', type='digression', confidence=.9),     # reversed
        dict(first_id='s4', last_id='s4', type='hype', confidence=.9),           # unknown type
        dict(first_id='s3', last_id='s4', type='digression', confidence=.2),     # overlaps a better one
        dict(first_id='s5', last_id='s5', type='digression', risk='Pode ser piada.', confidence=.7),
    ])
    issues = anti_prolixity.validate(raw, segments, 60)
    assert [i['id'] for i in issues] == ['prolix-s2-s3', 'prolix-s5-s5']
    first = issues[0]
    words = [w for s in segments for w in s['words']]
    # Starts just before the first removed word, ends before the next kept sentence starts.
    assert first['start'] >= segments[0]['end'] and first['start'] <= segments[1]['words'][0]['start']
    assert first['end'] < segments[3]['words'][0]['start']
    assert not any(w['start'] < first['end'] and w['end'] > first['start'] for w in segments[3]['words'])
    assert all(w['end'] <= first['end'] for w in words if w['id'].startswith(('s2-', 's3-')))
    assert 'piada' in issues[1]['risk'] and 'números' in issues[1]['risk']


def test_suggestions_never_exceed_a_share_of_the_video():
    transcript = normalize(_segments(), {})
    raw = dict(issues=[dict(first_id='s1', last_id='s5', type='digression', confidence=.9)])
    assert anti_prolixity.validate(raw, transcript['segments'], 30) == []


def test_api_reads_cache_on_get_and_calls_provider_only_on_post(jobs, ready, tmp_path, monkeypatch):
    job = ready(duration=30, segments=_segments())
    calls = []

    def generate(path, text):
        calls.append(text)
        return dict(issues=[dict(first_id='s2', last_id='s3', type='padding', confidence=.8)])

    monkeypatch.setattr(llm_client, 'generate', generate)
    config = tmp_path / 'provider.json'
    storage.write(config, dict(enabled=True, provider='gemini-free', api_key='x', free_tier_confirmed=True,
                               model='gemini-3.5-flash'))
    app = create_app(jobs.root, jobs.source_dir, recover=False, editorial_config=config)
    url = f"/api/jobs/{job['job_id']}/editorial/anti-prolixity"
    with TestClient(app) as client:
        assert client.get(url).json()['state'] == 'not_analyzed' and not calls
        first = client.post(url).json()
        assert first['state'] == 'complete' and first['count'] == 1 and len(calls) == 1
        assert 'PERSONA' in calls[0] and '[s2]' in calls[0]
        assert client.get(url).json()['issues'] == first['issues']
        client.post(url)
        assert len(calls) == 1  # cached per transcript: no second provider call


def test_without_provider_only_measured_repetition_is_reported(jobs, ready, tmp_path, monkeypatch):
    def sentence(text, start):
        words = [dict(word=w, start=round(start + i * .3, 2), end=round(start + .25 + i * .3, 2))
                 for i, w in enumerate(text.split())]
        return dict(start=words[0]['start'], end=words[-1]['end'], text=text, words=words)

    segments = [sentence('São Bernardo, Santo André, Diadema e Mauá.', 1),
                sentence('São Bernardo, Santo André, Diadema', 5)]
    job = ready(duration=10, segments=segments)

    def unavailable(*args):
        raise llm_client.ManualFallback('sem provider')

    monkeypatch.setattr(llm_client, 'generate', unavailable)
    transcript = jobs.read(job['job_id'], 'transcript.json')
    value = anti_prolixity.analyze(jobs, job['job_id'], transcript, 10, tmp_path / 'none.json')
    assert value['state'] == 'local_only'
    assert [i['segment_ids'] for i in value['issues']] == [['s2']]
    assert value['issues'][0]['origin'] == 'local'


def test_validate_rejects_malformed_payload():
    with pytest.raises(ValueError):
        anti_prolixity.validate(['not', 'a', 'dict'], [], 10)
