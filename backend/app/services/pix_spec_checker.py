"""Строгая проверка выгрузок под импортёр PIX Процессной студии.

Зачем отдельный модуль рядом с ``export_validation``. Тот проверяет файл по
спецификации BPMN 2.0 и по правилам, общим для импортёров (bpmn.io, Camunda):
разрешимые ссылки, уникальные ID, наличие BPMNDI. Этого достаточно, чтобы файл
открылся в браузерном редакторе, и НЕ достаточно, чтобы его принял PIX: студия
разбирает файл своим парсером и спотыкается там, где bpmn.io пожимает плечами —
на префиксе неймспейса, на дробной координате, на типе фигуры, которого нет в
её каталоге нотаций.

Отсюда разделение: ``export_validation`` отвечает на вопрос «файл соответствует
стандарту?», этот модуль — на вопрос «файл откроется именно в PIX?». Второй
строже первого и запускается после него.

ОТКУДА ВЗЯТО КАЖДОЕ ПРАВИЛО. Это важно: доступа к исходникам парсера студии у
платформы нет, и выдавать догадки за спецификацию нельзя. Поэтому у каждой
проверки в коде проставлен источник, а всего их четыре:

``catalog``
    Проверено по эталонному каталогу студии ``app/resources/pix_configuration.xml``
    — это файл самой PIX, он же уезжает внутри каждого ``.pmm``. Всё, что
    сверяется с ним (имена нотаций, типы элементов, вложенность), — факт, а не
    предположение.

``observed``
    Воспроизведено по сообщениям, которые студия выдавала на реальных отказах:
    «Notation element not found (Parameter 'type')», «Connector source and
    target node cannot be the same», — и по её собственной выгрузке
    ``tests/fixtures/sap.pmm``. Эталон стоит выше догадок: правило, которое
    его бракует, ошибочно по определению, и так снялись требование точного
    регистра имени нотации и требование целых координат ``waypoint``.

``reference``
    Соглашения формата ``.pmm``, снятые с эталонной выгрузки студии и описанные
    в ``pmm_exporter``: относительные координаты внутри дорожки, подпись связи в
    атрибуте ``Text``, полная ломаная в ``waypoint``.

``required``
    Требования, заданные командой платформы к выгрузке для PIX: жёсткие
    префиксы неймспейсов, явные ``processType``/``isExecutable``, только целые
    числа в ``dc:Bounds`` и ``di:waypoint``, строгое соответствие
    ``bpmnElement`` идентификатору узла. Формально стандарт допускает и другое
    (BPMN разрешает любой префикс и дробные координаты), но выгрузка обязана
    держать этот более узкий профиль — он и проверяется здесь.

Правила с источником ``required`` — единственные, которые нельзя подтвердить
файлом из репозитория; если студия окажется к чему-то из них терпима, правило
надо ослабить осознанно, а не тихо.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional, Set, Tuple

from app.models.process import BusinessProcess
from app.services.export_validation import ExportCheck, Problem, _local, _number_ok
from app.services.pmm_exporter import (
    generate_pmm_zip,
    notation_categories,
)

# ── Профиль неймспейсов, который обязана держать выгрузка (required) ─────────
#: Префикс -> URI. Проверяется и то, и другое: URI задаёт смысл, префикс —
#: то, как элементы выглядят в тексте файла.
_REQUIRED_NS: Dict[str, str] = {
    'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
    'dc': 'http://www.omg.org/spec/DD/20100524/DC',
    'di': 'http://www.omg.org/spec/DD/20100524/DI',
}

#: processType по спецификации — None | Private | Public.
_PROCESS_TYPES = {'None', 'Private', 'Public'}

#: Целое число без дробной части и экспоненты.
_INTEGER_RE = re.compile(r'^-?\d+$')

#: xsd:ID — имя XML, а не произвольная строка.
_NCNAME_RE = re.compile(r'^[A-Za-z_][\w.\-]*$')

#: Канонический вид UUID: студия хранит идентификаторы карты именно так.
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)

#: Узлы потока BPMN — у каждого на диаграмме обязана быть своя фигура.
_FLOW_NODES = {
    'startevent', 'endevent', 'intermediatecatchevent', 'intermediatethrowevent',
    'boundaryevent', 'task', 'usertask', 'servicetask', 'manualtask', 'scripttask',
    'sendtask', 'receivetask', 'businessruletask', 'subprocess', 'callactivity',
    'transaction', 'exclusivegateway', 'parallelgateway', 'inclusivegateway',
    'complexgateway', 'eventbasedgateway',
}

#: Связи BPMN — у каждой обязан быть свой BPMNEdge.
_FLOW_EDGES = {'sequenceflow', 'messageflow', 'association', 'dataassociation'}

#: Категория каталога, которой единственной положено содержать вложенные фигуры.
_CONTAINER_CATEGORY = 'Участники'

#: Свойства-длительности фигуры (в каталоге студии у них ``typeId="7"``).
#: Ключ не из каталога панель свойств не показывает, поэтому неизвестное имя
#: проверяется отдельным правилом ниже.
_TIMESPAN_PROPERTIES = frozenset({
    'vremya_protsessa', 'system_process_time', 'vremya_ozhidaniya',
})

#: Длительность студия пишет в свою выгрузку целым числом секунд
#: (``<prop … value="7200">``, PIX банка, 2026-09-22).
_TIMESPAN_RE = re.compile(r'^\d+$')


def _add(check: ExportCheck, level: str, code: str, message: str,
         where: Optional[str] = None) -> None:
    check.problems.append(Problem(level, code, message, where))


#: Порядок детей по схеме BPMN 2.0 (Semantic.xsd). ``*root`` — любой корневой
#: элемент, ``*flow`` — любой элемент потока, ``*artifact`` — артефакт.
#:
#: observed: PIX разбирает файл строго по схеме, и элемент не на своём месте
#: валит импорт целиком — «Импорт невозможен. Файл не является диаграммой
#: BPMN 2.0». Проба на PIX банка (PIX Процессы 2.3.2): ассоциация перед
#: потоком сообщений в collaboration — отказ, та же ассоциация после него —
#: карта открылась.
_CHILD_ORDER: Dict[str, Tuple[str, ...]] = {
    'definitions': ('import', 'extension', '*root', 'bpmndiagram', 'relationship'),
    'collaboration': ('documentation', 'extensionelements', 'participant', 'messageflow', '*artifact'),
    'process': ('documentation', 'extensionelements', 'auditing', 'monitoring', 'property',
                'laneset', '*flow', '*artifact'),
    '*activity': ('documentation', 'extensionelements', 'incoming', 'outgoing', 'iospecification',
                  'property', 'datainputassociation', 'dataoutputassociation'),
}
_ACTIVITY_TAGS = {
    'task', 'usertask', 'servicetask', 'manualtask', 'scripttask', 'sendtask', 'receivetask',
    'businessruletask', 'subprocess', 'callactivity', 'transaction',
}
_ARTIFACT_TAGS = {'association', 'textannotation', 'group'}


def _child_rank(order: Tuple[str, ...], tag: str) -> Optional[int]:
    if tag in order:
        return order.index(tag)
    for group, members in (('*artifact', _ARTIFACT_TAGS), ('*root', None), ('*flow', None)):
        if group in order and (members is None or tag in members):
            return order.index(group)
    return None


def _declared_namespaces(xml: str) -> Dict[str, str]:
    """Объявленные в файле префиксы неймспейсов.

    ElementTree разворачивает префиксы в ``{uri}tag`` и сам префикс теряет,
    а проверять надо именно его: файл с ``xmlns:b=…MODEL`` формально
    эквивалентен нашему, но выглядит иначе, и профиль выгрузки этого не
    допускает. Поэтому читаем события ``start-ns`` — единственный способ
    увидеть префиксы штатными средствами.
    """
    found: Dict[str, str] = {}
    try:
        for event, payload in ET.iterparse(io.StringIO(xml), events=('start-ns',)):
            prefix, uri = payload
            found.setdefault(prefix, uri)
    except ET.ParseError:
        pass
    return found


# ────────────────────────────── BPMN 2.0 ──────────────────────────────────

def validate_bpmn_for_pix(xml: str) -> ExportCheck:
    """Проверяет BPMN профилем PIX. Никогда не падает: стоит на пути выгрузки."""
    check = ExportCheck(format='bpmn/pix')
    try:
        _validate_bpmn_for_pix(xml, check)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        _add(check, 'error', 'pix_bpmn_unreadable',
             f'Проверка профиля PIX не смогла разобрать файл: {exc}.')
    return check


def _validate_bpmn_for_pix(xml: str, check: ExportCheck) -> None:
    # ── Неймспейсы и префиксы (required) ────────────────────────────────────
    declared = _declared_namespaces(xml)
    for prefix, uri in _REQUIRED_NS.items():
        actual = declared.get(prefix)
        if actual is None:
            owner = next((p for p, u in declared.items() if u == uri), None)
            hint = f' (тот же URI объявлен под префиксом «{owner}»)' if owner else ''
            _add(check, 'error', 'pix_ns_prefix_missing',
                 f'Не объявлен префикс «{prefix}» для {uri}{hint}. '
                 'Профиль выгрузки для PIX требует именно эти префиксы.')
        elif actual != uri:
            _add(check, 'error', 'pix_ns_prefix_wrong',
                 f'Префикс «{prefix}» привязан к {actual}, ожидался {uri}.')

    try:
        root = ET.fromstring(xml.encode('utf-8'))
    except ET.ParseError as exc:
        _add(check, 'error', 'pix_bpmn_broken', f'Файл не разбирается как XML: {exc}.')
        return
    if _local(root.tag) != 'definitions':
        _add(check, 'error', 'pix_bpmn_root',
             f'Корень — <{root.tag}>, ожидался <bpmn:definitions>.')
        return

    if not (root.get('targetNamespace') or '').strip():
        _add(check, 'error', 'pix_target_namespace',
             'У <bpmn:definitions> пуст targetNamespace.')
    if not (root.get('id') or '').strip():
        _add(check, 'error', 'pix_definitions_id',
             'У <bpmn:definitions> нет id — студия адресует по нему всю модель.')

    # ── Порядок элементов по схеме (observed) ───────────────────────────────
    for holder in root.iter():
        tag = _local(holder.tag)
        order = _CHILD_ORDER.get(tag) or (_CHILD_ORDER['*activity'] if tag in _ACTIVITY_TAGS else None)
        if order is None:
            continue
        reached, previous = -1, ''
        for child in holder:
            child_tag = _local(child.tag)
            rank = _child_rank(order, child_tag)
            if rank is None:
                continue
            if rank < reached:
                _add(check, 'error', 'pix_bpmn_child_order',
                     f'В <{tag}> элемент <{child_tag}> стоит после <{previous}>, а схема '
                     'BPMN 2.0 требует обратного порядка. PIX такой файл не открывает: '
                     '«Файл не является диаграммой BPMN 2.0».', holder.get('id'))
                break
            reached, previous = rank, child_tag

    by_id: Dict[str, ET.Element] = {}
    for el in root.iter():
        eid = el.get('id')
        if eid:
            by_id.setdefault(eid, el)

    def label(eid: str) -> str:
        el = by_id.get(eid)
        return (el.get('name') if el is not None else None) or eid

    # ── Процессы: явные атрибуты (required) ─────────────────────────────────
    processes = [el for el in root.iter() if _local(el.tag) == 'process']
    if not processes:
        _add(check, 'error', 'pix_no_process', 'В файле нет ни одного <bpmn:process>.')
    for proc in processes:
        pid = proc.get('id') or ''
        if not pid:
            _add(check, 'error', 'pix_process_no_id', 'У <bpmn:process> нет id.')
        elif not _NCNAME_RE.match(pid):
            _add(check, 'error', 'pix_process_id_ncname',
                 f'id процесса «{pid}» не является именем XML.', pid)
        if proc.get('isExecutable') is None:
            _add(check, 'error', 'pix_process_is_executable',
                 f'У процесса «{label(pid)}» не задан isExecutable — '
                 'профиль PIX требует его явно.', pid)
        elif proc.get('isExecutable') not in ('true', 'false'):
            _add(check, 'error', 'pix_process_is_executable',
                 f'isExecutable у процесса «{label(pid)}» — '
                 f'«{proc.get("isExecutable")}», ожидается true или false.', pid)
        kind = proc.get('processType')
        if kind is None:
            _add(check, 'error', 'pix_process_type',
                 f'У процесса «{label(pid)}» не задан processType — '
                 'профиль PIX требует его явно.', pid)
        elif kind not in _PROCESS_TYPES:
            _add(check, 'error', 'pix_process_type',
                 f'processType «{kind}» у процесса «{label(pid)}» вне '
                 f'допустимых значений ({", ".join(sorted(_PROCESS_TYPES))}).', pid)

    # ── Диаграмма: идентификаторы и привязка (required) ─────────────────────
    diagrams = [el for el in root.iter() if _local(el.tag) == 'bpmndiagram']
    planes = [el for el in root.iter() if _local(el.tag) == 'bpmnplane']
    if not diagrams:
        _add(check, 'error', 'pix_no_diagram',
             'Нет <bpmndi:BPMNDiagram> — студии нечего рисовать.')
    for diagram in diagrams:
        if not (diagram.get('id') or '').strip():
            _add(check, 'error', 'pix_diagram_id', 'У <bpmndi:BPMNDiagram> нет id.')
    for plane in planes:
        if not (plane.get('id') or '').strip():
            _add(check, 'error', 'pix_plane_id', 'У <bpmndi:BPMNPlane> нет id.')
        anchor = plane.get('bpmnElement')
        if not anchor:
            _add(check, 'error', 'pix_plane_anchor',
                 'У <bpmndi:BPMNPlane> не задан bpmnElement.', plane.get('id'))
        elif anchor not in by_id:
            _add(check, 'error', 'pix_plane_anchor',
                 f'<bpmndi:BPMNPlane> ссылается на несуществующий {anchor}.',
                 plane.get('id'))

    # ── Геометрия: только целые числа (required) ───────────────────────────
    # Дробная координата — обычный результат импорта из draw.io, где фигуру
    # подвинули мышью: x="500.0000000000002". Стандарт такое допускает, профиль
    # выгрузки — нет, и округлять надо на нашей стороне, а не надеяться на чужой
    # разбор.
    drawn: Dict[str, int] = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ('bpmnshape', 'bpmnedge'):
            continue
        anchor = el.get('bpmnElement') or ''
        drawn[anchor] = drawn.get(anchor, 0) + 1
        if not anchor:
            _add(check, 'error', 'pix_di_no_anchor',
                 f'У <{tag}> не задан bpmnElement.', el.get('id'))
        elif anchor not in by_id:
            _add(check, 'error', 'pix_di_dangling',
                 f'<{tag}> нарисован для несуществующего элемента {anchor}.', anchor)

        if tag == 'bpmnshape':
            bounds = [c for c in el if _local(c.tag) == 'bounds']
            if not bounds:
                _add(check, 'error', 'pix_shape_no_bounds',
                     f'У фигуры «{label(anchor)}» нет <dc:Bounds>.', anchor)
                continue
            box = bounds[0]
            for attr in ('x', 'y', 'width', 'height'):
                raw = box.get(attr)
                if raw is None:
                    _add(check, 'error', 'pix_bounds_missing',
                         f'В <dc:Bounds> фигуры «{label(anchor)}» нет «{attr}».', anchor)
                elif not _INTEGER_RE.match(raw.strip()):
                    _add(check, 'error', 'pix_bounds_not_integer',
                         f'В <dc:Bounds> фигуры «{label(anchor)}» «{attr}»="{raw}" — '
                         'профиль PIX допускает только целые числа.', anchor)
            if _number_ok(box.get('width')) and float(box.get('width')) <= 0:
                _add(check, 'error', 'pix_shape_zero_size',
                     f'У фигуры «{label(anchor)}» нулевая ширина.', anchor)
            if _number_ok(box.get('height')) and float(box.get('height')) <= 0:
                _add(check, 'error', 'pix_shape_zero_size',
                     f'У фигуры «{label(anchor)}» нулевая высота.', anchor)
        else:
            points = [c for c in el if _local(c.tag) == 'waypoint']
            if len(points) < 2:
                _add(check, 'error', 'pix_edge_waypoints',
                     f'У связи «{label(anchor)}» {len(points)} точек маршрута, '
                     'нужно минимум две.', anchor)
            for point in points:
                for attr in ('x', 'y'):
                    raw = point.get(attr)
                    if raw is None or not _INTEGER_RE.match(raw.strip()):
                        _add(check, 'error', 'pix_waypoint_not_integer',
                             f'У связи «{label(anchor)}» точка маршрута '
                             f'«{attr}»="{raw}" — нужны целые числа.', anchor)
                        break

    for anchor, times in drawn.items():
        if times > 1:
            _add(check, 'error', 'pix_di_duplicate',
                 f'Элемент «{label(anchor)}» нарисован {times} раза.', anchor)

    # ── Каждому узлу — своя фигура, каждой связи — своя линия (required) ────
    for el in root.iter():
        tag = _local(el.tag)
        eid = el.get('id')
        if not eid:
            continue
        if tag in _FLOW_NODES and eid not in drawn:
            _add(check, 'error', 'pix_node_not_drawn',
                 f'У узла «{label(eid)}» нет <bpmndi:BPMNShape> — '
                 'студия не примет узел без координат.', eid)
        elif tag in _FLOW_EDGES and eid not in drawn:
            _add(check, 'error', 'pix_edge_not_drawn',
                 f'У связи «{label(eid)}» нет <bpmndi:BPMNEdge>.', eid)


# ──────────────────────────────── PIX .pmm ─────────────────────────────────

def validate_pmm_for_pix(payload: bytes) -> ExportCheck:
    """Проверяет пакет .pmm профилем студии. Никогда не падает."""
    check = ExportCheck(format='pmm/pix')
    try:
        _validate_pmm_for_pix(payload, check)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        _add(check, 'error', 'pix_pmm_unreadable',
             f'Проверка профиля PIX не смогла разобрать пакет: {exc}.')
    return check


def _validate_pmm_for_pix(payload: bytes, check: ExportCheck) -> None:
    try:
        package = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        _add(check, 'error', 'pix_pmm_not_zip', f'Пакет не читается как ZIP: {exc}.')
        return

    names = set(package.namelist())
    maps = sorted(n for n in names if n.startswith('pm/maps/') and n.endswith('.xml'))
    if not maps:
        _add(check, 'error', 'pix_pmm_no_map', 'В пакете нет карты pm/maps/*.xml.')
        return
    if 'pm/configuration.xml' not in names:
        _add(check, 'error', 'pix_pmm_no_config',
             'В пакете нет pm/configuration.xml — студии нечем опознать типы фигур.')
        return

    config = ET.fromstring(package.read('pm/configuration.xml').decode('utf-8'))
    # Карт в пакете бывает несколько — главная и дочерние (``pmm_tree``), и
    # каждая проверяется целиком. Идентификаторы общие на весь пакет: по ним
    # дочерняя карта находит раскрывающий её подпроцесс.
    seen: Set[str] = set()
    parents: Dict[str, str] = {}
    openers: Dict[str, str] = {}
    for map_part in maps:
        root = ET.fromstring(package.read(map_part).decode('utf-8'))
        _validate_pmm_map(root, map_part, config, check, seen)
        if root.get('parentProcessId'):
            parents[map_part] = root.get('parentProcessId') or ''
        for node in root.iter('node'):
            if node.get('type') == 'sub_process_collapsed' and node.get('id'):
                openers[node.get('id')] = map_part
    _validate_map_tree(maps, parents, openers, check)


def _validate_map_tree(maps: List[str], parents: Dict[str, str], openers: Dict[str, str],
                       check: ExportCheck) -> None:
    """Дерево карт пакета — так, как его хранит сама студия.

    Главная карта ровно одна, без ``parentProcessId``; у каждой дочерней он
    равен идентификатору свёрнутого подпроцесса (``sub_process_collapsed``)
    другой карты пакета — так устроена выгрузка «SANOATQURILISHBANK» самой
    PIX. Пакет из 18 главных карт студия отвергла целиком: «Sequence contains
    more than one matching element».
    """
    roots = [part for part in maps if part not in parents]
    if len(roots) != 1:
        _add(check, 'error', 'pix_pmm_roots',
             f'Главных карт (без parentProcessId) в пакете {len(roots)}, а должна быть '
             'одна: остальные студия ждёт дочерними картами подпроцессов.')
    opened: Dict[str, str] = {}
    for part, parent in parents.items():
        owner = openers.get(parent)
        if owner is None:
            _add(check, 'error', 'pix_pmm_parent_missing',
                 f'Карту «{part}» раскрывает подпроцесс {parent}, которого нет ни на '
                 'одной карте пакета.', part)
        elif owner == part:
            _add(check, 'error', 'pix_pmm_parent_self',
                 f'Карту «{part}» раскрывает подпроцесс на ней же самой.', part)
        elif parent in opened:
            _add(check, 'error', 'pix_pmm_parent_twice',
                 f'Один подпроцесс раскрывает сразу две карты: «{opened[parent]}» и «{part}».', part)
        opened[parent] = part


def _validate_pmm_map(root: ET.Element, map_part: str, config: ET.Element,
                      check: ExportCheck, seen: Set[str]) -> None:
    """Одна карта пакета: нотация, имя, фигуры, связи, дорожки."""

    # ── Нотация карты объявлена в каталоге (observed) ───────────────────────
    # Регистр имени не значит ничего: каталог объявляет нотацию как ``BPMN``,
    # а сама студия в своей выгрузке пишет ``notation="bpmn"``
    # (tests/fixtures/sap.pmm). Сверка точным равенством забраковала бы файл,
    # сделанный самой PIX, поэтому имя приводим к нижнему регистру, а дальше
    # работаем с каноническим написанием из каталога.
    notation = root.get('notation') or ''
    catalogue: Dict[str, Set[str]] = {
        n.get('name'): {e.get('name') for e in n.findall('element') if e.get('name')}
        for n in config.iter('notation') if n.get('name')
    }
    # Категория элемента («Задачи», «Шлюзы», «События»…). Имени в каталоге
    # мало: студия идёт за категорией и на её отсутствии падает с сообщением,
    # где параметр так и назван — ``'type'``. В нотации BPMN категории нет
    # ровно у одного элемента из 91 — ``input`` («Текст»). Источник: catalog.
    element_category: Dict[str, Dict[str, str]] = {
        n.get('name'): {
            e.get('name'): e.get('type') or ''
            for e in n.findall('element') if e.get('name')
        }
        for n in config.iter('notation') if n.get('name')
    }
    canonical = {name.lower(): name for name in catalogue}
    notation_name = canonical.get(notation.lower())
    allowed = catalogue.get(notation_name) if notation_name else None
    categories = element_category.get(notation_name or '', {})
    if allowed is None:
        _add(check, 'error', 'pix_pmm_notation',
             f'Нотация «{notation}» не объявлена в каталоге студии. '
             f'Есть: {", ".join(sorted(catalogue)) or "ни одной"}.', map_part)
        allowed = set()

    # Имя карты и имя части совпадают (reference).
    expected = map_part[len('pm/maps/'):-len('.xml')]
    if (root.get('name') or '') != expected:
        _add(check, 'error', 'pix_pmm_map_name',
             f'Имя карты «{root.get("name")}» не совпадает с именем части «{expected}».',
             map_part)

    # Дальше по каталогу ходим под каноническим именем: с ``bpmn`` из файла
    # ни категории, ни ``canHaveChildren`` не нашлись бы, и дорожка с шагами
    # внутри выглядела бы недопустимым вложением.
    lookup = notation_name or notation
    #: Имена шаблонов свойств из каталога: панель свойств строится по ним.
    property_names = {
        t.get('name') for t in config.findall('propertyTemplate') if t.get('name')
    }
    categories = notation_categories(config, lookup)
    containers = {name for name, kind in categories.items() if kind == _CONTAINER_CATEGORY}
    with_children = {
        e.get('name')
        for n in config.iter('notation') if n.get('name') == lookup
        for e in n.findall('element') if e.get('canHaveChildren') == 'true'
    }

    # ── Фигуры ──────────────────────────────────────────────────────────────
    def walk(parent: ET.Element, box: Optional[Tuple[float, float]], depth: int) -> None:
        for node in parent:
            if node.tag != 'node':
                continue
            nid = node.get('id') or ''
            name = node.get('label') or nid or '(без подписи)'
            kind = node.get('type') or ''

            # Идентификатор — канонический UUID (reference).
            if not _UUID_RE.match(nid):
                _add(check, 'error', 'pix_pmm_id_not_uuid',
                     f'Идентификатор фигуры «{name}» — «{nid}», ожидается UUID.', nid)
            if nid in seen:
                _add(check, 'error', 'pix_pmm_duplicate_id',
                     f'Идентификатор фигуры «{name}» повторяется.', nid)
            seen.add(nid)

            # Тип есть в нотации карты (catalog + observed: студия отвечает
            # «Notation element not found (Parameter 'type')»).
            if not kind:
                _add(check, 'error', 'pix_pmm_no_type',
                     f'У фигуры «{name}» не указан type.', nid)
            elif allowed and kind not in allowed:
                _add(check, 'error', 'pix_pmm_type_unknown',
                     f'Тип «{kind}» фигуры «{name}» не объявлен в нотации '
                     f'«{notation}».', nid)
            elif not categories.get(kind, ''):
                _add(check, 'error', 'pix_pmm_type_no_category',
                     f'У типа «{kind}» фигуры «{name}» в каталоге студии не '
                     f'проставлена категория — студия откажется открыть пакет.',
                     nid)

            # Геометрия — целые числа (required).
            geometry: Dict[str, float] = {}
            for attr in ('x', 'y', 'width', 'height'):
                raw = node.get(attr)
                if raw is None:
                    _add(check, 'error', 'pix_pmm_geometry',
                         f'У фигуры «{name}» нет координаты «{attr}».', nid)
                elif not _INTEGER_RE.match(raw.strip()):
                    _add(check, 'error', 'pix_pmm_geometry_not_integer',
                         f'У фигуры «{name}» «{attr}»="{raw}" — нужны целые числа.', nid)
                else:
                    geometry[attr] = float(raw)
            if geometry.get('width', 1) <= 0 or geometry.get('height', 1) <= 0:
                _add(check, 'error', 'pix_pmm_zero_size',
                     f'У фигуры «{name}» нулевой размер.', nid)

            # Свойства фигуры: ``<prop inner_name=… value=…>``, время — секунды.
            for prop in node.findall('prop'):
                pname = prop.get('inner_name') or ''
                value = prop.get('value')
                if not pname:
                    _add(check, 'error', 'pix_pmm_property_no_name',
                         f'У свойства фигуры «{name}» не указано имя.', nid)
                elif pname not in property_names:
                    # Ключ не из каталога студия принимает молча: значение
                    # оседает теневым атрибутом, файл открывается без
                    # ошибок, а в панели свойств поля просто нет. Дефект
                    # без этого правила обнаруживается только глазами.
                    _add(check, 'error', 'pix_pmm_property_unknown',
                         f'Свойство «{pname}» фигуры «{name}» не объявлено '
                         'в каталоге студии: значение сохранится, но в '
                         'панели свойств не появится.', nid)
                if pname in _TIMESPAN_PROPERTIES and not _TIMESPAN_RE.match(value or ''):
                    _add(check, 'error', 'pix_pmm_property_timespan',
                         f'Свойство «{pname}» фигуры «{name}» = «{value}»: '
                         'ожидается целое число секунд, как в выгрузке '
                         'самой студии.', nid)

            # Вложение: держать детей вправе только участники и элементы,
            # помеченные canHaveChildren (catalog).
            children = [c for c in node if c.tag == 'node']
            if children and kind not in containers and kind not in with_children:
                _add(check, 'error', 'pix_pmm_nesting',
                     f'Фигура «{name}» типа «{kind}» содержит вложенные фигуры, '
                     'хотя каталог студии этого не допускает.', nid)

            # Координаты ребёнка отсчитываются от родителя и обязаны лежать
            # внутри него (reference): дорожка рисуется по своим границам, и
            # шаг, выехавший за них, в студии оказывается в чужой дорожке.
            if depth > 0 and box and 'x' in geometry and 'width' in geometry:
                pw, ph = box
                if (geometry['x'] < 0 or geometry['y'] < 0
                        or geometry['x'] + geometry['width'] > pw
                        or geometry['y'] + geometry['height'] > ph):
                    _add(check, 'error', 'pix_pmm_child_outside',
                         f'Фигура «{name}» выходит за границы своей дорожки '
                         f'({geometry["x"]:.0f},{geometry["y"]:.0f} '
                         f'{geometry["width"]:.0f}×{geometry["height"]:.0f} '
                         f'при дорожке {pw:.0f}×{ph:.0f}).', nid)

            walk(node, (geometry.get('width', 0), geometry.get('height', 0)), depth + 1)

    walk(root, None, 0)
    if root.find('.//node') is None:
        _add(check, 'error', 'pix_pmm_empty', 'В карте нет ни одной фигуры.', map_part)

    # ── Связи ───────────────────────────────────────────────────────────────
    map_ids = {n.get('id') for n in root.iter('node')}
    for connector in root.iter('connector'):
        cid = connector.get('id') or ''
        src = connector.get('sourceNodeId') or ''
        tgt = connector.get('targetNodeId') or ''
        if not _UUID_RE.match(cid):
            _add(check, 'error', 'pix_pmm_id_not_uuid',
                 f'Идентификатор связи «{cid}» не является UUID.', cid)
        if cid in seen:
            _add(check, 'error', 'pix_pmm_duplicate_id',
                 f'Идентификатор связи {cid} уже занят фигурой.', cid)
        seen.add(cid)

        # observed: «Connector source and target node cannot be the same».
        if src and src == tgt:
            _add(check, 'error', 'pix_pmm_self_loop',
                 'Связь начинается и заканчивается на одной фигуре.', cid)
        for role, ref in (('источник', src), ('приёмник', tgt)):
            if not ref:
                _add(check, 'error', 'pix_pmm_connector_end',
                     f'У связи не задан {role}.', cid)
            elif ref not in map_ids:
                _add(check, 'error', 'pix_pmm_connector_end',
                     f'{role.capitalize()} связи ссылается на несуществующую фигуру.', cid)

        # reference: подпись связи студия читает из Text, атрибут label
        # игнорирует — подписи веток шлюзов иначе теряются.
        if connector.get('label') and not connector.get('Text'):
            _add(check, 'error', 'pix_pmm_connector_label',
                 'Подпись связи задана атрибутом label; студия читает Text.', cid)

        points = [w for w in connector if w.tag == 'waypoint']
        if points and len(points) < 2:
            _add(check, 'error', 'pix_pmm_waypoints',
                 f'У связи одна точка маршрута — нужны либо две и больше, '
                 'либо ни одной.', cid)
        # observed: требовать здесь целые числа было ошибкой. Студия сама
        # пишет точки маршрута дробными и с накопленной погрешностью
        # (`x="1704.0000385955093"` в tests/fixtures/sap.pmm), так что правило
        # забраковало бы её собственную выгрузку. Проверяем только то, что
        # координата вообще число: разбор нечислового значения студия не
        # переживёт.
        for point in points:
            if not all(_number_ok((point.get(a) or '').strip()) for a in ('x', 'y')):
                _add(check, 'error', 'pix_pmm_waypoint_not_number',
                     'У точки маршрута связи координаты не числовые.', cid)
                break

    # ── Роли: дорожка без подписи оставляет шаг без ответственного ──────────
    for node in root.iter('node'):
        if node.get('type') in containers and not (node.get('label') or '').strip():
            _add(check, 'warning', 'pix_pmm_lane_unnamed',
                 'У дорожки нет подписи — в студии шаги внутри неё останутся '
                 'без ответственного подразделения.', node.get('id'))


def validate_for_pix(process: BusinessProcess) -> List[ExportCheck]:
    """Выгрузка процесса для PIX, проверенная профилем студии.

    В PIX карта уезжает только пакетом .pmm: импорт BPMN студия перекладывает
    по-своему (растягивает карту, дублирует фигуры у границ дорожек, теряет
    связи «пул ↔ шлюз»), и время шага в BPMN не передать.
    """
    return [validate_pmm_for_pix(generate_pmm_zip(process))]
