import uuid
from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.process import (
    BusinessProcess, PixRegistryRecord, ProcessValidation
)
from app.services.canvas_cache import warm_up
from app.services.diagnostics import refresh_diagnostics
from app.services.process_stats import apply_costs
from app.services.auth import current_user
from app.services import catalog
from app.services.store import ProcessConflict, ProcessVersion, User, get_store, restore

router = APIRouter(prefix="/processes", tags=["processes"])


# ──────────────── Response schemas ────────────────

class ProcessSummary(BaseModel):
    id: str
    name: str
    fileName: str
    passportCode: str
    passportStatus: str
    nodeCount: int
    laneCount: int
    conformanceRate: float
    slaHours: float

class CreateCaseBody(BaseModel):
    caseId: str
    assignedTo: str
    data: dict


# ──────────────── Endpoints ────────────────

@router.get("/", response_model=List[ProcessSummary], summary="List all loaded processes")
def list_processes():
    # Список собирается из столбцов, без самих карт: читать ради него все
    # 96 МБ незачем.
    return [
        ProcessSummary(
            id=p.id,
            name=p.name,
            fileName=p.fileName,
            passportCode=p.passportCode,
            passportStatus=p.passportStatus,
            nodeCount=p.nodeCount,
            laneCount=p.laneCount,
            conformanceRate=p.conformanceRate,
            slaHours=p.slaHours,
        )
        for p in get_store().infos()
    ]


@router.get("/{process_id}", response_model=BusinessProcess, summary="Get full process by ID")
def get_process(process_id: str):
    proc = get_store().get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc


def refresh_checks(process: BusinessProcess) -> None:
    """Замечания карты и всех её страниц — по тому, что в модели сейчас.

    Сохраняется карта после правки шага, после смены реестра, шаблон при
    открытии. Список замечаний обязан описывать ровно то, что сохранено: шаблон
    раньше приезжал с пустым списком и назывался «импортированным без
    замечаний», хотя проверку не проходил ни разу.
    """
    for page in [process, *process.subProcesses, *process.variants]:
        page.validation = refresh_diagnostics(page)


@router.post(
    "/",
    response_model=BusinessProcess,
    status_code=201,
    summary="Save a parsed process",
    responses={409: {"description": "Карту уже сохранили по более свежей копии"}},
)
def save_process(process: BusinessProcess, user: User = Depends(current_user)):
    """Сохранение карты — новая ревизия и запись в истории версий.

    ``revision`` в теле — ревизия копии, которую правил клиент. Если на сервере
    уже другая, сохранение отклоняется (409): иначе правка, сделанная по
    устаревшей копии, молча стёрла бы чужие.
    """
    # Себестоимость шага — время × ставка его дорожки: поменяли время или
    # ставку на холсте — сохраняется уже пересчитанная.
    for page in [process, *process.subProcesses, *process.variants]:
        apply_costs(page.nodes, page.lanes)
    refresh_checks(process)
    if not process.id or not process.id.strip():
        process.id = f"proc_{uuid.uuid4().hex[:8]}"
    # Избегаем коллизии hardcoded sample id (proc-sqb-*) при повторном сохранении — генерируем уникальный id
    store = get_store()
    existing = store.info(process.id) if process.id.startswith("proc-sqb-") else None
    if existing:
        # Если это не тот же самый объект (разный passport.code или разное кол-во нод) — клонируем под новым id
        if existing.passportCode != process.passport.code or existing.nodeCount != len(process.nodes):
            process.id = f"proc_{uuid.uuid4().hex[:8]}"
    try:
        saved = store.save(process, 'edit', expected_revision=process.revision, author=user.id)
    except ProcessConflict as err:
        raise HTTPException(
            status_code=409,
            detail=f"Карту уже сохранили по более свежей копии (ревизия {err.revision}). "
                   "Обновите страницу: правка сделана по устаревшей копии и не сохранена.",
        )
    # Карту откроют сразу после сохранения — линии её страниц считаем заранее.
    warm_up(saved)
    return saved


@router.delete("/{process_id}", status_code=204, summary="Delete a process",
               responses={409: {"description": "Схема лежит в пространстве"}})
def delete_process(process_id: str):
    """Удаление схемы вместе с её историей — только схемы без пространства.

    Карта каталога ссылается на схему; удалённая из-под неё схема оставила бы
    карту с висячей ссылкой. Поэтому сначала карту убирают из пространства (схема
    уходит в «Без пространства»), и только потом удаляют саму схему.
    """
    item = catalog.holder(process_id)
    if item is not None:
        raise HTTPException(status_code=409, detail=f"Схема лежит в карте «{item.title}». "
                            "Сначала удалите карту из пространства — схема перейдёт в «Без пространства».")
    if not get_store().delete(process_id):
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")


@router.get("/{process_id}/validate", response_model=List[ProcessValidation], summary="Validate a process")
def validate_process(process_id: str):
    proc = get_store().get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc.validation


@router.post("/{process_id}/registry/cases", response_model=PixRegistryRecord, status_code=201, summary="Create a new registry case")
def create_case(process_id: str, body: CreateCaseBody, user: User = Depends(current_user)):
    store = get_store()
    proc = store.get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")

    # Выбираем первый исполняемый шаг (только задачи), исключаем lane/start/end/gateway
    first_step = next((n for n in proc.nodes if n.type in ('userTask', 'serviceTask', 'task')), None)
    if not first_step:
        first_step = next((n for n in proc.nodes if n.type not in ('lane', 'startEvent', 'endEvent', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')), None)
    record = PixRegistryRecord(
        id=f"rec-{uuid.uuid4().hex[:8]}",
        caseId=body.caseId,
        createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
        status='in_progress',
        currentStepId=first_step.id if first_step else 'step-1',
        currentStepName=first_step.name if first_step else 'Первичный шаг',
        assignedTo=body.assignedTo,
        elapsedMinutes=0,
        data=body.data
    )
    proc.registry.records.append(record)
    store.save(proc, 'registry', author=user.id)
    return record


@router.get("/{process_id}/registry/cases", response_model=List[PixRegistryRecord], summary="List all registry cases")
def list_cases(process_id: str):
    proc = get_store().get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc.registry.records



# ──────────────── История версий ────────────────

@router.get("/{process_id}/versions", response_model=List[ProcessVersion],
            summary="История версий карты, новые сверху")
def list_versions(process_id: str):
    store = get_store()
    if store.info(process_id) is None:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return store.versions(process_id)


@router.get("/{process_id}/versions/{version}", response_model=BusinessProcess,
            summary="Карта такой, какой она была в этой версии")
def get_version(process_id: str, version: int):
    proc = get_store().get_version(process_id, version)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Version {version} of '{process_id}' not found")
    return proc


@router.post("/{process_id}/versions/{version}/restore", response_model=BusinessProcess,
             summary="Вернуть карту к версии — новой версией, история не теряется")
def restore_version(process_id: str, version: int, user: User = Depends(current_user)):
    saved = restore(get_store(), process_id, version, author=user.id)
    if not saved:
        raise HTTPException(status_code=404, detail=f"Version {version} of '{process_id}' not found")
    warm_up(saved)
    return saved
