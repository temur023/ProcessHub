"""Preparation API: only source reading is available to document readers."""
from uuid import UUID

from fastapi import APIRouter, Request

from .preparation_store import cancel_preparation, get_preparation, request_preparation

router = APIRouter()


@router.get('/instruction-versions/{version_id}/preparation')
def preparation(version_id: UUID, request: Request):
    return get_preparation(request.app.state.store, request.state.actor, version_id)


@router.post('/instruction-versions/{version_id}/preparation', status_code=202)
def prepare(version_id: UUID, request: Request):
    return request_preparation(request.app.state.store, request.state.actor, version_id)


@router.post('/instruction-versions/{version_id}/preparation/cancel', status_code=202)
def cancel(version_id: UUID, request: Request):
    return cancel_preparation(request.app.state.store, request.state.actor, version_id)
