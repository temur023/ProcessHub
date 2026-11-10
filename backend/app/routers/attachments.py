"""Материалы карты: утверждённый PDF, регламент в Excel, презентация.

Схема карты живёт в draw.io и разбирается платформой; эти файлы — нет. Их не
пересобрать из карты: подписанный экземпляр, таблица с KPI, защита проекта.
Поэтому платформа хранит их как есть, рядом с картой, со всей историей
загрузок: новая версия не стирает прежнюю.
"""
import os
import threading
from collections import OrderedDict
from typing import List, Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.routers.import_export import MAX_UPLOAD_BYTES, attachment_headers
from app.services.auth import current_user
from app.services.store import ProcessAttachment, User, get_store

router = APIRouter(prefix="/processes/{process_id}/attachments", tags=["attachments"])

Kind = Literal['approved', 'regulation', 'presentation', 'other']

_OLE = bytes.fromhex('d0cf11e0a1b11ae1')
#: Расширение -> (тип содержимого, начало файла). Начало проверяется, чтобы под
#: видом PDF не лёг произвольный файл.
FORMATS = {
    'pdf': ('application/pdf', b'%PDF-'),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', b'PK'),
    'xls': ('application/vnd.ms-excel', _OLE),
    'pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', b'PK'),
    'ppt': ('application/vnd.ms-powerpoint', _OLE),
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', b'PK'),
    'doc': ('application/msword', _OLE),
}


def _require_process(process_id: str) -> None:
    if get_store().info(process_id) is None:
        raise HTTPException(404, 'Карта не найдена')


@router.get("", response_model=List[ProcessAttachment], summary="Материалы карты, новые сверху")
def list_attachments(process_id: str):
    _require_process(process_id)
    return get_store().attachments(process_id)


@router.post("", response_model=ProcessAttachment, status_code=201, summary="Прикрепить материал к карте")
async def add_attachment(process_id: str, kind: Kind = Form(...), file: UploadFile = File(...),
                         user: User = Depends(current_user)):
    _require_process(process_id)
    name = ' '.join((file.filename or '').split()) or 'файл'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in FORMATS:
        raise HTTPException(415, 'Можно прикрепить PDF, Excel (XLSX, XLS), PowerPoint (PPTX, PPT) или Word (DOCX, DOC)')
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Файл слишком большой (макс. 30 МБ)')
    media_type, signature = FORMATS[ext]
    if not content.startswith(signature):
        raise HTTPException(422, f'Файл повреждён или это не {ext.upper()}: содержимое не совпадает с расширением')
    return get_store().add_attachment(process_id, kind, name, media_type, content, author=user.id)


@router.get("/{attachment_id}", summary="Скачать материал; PDF можно открыть в браузере (inline=1)")
def download_attachment(process_id: str, attachment_id: str, inline: bool = Query(False)):
    found = get_store().attachment(process_id, attachment_id)
    if found is None:
        raise HTTPException(404, 'Материал не найден')
    meta, content = found
    headers = attachment_headers(meta.fileName)
    if inline and meta.mediaType == 'application/pdf':
        headers['Content-Disposition'] = headers['Content-Disposition'].replace('attachment;', 'inline;', 1)
    headers['X-Content-Type-Options'] = 'nosniff'
    headers['Cache-Control'] = 'no-store'
    return Response(content=content, media_type=meta.mediaType, headers=headers)


@router.delete("/{attachment_id}", status_code=204, summary="Убрать материал: автор загрузки или администратор")
def remove_attachment(process_id: str, attachment_id: str, user: User = Depends(current_user)):
    store = get_store()
    found = store.attachment(process_id, attachment_id)
    if found is None:
        raise HTTPException(404, 'Материал не найден')
    if not user.isAdmin and found[0].uploadedById != user.id:
        raise HTTPException(403, 'Удалить материал может тот, кто его загрузил, или администратор')
    store.remove_attachment(process_id, attachment_id)


# ── Просмотр Excel и PowerPoint ─────────────────────────────────────────────
# Браузер таблицу и презентацию не покажет, поэтому их PDF-копию строит
# изолированный сервис просмотра (LibreOffice Calc и Impress, без сети и базы).
# Материал не меняется после загрузки, так что готовая копия переиспользуется.

#: Расширение -> формат сервиса просмотра.
PREVIEWABLE = {'xlsx': 'xlsx', 'xls': 'xls', 'pptx': 'pptx', 'ppt': 'ppt'}
_CACHE_LIMIT = 24
_previews: 'OrderedDict[str, bytes]' = OrderedDict()
_previews_lock = threading.Lock()


def _cached(key: str) -> bytes | None:
    with _previews_lock:
        if key in _previews:
            _previews.move_to_end(key)
            return _previews[key]
    return None


def _remember(key: str, pdf: bytes) -> None:
    with _previews_lock:
        _previews[key] = pdf
        while len(_previews) > _CACHE_LIMIT:
            _previews.popitem(last=False)


def _pdf_response(content: bytes, name: str) -> Response:
    headers = attachment_headers(name)
    headers['Content-Disposition'] = headers['Content-Disposition'].replace('attachment;', 'inline;', 1)
    headers['X-Content-Type-Options'] = 'nosniff'
    headers['Cache-Control'] = 'no-store'
    return Response(content=content, media_type='application/pdf', headers=headers)


@router.get("/{attachment_id}/preview", summary="Материал для просмотра в браузере: PDF как есть, Excel и PowerPoint — PDF-копией")
async def preview_attachment(process_id: str, attachment_id: str):
    found = get_store().attachment(process_id, attachment_id)
    if found is None:
        raise HTTPException(404, 'Материал не найден')
    meta, content = found
    if meta.mediaType == 'application/pdf':
        return _pdf_response(content, meta.fileName)
    ext = meta.fileName.rsplit('.', 1)[-1].lower()
    if ext not in PREVIEWABLE:
        raise HTTPException(415, 'Этот файл не открывается в браузере. Скачайте его.')
    name = meta.fileName.rsplit('.', 1)[0] + '.pdf'
    if (pdf := _cached(meta.id)) is not None:
        return _pdf_response(pdf, name)
    url = os.environ.get('DOCUMENT_PREVIEW_URL', '').rstrip('/')
    if not url:
        raise HTTPException(503, 'Просмотр Excel и PowerPoint на этом сервере не подключён. Скачайте файл.')
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
            res = await client.post(f'{url}/render-office/{PREVIEWABLE[ext]}', content=content)
    except httpx.HTTPError:
        raise HTTPException(503, 'Сервис просмотра недоступен. Скачайте файл или повторите позже.') from None
    if res.status_code != 200 or not res.content.startswith(b'%PDF-'):
        try:
            detail = res.json().get('detail')
        except ValueError:
            detail = None
        status = res.status_code if res.status_code in (413, 422, 429) else 503
        raise HTTPException(status, detail if isinstance(detail, str) else 'Не удалось построить просмотр. Скачайте файл.')
    _remember(meta.id, res.content)
    return _pdf_response(res.content, name)
