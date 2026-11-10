"""Private, bounded renderer. No database, credentials or outbound network."""
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

MAX_INPUT = 10 * 1024 * 1024
#: Материалы карт: таблицы и презентации бывают крупнее документов Word.
MAX_OFFICE_INPUT = 30 * 1024 * 1024
_OLE = bytes.fromhex('d0cf11e0a1b11ae1')
OFFICE_FORMATS = {'xlsx': b'PK', 'xls': _OLE, 'pptx': b'PK', 'ppt': _OLE}
MAX_OUTPUT = 30 * 1024 * 1024
slot = asyncio.Semaphore(1)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def terminate(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def render(content, suffix, target='pdf', mode='markup', timeout=20):
    with tempfile.TemporaryDirectory(prefix='document-') as directory:
        root = Path(directory)
        original, output = root / ('source' + suffix), root / ('preview.' + target)
        original.write_bytes(content)
        # The clean view rejects tracked changes in memory, which Office allows
        # only for a writable file; it is a private, disposable copy.
        original.chmod(0o600 if mode == 'clean' else 0o400)
        pipe = 'preview_' + uuid4().hex
        office = client = None
        try:
            office = subprocess.Popen(['soffice', '--headless', '--nologo', '--nodefault',
                '--nofirststartwizard', '-env:UserInstallation=' + (root / 'profile').as_uri(),
                '--accept=pipe,name=' + pipe + ';urp;StarOffice.ComponentContext'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            client = subprocess.Popen(['/usr/bin/python3', str(Path(__file__).with_name('convert.py')), pipe, str(original), str(output), target, mode],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            client.wait(timeout=timeout)
            if client.returncode != 0 or not output.is_file():
                raise HTTPException(422, 'Не удалось построить просмотр. Скачайте исходный файл.' if mode == 'office'
                                    else 'Не удалось построить просмотр. Откройте исходный файл в Word.')
            size = output.stat().st_size
            if not 5 < size <= MAX_OUTPUT:
                raise HTTPException(422, 'Документ слишком большой для просмотра. Скачайте исходный файл.')
            result = output.read_bytes()
            if not result.startswith(b'%PDF-' if target == 'pdf' else b'PK'):
                raise HTTPException(422, 'Не удалось построить просмотр документа.')
            return result
        except subprocess.TimeoutExpired:
            raise HTTPException(422, 'Просмотр не подготовлен за отведённое время. Скачайте исходный файл.') from None
        finally:
            terminate(client)
            terminate(office)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/render/{format}')
async def preview(format: str, request: Request, mode: str = 'markup'):
    if mode not in ('markup', 'clean'):
        raise HTTPException(422, 'Допустимы режимы markup и clean')
    return await convert(format, request, mode=mode)


@app.post('/functions-source/doc')
async def functions_source(request: Request):
    return await convert('doc', request, target='docx')


@app.post('/render-office/{format}')
async def render_office(format: str, request: Request):
    """Таблица Excel или презентация PowerPoint — PDF для просмотра в браузере."""
    if format not in OFFICE_FORMATS:
        raise HTTPException(415, 'Допустимы XLSX, XLS, PPTX и PPT')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_OFFICE_INPUT:
            raise HTTPException(413, 'Файл превышает 30 МБ')
    if not body or not body.startswith(OFFICE_FORMATS[format]):
        raise HTTPException(422, 'Некорректный файл Excel или PowerPoint')
    try:
        await asyncio.wait_for(slot.acquire(), timeout=0.2)
    except TimeoutError:
        raise HTTPException(429, 'Просмотр занят. Повторите через несколько секунд.') from None
    try:
        result = await run_in_threadpool(render, bytes(body), '.' + format, 'pdf', 'office', 45)
        return Response(result, media_type='application/pdf', headers={'Cache-Control': 'no-store'})
    finally:
        slot.release()


async def convert(format: str, request: Request, target='pdf', mode='markup'):
    if format not in ('doc', 'docx'):
        raise HTTPException(415, 'Допустимы DOC и DOCX')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_INPUT:
            raise HTTPException(413, 'Файл превышает 10 МБ')
    if not body or (format == 'doc' and not body.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))) or (format == 'docx' and not body.startswith(b'PK')):
        raise HTTPException(422, 'Некорректный файл Word')
    try:
        await asyncio.wait_for(slot.acquire(), timeout=0.2)
    except TimeoutError:
        raise HTTPException(429, 'Просмотр занят. Повторите через несколько секунд.') from None
    try:
        result = await run_in_threadpool(render, bytes(body), '.' + format, target, mode)
        return Response(result, media_type='application/pdf' if target == 'pdf' else
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', headers={'Cache-Control': 'no-store'})
    finally:
        slot.release()
