"""Multipart workflow: an instruction is never saved without its first file."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from .files import read_original, validate_effective_dates
from .registry_store import create_instruction_with_version, upload_instruction_version

router = APIRouter()


def _actor(request: Request):
    return request.state.actor


def _store(request: Request):
    return request.app.state.store


@router.post('/instructions/upload', status_code=201)
async def upload_instruction(request: Request, department_id: UUID = Form(), role_id: UUID | None = Form(None),
                             title: str = Form(min_length=1, max_length=300), file: UploadFile = File(),
                             effective_from: date | None = Form(None), effective_to: date | None = Form(None),
                             upload_id: UUID | None = Form(None),
                             actor=Depends(_actor), db=Depends(_store)):
    # Reject inaccessible departments and invalid role links before inspecting
    # a document; check permissions again in the write transaction.
    with db.pool.connection() as conn:
        db.authorize(conn, actor, department_id, capability='instructions.create')
        if role_id is not None and not conn.execute('SELECT 1 FROM roles WHERE id=%s AND department_id=%s',
                            (role_id, department_id)).fetchone():
            raise HTTPException(422, 'Роль не принадлежит выбранному департаменту')
    title = ' '.join(title.split())
    if not title:
        raise HTTPException(422, 'Укажите название инструкции')
    validate_effective_dates(effective_from, effective_to)
    original = await read_original(file, request.app.state.settings.max_file_bytes)
    # Waiting for another writer's transaction must not block the API loop.
    return await run_in_threadpool(create_instruction_with_version, db, actor, department_id, role_id, title,
                                  **original, effective_from=effective_from, effective_to=effective_to,
                                  upload_id=upload_id)


@router.post('/instructions/{instruction_id}/versions', status_code=201)
async def upload_version(request: Request, instruction_id: UUID, file: UploadFile = File(),
                         effective_from: date | None = Form(None), effective_to: date | None = Form(None),
                         upload_id: UUID | None = Form(None), actor=Depends(_actor), db=Depends(_store)):
    with db.pool.connection() as conn:
        db.instruction(conn, actor, instruction_id, capability='versions.create')
    validate_effective_dates(effective_from, effective_to)
    original = await read_original(file, request.app.state.settings.max_file_bytes)
    return await run_in_threadpool(upload_instruction_version, db, actor, instruction_id, **original,
                                  effective_from=effective_from, effective_to=effective_to, upload_id=upload_id)
