"""Global administration of named access roles and their Hub-user members."""
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from . import rbac_store
from .permissions import CAPABILITIES, normalize

router = APIRouter()


def actor(request: Request):
    return request.state.actor


def store(request: Request):
    return request.app.state.store


class AccessRoleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=300)
    permission: Literal['reader','owner','reviewer']
    capabilities: list[str] | None = Field(default=None,max_length=len(CAPABILITIES))
    department_ids: list[UUID] = Field(default_factory=list,max_length=500)
    reason: str = Field(min_length=1,max_length=300)

    @field_validator('capabilities')
    @classmethod
    def valid_capabilities(cls, value):
        # None retains the legacy preset; an explicit list is authoritative.
        return normalize(value, 'reader') if value is not None else None

    @field_validator('name')
    @classmethod
    def name_text(cls, value):
        value = ' '.join(unicodedata.normalize('NFKC',value).split())
        if not value or len(value)>300 or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Укажите название роли доступа до 300 символов')
        return value

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        value = ' '.join(value.split())
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Укажите основание изменения роли доступа')
        return value

    @field_validator('department_ids')
    @classmethod
    def unique_departments(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Папки департаментов не должны повторяться')
        return value


class AccessRoleMemberInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role_id: UUID
    user_id: int = Field(gt=0,strict=True)
    enabled: StrictBool
    reason: str = Field(min_length=1,max_length=300)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        value = ' '.join(value.split())
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Укажите основание назначения или отзыва роли доступа')
        return value


class AccessRoleUpdateInput(AccessRoleInput):
    expected_updated_at: datetime

    @field_validator('expected_updated_at')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError('Передайте время последнего изменения роли с часовым поясом')
        return value


@router.get('/access-roles')
def access_roles(current=Depends(actor),db=Depends(store)):
    return rbac_store.roles(db,current)


@router.post('/access-roles',status_code=201)
def create_access_role(data:AccessRoleInput,current=Depends(actor),db=Depends(store)):
    return rbac_store.create_role(db,current,data)


@router.patch('/access-roles/{role_id}')
def update_access_role(role_id:UUID,data:AccessRoleUpdateInput,current=Depends(actor),db=Depends(store)):
    return rbac_store.update_role(db,current,role_id,data)


@router.get('/access-role-memberships')
def access_role_memberships(current=Depends(actor),db=Depends(store)):
    return rbac_store.memberships(db,current)


@router.post('/access-role-memberships')
def change_access_role_membership(data:AccessRoleMemberInput,current=Depends(actor),db=Depends(store)):
    return rbac_store.change_membership(db,current,data)


@router.get('/access-role-audit')
def access_role_audit(current=Depends(actor),db=Depends(store)):
    return rbac_store.audit(db,current)
