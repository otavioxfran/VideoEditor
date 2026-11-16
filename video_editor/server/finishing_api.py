from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from video_editor.finishing import suggest


class GenerateRequest(BaseModel):
    export_id: str | None = None


class DecideRequest(BaseModel):
    expected_revision: int
    item_id: str
    action: str
    payload: dict[str, Any] | None = None
    start: float | None = None
    end: float | None = None
    needs_text: bool | None = None


class PlanRequest(BaseModel):
    accept_all: bool = False


def router(jobs):
    api = APIRouter(prefix='/api/jobs/{job_id}/finishing')

    @api.get('/suggestions')
    def suggestions(job_id: str):
        return suggest.load_suggestions(jobs, job_id)

    @api.post('/suggestions/generate')
    def generate(job_id: str, request: GenerateRequest | None = None):
        return suggest.suggest(jobs, job_id, (request or GenerateRequest()).export_id)

    @api.post('/suggestions/decide')
    def decide(job_id: str, request: DecideRequest):
        return suggest.decide(jobs, job_id, request.model_dump())

    @api.post('/plan')
    def plan(job_id: str, request: PlanRequest | None = None):
        return suggest.build_plan(
            jobs, job_id, accept_all=(request or PlanRequest()).accept_all
        )

    return api
