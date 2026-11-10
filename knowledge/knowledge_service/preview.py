"""Local views of immutable files; authorization precedes every cache lookup."""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
import hashlib
from threading import Lock
import os

import httpx
from fastapi import HTTPException

MAX_BYTES = 30 * 1024 * 1024
_cache = OrderedDict()
_clean_cache = OrderedDict()
_functions_cache = OrderedDict()
_lock = Lock()
_inflight = {}


def _renderer(path, content):
    url = os.environ.get('DOCUMENT_PREVIEW_URL', '').rstrip('/')
    if not url:
        raise HTTPException(503, 'Обработка Word ещё не подключена. Откройте оригинал.')
    try:
        with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', url + path, content=content,
                               headers={'Content-Type': 'application/octet-stream'}) as response:
                if response.status_code == 429:
                    raise HTTPException(429, 'Обработка Word занята. Повторите через несколько секунд.')
                if response.status_code != 200:
                    raise HTTPException(422, 'Не удалось подготовить Word. Откройте исходный файл.')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise HTTPException(422, 'Документ слишком большой для просмотра. Откройте оригинал.')
    except httpx.HTTPError:
        raise HTTPException(503, 'Обработка Word временно недоступна. Откройте оригинал.') from None
    return bytes(data)


def _converted(content, cache, kind, path, validate):
    """Coalesce concurrent reads of one source (e.g. StrictMode or two readers).

    The cache and in-flight computation carry no actor identity. Every caller
    has independently passed its current document permission check.
    """
    key = hashlib.sha256(content).hexdigest()
    pending_key = (kind, key)
    with _lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        future = _inflight.get(pending_key)
        owner = future is None
        if owner:
            future = Future()
            _inflight[pending_key] = future
    if not owner:
        try:
            return future.result(timeout=30)
        except TimeoutError:
            raise HTTPException(503, 'Подготовка просмотра ещё не завершена. Повторите запрос.') from None
    try:
        data = _renderer(path, content)
        validate(data)
        with _lock:
            cache[key] = data
            cache.move_to_end(key)
            while sum(map(len, cache.values())) > MAX_BYTES:
                cache.popitem(last=False)
        future.set_result(data)
        return data
    except Exception as error:
        future.set_exception(error)
        raise
    finally:
        with _lock:
            _inflight.pop(pending_key, None)


def preview_pdf(row, clean=True):
    """Word as a readable PDF. The clean view shows the text in force:
    unaccepted tracked changes rejected and highlighter marks removed."""
    content = bytes(row['content'])
    if row['media_type'] == 'application/pdf':
        return content
    suffix = row['filename'].rsplit('.', 1)[-1].lower()
    if suffix not in ('doc', 'docx'):
        raise HTTPException(415, 'Этот формат открывается в исходном приложении. Скачайте оригинал.')
    def validate(data):
        if not data.startswith(b'%PDF-'):
            raise HTTPException(422, 'Просмотр документа недоступен. Скачайте оригинал.')
    if clean:
        return _converted(content, _clean_cache, 'pdf-clean', f'/render/{suffix}?mode=clean', validate)
    return _converted(content, _cache, 'pdf', f'/render/{suffix}?mode=markup', validate)


def word_functions_source(row):
    """Transient DOC -> OOXML, retaining revisions; original/ledger unchanged."""
    from .files import validate_original
    return _converted(bytes(row['content']), _functions_cache, 'functions',
                      '/functions-source/doc', lambda data: validate_original('functions.docx', data))
