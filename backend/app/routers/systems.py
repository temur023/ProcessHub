"""Справочник ИТ-систем: какие системы подписаны у шагов карт и сколько шагов в них работает."""
from collections import Counter
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.it_systems import canonical
from app.services.store import get_store

router = APIRouter(prefix="/systems", tags=["systems"])


class SystemUsage(BaseModel):
    name: str
    #: Шагов всех карт, у которых эта система подписана (в том числе среди нескольких).
    steps: int


@router.get("/", response_model=List[SystemUsage], summary="ИТ-системы шагов всех карт — в написании справочника")
def list_systems():
    counts: Counter = Counter()
    for text, steps in get_store().system_mentions():
        for name in (canonical(text) or '').split(', '):
            if name:
                counts[name] += steps
    return [SystemUsage(name=name, steps=steps)
            for name, steps in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
