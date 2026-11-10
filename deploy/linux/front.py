"""Веб-вход Process Hub без nginx — для тестового стенда без root (processhub-user.sh).

Повторяет app/nginx.conf: страницы — из сборки фронтенда (адреса приложения — одна
страница), /drawio/ — редактор draw.io, /api/ — пересылка в API платформы с исходным
Host (по нему API проверяет Origin). Запуск: uvicorn front:app (из supervise.py).
"""
import mimetypes
import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

HOME = Path(os.environ.get('PROCESSHUB_HOME') or Path(__file__).resolve().parent)
WEB = (HOME / 'web').resolve()
DRAWIO = HOME / 'drawio'
API = os.environ.get('PROCESSHUB_API', 'http://127.0.0.1:8000')
# Таблица типов берётся из системы и бывает неполной: скрипты редактора и PDF.js без
# правильного типа браузер не запустит.
for kind, extension in (('text/javascript', '.js'), ('text/javascript', '.mjs'), ('text/css', '.css'),
                        ('image/svg+xml', '.svg'), ('application/json', '.json'), ('application/wasm', '.wasm')):
    mimetypes.add_type(kind, extension)

HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers',
              'transfer-encoding', 'upgrade'}
# Большие карты считаются и выгружаются долго (импорт «Харид» — минуты).
client = httpx.AsyncClient(base_url=API, timeout=300, trust_env=False, follow_redirects=False)


async def api(request):
    headers = [(name, value) for name, value in request.headers.raw if name.decode().lower() not in HOP_BY_HOP]
    headers += [(b'x-forwarded-proto', request.url.scheme.encode()),
                (b'x-forwarded-for', (request.client.host if request.client else '').encode())]
    has_body = 'content-length' in request.headers or 'transfer-encoding' in request.headers
    target = request.url.path + ('?' + request.url.query if request.url.query else '')
    upstream = client.build_request(request.method, target, headers=headers,
                                    content=request.stream() if has_body else None)
    try:
        response = await client.send(upstream, stream=True)
    except httpx.TransportError:
        return PlainTextResponse('API платформы не отвечает', status_code=502)
    result = StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                               background=BackgroundTask(response.aclose))
    # Сырые заголовки — чтобы не потерять повторяющиеся Set-Cookie.
    result.raw_headers = [(name, value) for name, value in response.headers.raw
                          if name.decode().lower() not in HOP_BY_HOP]
    return result


async def page(request):
    path = (WEB / request.path_params['path']).resolve()
    if path.is_file() and WEB in path.parents:
        # Сборка Vite кладёт файлы с хэшем в имени — их можно кэшировать навсегда.
        cache = 'public, max-age=31536000, immutable' if request.url.path.startswith('/assets/') else 'no-cache'
        return FileResponse(path, headers={'Cache-Control': cache})
    if request.url.path.startswith('/assets/'):
        return PlainTextResponse('Не найдено', status_code=404)
    # Адреса приложения (/process/…, /catalog/…) — это одна страница.
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-cache'})


static = GZipMiddleware(Starlette(routes=[
    Route('/drawio', lambda request: RedirectResponse('/drawio/', status_code=301)),
    Mount('/drawio', app=StaticFiles(directory=DRAWIO, html=True)),
    Route('/{path:path}', page),
]))

app = Starlette(routes=[
    Route('/api/{path:path}', api, methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']),
    Mount('', app=static),
])
