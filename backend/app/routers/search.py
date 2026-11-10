"""Поиск по всем картам: где встречается шаг, где работает роль, где используется ИТ-система."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.store import get_store

router = APIRouter(prefix="/search", tags=["search"])

#: Сколько найденных шагов отдаёт один ответ; всего найдено — в ``total``.
LIMIT = 300


class StepHitOut(BaseModel):
    processId: str
    processName: str
    pageId: str
    pageName: str
    nodeId: str
    nodeName: str
    laneName: Optional[str] = None
    system: Optional[str] = None


class SearchResult(BaseModel):
    total: int
    hits: List[StepHitOut]


@router.get("/", response_model=SearchResult, summary="Шаги всех карт по названию, роли или ИТ-системе")
def search(
    q: str = Query(..., min_length=2, max_length=200, description="Что искать, без регистра"),
    field: Literal['step', 'role', 'system'] = Query('step', description="По названию шага, роли (дорожке) или ИТ-системе"),
):
    total, hits = get_store().search_steps(q.strip(), field, LIMIT)
    return SearchResult(total=total, hits=[StepHitOut(**hit.__dict__) for hit in hits])
