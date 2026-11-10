"""Analysis API: start, follow progress, read the report, record expert decisions."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator

from . import analysis_store
from .document_models import StrictInput

router = APIRouter()


class StartInput(StrictInput):
    edition_id: UUID


class ReviewInput(StrictInput):
    finding_id: str = Field(pattern=r'^[FM][0-9]{1,4}$')
    decision: Literal['confirmed', 'rejected', 'clarify']
    comment: str = Field(default='', max_length=2000)

    @field_validator('comment')
    @classmethod
    def trimmed(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def reason_required(self):
        if self.decision != 'confirmed' and not self.comment:
            raise ValueError('Укажите основание: почему замечание отклонено или что нужно уточнить')
        return self


class BindingInput(StrictInput):
    document: str | None = Field(default=None, max_length=500)

    @field_validator('document')
    @classmethod
    def trimmed(cls, value):
        return value.strip() or None if value is not None else None


@router.get('/nova/status')
def nova_status(request: Request):
    return analysis_store.nova_status(request.app.state.settings)


@router.get('/departments/{department_id}/analysis-registry')
def registry(department_id: UUID, request: Request):
    return analysis_store.department_registry(request.app.state.store, request.state.actor, department_id)


@router.get('/library/overview')
def overview(request: Request):
    return analysis_store.library_overview(request.app.state.store, request.state.actor)


@router.get('/documents/{document_id}/analysis-runs')
def runs(document_id: UUID, request: Request):
    return analysis_store.list_runs(request.app.state.store, request.state.actor, document_id)


@router.post('/documents/{document_id}/analysis-runs', status_code=202)
def start(document_id: UUID, data: StartInput, request: Request):
    return analysis_store.start_run(request.app.state.store, request.app.state.settings, request.state.actor,
                                    document_id, data.edition_id)


@router.get('/analysis-runs/{run_id}')
def run(run_id: UUID, request: Request):
    return analysis_store.get_run(request.app.state.store, request.state.actor, run_id)


@router.post('/analysis-runs/{run_id}/cancel')
def cancel(run_id: UUID, request: Request):
    return analysis_store.cancel_run(request.app.state.store, request.state.actor, run_id)


@router.post('/analysis-runs/{run_id}/reviews')
def review(run_id: UUID, data: ReviewInput, request: Request):
    return analysis_store.review_finding(request.app.state.store, request.state.actor, run_id,
                                         data.finding_id, data.decision, data.comment)


@router.patch('/departments/{department_id}/nova-binding')
def bind(department_id: UUID, data: BindingInput, request: Request):
    return analysis_store.bind_regulation(request.app.state.store, request.state.actor, department_id, data.document)
