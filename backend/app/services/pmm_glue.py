"""Склейка: части большой карты из PIX обратно в один процесс — для Infomaximum.

Большую карту выгрузка режет для PIX на части (``pmm_tree``): одну страницу
студия иначе не открывает. Infomaximum же каждую карту PIX читает отдельным
процессом, и семь частей «Mukammal va joriy ta'mirlash…» были бы для него семью
обрывками. Поэтому перед отправкой в Infomaximum выгрузка PIX с дочерними
картами собирается обратно: части одной страницы — в одну карту.

Склеивать есть по чему, потому что разрез делаем мы сами:

* части узнаются по имени карты: «Часть 2 из 7 · шаги 101–189 — Страница»
  (и прежнее «Страница — часть 2 из 7»);
* координаты фигур при разрезе не менялись — части лежат рядом, как на
  исходной карте;
* дорожка в каждой части обрезана по краям части: куски одной дорожки —
  те, что в соседних частях упираются в общий край или лежат друг на друге, с
  той же подписью, высотой и местом по вертикали. Одноимённые полосы в разных
  местах карты (у Mukammal пять «Loyiha tashkiloti») так и остаются разными;
  копия полосы у края части помечена «(часть N)» и возвращается на своё место;
* линия через разрез — пара событий: «в часть N[, шаг S]» у источника и «из
  части M[, шаг R]» у приёмника. Пара ищется по номерам частей, номерам шагов
  и месту; найденная пара снова становится одной линией.

Работа идёт по XML карт студии, а не через модель платформы: после PIX важны
его свойства (время шага, стоимость), и они переносятся как есть. Что не
сошлось, склейка не угадывает, а называет в отчёте.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

THROW_LINK = 'intermediate_event_throw_link'
CATCH_LINK = 'intermediate_event_catch_link'
_LANE_TYPES = ('horizontalRoad', 'verticalRoad')
_ARTIFACT_TYPES = ('dataStorage', 'dataObject', 'emptyPool')

_PART_NAMES = (
    (re.compile(r'^Часть (\d+) из (\d+)(?: · .*?)? — (.+)$'), (3, 1, 2)),
    (re.compile(r'^(.+) — часть (\d+) из (\d+)(?: \(\d+\))?$'), (1, 2, 3)),
)
_TO_RE = re.compile(r'в\s+часть\s+(\d+)(?:,\s*шаг\s+([\d.]+))?')
_FROM_RE = re.compile(r'из\s+части\s+(\d+)(?:,\s*шаг\s+([\d.]+))?')
_STEP_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)')
_MOVED_RE = re.compile(r'^(.*) \(часть (\d+)\)$')
#: Насколько края кусков одной дорожки могут не совпасть, px.
_EDGE_TOLERANCE = 2


def _text(value: Optional[str]) -> str:
    return ' '.join((value or '').replace(' ', ' ').split())


def part_of_name(name: str) -> Optional[Tuple[str, int, int]]:
    """``(страница, номер части, всего частей)`` по имени карты; ``None`` — не часть."""
    for pattern, (page, index, total) in _PART_NAMES:
        match = pattern.match(_text(name))
        if match:
            return match.group(page), int(match.group(index)), int(match.group(total))
    return None


@dataclass
class Shape:
    """Фигура карты в абсолютных координатах."""
    el: ET.Element
    id: str
    type: str
    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Part:
    name: str
    page: str
    index: int
    total: int
    lanes: List[Shape]
    shapes: List[Shape]
    connectors: List[ET.Element]

    @property
    def frame(self) -> Tuple[float, float]:
        """Левый и правый край части — по дорожкам, обрезанным по нему."""
        return (min(l.x for l in self.lanes), max(l.x + l.w for l in self.lanes))


@dataclass
class GlueReport:
    page: str
    parts: int = 0
    stitched: int = 0
    copies_merged: int = 0
    lanes: int = 0
    problems: List[str] = field(default_factory=list)


def _flatten(map_el: ET.Element) -> Tuple[List[Shape], List[Shape]]:
    lanes: List[Shape] = []
    shapes: List[Shape] = []

    def walk(parent: ET.Element, ox: float, oy: float) -> None:
        for el in parent.findall('node'):
            x, y = ox + float(el.get('x') or 0), oy + float(el.get('y') or 0)
            shape = Shape(el, el.get('id') or '', el.get('type') or '', _text(el.get('label')),
                          x, y, float(el.get('width') or 0), float(el.get('height') or 0))
            (lanes if shape.type in _LANE_TYPES else shapes).append(shape)
            walk(el, x, y)
    walk(map_el, 0.0, 0.0)
    return lanes, shapes


def read_package(package: bytes) -> List[Tuple[str, ET.Element]]:
    archive = zipfile.ZipFile(io.BytesIO(package))
    return [(n, ET.fromstring(archive.read(n))) for n in archive.namelist()
            if n.startswith('pm/maps/') and n.endswith('.xml')]


def find_parts(package: bytes) -> Dict[Tuple[str, str], List[Part]]:
    """Части по страницам: ``(карта-обзор, строка обзора) -> части по порядку``.

    Части одной страницы раскрываются подпроцессами одной строки (дорожки)
    обзорной карты — по ней их и собираем. Имя для этого не годится: длинное
    имя части студия получает обрезанным, и в разных частях оно обрезано в
    разных местах, а у «Харид…» три страницы называются одинаково.
    """
    maps = read_package(package)
    row_of_opener: Dict[str, Tuple[str, str, str, float]] = {}
    for name, map_el in maps:
        for top in map_el.findall('node'):
            nested = top.findall('node') if top.get('type') in _LANE_TYPES else [top]
            for node in nested:
                if node.get('type') == 'sub_process_collapsed':
                    row = top.get('id') if top.get('type') in _LANE_TYPES else ''
                    label = _text(top.get('label')) if row else ''
                    row_of_opener[node.get('id')] = (name, row, label, float(node.get('x') or 0))
    groups: Dict[Tuple[str, str], List[Tuple[float, Part]]] = {}
    for _name, map_el in maps:
        parsed = part_of_name(map_el.get('name') or '')
        opener = row_of_opener.get(map_el.get('parentProcessId') or '')
        if parsed is None or opener is None:
            continue
        page, index, total = parsed
        overview, row, row_label, x = opener
        if row_label and not row_label.endswith('…'):
            page = row_label
        lanes, shapes = _flatten(map_el)
        # Плашка с именем части — оформление самой части, в процессе её нет.
        shapes = [s for s in shapes if not (s.type == 'emptyPool' and part_of_name(s.label))]
        part = Part(map_el.get('name') or '', page, index, total, lanes, shapes,
                    list(map_el.findall('connector')))
        groups.setdefault((overview, row), []).append((x, part))
    return {key: [part for _x, part in sorted(items, key=lambda item: (item[1].index, item[0]))]
            for key, items in groups.items()}


def _same_row(a: Shape, b: Shape) -> bool:
    return (a.label == b.label and abs(a.y - b.y) <= _EDGE_TOLERANCE
            and abs(a.h - b.h) <= _EDGE_TOLERANCE)


def _continues(a: Shape, a_right: float, b: Shape, b_left: float) -> bool:
    """Кусок ``b`` следующей части продолжает кусок ``a``: оба упёрлись в общий
    край частей или лежат друг на друге (края соседних частей заходят друг за
    друга на поля части)."""
    at_edges = abs(a.x + a.w - a_right) <= _EDGE_TOLERANCE and abs(b.x - b_left) <= _EDGE_TOLERANCE
    overlap = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    return _same_row(a, b) and (at_edges or overlap > _EDGE_TOLERANCE)


def _merge_lanes(parts: Sequence[Part]) -> Tuple[List[Shape], Dict[str, int], List[str]]:
    """Куски одной дорожки — в одну.

    Возвращает дорожки, номер дорожки у каждого куска и замечания. Кусок
    продолжается куском той же строки в следующей части (``_continues``).
    Полоса участника, к которой идёт пунктир из чужой части, стоит там копией у
    края, с пометкой «(часть N)» в подписи (``pmm_tree._cut``), — она
    возвращается к куску той же строки в части N.
    """
    merged: List[List[Shape]] = []
    owner: Dict[str, int] = {}
    moved: List[Tuple[Shape, int]] = []
    problems: List[str] = []
    prev: List[Tuple[int, Shape]] = []
    prev_right = 0.0
    for part in parts:
        left, right = part.frame
        current: List[Tuple[int, Shape]] = []
        for lane in part.lanes:
            copy_of = _MOVED_RE.match(lane.label)
            if copy_of:
                moved.append((replace(lane, label=copy_of.group(1)), int(copy_of.group(2))))
                continue
            index = next((i for i, p in prev if _continues(p, prev_right, lane, left)), None)
            if index is None:
                merged.append([])
                index = len(merged) - 1
            merged[index].append(lane)
            owner[lane.id] = index
            current.append((index, lane))
        prev, prev_right = current, right

    part_at = {part.index: part for part in parts}
    for lane, home in moved:
        pieces = part_at[home].lanes if home in part_at else []
        index = next((owner[p.id] for p in pieces if p.id in owner and _same_row(p, lane)), None)
        if index is None:
            problems.append(f'полоса «{lane.label}» из части {home} не нашлась на своём месте')
            lane.el = copy.deepcopy(lane.el)
            lane.el.set('label', lane.label)
            merged.append([lane])
            index = len(merged) - 1
        owner[lane.id] = index

    lanes: List[Shape] = []
    for pieces in merged:
        first = pieces[0]
        x0 = min(p.x for p in pieces)
        x1 = max(p.x + p.w for p in pieces)
        lanes.append(Shape(first.el, first.id, first.type, first.label, x0, first.y, x1 - x0, first.h))
    return lanes, owner, problems


def _step(label: str) -> Optional[str]:
    match = _STEP_RE.match(label or '')
    return match.group(1) if match else None


def glue_parts(parts: Sequence[Part]) -> Tuple[ET.Element, GlueReport]:
    """Части одной страницы -> одна карта студии и отчёт о склейке."""
    report = GlueReport(parts[0].page, parts=len(parts))
    for expected, part in enumerate(parts, 1):
        if part.index != expected:
            report.problems.append(f'нет части {expected} из {part.total}')
            break
    lanes, lane_of_piece, lane_problems = _merge_lanes(parts)
    report.lanes = len(lanes)
    report.problems += lane_problems

    shape_by_id: Dict[str, Shape] = {}
    part_of_shape: Dict[str, int] = {}
    alias: Dict[str, str] = {}
    kept: List[Shape] = []
    seen_artifacts: Dict[Tuple, str] = {}
    for part in parts:
        for shape in part.shapes:
            shape_by_id[shape.id] = shape
            part_of_shape[shape.id] = part.index
            if shape.type in (THROW_LINK, CATCH_LINK):
                continue
            if shape.type in _ARTIFACT_TYPES:
                key = (shape.type, shape.label, round(shape.x), round(shape.y), round(shape.w), round(shape.h))
                if key in seen_artifacts:
                    alias[shape.id] = seen_artifacts[key]
                    report.copies_merged += 1
                    continue
                seen_artifacts[key] = shape.id
            kept.append(shape)
    lane_piece_ids = {lane.id for part in parts for lane in part.lanes}
    lane_id_of_piece = {piece: lanes[index].id for piece, index in lane_of_piece.items()}

    # Связи частей: обычные остаются, половинки через разрез ждут пары.
    connectors: List[ET.Element] = []
    into_throw: Dict[str, List[ET.Element]] = {}
    out_of_catch: Dict[str, List[ET.Element]] = {}
    for part in parts:
        for c in part.connectors:
            src, tgt = c.get('sourceNodeId'), c.get('targetNodeId')
            if shape_by_id.get(tgt) is not None and shape_by_id[tgt].type == THROW_LINK:
                into_throw.setdefault(tgt, []).append(c)
            elif shape_by_id.get(src) is not None and shape_by_id[src].type == CATCH_LINK:
                out_of_catch.setdefault(src, []).append(c)
            else:
                connectors.append(c)

    catches = [s for s in shape_by_id.values() if s.type == CATCH_LINK]
    matched_catches = set()
    for throw_id, halves in into_throw.items():
        throw = shape_by_id[throw_id]
        to = _TO_RE.search(throw.label)
        if not to:
            report.problems.append(f'переход «{throw.label}» без номера части')
            continue
        target_part, target_step = int(to.group(1)), to.group(2)
        own_part = part_of_shape[throw_id]
        for half in halves:
            source = shape_by_id.get(half.get('sourceNodeId'))
            source_step = _step(source.label) if source is not None else None
            best = None
            for catch in catches:
                if part_of_shape[catch.id] != target_part:
                    continue
                came = _FROM_RE.search(catch.label)
                if not came or int(came.group(1)) != own_part:
                    continue
                for out in out_of_catch.get(catch.id, []):
                    target = shape_by_id.get(out.get('targetNodeId'))
                    if target_step and (target is None or _step(target.label) != target_step):
                        continue
                    if came.group(2) and source_step and came.group(2) != source_step:
                        continue
                    distance = abs(catch.y - throw.y) + abs(catch.x - throw.x) / 10
                    if best is None or distance < best[0]:
                        best = (distance, out, catch.id)
            if best is None:
                report.problems.append(f'нет пары для «{throw.label}» (часть {own_part})')
                continue
            _distance, out, catch_id = best
            matched_catches.add(catch_id)
            joined = copy.deepcopy(half)
            joined.set('targetNodeId', out.get('targetNodeId'))
            for attr in ('targetPoint',):
                if out.get(attr) is not None:
                    joined.set(attr, out.get(attr))
                elif joined.get(attr) is not None:
                    del joined.attrib[attr]
            if not joined.get('Text') and out.get('Text'):
                joined.set('Text', out.get('Text'))
            points = [w for w in half.findall('waypoint')][:-1] + [w for w in out.findall('waypoint')][1:]
            for w in joined.findall('waypoint'):
                joined.remove(w)
            anchor = joined.find('labelPosition')
            insert_at = list(joined).index(anchor) if anchor is not None else len(joined)
            for i, w in enumerate(points):
                point = copy.deepcopy(w)
                point.set('index', str(i))
                joined.insert(insert_at + i, point)
            connectors.append(joined)
            report.stitched += 1

    # ── Карта ───────────────────────────────────────────────────────────────
    first = parts[0]
    map_el = ET.Element('Map', {
        'name': first.page, 'notation': 'bpmn', 'paperEnabled': 'false', 'paperType': '0',
    })
    children: Dict[str, List[Shape]] = {}
    loose: List[Shape] = []
    for shape in kept:
        host = _host_lane(shape, lanes)
        (children.setdefault(host.id, []) if host is not None else loose).append(shape)

    def node_el(shape: Shape, ox: float, oy: float) -> ET.Element:
        el = copy.deepcopy(shape.el)
        for inner in el.findall('node'):
            el.remove(inner)
        el.set('x', str(int(round(shape.x - ox))))
        el.set('y', str(int(round(shape.y - oy))))
        return el

    for lane in lanes:
        el = node_el(lane, 0, 0)
        el.set('width', str(int(round(lane.w))))
        for shape in children.get(lane.id, []):
            el.append(node_el(shape, lane.x, lane.y))
        map_el.append(el)
    for shape in loose:
        map_el.append(node_el(shape, 0, 0))
    for c in connectors:
        c = copy.deepcopy(c)
        for attr in ('sourceNodeId', 'targetNodeId'):
            ref = c.get(attr)
            ref = alias.get(ref, ref)
            if ref in lane_piece_ids:
                ref = lane_id_of_piece.get(ref, ref)
            c.set(attr, ref)
        map_el.append(c)
    for catch in catches:
        if catch.id not in matched_catches:
            report.problems.append(f'событие «{catch.label}» (часть {part_of_shape[catch.id]}) осталось без пары')
    return map_el, report


def _host_lane(shape: Shape, lanes: Sequence[Shape]) -> Optional[Shape]:
    """Дорожка, в которой лежит центр фигуры; из перекрывающихся — последняя."""
    cx, cy = shape.x + shape.w / 2, shape.y + shape.h / 2
    host = None
    for lane in lanes:
        if lane.x <= cx <= lane.x + lane.w and lane.y <= cy <= lane.y + lane.h:
            host = lane
    return host


def glue_package(package: bytes) -> Tuple[List[Tuple[str, str]], List[GlueReport]]:
    """Все разрезанные страницы выгрузки PIX -> ``[(имя карты, XML)]`` и отчёты."""
    out: List[Tuple[str, str]] = []
    reports: List[GlueReport] = []
    for parts in find_parts(package).values():
        map_el, report = glue_parts(parts)
        xml = ET.tostring(map_el, encoding='unicode')
        out.append((report.page, '<?xml version="1.0" encoding="utf-8"?>\n' + xml))
        reports.append(report)
    return out, reports
