"""Personal review timing, independent of editorial policy and human acceptance."""
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter
from pydantic import Field, model_validator

from video_editor import storage
from video_editor.acceptance import policy_hash
from video_editor.editing import RevisionConflict, load_edit, require_revision
from video_editor.models import Request
from video_editor.timeline import build_timeline, digest
from typing import Literal


class TimingRequest(Request):
    expected_revision: int = Field(ge=0, strict=True)
    action: Literal['start', 'pause', 'resume', 'finish']
    session_id: str | None = None
    expected_session_revision: int | None = Field(default=None, ge=0, strict=True)
    manual_minutes: float | None = Field(default=None, gt=0, le=100000)
    manual_basis: Literal['estimated', 'measured'] = 'estimated'

    @model_validator(mode='after')
    def target(self):
        if self.action == 'start':
            if self.session_id is not None or self.expected_session_revision is not None:
                raise ValueError('Início não aceita uma sessão anterior.')
        elif not self.session_id or self.expected_session_revision is None:
            raise ValueError('Informe a sessão e sua versão atual.')
        elif self.manual_minutes is not None:
            raise ValueError('Informe o tempo manual ao iniciar a comparação.')
        return self


def elapsed(session, now):
    seconds = session['active_seconds']
    if session['state'] == 'running':
        seconds += max(0, (datetime.fromisoformat(now) - datetime.fromisoformat(session['running_since'])).total_seconds())
    return seconds


def comparison(session, job, edit):
    # Compare the union of actual frame-aligned ranges, including partial restores
    # and human reapplication. Suggestion IDs alone cannot describe retained content.
    current = build_timeline(job['media']['duration'], edit['cuts'], job['media']['fps'])
    intact = partial = restored = 0
    restored_seconds = 0.0
    for cut in session['initial_cuts']:
        length = cut['end'] - cut['start']
        covered = sum(max(0, min(b, cut['end']) - max(a, cut['start'])) for a, b in current['cuts'])
        missing = max(0, length - covered)
        restored_seconds += missing
        if missing < 1e-7:
            intact += 1
        elif covered < 1e-7:
            restored += 1
        else:
            partial += 1
    return dict(intact_cuts=intact, partially_restored=partial, fully_restored=restored,
                restored_seconds=restored_seconds, current_revision=edit['revision'])


def document(jobs, job_id):
    path = jobs.path(job_id, 'productivity.json')
    return storage.read(path) if path.exists() else dict(schema=1, sessions=[])


def status(jobs, job_id):
    jobs.validate(job_id)
    job, edit, _ = load_edit(jobs, job_id)
    data = document(jobs, job_id)
    if not data['sessions']:
        return dict(state='not_started', session=None, history=[])
    session = dict(data['sessions'][-1])
    session['elapsed_seconds'] = elapsed(session, storage.now())
    session['comparison'] = session.get('final_comparison') or comparison(session, job, edit)
    session['minutes_saved'] = (session['manual_minutes'] - session['elapsed_seconds'] / 60
                                if session['manual_minutes'] is not None else None)
    session['policy_changed'] = session['policy_hash'] != policy_hash()
    return dict(state=session['state'], session=session,
                history=[dict(id=s['id'], state=s['state'], created_at=s['created_at'],
                              active_seconds=s['active_seconds']) for s in data['sessions'][:-1]])


def transition(jobs, exports, job_id, request):
    with jobs.lock(job_id):
        jobs.validate(job_id)
        job, edit, transcript = load_edit(jobs, job_id)
        require_revision(edit, request['expected_revision'])
        data = document(jobs, job_id)
        now = storage.now()
        action = request['action']
        if action == 'start':
            if data['sessions'] and data['sessions'][-1]['state'] != 'finished':
                raise RevisionConflict('Já existe uma medição aberta. Pause ou conclua essa sessão.')
            if not jobs.path(job_id, 'automatic_report.json').exists() or not jobs.path(job_id, 'automatic.json').exists():
                raise ValueError('Prepare uma edição automática com MP4 antes de iniciar a medição.')
            report = jobs.read(job_id, 'automatic_report.json')
            state = jobs.read(job_id, 'automatic.json')
            if (state['state'] != 'complete' or report['revision'] != edit['revision']
                    or report.get('policy_hash') != policy_hash()
                    or report.get('transcript_hash') != digest(transcript)):
                raise ValueError('Inicie com uma edição automática concluída e seu relatório vigente.')
            output = exports.get(job_id, state['render_id'])
            if output['state'] != 'complete' or output['status'] != 'current':
                raise ValueError('A medição exige o MP4 concluído da edição atual.')
            session = dict(id=uuid4().hex, revision=0, state='running', created_at=now,
                           running_since=now, active_seconds=0.0, initial_revision=edit['revision'],
                           source=job['source'], transcript_hash=digest(transcript),
                           policy_hash=report['policy_hash'], report_hash=digest(report),
                           initial_cuts=report.get('applied_cuts', []),
                           render_id=output['render_id'], render_identity=output['identity'],
                           manual_minutes=request.get('manual_minutes'),
                           manual_basis=request.get('manual_basis', 'estimated'),
                           role='self_reported_timing')
            data['sessions'].append(session)
        else:
            if not data['sessions']:
                raise ValueError('Inicie uma medição primeiro.')
            session = data['sessions'][-1]
            if session['id'] != request['session_id'] or session['revision'] != request['expected_session_revision']:
                raise RevisionConflict('A medição mudou em outra aba. Recarregue antes de continuar.')
            if session['transcript_hash'] != digest(transcript) or session['source'] != job['source']:
                raise ValueError('A fonte ou a transcrição mudou; esta comparação não é válida.')
            if session['state'] == 'finished':
                raise RevisionConflict('Esta medição já foi concluída.')
            if action == 'pause' and session['state'] != 'running':
                raise RevisionConflict('O cronômetro já está pausado.')
            if action == 'resume' and session['state'] != 'paused':
                raise RevisionConflict('O cronômetro já está contando.')
            session['active_seconds'] = elapsed(session, now)
            session['running_since'] = now if action == 'resume' else None
            session['state'] = {'pause': 'paused', 'resume': 'running', 'finish': 'finished'}[action]
            session['revision'] += 1
            if action == 'finish':
                session['finished_at'] = now
                session['final_comparison'] = comparison(session, job, edit)
        jobs.write(job_id, 'productivity.json', data)
    return status(jobs, job_id)


def router(jobs, exports):
    api = APIRouter(prefix='/api/jobs/{job_id}/productivity')

    @api.get('')
    def get_timing(job_id: str):
        return status(jobs, job_id)

    @api.post('')
    def update_timing(job_id: str, request: TimingRequest):
        return transition(jobs, exports, job_id, request.model_dump())

    return api
