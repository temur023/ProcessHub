import io
import re
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from app.services.drawio_parser import parse_drawio_xml
from app.services.drawio_precheck import (
    PrecheckResult,
    precheck_drawio,
)
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.pmm_tree import LAYOUTS, generate_pmm_package
from app.services.pmm_importer import PmmError, parse_pmm_package
from app.services.exporters import generate_event_log_csv, generate_regulation_csv
from app.services.export_validation import (
    ExportCheck,
    summary_line,
    validate_bpmn_xml,
    validate_pmm_package,
)
from app.services.pix_spec_checker import validate_pmm_for_pix
from app.models.process import BusinessProcess
from app.services.auth import current_user
from app.services.store import ProcessConflict, User, get_store
from app.services.canvas_cache import warm_up
from app.services.drawio_exporter import generate_drawio_xml
from app.services.drawio_edit import apply_drawio
from app.services.process_stats import apply_costs
from app.routers.processes import refresh_checks

router = APIRouter(prefix="/import", tags=["import & export"])

#: Карты банка бывают по 12 МБ («Харид бизнес жараёнлар», 12 страниц).
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _sanitize_filename(filename: str) -> str:
    """Удаляет path traversal и недопустимые символы, гарантирует безопасное имя файла."""
    # Убираем директории: оставляем только basename
    basename = filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    # Заменяем недопустимые символы, убираем ведущие точки/дефисы (скрытые файлы)
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', basename).strip('._')
    # Ограничиваем длину
    safe = safe[:120] if len(safe) > 120 else safe
    return safe or 'export'


def attachment_headers(filename: str) -> dict:
    ascii_name = _sanitize_filename(filename)
    # quote для RFC5987 filename* (UTF-8)
    quoted = quote(_sanitize_filename(filename), safe='')
    # На случай если исходное имя содержало не-ASCII, также экранируем для filename*
    utf8_quoted = quote(filename, safe='')
    # Используем sanitized ascii_name для fallback filename, оригинальное quote для filename*
    return {
        'Content-Disposition': f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_quoted}"
    }


class XmlImportBody(BaseModel):
    xml: str
    fileName: str = "Pasted_Process.drawio"
    #: Рисунок из встроенного редактора: пустая фигура в нём — неподписанный шаг.
    fromEditor: bool = False


def _check_headers(check: ExportCheck, base: dict,
                   pix: Optional[ExportCheck] = None) -> dict:
    """Итог проверки файла — в заголовках ответа рядом с самим файлом.

    Скачивание не блокируем: файл нужен сотруднику в любом случае, а если в нём
    что-то не так, платформа скажет об этом раньше, чем PIX, — и адресно.

    Заголовков два, потому что и вопросов два: ``X-Export-Check`` отвечает
    «файл соответствует стандарту», ``X-Pix-Check`` — «файл откроется именно в
    Процессной студии». Второй строже, и путать их нельзя: файл, валидный по
    BPMN 2.0, студия всё ещё может не принять.
    """
    headers = {
        **base,
        'X-Export-Check': summary_line([check]),
        'X-Export-Check-Errors': str(len(check.errors)),
    }
    if pix is not None:
        headers['X-Pix-Check'] = summary_line([pix])
        headers['X-Pix-Check-Errors'] = str(len(pix.errors))
    return headers


def _guarded_parse(text: str, filename: str, keep_blank_shapes: bool = False) -> BusinessProcess:
    """Предпроверка исходника, затем разбор. Единая дверь для обоих импортов.

    Смотрим файл до разбора, но дефект предпроверки импорт больше НЕ отменяет.
    Раньше отменял: раз студия такой пакет не откроет, разбирать было незачем.
    На деле выходило хуже — сотрудник получал красную плашку вместо карты и не
    видел ни фигуры, о которой речь, ни того, что с остальной схемой всё в
    порядке. Чинить-то ему в draw.io, а найти там фигуру по одному
    идентификатору из текста ошибки нечем.

    Наш холст — не Процессная студия: карту он рисует и с нулевой дорожкой.
    Поэтому файл открывается всегда, когда его удалось разобрать, а дефект
    едет тем же списком в карточку «Проверка импорта» и остаётся ошибкой —
    красной строкой с адресом фигуры.

    Выгрузку это не разрешает вслепую: собранный пакет отдельно проверяют
    ``export_validation`` и ``pix_spec_checker`` — уже по результату, а не по
    исходнику. Часть дефектов до результата не доживает: дорожку без ``height``
    в «AVGUST-1.29» укладка чинит сама (``normalize_layout`` даёт ей 2881×60),
    и обе выгрузки этой карты выходят без единой ошибки по правилам PIX.
    Предупредить о кривом исходнике всё равно надо — правит его аналитик, — но
    отменять из-за этого импорт значило бы отменять и то, что уже исправлено.

    Отказ остаётся один — файл, который не разбирается вовсе: пустой,
    нечитаемый как XML, без единой фигуры. Показывать там нечего.
    """
    try:
        precheck = precheck_drawio(text, filename)
    except Exception:  # noqa: BLE001 — предпроверка не имеет права ронять импорт
        precheck = None

    try:
        process = parse_drawio_xml(text, filename, keep_blank_shapes)
    except Exception as exc:
        raise HTTPException(422, f"Ошибка парсинга: {exc}")

    if precheck is not None:
        # Петлю, которая дошла до карты, называет проверка самой карты — и
        # будет называть её и после правок на холсте. Замечание исходника о той
        # же стрелке давало вторую ошибку: на карте «1.3» две петли числились
        # четырьмя ошибками.
        loops = {e.id for e in process.edges if e.sourceId and e.sourceId == e.targetId and e.kind != 'annotationLine'}
        notes = [n for n in _precheck_notes(precheck) if not (n.code == 'source_edge_self_loop' and n.nodeId in loops)]
        process.validation = notes + list(process.validation or [])
    return process


def _precheck_notes(precheck: PrecheckResult) -> list:
    """Замечания предпроверки в том виде, в каком их показывает интерфейс."""
    from app.models.process import ProcessValidation

    notes = []
    for problem in precheck.problems:
        if problem.level == 'error':
            level = 'error'
        elif problem.loses_data:
            # Потеря содержимого — не косметика: в списке она должна стоять
            # рядом с ошибками, а не теряться среди информационных строк.
            level = 'warning'
        else:
            level = 'info'
        notes.append(ProcessValidation(
            level=level,
            code=f'source_{problem.code}',
            message=problem.message[0].upper() + problem.message[1:],
            hint=problem.hint,
            nodeId=problem.where,
        ))
    return notes


# ──────────── Import ────────────

@router.post(
    "/file",
    response_model=BusinessProcess,
    summary="Upload a .drawio / .bpmn / .xml file and parse it into a BusinessProcess"
)
async def import_file(
    file: UploadFile = File(...),
    replace: Optional[str] = Query(None, description="Обновить эту карту из файла: новая версия той же карты"),
    asNew: bool = Query(False, description="Сохранить отдельной картой, даже если карта с таким названием уже есть"),
    user: User = Depends(current_user),
):
    """Загрузка файла схемы.

    Изменённый файл получает новый идентификатор (``process_id_for``), и раньше
    каждый повторный импорт заводил рядом вторую карту: «1.3», «1.53»,
    «AVGUST-1.29» лежали на платформе по две-три штуки. Поэтому карта с тем же
    названием без явного выбора не создаётся — ответ 409 со списком таких карт,
    и сотрудник решает: обновить существующую (``replace``) или сохранить
    отдельной (``asNew``). Тот же файл без изменений по-прежнему ложится в ту
    же карту новой версией — выбирать там нечего.
    """
    allowed = {'.drawio', '.xml', '.bpmn', '.txt'}
    raw_name = file.filename or ''
    if '.' in raw_name:
        ext = '.' + raw_name.rsplit('.', 1)[-1].lower()
        # нормализуем: убираем пробелы, оставляем только точку+расширение
        ext = '.' + ext.lstrip('.').strip()
    else:
        ext = ''
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type '{ext or '(no extension)'}'. Allowed: {', '.join(sorted(allowed))}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл слишком большой (макс. 30 МБ)")

    text = content.decode('utf-8', errors='ignore')

    if not text.strip():
        raise HTTPException(400, "Uploaded file is empty")

    filename = file.filename or 'process.drawio'
    store = get_store()
    if replace:
        saved = _reimport(replace, text, filename, user)
        store.save_source(saved.id, filename, content, author=user.id)
        return saved
    process = _guarded_parse(text, filename)
    if not asNew and store.info(process.id) is None:
        key = same_name_key(process.name)
        same = [p for p in store.infos() if same_name_key(p.name) == key]
        if same:
            raise HTTPException(409, detail={
                'code': 'same_name',
                'message': f"На платформе уже есть карта «{same[0].name}». Обновить её или сохранить отдельной картой?",
                'existing': [{'id': p.id, 'name': p.name, 'stepCount': p.stepCount,
                              'updatedAt': p.updatedAt.isoformat() if p.updatedAt else None,
                              'updatedBy': p.updatedBy} for p in same],
            })
    saved = store.save(process, 'import', author=user.id)
    store.save_source(saved.id, filename, content, author=user.id)
    return saved


def same_name_key(name: str) -> str:
    """Название без регистра, лишних пробелов и хвостов копий «(1) (2)» — так дубли и появлялись."""
    key = ' '.join((name or '').lower().split())
    while True:
        trimmed = re.sub(r'\s*\(\d+\)$', '', key)
        if trimmed == key:
            return key
        key = trimmed


def _reimport(process_id: str, text: str, filename: str, user: User) -> BusinessProcess:
    """Файл ложится в существующую карту новой версией истории («Импорт файла»).

    Тот же путь, что у сохранения из встроенного draw.io: рисунок из файла,
    паспорт и номера страниц — от карты, ставки дорожек переносятся по имени.
    """
    store = get_store()
    current = store.get(process_id)
    if not current:
        raise HTTPException(404, f"Process '{process_id}' not found")
    updated = apply_drawio(current, _guarded_parse(text, filename))
    for page in [updated, *updated.subProcesses, *updated.variants]:
        apply_costs(page.nodes, page.lanes)
    refresh_checks(updated)
    saved = store.save(updated, 'import', note=f'повторный импорт {filename}', author=user.id)
    warm_up(saved)
    return saved


@router.post(
    "/xml",
    response_model=BusinessProcess,
    summary="Parse draw.io / BPMN XML from raw string body"
)
def import_xml(body: XmlImportBody, user: User = Depends(current_user)):
    if not body.xml.strip():
        raise HTTPException(400, "XML body is empty")
    if len(body.xml.encode('utf-8')) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "XML слишком большой (макс. 30 МБ)")
    process = _guarded_parse(body.xml, body.fileName, keep_blank_shapes=body.fromEditor)
    return get_store().save(process, 'import', author=user.id)


class DrawioSaveBody(BaseModel):
    xml: str
    #: Ревизия карты, открытой в редакторе: сохранение по устаревшей — 409.
    revision: Optional[int] = None


@router.post(
    "/{process_id}/drawio",
    response_model=BusinessProcess,
    summary="Save a map edited in the embedded draw.io editor as a new version of the same map",
    responses={409: {"description": "Карту уже сохранили по более свежей копии"}},
)
def save_from_drawio(process_id: str, body: DrawioSaveBody, user: User = Depends(current_user)):
    """Сохранение из встроенного draw.io: рисунок — из файла, остальное — от карты.

    Файл разбирается тем же импортом, что и загруженный (предпроверка, разбор,
    свойства платформы ``sqb_*``), и ложится в ту же карту новой версией
    истории («Правка в draw.io»). Себестоимость и замечания пересчитываются,
    как при любом сохранении.
    """
    store = get_store()
    current = store.get(process_id)
    if not current:
        raise HTTPException(404, f"Process '{process_id}' not found")
    if not body.xml.strip():
        raise HTTPException(400, "Файл draw.io пустой")
    if len(body.xml.encode('utf-8')) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл draw.io слишком большой (макс. 30 МБ)")
    updated = apply_drawio(current, _guarded_parse(body.xml, f'{current.fileName or current.name}.drawio', keep_blank_shapes=True))
    for page in [updated, *updated.subProcesses, *updated.variants]:
        apply_costs(page.nodes, page.lanes)
    refresh_checks(updated)
    try:
        saved = store.save(updated, 'drawio', expected_revision=body.revision, author=user.id)
    except ProcessConflict as err:
        raise HTTPException(
            status_code=409,
            detail=f"Карту уже сохранили по более свежей копии (ревизия {err.revision}). "
                   "Правка из draw.io сделана по устаревшей копии и не сохранена.",
        )
    warm_up(saved)
    return saved


class ExportPreview(BaseModel):
    """Готовая выгрузка, открытая для просмотра."""
    process: BusinessProcess
    format: str
    fileName: str
    #: XML, который реально разобран: BPMN целиком либо карта из пакета .pmm.
    source: str
    #: Части ZIP — показываем, из чего собран пакет.
    parts: list = []


@router.post(
    "/preview",
    response_model=ExportPreview,
    summary="Read a ready .bpmn / .pmm export for viewing, without saving it",
)
async def preview_export(file: UploadFile = File(...)):
    """Открывает готовый файл выгрузки как карту — и НЕ кладёт его в хранилище.

    Отличий от ``/file`` два, и оба существенные.

    Первое: результат никуда не сохраняется. Просмотр выгрузки — разовая
    сверка, а не импорт процесса; попади он в хранилище, список процессов
    засорялся бы копиями собственных файлов.

    Второе: геометрия НЕ нормализуется. Смысл просмотра в том, чтобы увидеть
    файл ровно таким, каким его получит Процессная студия. Для ``.bpmn`` это
    выходит само — ``parse_bpmn_xml`` укладкой не занимается; для ``.pmm``
    тем же занят ``pmm_importer``.
    """
    raw_name = file.filename or 'export'
    ext = ('.' + raw_name.rsplit('.', 1)[-1].lower()) if '.' in raw_name else ''
    if ext not in {'.bpmn', '.xml', '.pmm', '.zip'}:
        raise HTTPException(400, f"Unsupported file type '{ext or '(no extension)'}'. "
                                 "Allowed: .bpmn, .xml, .pmm, .zip")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл слишком большой (макс. 30 МБ)")
    if not content:
        raise HTTPException(400, "Uploaded file is empty")

    if ext in ('.pmm', '.zip'):
        try:
            process, map_xml, parts = parse_pmm_package(content, raw_name)
        except PmmError as exc:
            raise HTTPException(422, str(exc))
        return ExportPreview(process=process, format='pmm', fileName=raw_name,
                             source=map_xml, parts=parts)

    text = content.decode('utf-8', errors='ignore')
    if not text.strip():
        raise HTTPException(400, "Uploaded file is empty")
    try:
        process = parse_drawio_xml(text, raw_name)
    except Exception as exc:
        raise HTTPException(422, f"Ошибка разбора: {exc}")
    return ExportPreview(process=process, format='bpmn', fileName=raw_name, source=text)


# ──────────── Export ────────────

_LAYOUT_HELP = ('auto — деревом карт, если страниц несколько или карта больше тысячи узлов; '
                'single — одной картой; tree — главная карта и дочерние всегда')


def _layout(value: str) -> str:
    if value not in LAYOUTS:
        raise HTTPException(422, f"layout: одно из {', '.join(LAYOUTS)}")
    return value

@router.get(
    "/{process_id}/export/bpmn",
    summary="Export process map as BPMN 2.0 XML for Processet"
)
def export_bpmn(process_id: str):
    """Карта в BPMN 2.0 — эталон для Processet.

    В PIX карта уезжает только пакетом .pmm (``export_pmm``): импорт BPMN
    студия перекладывает по-своему — растягивает карту, дублирует фигуры у
    границ дорожек, теряет связи «пул ↔ шлюз», — а время шага BPMN не несёт.
    Поэтому и имя файла не обещает PIX.
    """
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    xml = generate_bpmn_xml(process)
    filename = f"{_sanitize_filename(process.passport.code)}_Processet.bpmn"
    return Response(
        content=xml.encode('utf-8'),
        media_type='application/xml',
        headers=_check_headers(validate_bpmn_xml(xml), attachment_headers(filename)),
    )


@router.get(
    "/{process_id}/source",
    summary="The file the map was last uploaded from, byte for byte",
    responses={404: {"description": "Оригинал не сохранён"}},
)
def download_source(process_id: str):
    """Загруженный файл как есть — не пересобранная платформой выгрузка.

    Хранится последний загруженный файл карты. У карт, загруженных до того,
    как платформа начала хранить оригиналы, и у нарисованных во встроенном
    редакторе оригинала нет.
    """
    source = get_store().source(process_id)
    if source is None:
        raise HTTPException(404, 'Оригинал файла не сохранён: карту загрузили до того, как платформа начала '
                                 'хранить исходные файлы, или нарисовали прямо в редакторе.')
    headers = attachment_headers(source.fileName)
    headers['X-Uploaded-At'] = source.uploadedAt.isoformat()
    if source.uploadedBy:
        headers['X-Uploaded-By'] = quote(source.uploadedBy)
    return Response(content=source.content, media_type='application/octet-stream', headers=headers)


@router.get(
    "/{process_id}/export/drawio",
    summary="Export process map as a draw.io file (.drawio) that imports back unchanged"
)
def export_drawio(process_id: str):
    """Карта в draw.io — со всеми страницами, дорожками, значками времени и данными платформы.

    Открывается в draw.io так, как её нарисовали, и импортируется обратно той же
    картой (``drawio_exporter``): правку можно сделать в draw.io и вернуть.
    """
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")
    xml = generate_drawio_xml(process)
    filename = f"{_sanitize_filename(process.passport.code)}.drawio"
    return Response(content=xml.encode('utf-8'), media_type='application/xml',
                    headers=attachment_headers(filename))


@router.get(
    "/{process_id}/export/pmm",
    summary="Export process map as PIX Process Studio native .pmm package"
)
def export_pmm(process_id: str, layout: str = Query('auto', description=_LAYOUT_HELP)):
    """Пакет .pmm для PIX: одной картой или деревом карт (``pmm_tree``)."""
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    payload = generate_pmm_package(process, _layout(layout))
    filename = f"{_sanitize_filename(process.passport.code)}_PIX_Map.pmm"
    return Response(
        content=payload,
        media_type='application/zip',
        headers=_check_headers(validate_pmm_package(payload), attachment_headers(filename),
                               validate_pmm_for_pix(payload)),
    )


@router.get(
    "/{process_id}/export/event-log",
    summary="Export Event Log CSV for Processet (XES-compatible)"
)
def export_event_log(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    csv_data = generate_event_log_csv(process)
    filename = f"{_sanitize_filename(process.passport.code)}_EventLog.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers=attachment_headers(filename)
    )


@router.get(
    "/{process_id}/export/regulation",
    summary="Export process regulation matrix as Excel-compatible CSV"
)
def export_regulation(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    csv_data = generate_regulation_csv(process)
    filename = f"{_sanitize_filename(process.passport.code)}_Regulation.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers=attachment_headers(filename)
    )


@router.get(
    "/{process_id}/export/check",
    summary="Проверить выгрузки процесса теми же правилами, по которым их читает PIX"
)
def check_exports(process_id: str, layout: str = Query('auto', description=_LAYOUT_HELP)):
    """Отчёт о готовности пакета .pmm к загрузке в PIX Процессную студию.

    Студия отвергает пакет целиком из-за одного дефекта и называет только код
    ошибки. Здесь те же проверки делаются заранее и с указанием фигуры.

    Наборов правил два, и берутся оба — те же, что и при скачивании файла.
    ``export_validation`` отвечает «пакет собран по формату .pmm»,
    ``pix_spec_checker`` — «файл откроется именно в Процессной студии». Второй
    строже, и без него панель экспорта показывала бы зелёный баннер на файле,
    который студия не примет. BPMN в PIX больше не уезжает и здесь не
    проверяется.
    """
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    pmm = generate_pmm_package(process, _layout(layout))
    checks = [
        validate_pmm_package(pmm),
        validate_pmm_for_pix(pmm),
    ]
    return {
        'processId': process_id,
        'ok': all(c.ok for c in checks),
        'summary': summary_line(checks),
        'formats': [
            {
                'format': c.format,
                'ok': c.ok,
                'errors': len(c.errors),
                'warnings': len(c.warnings),
                'problems': [
                    {'level': p.level, 'code': p.code, 'message': p.message, 'where': p.where}
                    for p in c.problems
                ],
            }
            for c in checks
        ],
    }


@router.get(
    "/{process_id}/export/pix-json",
    summary="Export PIX BPM Registry JSON for PIX platform import"
)
def export_pix_json(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    import json
    registry_data = process.registry.model_dump()
    registry_data['processCode'] = process.passport.code
    registry_data['processName'] = process.name
    filename = f"{_sanitize_filename(process.passport.code)}_PIX_Registry.json"
    return Response(
        content=json.dumps(registry_data, ensure_ascii=False, indent=2).encode('utf-8'),
        media_type='application/json',
        headers=attachment_headers(filename)
    )
