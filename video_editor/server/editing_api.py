from fastapi import APIRouter
from video_editor import editing
from video_editor.models import EditRequest, RevisionRequest
from video_editor.timeline import build_timeline


def router(jobs, exports):
    api = APIRouter(prefix='/api/jobs/{job_id}')

    @api.get('/edit')
    def get_edit(job_id: str):
        return editing.view(jobs, job_id)

    @api.get('/audition')
    def audition(job_id: str,suggestion_id: str):
        jobs.validate(job_id)
        current=editing.view(jobs,job_id)
        suggestion=next((s for s in current['suggestions'] if s['id']==suggestion_id),None)
        if suggestion is None:raise ValueError('Sugestão inexistente.')
        cuts=[*current['edit']['cuts'],suggestion]
        return dict(timeline=build_timeline(current['job']['media']['duration'],cuts,current['job']['media']['fps']),
                    start=suggestion['start'],end=suggestion['end'],revision=current['edit']['revision'])

    @api.post('/edit')
    def update_edit(job_id: str, request: EditRequest):
        return editing.mutate(jobs, job_id, request.model_dump())

    @api.post('/exports', status_code=202)
    def request_export(job_id: str, request: RevisionRequest):
        return exports.request(job_id, request.expected_revision)

    return api
