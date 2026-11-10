"""Чтение нативного пакета PIX Процессной студии (`.pmm`) — для просмотра выгрузки.

Пакет — это ZIP из ``main.xml``, ``pm/configuration.xml`` и карты
``pm/maps/<имя>.xml``. Читаем только карту: каталог нотаций студии на то, как
выглядит схема, не влияет.

Соглашения формата — обратные экспортёру (``pmm_exporter.py``):

* узлы дорожки (``horizontalRoad``) лежат в ОТНОСИТЕЛЬНЫХ координатах, сама
  дорожка — в абсолютных; здесь координаты снова разворачиваются в абсолютные,
  иначе вся карта сложилась бы в левый верхний угол;
* подпись связи хранится в атрибуте ``Text``, а не ``label``;
* ``emptyPool`` — заголовочная плашка карты, а не шаг: из неё берём имя
  процесса и на холст не выносим;
* мелкий ``intermediate_event_catch_timer`` без единой связи — это значок
  длительности у шага; его время возвращается в ST/WT ближайшего шага, и на
  холсте снова появляются часы в углу.

Геометрия НЕ нормализуется. Смысл просмотра в том, чтобы увидеть файл ровно
таким, каким его получит студия, а не таким, каким его перерисовала бы
платформа, — поэтому ни укладка дорожек, ни подгонка подписей здесь не
вызываются.
"""
import io
import re
import uuid
import zipfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from app.models.process import (
    BusinessProcess,
    EdgeKind,
    Geometry,
    NodeType,
    PixRegistryRecord,
    PixRegistrySchema,
    ProcessEdge,
    ProcessEdgePoint,
    ProcessField,
    ProcessNode,
    ProcessPassport,
)
from app.models.process import TASK_NODE_TYPES
from app.services.conformance_engine import analyze_process_conformance
from app.services.diagnostics import collect_import_diagnostics
from app.services.drawio_parser import classify_category, duration_minutes, guess_system

#: Тип фигуры в карте студии -> тип узла модели.
_PIX_TYPES: Dict[str, NodeType] = {
    'start_event_none': 'startEvent',
    'start_event_timer': 'startEvent',
    'start_event_message': 'startEvent',
    'end_event_none': 'endEvent',
    'end_event_terminate': 'endEvent',
    'end_event_message': 'endEvent',
    'intermediate_event_catch_timer': 'intermediateTimerEvent',
    'boundary_non_interrupting_event_timer': 'intermediateTimerEvent',
    'intermediate_event_catch_message': 'intermediateMessageEvent',
    'gateway_xor': 'exclusiveGateway',
    'gateway_parallel': 'parallelGateway',
    'gateway_or': 'inclusiveGateway',
    'gateway_complex': 'complexGateway',
    'sub_process': 'subProcess',
    'datastorage': 'dataStore',
    'dataobject': 'dataObject',
    'input': 'textAnnotation',
    'servicetask': 'serviceTask',
    'usertask': 'userTask',
    'task': 'task',
}

#: Значок длительности не больше этого размера: настоящий таймер крупнее.
MARKER_MAX_SIDE = 32

#: Тип, которым экспортёр пишет значок длительности (``_DURATION_ELEMENT``).
#:
#: Это непрерывающее ГРАНИЧНОЕ событие: по спецификации оно живёт на границе
#: активности и связей не требует, поэтому элементом потока не бывает никогда и
#: значком является всегда — независимо от размера. Клиентский читатель, на
#: месте которого стоит этот модуль, такого типа не знал: в «overnight» он
#: превращал 38 значков времени в 38 лишних шагов, а само время у шагов при
#: этом терялось.
DURATION_MARKER_TYPE = 'boundary_non_interrupting_event_timer'
#: На каком расстоянии значок ещё считается принадлежащим шагу, px.
MARKER_ATTACH_RADIUS = 40.0

_WAIT_RE = re.compile(r'ожидани[ея]\s*(.+)$', re.I)
_WAIT_TAIL_RE = re.compile(r'[·|,;]?\s*ожидани[ея].*$', re.I)

#: Единицы, которыми платформа подписывает значок длительности.
#:
#: ``format_duration`` печатает «45 мин», «2 ч», «1 ч 30 мин», «2 дн», а общий
#: ``duration_minutes`` знает «мин», «min», «soat», «kun», но НЕ знает «ч» и
#: «дн» — и намеренно: в подписи ШАГА «2 ч» неотличимо от начала «2 через
#: банк», и разбор съел бы половину имени. Внутри значка этой двусмысленности
#: нет: его подпись целиком и есть длительность, поэтому здесь единицы читаются
#: те же, какими написаны.
_BADGE_UNITS = {'дн': 1440.0, 'ч': 60.0, 'мин': 1.0}
_BADGE_PART_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(дн|мин|ч)\b', re.I)


def badge_minutes(label: Optional[str]) -> Optional[float]:
    """Минуты из подписи значка длительности; ``None`` — если числа нет."""
    if not label:
        return None
    total = 0.0
    for number, unit in _BADGE_PART_RE.findall(label):
        total += float(number.replace(',', '.')) * _BADGE_UNITS[unit.lower()]
    if total:
        return total
    return duration_minutes(label)


class PmmError(ValueError):
    """Пакет не читается как карта Процессной студии."""


def _num(raw: Optional[str], fallback: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


class _RawNode:
    """Фигура карты до решения, шаг это или значок длительности."""

    __slots__ = ('el', 'lane_id', 'type', 'pix_type', 'id', 'label', 'x', 'y', 'width', 'height')

    def __init__(self, el: ET.Element, origin_x: float, origin_y: float, lane_id: str = ''):
        self.el = el
        self.lane_id = lane_id
        self.pix_type = (el.get('type') or '').lower()
        self.type = _PIX_TYPES.get(self.pix_type, 'task')
        self.id = el.get('id') or f'pix_{uuid.uuid4().hex[:8]}'
        # Подпись события сбоку выгрузка подбивает неразрывными пробелами.
        self.label = (el.get('label') or '').replace(' ', ' ').strip()
        self.x = origin_x + _num(el.get('x'))
        self.y = origin_y + _num(el.get('y'))
        self.width = _num(el.get('width'), 120)
        self.height = _num(el.get('height'), 60)


def _marker(connector: ET.Element, tag: str) -> str:
    el = connector.find(tag)
    return ((el.text if el is not None else '') or '').strip().lower()


def connector_kind(line_style: str, marker_start: str, marker_end: str, touches_lane: bool) -> EdgeKind:
    """Вид связи — по её оформлению, обратное ``pmm_exporter._line_decoration``.

    В выгрузке самой студии (``tests/fixtures/sap.pmm``) сочетаний три, и
    каждое отвечает своему понятию BPMN: сплошная со стрелкой — переход,
    точечная — ассоциация, пунктир с кружком в начале и пустой стрелкой в
    конце — поток сообщений. Читатель смотрел только на стиль линии и ждал
    сообщения точечными: потоки сообщений из выгрузок платформы (они
    пунктирные) читались переходами, а пунктирные переходы — ассоциациями.

    Решают маркеры, а не один стиль: пунктиром рисуется и переход, если он
    пунктирный в draw.io, но стрелка у него обычная.
    """
    if marker_start == 'circle' or marker_end == 'arrowempty':
        return 'messageFlow'
    if line_style == 'dotted':
        # Точечная линия к полосе участника — связь с внешней стороной.
        return 'messageFlow' if touches_lane else 'association'
    return 'sequenceFlow'


def map_parts(package: bytes) -> List[Tuple[str, str]]:
    """Все карты пакета в порядке манифеста: ``(имя части, XML)``.

    Первая — основная карта, остальные — страницы-подпроцессы: так пакет
    собирает ``pmm_exporter`` из многостраничного draw.io. Если манифеста нет,
    берётся порядок архива; если нет и ``pm/maps`` — любой XML с корнем ``<Map>``.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile as exc:
        raise PmmError(f'Пакет .pmm не читается как ZIP: {exc}')
    names = [n for n in archive.namelist() if not n.endswith('/')]
    maps = [n for n in names if re.fullmatch(r'pm/maps/.+\.xml', n.lstrip('/'), re.I)]
    manifest = next((n for n in names if n.lstrip('/').lower() == 'main.xml'), None)
    if manifest and maps:
        declared = archive.read(manifest).decode('utf-8', errors='replace')
        maps.sort(key=lambda n: (declared.find('/' + n.lstrip('/')) < 0, declared.find('/' + n.lstrip('/'))))
    out: List[Tuple[str, str]] = []
    for name in (maps or [n for n in names if n.lower().endswith('.xml')]):
        text = archive.read(name).decode('utf-8', errors='replace')
        if re.search(r'<Map[\s>]', text):
            out.append((name, text))
    if not out:
        raise PmmError('В пакете .pmm нет карты: не найдено ни одного pm/maps/*.xml с корнем <Map>')
    return out


def find_map_part(package: bytes) -> Tuple[str, str]:
    """Имя и XML основной карты пакета."""
    return map_parts(package)[0]


def package_parts(package: bytes) -> List[str]:
    """Состав пакета — показываем сотруднику, из чего он собран."""
    try:
        return [n for n in zipfile.ZipFile(io.BytesIO(package)).namelist() if not n.endswith('/')]
    except zipfile.BadZipFile:
        return []


def _host_step(marker: _RawNode, steps: List[ProcessNode]) -> Optional[ProcessNode]:
    """Шаг, которому принадлежит значок длительности.

    Экспортёр сажает значок НА нижнюю грань шага, поэтому его квадрат
    перекрывается с прямоугольником своего шага — по этому перекрытию шаг и
    опознаётся. Сравнение по расстоянию между центрами на плотной карте банка
    промахивалось: центр значка у края широкого шага может оказаться ближе к
    центру соседнего, и время уезжало не туда, затирая чужое.
    """
    same_lane = [s for s in steps if marker.lane_id and s.laneId == marker.lane_id]
    pool = same_lane or steps
    mx1, my1 = marker.x, marker.y
    mx2, my2 = marker.x + marker.width, marker.y + marker.height

    host: Optional[ProcessNode] = None
    best_overlap = 0.0
    best_gap = MARKER_ATTACH_RADIUS
    for step in pool:
        g = step.geometry
        overlap = (
            max(0.0, min(mx2, g.x + g.width) - max(mx1, g.x))
            * max(0.0, min(my2, g.y + g.height) - max(my1, g.y))
        )
        if overlap > best_overlap:
            best_overlap, host = overlap, step
            continue
        if best_overlap > 0:
            continue
        # Значок, зажатый в границы дорожки, мог отойти от грани: берём зазор
        # до прямоугольника шага, а не расстояние между центрами.
        gap = (
            max(g.x - mx2, 0.0, mx1 - (g.x + g.width)) ** 2
            + max(g.y - my2, 0.0, my1 - (g.y + g.height)) ** 2
        ) ** 0.5
        if gap < best_gap:
            best_gap, host = gap, step
    return host


def _make_node(raw: _RawNode, lane: Optional[ProcessNode]) -> ProcessNode:
    node = ProcessNode(
        id=raw.id,
        name=raw.label,
        type=raw.type,
        category=classify_category(raw.type, raw.label, ''),
        geometry=Geometry(
            x=int(round(raw.x)), y=int(round(raw.y)),
            width=int(round(raw.width)), height=int(round(raw.height)),
        ),
        style='',
        slaMinutes=0,
        waitMinutes=0,
        laneId=lane.id if lane else None,
        laneName=lane.name if lane else None,
        role=lane.name if lane else None,
    )
    guess_system(node, lane.name if lane else '')
    if raw.pix_type == 'intermediate_event_catch_timer':
        node.slaMinutes = int(badge_minutes(raw.label) or 0)
    return node


def parse_pmm_map_xml(xml_text: str, filename: str) -> BusinessProcess:
    """Карта ``.pmm`` -> модель процесса, без нормализации геометрии."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise PmmError(f'Карту .pmm не разобрать: {exc}')
    map_el = root if root.tag == 'Map' else root.find('.//Map')
    if map_el is None:
        raise PmmError('В карте .pmm нет корневого элемента <Map>')

    lanes: List[ProcessNode] = []
    nodes: List[ProcessNode] = []
    markers: List[_RawNode] = []
    title = ''

    def collect(raw: _RawNode, lane: Optional[ProcessNode]) -> None:
        # Граничное событие — значок длительности всегда. Мелкий таймер внутри
        # потока — только кандидат: значок он или настоящее событие, видно по
        # связям, поэтому решение откладываем.
        if raw.pix_type == DURATION_MARKER_TYPE or (
            raw.pix_type == 'intermediate_event_catch_timer'
            and raw.width <= MARKER_MAX_SIDE and raw.height <= MARKER_MAX_SIDE
        ):
            markers.append(raw)
            return
        nodes.append(_make_node(raw, lane))

    for child in list(map_el):
        if child.tag != 'node':
            continue
        raw = _RawNode(child, 0.0, 0.0)
        if raw.pix_type == 'emptypool':
            title = raw.label or title
            continue
        if raw.pix_type in ('horizontalroad', 'verticalroad'):
            lane = ProcessNode(
                id=raw.id,
                name=raw.label or f'Дорожка {len(lanes) + 1}',
                type='lane',
                role=raw.label,
                geometry=Geometry(
                    x=int(round(raw.x)), y=int(round(raw.y)),
                    width=int(round(raw.width)), height=int(round(raw.height)),
                ),
                style='swimlane;horizontal=0;startSize=30;',
            )
            lanes.append(lane)
            for inner in list(child):
                if inner.tag == 'node':
                    collect(_RawNode(inner, raw.x, raw.y, lane.id), lane)
            continue
        collect(raw, None)

    step_index = 1
    for node in nodes:
        if node.type in TASK_NODE_TYPES:
            node.code = f'STEP-{step_index:02d}'
            step_index += 1
        elif node.type == 'startEvent':
            node.code = 'START'
        elif node.type == 'endEvent':
            node.code = 'END'

    # ── Связи ───────────────────────────────────────────────────────────────
    # Кандидаты в значки тоже считаются известными: иначе их связи отсеются
    # раньше, чем мы поймём, значок это или настоящее событие потока.
    known = {n.id for n in nodes} | {l.id for l in lanes} | {m.id for m in markers}
    lane_ids = {l.id for l in lanes}
    edges: List[ProcessEdge] = []
    for connector in map_el.iter('connector'):
        source_id = connector.get('sourceNodeId')
        target_id = connector.get('targetNodeId')
        if not source_id or not target_id or source_id not in known or target_id not in known:
            continue
        style_el = connector.find('lineStyle')
        line_style = (connector.get('lineStyle')
                      or (style_el.text if style_el is not None else '')
                      or '').strip().lower()
        kind = connector_kind(line_style, _marker(connector, 'MarkerStart'),
                              _marker(connector, 'MarkerEnd'),
                              source_id in lane_ids or target_id in lane_ids)
        points = sorted(
            ((int(_num(w.get('index'))), _num(w.get('x')), _num(w.get('y')))
             for w in connector.iter('waypoint')),
            key=lambda item: item[0],
        )
        edges.append(ProcessEdge(
            id=connector.get('id') or f'conn_{len(edges)}',
            name=(connector.get('Text') or connector.get('label') or '').strip(),
            kind=kind,
            sourceId=source_id,
            targetId=target_id,
            points=[ProcessEdgePoint(x=int(round(x)), y=int(round(y))) for _, x, y in points],
            dashed=line_style in ('dotted', 'dashed') or None,
        ))

    # ── Значки длительности возвращаются в ST/WT ближайшего шага ────────────
    steps = [n for n in nodes if n.type in TASK_NODE_TYPES]
    connected = {e.sourceId for e in edges} | {e.targetId for e in edges}
    lane_by_id = {l.id: l for l in lanes}
    for marker in markers:
        if marker.id in connected:
            # Таймер, вписанный в поток связями, — событие карты, а не значок.
            nodes.append(_make_node(marker, lane_by_id.get(marker.lane_id)))
            continue
        wait_match = _WAIT_RE.search(marker.label)
        wait = badge_minutes(wait_match.group(1)) if wait_match else None
        work = badge_minutes(_WAIT_TAIL_RE.sub('', marker.label).strip())
        if not work and not wait:
            continue
        host = _host_step(marker, steps)
        if host is None:
            continue
        if work:
            host.slaMinutes = int(work)
        if wait:
            host.waitMinutes = int(wait)

    name = title or re.sub(r'\.[^.]+$', '', filename) or map_el.get('name') or 'Карта PIX'
    return assemble_opened_process(name, filename, 'PIX .pmm', nodes, lanes, edges)


def parse_pmm_package(package: bytes, filename: str) -> Tuple[BusinessProcess, str, List[str]]:
    """Пакет ``.pmm`` -> модель процесса, XML основной карты и состав пакета.

    Остальные карты пакета возвращаются страницами-подпроцессами: раньше
    читалась одна первая карта, и страницы многостраничной выгрузки терялись.
    """
    maps = map_parts(package)
    _main_name, main_xml = maps[0]
    process = parse_pmm_map_xml(main_xml, filename)
    for index, (_name, xml_text) in enumerate(maps[1:], 1):
        page = parse_pmm_map_xml(xml_text, filename)
        page.id = f'{process.id}_p{index}'
        process.subProcesses.append(page)
    return process, main_xml, package_parts(package)


def assemble_opened_process(
    process_name: str,
    filename: str,
    source_label: str,
    nodes: List[ProcessNode],
    lanes: List[ProcessNode],
    edges: List[ProcessEdge],
) -> BusinessProcess:
    """Сборка модели для карты, открытой из готового файла выгрузки.

    Паспорт, реестр и метрики модели нужны, но к содержимому выгрузки отношения
    не имеют: заполняем их правдоподобным минимумом. Геометрия при этом НЕ
    нормализуется — в отличие от импорта draw.io.
    """
    steps = [n for n in nodes if n.type in TASK_NODE_TYPES]
    total_minutes = sum((n.slaMinutes or 0) + (n.waitMinutes or 0) for n in steps)
    first_step = steps[0] if steps else (nodes[0] if nodes else None)
    suffix = uuid.uuid4().hex[:8]
    code = f'PRC-SQB-{suffix.upper()}'

    passport = ProcessPassport(
        code=code,
        name=process_name,
        version='1.0',
        status='draft',
        owner='Департамент бизнес-процессов АКБ «Узпромстройбанк»',
        department=lanes[0].name if lanes else 'Операционный блок',
        category='Банковские процессы',
        targetSlaHours=max(1.0, round(total_minutes / 60, 1)),
        description=f'Просмотр выгрузки {source_label}: файл {filename}.',
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d'),
    )
    registry = PixRegistrySchema(
        id=f'reg-{suffix}',
        code='REG_' + re.sub(r'[^A-Za-z0-9_]', '_', code),
        name=f'Реестр: {process_name}',
        description=f'Реестр заявок по процессу {process_name}',
        fields=[
            ProcessField(id='f1', code='case_number', name='Номер заявки', type='string', required=True),
            ProcessField(id='f2', code='client_inn', name='ИНН клиента', type='string', required=True),
            ProcessField(id='f3', code='status', name='Статус', type='select', required=True,
                         options=['В работе', 'Одобрено', 'Отклонено']),
        ],
        records=[],
    )
    if first_step is not None:
        registry.records.append(PixRegistryRecord(
            id='rec-preview',
            caseId='PREVIEW-0001',
            createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
            status='in_progress',
            currentStepId=first_step.id,
            currentStepName=first_step.name,
            assignedTo=first_step.role or 'Сотрудник банка',
            elapsedMinutes=0,
            data={'case_number': 'PREVIEW-0001', 'client_inn': '—', 'status': 'В работе'},
        ))

    return BusinessProcess(
        id=f'opened_{suffix}',
        name=process_name,
        fileName=filename,
        passport=passport,
        nodes=nodes,
        edges=edges,
        lanes=lanes,
        validation=collect_import_diagnostics(nodes, lanes, edges),
        registry=registry,
        miningMetrics=analyze_process_conformance(nodes, passport, len(registry.records)),
    )
