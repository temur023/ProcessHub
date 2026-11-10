"""Bounded multipart intake of complete editions and existing file workflows."""
import json
from datetime import date
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import document_store
from .document_models import (DOCUMENT_TYPES, MAX_BUNDLE_BYTES, MAX_BUNDLE_FILES, ApplicabilityInput,
    DocumentInput, DocumentPatch, DocumentType, EditionInput, FileMetadata, VerificationInput, EndValidityInput)
from .files import read_original
from .preview import preview_pdf
from .lifecycle_routes import LifecycleInput
from .lifecycle_store import change_state
from .preparation_store import cancel_preparation, get_preparation, request_preparation

router=APIRouter()


def _json(value,expected):
    try:
        if len(value.encode())>768*1024:
            raise ValueError()
        parsed=json.loads(value,parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(parsed,expected):
            raise ValueError()
        return parsed
    except (ValueError,RecursionError):
        raise HTTPException(422,'Проверьте структуру реквизитов и применимости') from None


def _model(model,**kwargs):
    try:
        return model.model_validate(kwargs)
    except (ValidationError,ValueError):
        raise HTTPException(422,'Проверьте обязательные реквизиты, тип документа и основание применимости') from None


async def _bundle(request,files,file_metadata):
    try:
        if not 1<=len(files)<=MAX_BUNDLE_FILES:
            raise HTTPException(422,'Редакция должна содержать от 1 до 10 файлов')
        raw=_json(file_metadata,list) if file_metadata else [
            {'purpose':'main' if i==0 else 'attachment'} for i in range(len(files))]
        if len(raw)!=len(files) or any(not isinstance(item,dict) for item in raw):
            raise HTTPException(422,'Для каждого файла укажите его назначение и происхождение')
        meta=[_model(FileMetadata,**item) for item in raw]
        if sum(item.purpose=='main' for item in meta)!=1:
            raise HTTPException(422,'В комплекте должен быть один основной файл')
        originals=[]
        total=0
        signatures=set()
        for file in files:
            original=await read_original(file,min(request.app.state.settings.max_file_bytes,10*1024*1024))
            total+=len(original['content'])
            if total>MAX_BUNDLE_BYTES:
                raise HTTPException(413,'Суммарный размер файлов не должен превышать 40 МБ')
            signature=(original['filename'],original['content_hash'])
            if signature in signatures:
                raise HTTPException(422,'Один и тот же файл повторён внутри редакции')
            signatures.add(signature)
            originals.append(original)
        return originals,meta
    finally:
        for file in files:
            await file.close()


@router.get('/documents/types')
def document_types():
    return [{'id':id,'label':label,'category':category} for id,label,category in DOCUMENT_TYPES]


@router.get('/documents')
def documents(request:Request,library_scope:Literal['department','bank']='department',
              department_id:UUID|None=None,document_type:DocumentType|None=None,
              view:Literal['all','current','unverified','historical','analytics','trash']='all'):
    return document_store.list_documents(request.app.state.store,request.state.actor,library_scope,
                                         department_id,document_type,view)


@router.post('/documents/upload',status_code=201)
async def upload_document(request:Request,title:str=Form(min_length=1,max_length=300),
        document_type:DocumentType=Form('other'),library_scope:Literal['department','bank']=Form('department'),
        department_id:UUID|None=Form(None),owner_department_id:UUID|None=Form(None),role_id:UUID|None=Form(None),
        bank_number:str|None=Form(None,max_length=100),document_date:date|None=Form(None),
        metadata:str=Form('{}'),applicability:str=Form('[]'),edition_label:str|None=Form(None),
        edition_metadata:str=Form('{}'),file_metadata:str|None=Form(None),new_position:str|None=Form(None),
        upload_id:UUID=Form(),files:list[UploadFile]=File()):
    data=_model(DocumentInput,title=title,document_type=document_type,library_scope=library_scope,
        department_id=department_id,owner_department_id=owner_department_id,role_id=role_id,
        bank_number=bank_number,document_date=document_date,metadata=_json(metadata,dict),
        applicability=_json(applicability,list),new_position=_json(new_position,dict) if new_position else None)
    edition=_model(EditionInput,label=edition_label,metadata=_json(edition_metadata,dict))
    db,actor=request.app.state.store,request.state.actor
    with db.pool.connection() as conn:
        db.authorize(conn,actor,department_id,capability='instructions.create')
        document_store._references(conn,data)
    originals,meta=await _bundle(request,files,file_metadata)
    return await run_in_threadpool(document_store.create_document,db,actor,data,edition,originals,meta,upload_id)


@router.get('/documents/files/{file_id}/original')
def original(file_id:UUID,request:Request):
    row=request.app.state.store.original(request.state.actor,file_id)
    return Response(bytes(row['content']),media_type=row['media_type'],headers={
        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename'],safe=''),
        'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})


@router.get('/documents/files/{file_id}/preparation')
def preparation(file_id:UUID,request:Request):
    return get_preparation(request.app.state.store,request.state.actor,file_id)


@router.get('/documents/files/{file_id}/preview')
def preview(file_id:UUID,request:Request,view:Literal['clean','markup']='clean'):
    row=request.app.state.store.original(request.state.actor,file_id)
    return Response(preview_pdf(row,clean=view=='clean'), media_type='application/pdf', headers={
        'Content-Disposition':'inline', 'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store'})


@router.post('/documents/files/{file_id}/preparation',status_code=202)
def prepare(file_id:UUID,request:Request):
    return request_preparation(request.app.state.store,request.state.actor,file_id)


@router.delete('/documents/files/{file_id}/preparation',status_code=202)
@router.post('/documents/files/{file_id}/preparation/cancel',status_code=202)
def cancel(file_id:UUID,request:Request):
    return cancel_preparation(request.app.state.store,request.state.actor,file_id)


@router.get('/documents/{document_id}')
def document(document_id:UUID,request:Request):
    return document_store.get_document(request.app.state.store,request.state.actor,document_id)


@router.patch('/documents/{document_id}')
def update_document(document_id:UUID,data:DocumentPatch,request:Request):
    return document_store.update_document(request.app.state.store,request.state.actor,document_id,data)


@router.post('/documents/{document_id}/editions',status_code=201)
async def upload_edition(document_id:UUID,request:Request,edition_label:str|None=Form(None),
                         edition_metadata:str=Form('{}'),file_metadata:str|None=Form(None),
                         upload_id:UUID=Form(),files:list[UploadFile]=File()):
    db,actor=request.app.state.store,request.state.actor
    with db.pool.connection() as conn:
        document_store._document(db,conn,actor,document_id,'versions.create')
    edition=_model(EditionInput,label=edition_label,metadata=_json(edition_metadata,dict))
    originals,meta=await _bundle(request,files,file_metadata)
    return await run_in_threadpool(document_store.add_edition,db,actor,document_id,edition,originals,meta,upload_id)


@router.post('/documents/{document_id}/editions/{edition_id}/verify')
def verify(document_id:UUID,edition_id:UUID,data:VerificationInput,request:Request):
    return document_store.verify_edition(request.app.state.store,request.state.actor,document_id,edition_id,data)


@router.post('/documents/{document_id}/delete')
def delete(document_id:UUID,data:LifecycleInput,request:Request):
    return change_state(request.app.state.store,request.state.actor,document_id,data.reason)


@router.post('/documents/{document_id}/editions/{edition_id}/end-validity')
def end_validity(document_id:UUID,edition_id:UUID,data:EndValidityInput,request:Request):
    return document_store.end_validity(request.app.state.store,request.state.actor,document_id,edition_id,data)


@router.post('/documents/{document_id}/restore')
def restore(document_id:UUID,data:LifecycleInput,request:Request):
    return change_state(request.app.state.store,request.state.actor,document_id,data.reason,restore=True)
