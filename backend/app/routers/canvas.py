"""Раскладка карты для холста: линии и подписи считает бэкенд."""
import gzip

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import ValidationError

from app.models.process import CanvasLayout, CanvasPage, ProcessStats
from app.services.bpmn_exporter import canvas_layout
from app.services.canvas_cache import as_process, cached_layout_json
from app.services.process_stats import compute_stats

router = APIRouter(prefix="/canvas", tags=["canvas"])

#: Больше этого после распаковки страница карты не бывает (у «Харид…» — 22 МБ целиком).
MAX_PAGE_BYTES = 64 * 1024 * 1024


def _read_page(body: bytes, encoding: str) -> CanvasPage:
    if encoding == 'gzip':
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as err:
            raise HTTPException(status_code=400, detail=f'Тело запроса не распаковалось: {err}')
    if len(body) > MAX_PAGE_BYTES:
        raise HTTPException(status_code=413, detail='Страница карты больше 64 МБ')
    try:
        return CanvasPage.model_validate_json(body)
    except ValidationError as err:
        raise HTTPException(status_code=422, detail=err.errors(include_url=False, include_input=False))


def _layout_json(body: bytes, encoding: str) -> str:
    page = _read_page(body, encoding)
    return cached_layout_json(page, lambda p: canvas_layout(as_process(p)).model_dump_json())


def _stats_json(body: bytes, encoding: str) -> str:
    page = _read_page(body, encoding)
    return compute_stats(page.nodes, page.edges, page.lanes).model_dump_json()


@router.post(
    "/layout",
    response_model=CanvasLayout,
    summary="Линии и подписи страницы карты — тем же расчётом, что и выгрузка .bpmn",
)
async def layout(request: Request) -> Response:
    """Холст присылает страницу, которую показывает, и получает её линии и подписи.

    Своего трассировщика у холста больше нет: пока он был, линии на экране и в
    выгрузке расходились по форме — по замеру приёмки у 17–31 % связей.
    Страница приходит сама, а не по id: холст показывает и шаблоны, и
    страницы-подпроцессы, и варианты, которых в хранилище нет отдельными
    записями. Нужны только фигуры, связи и дорожки (``CanvasPage``), можно
    сжатые gzip (``Content-Encoding: gzip``); полная карта тоже принимается.
    Посчитанная раскладка хранится по содержимому страницы
    (``canvas_cache``): повторное открытие карты её не пересчитывает.
    """
    body = await request.body()
    encoding = request.headers.get('content-encoding', '').strip().lower()
    # Разбор мегабайтов и расчёт линий — в пуле потоков, а не в цикле событий:
    # иначе на время расчёта сервер не отвечал бы и на другие запросы.
    text = await run_in_threadpool(_layout_json, body, encoding)
    return Response(content=text, media_type='application/json')


@router.post(
    "/stats",
    response_model=ProcessStats,
    summary="Сколько страница карты длится и во что обходится",
)
async def stats(request: Request) -> Response:
    """Длительность (самый долгий путь), трудоёмкость, ожидание и себестоимость
    страницы с разбивкой по дорожкам.

    Страница приходит так же, как в ``/canvas/layout``: холст считает то, что
    показывает, — вместе с правками, ещё не сохранёнными на сервере.
    """
    body = await request.body()
    encoding = request.headers.get('content-encoding', '').strip().lower()
    text = await run_in_threadpool(_stats_json, body, encoding)
    return Response(content=text, media_type='application/json')
