import copy
import json
import pytest
from fastapi.testclient import TestClient
from video_editor.editorial import brief, facts, llm_client
from video_editor.server.app import create_app


def result():
    return dict(thesis='Uma tese atribuída.',summary='Resumo.',chapters=[], suggestions=[], titles=['Ideia'], risks=[],
                claims=[dict(id='c1',text='Afirmação',segment_ids=['s1'],quote='Primeira fala.',status='SUPPORTED')])


def test_import_cannot_cut_or_verify(jobs, ready):
    job = ready()
    app = create_app(jobs.root, jobs.source_dir, recover=False)
    with TestClient(app) as client:
        prefix = '/api/jobs/' + job['job_id']
        before = jobs.read(job['job_id'],'edit.json')
        response = client.post(prefix+'/editorial/import',json={'result':result()})
        assert response.status_code == 200
        assert response.json()['claims'][0]['status'] == 'NEEDS_VERIFICATION'
        assert response.json()['origin'] == 'imported'
        assert jobs.read(job['job_id'],'edit.json') == before
        bad = result();bad['claims'][0]['quote']='Trecho inexistente'
        assert client.post(prefix+'/editorial/import',json={'result':bad}).status_code == 422
        evidence = dict(expected_revision=0,claim_id='c1',status='SUPPORTED',url='https://example.com',excerpt='Evidência',human_confirmed=False)
        assert client.post(prefix+'/editorial/evidence',json=evidence).status_code == 422
        evidence['human_confirmed'] = True
        response = client.post(prefix+'/editorial/evidence',json=evidence)
        assert response.status_code == 200
        assert response.json()['claims'][0]['evidence'][0]['origin'] == 'manual'
        assert not response.json()['claims'][0]['evidence'][0]['fetched']
        assert client.post(prefix+'/editorial/evidence',json=evidence).status_code == 409


def test_external_send_requires_explicit_true(jobs, ready, monkeypatch):
    job = ready()
    sent = []
    def fake(path, prompt):
        assert isinstance(prompt,str) and 'transcript' in prompt
        sent.append(prompt)
        raise llm_client.ManualFallback('quota')
    monkeypatch.setattr(llm_client,'generate',fake)
    app = create_app(jobs.root,jobs.source_dir,recover=False)
    with TestClient(app) as client:
        prefix = '/api/jobs/' + job['job_id'] + '/editorial'
        assert client.post(prefix+'/assist',json={}).status_code == 422
        assert client.post(prefix+'/assist',json={'send_text':False}).status_code == 422
        assert not sent
        assert client.post(prefix+'/assist',json={'send_text':True}).status_code == 202
        app.state.worker.queue.join()
        assert len(sent) == 1
        assert client.get(prefix).json()['assistance']['state'] == 'manual'
        assert client.get('/api/jobs/'+job['job_id']+'/edit').status_code == 200
        assert client.get(prefix+'/prompt').status_code == 200


def test_no_environment_key_detection(tmp_path,monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','ambient-key-should-not-be-used')
    with pytest.raises(llm_client.ManualFallback):
        llm_client.generate(tmp_path/'missing.json','Only text')


def test_removed_claim_remains_in_brief_with_raw_reference(jobs,ready):
    job=ready();app=create_app(jobs.root,jobs.source_dir,recover=False)
    with TestClient(app) as client:
        prefix='/api/jobs/'+job['job_id']
        assert client.post(prefix+'/editorial/import',json={'result':result()}).status_code==200
        assert client.post(prefix+'/edit',json=dict(expected_revision=0,action='cut',start=1,end=2)).status_code==200
        claim=client.get(prefix+'/editorial').json()['brief']['claims'][0]
        assert claim['segment_ids']==['s1']
        assert claim['content_state']=='removed'
        assert claim['status']=='NEEDS_VERIFICATION'


def test_hallucinated_number_rejected(jobs,ready):
    job=ready(segments=[dict(start=1,end=2,text='O valor é 100 reais.',words=[])])
    value=result();value['claims'][0].update(text='O valor é 200 reais.',quote='O valor é 100 reais.')
    with pytest.raises(ValueError,match='Números'):
        brief.validate(value,jobs.read(job['job_id'],'transcript.json'),'imported')


def test_generated_titles_preserve_claims_and_require_brief(jobs,ready,monkeypatch):
    job=ready()
    received=[]
    def provider(path,prompt):
        received.append(prompt)
        return {'titles':['Ideia com escolha humana']}
    monkeypatch.setattr(llm_client,'generate',provider)
    app=create_app(jobs.root,jobs.source_dir,recover=False)
    with TestClient(app) as client:
        prefix='/api/jobs/'+job['job_id']+'/editorial'
        request={'send_text':True,'task':'titles'}
        assert client.post(prefix+'/assist',json=request).status_code==422
        assert client.post(prefix+'/import',json={'result':result()}).status_code==200
        before=client.get(prefix).json()['brief']['claims']
        assert client.post(prefix+'/assist',json=request).status_code==202
        app.state.worker.queue.join()
        current=client.get(prefix).json()['brief']
        assert current['claims']==before
        assert current['titles']==['Ideia com escolha humana']
        assert not current['titles_stale']
        assert len(received)==1


def test_delayed_llm_cannot_overwrite_human_import(jobs,ready,monkeypatch):
    import threading
    started,release=threading.Event(),threading.Event()
    def provider(path,prompt):
        started.set();assert release.wait(30)
        value=result();value['thesis']='Resultado atrasado'
        return value
    monkeypatch.setattr(llm_client,'generate',provider)
    job=ready();app=create_app(jobs.root,jobs.source_dir,recover=False)
    with TestClient(app) as client:
        prefix='/api/jobs/'+job['job_id']+'/editorial'
        assert client.post(prefix+'/assist',json={'send_text':True}).status_code==202
        assert started.wait(3)
        try:
            value=result();value['thesis']='Importação humana mais recente'
            assert client.post(prefix+'/import',json={'result':value}).status_code==200
        finally:release.set()
        app.state.worker.queue.join()
        assert client.get(prefix).json()['brief']['thesis']=='Importação humana mais recente'
