"""Small request contracts. Persisted documents are explicit versioned JSON."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class PrepareConfig(Request):
    # large-v3-turbo: distilled large-v3 accuracy at small-model speed. large-v3 is the
    # slowest/most accurate option and needs ~5.4 GiB peak on a 6 GiB card.
    model: Literal['tiny', 'base', 'small', 'medium', 'large-v3-turbo', 'large-v3'] = 'large-v3-turbo'
    device: Literal['cuda', 'cpu'] = 'cuda'
    compute_type: Literal['int8_float16', 'int8', 'float16', 'float32'] = 'int8_float16'
    language: str = Field(default='pt', min_length=2, max_length=10)
    cpu_fallback: bool = True
    batch_size: Literal[1, 2, 4, 8] = 1
    refine_uncertain: bool = False


class AutomaticOptions(Request):
    send_text: bool = Field(default=False, strict=True)
    export: bool = True
    mode: Literal['standard', 'evaluation'] = 'standard'


class AutomaticJob(Request):
    source_path: str = Field(min_length=1)
    config: PrepareConfig = Field(default_factory=PrepareConfig)
    automatic: AutomaticOptions = Field(default_factory=AutomaticOptions)


class CreateJob(Request):
    source_path: str = Field(min_length=1)
    config: PrepareConfig = Field(default_factory=PrepareConfig)


class EditRequest(Request):
    expected_revision: int = Field(ge=0, strict=True)
    action: Literal['cut', 'restore', 'protect', 'marker', 'delete_marker', 'undo']
    suggestion_id: str | None = None
    segment_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)
    source_time: float | None = Field(default=None, ge=0)
    marker_id: str | None = None
    label: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def single_target(self):
        if self.action in ('cut', 'restore', 'protect'):
            if self.marker_id is not None or self.source_time is not None:
                raise ValueError('Operação de intervalo não aceita marcador.')
            if self.segment_ids:
                if self.suggestion_id or self.start is not None or self.end is not None:
                    raise ValueError('Informe segmentos, sugestão ou intervalo.')
            elif self.suggestion_id:
                if self.start is not None or self.end is not None:
                    raise ValueError('Informe sugestão ou intervalo, não ambos.')
            elif self.start is None or self.end is None:
                raise ValueError('Intervalo obrigatório.')
        elif any(x is not None for x in (self.start, self.end, self.suggestion_id, self.segment_ids)):
            raise ValueError('Operação não aceita intervalo.')
        if self.action == 'marker' and (self.source_time is None or self.marker_id is not None):
            raise ValueError('Marcador exige apenas tempo na origem.')
        if self.action == 'delete_marker' and (not self.marker_id or self.source_time is not None):
            raise ValueError('Excluir marcador exige ID.')
        if self.action == 'undo' and (self.source_time is not None or self.marker_id is not None):
            raise ValueError('Desfazer não aceita alvo.')
        return self


class RevisionRequest(Request):
    expected_revision: int = Field(ge=0, strict=True)


class EvaluationRequest(RevisionRequest):
    send_text: bool = Field(default=False, strict=True)


class AcceptanceRequest(RevisionRequest):
    labels: dict[str, Literal['correct', 'keep', 'uncertain']]
    watched_complete: Literal[True]
    approved: bool = Field(strict=True)
    severe_errors: int = Field(ge=0,strict=True)
    naturalness: int = Field(ge=1,le=5,strict=True)
    missed_errors: int = Field(ge=0,strict=True)
    correction_minutes: float = Field(ge=0)

    @model_validator(mode='before')
    @classmethod
    def actual_review(cls,value):
        if isinstance(value,dict) and value.get('watched_complete') is not True:
            raise ValueError('Confirmação audiovisual explícita obrigatória.')
        return value


class ImportBrief(Request):
    expected_revision: int = Field(default=-1, ge=-1, strict=True)
    result: dict


class LLMRequest(Request):
    send_text: Literal[True]
    task: Literal['brief', 'titles', 'retakes'] = 'brief'

    @model_validator(mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if not isinstance(value, dict) or value.get('send_text') is not True:
            raise ValueError('Envio externo exige consentimento explícito verdadeiro.')
        return value


class EvidenceRequest(Request):
    expected_revision: int = Field(ge=0, strict=True)
    claim_id: str
    status: Literal['NEEDS_VERIFICATION', 'SUPPORTED', 'CONTRADICTED', 'INCONCLUSIVE', 'NOT_CHECKABLE']
    url: str = Field(default='', max_length=2000)
    excerpt: str = Field(default='', max_length=3000)
    human_confirmed: bool = False
