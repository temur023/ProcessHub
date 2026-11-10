"""Роли — дорожки карт: где они работают и по какой ставке часа считаются."""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.services import role_rates
from app.services.auth import current_user
from app.services.process_stats import DEFAULT_HOURLY_RATE
from app.services.store import User, get_store, role_key

router = APIRouter(prefix="/roles", tags=["roles"])


class Role(BaseModel):
    name: str
    #: Карт и шагов с этой дорожкой: основные страницы и части, без страниц-вариантов и без карт TO-BE.
    maps: int
    steps: int
    #: Работы роли по картам, мин: сумма времени её шагов, один проход каждой карты.
    workMinutes: int
    #: Ставка роли, сум/ч; нет — считается по ставке по умолчанию.
    rate: Optional[int] = None


class Roles(BaseModel):
    defaultRate: int
    roles: List[Role]


class RateBody(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    #: Сум/ч; ``null`` — убрать ставку, роль снова по ставке по умолчанию.
    rate: Optional[int] = Field(default=None, ge=0, le=100_000_000)


@router.get("/", response_model=Roles, summary="Роли всех карт и их ставки часа")
def list_roles():
    rates = role_rates.rates()
    roles = [Role(name=u.name, maps=u.maps, steps=u.steps, workMinutes=u.workMinutes, rate=rates.get(role_key(u.name)))
             for u in get_store().role_usage()]
    roles.sort(key=lambda r: (-r.steps, r.name.lower()))
    return Roles(defaultRate=DEFAULT_HOURLY_RATE, roles=roles)


@router.put("/rate", status_code=204, summary="Ставка часа роли — для всех карт")
def set_rate(body: RateBody, user: User = Depends(current_user)):
    role_rates.set_rate(body.name.strip(), body.rate, user.id)
