from fastapi import APIRouter
from pydantic import BaseModel, Field

from video_editor import system


class ModelRequest(BaseModel):
    model: str = Field(default='', max_length=40)


def router():
    api = APIRouter(prefix='/api/system')

    @api.get('')
    def status(model: str = ''):
        return system.status(model or None)

    @api.post('/model', status_code=202)
    def download(request: ModelRequest):
        model = request.model or system.recommended_model()
        started = system.downloads.start(model)
        return dict(model=model, started=started, download=system.downloads.status(model))

    return api
