from urllib.parse import quote
from uuid import uuid4
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from video_editor import storage
from video_editor.editing import view, RevisionConflict
from video_editor.editorial import brief, facts, prompts, llm_client, titles
from video_editor.models import ImportBrief, LLMRequest, EvidenceRequest
from video_editor.timeline import digest, project_interval


def router(jobs, worker, config_path):
    api = APIRouter(prefix='/api/jobs/{job_id}/editorial')

    def packet(job_id, task='brief', current=None):
        current = current or view(jobs, job_id)
        path = jobs.path(job_id, 'editorial/brief.json')
        previous = storage.read(path) if path.exists() else None
        return prompts.package(current['transcript'], current['timeline'], task, previous, current['suggestions'])

    def import_result(job_id, result, origin, expected_revision):
        with jobs.lock(job_id):
            jobs.validate(job_id)
            value = brief.validate(result, jobs.read(job_id, 'transcript.json'), origin)
            previous = jobs.path(job_id, 'editorial/brief.json')
            actual_revision=storage.read(previous)['revision'] if previous.exists() else -1
            if expected_revision != actual_revision:
                raise RevisionConflict('Briefing mudou durante a análise; importe novamente após revisar.')
            if previous.exists():
                existing = storage.read(previous)
                value['revision'] = existing['revision'] + 1
                jobs.write(job_id, 'editorial/history/' + uuid4().hex + '.json', existing)
            jobs.write(job_id, 'editorial/brief.json', value)
            return value

    @api.get('/prompt', response_class=PlainTextResponse)
    def get_prompt(job_id: str):
        return PlainTextResponse(packet(job_id), headers={'Content-Disposition': 'attachment; filename="prompt.txt"'})

    @api.get('')
    def get_editorial(job_id: str):
        current = view(jobs, job_id)
        path = jobs.path(job_id, 'editorial/brief.json')
        value = storage.read(path) if path.exists() else None
        if value is not None:
            value['stale'] = value['transcript_hash'] != digest(current['transcript'])
            segments={s['id']:s for s in current['transcript']['segments']}
            for field in ('claims','chapters','suggestions','risks'):
                for item in value[field]:
                    references=[segments[s] for s in item['segment_ids'] if s in segments]
                    original=sum(s['end']-s['start'] for s in references)
                    kept=sum(b-a for s in references for a,b in project_interval(current['timeline'],s['start'],s['end']))
                    item['content_state']='removed' if not kept else 'kept' if abs(kept-original)<1e-6 else 'partially_removed'
        status_path = jobs.path(job_id, 'editorial/status.json')
        status = storage.read(status_path) if status_path.exists() else {'state': 'manual'}
        notes_path = jobs.path(job_id, 'editorial/retakes.json')
        notes = storage.read(notes_path) if notes_path.exists() else []
        if value:
            value['titles_stale'] = bool(value['titles']) and value.get('title_context_hash') != digest(current['timeline'])
        return dict(brief=value, local_signals=prompts.local_signals(current['transcript']), assistance=status, retakes=notes)

    @api.post('/import')
    def import_brief(job_id: str, request: ImportBrief):
        value=import_result(job_id, request.result, 'imported',request.expected_revision)
        jobs.write(job_id,'editorial/status.json',dict(state='imported'))
        return value

    def assist(job_id, prompt, task, context_hash, brief_revision):
        try:
            result = llm_client.generate(config_path, prompt)
            if task == 'brief':
                value = import_result(job_id, result, 'gemini-free',brief_revision)
            else:
                with jobs.lock(job_id):
                    if task == 'titles':
                        value = jobs.read(job_id, 'editorial/brief.json')
                        from video_editor.editing import require_revision
                        require_revision(value,brief_revision)
                        value.update(titles=titles.validate(result),title_context_hash=context_hash,
                                     revision=value['revision']+1)
                        jobs.write(job_id,'editorial/brief.json',value)
                    else:
                        known = {s['id'] for s in jobs.read(job_id,'suggestions.json')}
                        notes = result.get('retakes') if isinstance(result,dict) else None
                        if not isinstance(notes,list) or len(notes)>20:
                            raise ValueError('Notas de retake inválidas.')
                        if not all(isinstance(n,dict) and n.get('suggestion_id') in known and
                                   isinstance(n.get('reason'),str) and len(n['reason'])<=5000 for n in notes):
                            raise ValueError('Referência de retake inválida.')
                        value = [dict(suggestion_id=n['suggestion_id'],reason=n['reason']) for n in notes]
                        jobs.write(job_id,'editorial/retakes.json',value)
            jobs.write(job_id, 'editorial/status.json', {'state': 'complete'})
            return value
        except Exception:
            jobs.write(job_id, 'editorial/status.json', dict(state='manual', error='Assistência indisponível ou inválida. Copie o prompt local.'))
            return None

    @api.post('/assist', status_code=202)
    def request_assist(job_id: str, request: LLMRequest):
        with jobs.lock(job_id):
            current = view(jobs,job_id)
            prompt = packet(job_id, request.task, current)
            previous = jobs.path(job_id,'editorial/brief.json')
            revision = storage.read(previous)['revision'] if previous.exists() else -1
            status_path = jobs.path(job_id, 'editorial/status.json')
            if status_path.exists() and storage.read(status_path).get('state') == 'queued':
                from fastapi import HTTPException
                raise HTTPException(409, 'Assistência já em andamento.')
            jobs.write(job_id, 'editorial/status.json', dict(state='queued'))
            storage.write_text(jobs.path(job_id, 'editorial/prompt.md'), prompt)
        try:
            worker.submit(assist, job_id, prompt, request.task, digest(current['timeline']), revision)
        except Exception:
            jobs.write(job_id, 'editorial/status.json', {'state': 'manual', 'error': 'Fila cheia. Copie o prompt.'})
            raise
        return dict(state='queued', message='Somente texto será enviado ao provider configurado.')

    @api.post('/evidence')
    def evidence(job_id: str, request: EvidenceRequest):
        with jobs.lock(job_id):
            jobs.validate(job_id)
            value = jobs.read(job_id, 'editorial/brief.json')
            if value['transcript_hash'] != digest(jobs.read(job_id, 'transcript.json')):
                raise ValueError('Briefing desatualizado.')
            value = facts.record(value, request.model_dump())
            jobs.write(job_id, 'editorial/brief.json', value)
            return value

    @api.get('/claims/{claim_id}/search')
    def search(job_id: str, claim_id: str):
        value = jobs.read(job_id, 'editorial/brief.json')
        claim = next((c for c in value['claims'] if c['id'] == claim_id), None)
        if claim is None:
            raise ValueError('Claim inexistente.')
        return {'url': 'https://www.google.com/search?q=' + quote(claim['text'])}

    @api.get('/anti-prolixity')
    def get_anti_prolixity(job_id: str):
        # Reads only: no provider call and no write.
        from video_editor.editorial import anti_prolixity
        current = view(jobs, job_id)
        value = anti_prolixity.cached(jobs, job_id, current['transcript'], config_path)
        if value is None:
            return dict(state='not_analyzed', issues=[], count=0)
        return dict(value, count=len(value['issues']))

    @api.post('/anti-prolixity')
    def analyze_anti_prolixity(job_id: str):
        from video_editor.editorial import anti_prolixity
        jobs.validate(job_id)
        current = view(jobs, job_id)
        # No job lock around the provider call: editing stays available meanwhile, and
        # the cache is an atomic write keyed by transcript.
        value = anti_prolixity.analyze(jobs, job_id, current['transcript'],
                                       current['job']['media']['duration'], config_path)
        return dict(value, count=len(value['issues']))

    return api
