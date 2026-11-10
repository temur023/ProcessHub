"""Instruction trash: explicit action capability and mandatory reason."""
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .lifecycle_store import change_state, deleted_instructions

router = APIRouter()


class LifecycleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1,max_length=300)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        value = ' '.join(value.split())
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Укажите основание изменения инструкции')
        return value


@router.get('/departments/{department_id}/deleted-instructions')
def trash(department_id: UUID, request: Request):
    return deleted_instructions(request.app.state.store, request.state.actor, department_id)


@router.post('/instructions/{instruction_id}/delete')
def delete(instruction_id: UUID, data: LifecycleInput, request: Request):
    return change_state(request.app.state.store,request.state.actor,instruction_id,data.reason)


@router.post('/instructions/{instruction_id}/restore')
def restore(instruction_id: UUID, data: LifecycleInput, request: Request):
    return change_state(request.app.state.store,request.state.actor,instruction_id,data.reason,restore=True)
