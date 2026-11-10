"""Большая карта уезжает в PIX деревом карт: главная и дочерние.

Карту в несколько тысяч фигур студия не открывает: импорт 1.53 (2 370 узлов,
280 КБ) и «Mukammal va joriy ta'mirlash…» висит без конца, а карта в
~800 узлов (AVGUST-1.28) грузится. Поэтому такая карта уходит так, как PIX
сама хранит декомпозицию (её выгрузка «SANOATQURILISHBANK», 2026-09-25): одна
главная карта без ``parentProcessId``, на ней свёрнутые подпроцессы, и у
каждой дочерней карты ``parentProcessId`` — идентификатор подпроцесса, который
её раскрывает.

Дочерней картой становится:

* страница draw.io. Если на неё ссылается фигура с плюсом (главная страница
  «Kredit shartnomasining…» раскрывает так 18 страниц), её раскрывает эта
  фигура; остальные страницы встают подпроцессами на обзорную карту, по
  дорожке на страницу;
* часть страницы, если страница больше ``MAP_NODE_BUDGET`` узлов. Дорожки на
  картах банка горизонтальные, процесс идёт слева направо, поэтому страница
  режется поперёк — там, где разрез пересекает меньше всего линий и задевает
  меньше всего фигур. Линия, прошедшая через разрез, не теряется: в своей
  части она упирается в событие-переход «в часть N», в соседней выходит из
  события «из части M» (link event BPMN — ровно для этого он и нужен).
  Дорожки есть в каждой части, обрезанные по её ширине.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    TASK_NODE_TYPES,
    BusinessProcess,
    Geometry,
    ProcessEdge,
    ProcessEdgePoint,
    ProcessNode,
)
from app.services.bpmn_exporter import prefixed_page, step_duration_text
from app.services.pmm_exporter import (
    _pix_id,
    drawio_frames,
    drawio_label_point,
    generate_configuration_xml,
    generate_main_xml,
    generate_map_xml,
    generate_pmm_zip,
    map_slug,
)

#: Столько узлов (фигуры, дорожки, часы шагов) бывает на одной дочерней карте.
#: Вдвое меньше того, что студия ещё открывает (~800 у AVGUST-1.28).
MAP_NODE_BUDGET = 500

#: С какого размера карта сама уходит деревом, когда выбор — «Авто». Карты до
#: тысячи узлов банк в PIX уже загружал одной картой, и резать их незачем.
AUTO_TREE_THRESHOLD = 1000

#: Как уйдёт карта: «auto» — деревом, если в файле несколько страниц или карта
#: больше ``AUTO_TREE_THRESHOLD``; «single» — одной картой, как раньше;
#: «tree» — деревом всегда.
LAYOUTS = ('auto', 'single', 'tree')

COLLAPSED = 'sub_process_collapsed'
THROW_LINK = 'intermediate_event_throw_link'
CATCH_LINK = 'intermediate_event_catch_link'

#: Запас узлов части на события-переходы и плашку заголовка.
_LINK_RESERVE = 20
#: Во сколько линий обходится разрез сквозь фигуру: событие-переход на
#: разрезе легло бы на неё.
_BOX_PENALTY = 3
#: Разрез ищется не дальше этой доли средней части от её целевой границы.
_CUT_WINDOW = 0.3
#: Поля дорожки части: слева студия рисует полосу названия (30 px).
_LANE_LEFT_ROOM = 70
_LANE_RIGHT_ROOM = 40
#: Уже этого обрезок дорожки в части не оставляем.
_MIN_LANE_WIDTH = 120
#: Событие-переход: круг студии.
_LINK_SIDE = 40

#: Обзорная карта: высота строки-дорожки, шаг и размер свёрнутых подпроцессов.
_ROW_HEIGHT = 180
_ANCHOR_LEFT = 140
_ANCHOR_STEP = 266
_ANCHOR_SIZE = (156, 108)

#: Номер шага в начале подписи: «12. Hujjatlarni…».
_STEP_NUMBER_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)')
#: Символы, которых не бывает в имени части ZIP (и в имени карты студии).
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass
class MapPlan:
    """Одна карта пакета: что на ней нарисовать и какие карты она раскрывает."""
    name: str
    process: BusinessProcess
    #: Узлы, которые пишутся не своим элементом: свёрнутый подпроцесс, переходы.
    pix_types: Dict[str, str] = field(default_factory=dict)
    #: ``(id узла-подпроцесса на этой карте, дочерняя карта)``.
    children: List[Tuple[str, 'MapPlan']] = field(default_factory=list)


def map_node_count(process: BusinessProcess) -> int:
    """Сколько узлов выйдет на карте студии: фигуры, дорожки, часы шагов, заголовок."""
    flow = [n for n in process.nodes if n.type != 'lane']
    clocks = sum(1 for n in flow if n.type in TASK_NODE_TYPES and step_duration_text(n))
    return len(flow) + len(process.lanes) + clocks + 1


def wants_tree(process: BusinessProcess, layout: str = 'auto') -> bool:
    """Уходит ли карта деревом при выбранном виде выгрузки."""
    if layout == 'single':
        return False
    if layout == 'tree':
        return True
    return bool(process.subProcesses) or map_node_count(process) > AUTO_TREE_THRESHOLD


def generate_pmm_package(process: BusinessProcess, layout: str = 'auto') -> bytes:
    """Пакет .pmm в выбранном виде: одной картой или деревом карт."""
    if wants_tree(process, layout):
        return generate_pmm_tree_zip(process)
    return generate_pmm_zip(process)


def generate_pmm_tree_zip(process: BusinessProcess, budget: int = MAP_NODE_BUDGET) -> bytes:
    """Пакет студии с деревом карт: главная и дочерние, по карте на часть."""
    root = build_map_tree(process, budget)
    used: Set[str] = {root.name.lower()}
    parts: List[Tuple[str, str]] = []

    def emit(plan: MapPlan, parent: Optional[str]) -> None:
        name = plan.name if parent is None else _unique_name(plan.name, used)
        slug, xml = generate_map_xml(plan.process, parent, plan.pix_types, name=name)
        parts.append((slug, xml))
        for node_id, child in plan.children:
            emit(child, _pix_id(node_id))

    emit(root, None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('main.xml', generate_main_xml([slug for slug, _ in parts]).encode('utf-8'))
        zf.writestr('pm/configuration.xml', generate_configuration_xml().encode('utf-8'))
        for slug, xml in parts:
            zf.writestr(f'pm/maps/{slug}.xml', xml.encode('utf-8'))
    return buf.getvalue()


def build_map_tree(process: BusinessProcess, budget: int = MAP_NODE_BUDGET) -> MapPlan:
    """Дерево карт процесса: главная карта и всё, что она раскрывает."""
    pages = [process.model_copy(update={'subProcesses': [], 'variants': []})]
    pages += [prefixed_page(page, index) for index, page in enumerate(process.subProcesses, 1)]
    pages = [pages[0], *(page for page in pages[1:] if page.nodes or page.lanes)]
    title = process.passport.name or process.name
    names = {pages[0].id: process.mainPageName or title}
    names.update({page.id: page.name or f'Страница {index}' for index, page in enumerate(pages[1:], 1)})
    by_id = {page.id: page for page in pages}

    # Какая фигура какую страницу раскрывает: обход от главной, первая ссылка
    # побеждает, по кругу не ходим.
    opened_by: Dict[str, Tuple[str, str]] = {}
    queue, seen = [pages[0].id], {pages[0].id}
    while queue:
        page = by_id[queue.pop(0)]
        for node in page.nodes:
            target = node.calledSubProcessId
            if node.type == 'subProcess' and target in by_id and target not in seen:
                opened_by[target] = (page.id, node.id)
                seen.add(target)
                queue.append(target)
                # В дереве студии карта зовётся, как раскрывающий её шаг:
                # страницы аналитики называют номерами («2», «6.1»).
                if (node.name or '').strip():
                    names[target] = ' '.join(node.name.split())

    def maps_of(page_id: str) -> List[MapPlan]:
        page, name = by_id[page_id], names[page_id]
        parts = split_page(page, budget, name) if map_node_count(page) > budget else [page]
        plans = [MapPlan(name if len(parts) == 1 else _part_name(part, index, len(parts), name),
                         part, link_pix_types(part))
                 for index, part in enumerate(parts, 1)]
        for child_id, (parent_id, node_id) in opened_by.items():
            if parent_id != page_id:
                continue
            # Фигура-ссылка — шаг, а шаг достаётся ровно одной части.
            holder = next(plan for plan in plans if any(n.id == node_id for n in plan.process.nodes))
            holder.pix_types[node_id] = COLLAPSED
            holder.children.append((node_id, one_map(child_id)))
        return plans

    def one_map(page_id: str) -> MapPlan:
        plans = maps_of(page_id)
        if len(plans) == 1:
            return plans[0]
        return overview_plan(names[page_id], [(names[page_id], plans)], f'{page_id}:parts', process)

    loose = [page.id for page in pages[1:] if page.id not in opened_by]
    opens_pages = any(parent == pages[0].id for parent, _node in opened_by.values())
    if opens_pages and map_node_count(pages[0]) <= budget:
        # Главная страница сама раскрывает свои страницы — она и есть главная карта.
        root = maps_of(pages[0].id)[0]
        if loose:
            append_row(root, 'Другие страницы файла', [one_map(page_id) for page_id in loose],
                       f'{process.id}:loose')
    else:
        rows = [(names[page_id], maps_of(page_id)) for page_id in [pages[0].id, *loose]]
        root = overview_plan(title, rows, f'{process.id}:overview', process)
    root.name = map_slug(process)
    return root


# ── Обзорная карта ──────────────────────────────────────────────────────────

def _steps_label(process: BusinessProcess) -> str:
    """«шаги 12–40» по номерам в подписях шагов части; пусто, если номеров нет."""
    numbers = []
    for node in process.nodes:
        if node.type in TASK_NODE_TYPES:
            match = _STEP_NUMBER_RE.match(node.name or '')
            if match:
                numbers.append(int(match.group(1).split('.')[0]))
    if not numbers:
        return ''
    low, high = min(numbers), max(numbers)
    return f'шаг {low}' if low == high else f'шаги {low}–{high}'


def _part_name(part: BusinessProcess, index: int, total: int, page_name: str) -> str:
    """Имя части в дереве студии: номер части первым.

    Дерево PIX обрезает длинное имя, и семь частей «Mukammal va joriy
    ta'mirlash…» выглядели там одинаково (PIX банка, 2026-09-29).
    """
    steps = _steps_label(part)
    head = f'Часть {index} из {total}' + (f' · {steps}' if steps else '')
    return f'{head} — {page_name}'


def _anchor_label(row_name: str, plans: Sequence[MapPlan], index: int) -> str:
    if len(plans) == 1:
        return row_name
    label = f'Часть {index + 1} из {len(plans)}'
    steps = _steps_label(plans[index].process)
    return f'{label} · {steps}' if steps else label


def _anchor(key: str, row: int, index: int, name: str, lane: ProcessNode) -> ProcessNode:
    width, height = _ANCHOR_SIZE
    return ProcessNode(
        id=f'{key}:part:{row}:{index}', name=name, type='subProcess', category=None,
        laneId=lane.id, laneName=lane.name, role=lane.name, slaMinutes=None,
        geometry=Geometry(x=lane.geometry.x + _ANCHOR_LEFT + index * _ANCHOR_STEP,
                          y=lane.geometry.y + (_ROW_HEIGHT - height) // 2,
                          width=width, height=height))


def _row_width(count: int) -> int:
    return max(900, _ANCHOR_LEFT + count * _ANCHOR_STEP)


def overview_plan(
    title: str,
    rows: Sequence[Tuple[str, Sequence[MapPlan]]],
    key: str,
    base: BusinessProcess,
) -> MapPlan:
    """Обзорная карта: дорожка на страницу, в ней части страницы свёрнутыми подпроцессами."""
    width = max(_row_width(len(plans)) for _name, plans in rows)
    lanes: List[ProcessNode] = []
    nodes: List[ProcessNode] = []
    edges: List[ProcessEdge] = []
    plan = MapPlan(title, base)
    for row, (row_name, plans) in enumerate(rows):
        lane = ProcessNode(id=f'{key}:lane:{row}', name=row_name, type='lane', category=None,
                           geometry=Geometry(x=0, y=row * _ROW_HEIGHT, width=width, height=_ROW_HEIGHT))
        lanes.append(lane)
        previous: Optional[ProcessNode] = None
        for index, child in enumerate(plans):
            anchor = _anchor(key, row, index, _anchor_label(row_name, plans, index), lane)
            nodes.append(anchor)
            plan.pix_types[anchor.id] = COLLAPSED
            plan.children.append((anchor.id, child))
            # Части одной страницы идут друг за другом, как на ней самой.
            if previous is not None:
                edges.append(ProcessEdge(id=f'{key}:flow:{row}:{index}',
                                         sourceId=previous.id, targetId=anchor.id))
            previous = anchor
    plan.process = base.model_copy(update={
        'id': key, 'name': title,
        'passport': base.passport.model_copy(update={'name': title}),
        'nodes': nodes, 'lanes': lanes, 'edges': edges, 'subProcesses': [], 'variants': [],
    })
    return plan


def append_row(plan: MapPlan, row_name: str, children: Sequence[MapPlan], key: str) -> None:
    """Дорожка со свёрнутыми подпроцессами под всем, что уже есть на карте."""
    process = plan.process
    frames = [n.drawioGeometry or n.geometry for n in [*process.nodes, *process.lanes]]
    left = min((g.x for g in frames), default=0)
    bottom = max((g.y + g.height for g in frames), default=0)
    lane = ProcessNode(id=f'{key}:lane', name=row_name, type='lane', category=None,
                       geometry=Geometry(x=left, y=bottom + 80, width=_row_width(len(children)),
                                         height=_ROW_HEIGHT))
    anchors = [_anchor(key, 0, index, child.name, lane) for index, child in enumerate(children)]
    for anchor, child in zip(anchors, children):
        plan.pix_types[anchor.id] = COLLAPSED
        plan.children.append((anchor.id, child))
    plan.process = process.model_copy(update={
        'nodes': [*process.nodes, *anchors], 'lanes': [*process.lanes, lane]})


# ── Разрез страницы на части ────────────────────────────────────────────────

def _page_titles(flow: Sequence[ProcessNode], lanes: Sequence[ProcessNode],
                 edges: Sequence[ProcessEdge]) -> Set[str]:
    """Шапка страницы — ни с чем не связанный текст целиком над дорожками.

    Та же мера, что у заголовка карты в ``generate_map_xml``. В часть шапка не
    идёт: имя страницы стоит на плашке каждой части.
    """
    if not lanes:
        return set()
    top = min(lane.geometry.y for lane in lanes)
    linked = {end for e in edges if e.kind != 'annotationLine' for end in (e.sourceId, e.targetId)}
    return {
        n.id for n in flow
        if n.type == 'textAnnotation' and n.id not in linked and n.geometry.y + n.geometry.height <= top
    }


def split_page(page: BusinessProcess, budget: int = MAP_NODE_BUDGET,
               title: Optional[str] = None) -> List[BusinessProcess]:
    """Страница, разрезанная поперёк хода процесса на части не больше ``budget`` узлов.

    Возвращает части слева направо; страницу, которая помещается или которую
    резать не по чему, — одной частью как есть.
    """
    framed = drawio_frames(page)
    lanes = list(framed.lanes)
    flow = [n for n in framed.nodes if n.type != 'lane']
    titles = _page_titles(flow, lanes, framed.edges)
    items = [n for n in flow if n.id not in titles]
    # Дорожки есть в каждой части; если их больше бюджета, части — хотя бы по десятку фигур.
    capacity = max(10, budget - len(lanes) - 1 - _LINK_RESERVE)
    cuts = choose_cuts(items, framed.edges, capacity)
    if not cuts:
        return [page]
    parts = _cut(framed, items, lanes, cuts)
    name = title or page.passport.name or page.name
    return [
        part.model_copy(update={
            'name': f'{name} — часть {index} из {len(parts)}',
            'passport': part.passport.model_copy(update={'name': f'{name} — часть {index} из {len(parts)}'}),
        })
        for index, part in enumerate(parts, 1)
    ]


def _weight(node: ProcessNode) -> int:
    """Узлы студии на фигуру: шаг с замеренным временем приносит ещё часы."""
    return 2 if node.type in TASK_NODE_TYPES and step_duration_text(node) else 1


def choose_cuts(items: Sequence[ProcessNode], edges: Sequence[ProcessEdge], capacity: int) -> List[float]:
    """Абсциссы разрезов: части не больше ``capacity`` узлов и по возможности ровные.

    Разрез проходит между центрами соседних фигур. Из кандидатов около
    целевой границы части берётся тот, что пересекает меньше линий и задевает
    меньше фигур (``_BOX_PENALTY``); фигура достаётся части, где её центр.
    """
    centre = {n.id: n.geometry.x + n.geometry.width / 2 for n in items}
    order = sorted(items, key=lambda n: centre[n.id])
    xs = [centre[n.id] for n in order]
    prefix = [0]
    for node in order:
        prefix.append(prefix[-1] + _weight(node))
    total = prefix[-1]
    if total <= capacity:
        return []
    flows = [
        (min(centre[e.sourceId], centre[e.targetId]), max(centre[e.sourceId], centre[e.targetId]))
        for e in edges
        if e.kind in ('sequenceFlow', 'messageFlow') and e.sourceId in centre and e.targetId in centre
    ]
    lows, highs = sorted(lo for lo, _hi in flows), sorted(hi for _lo, hi in flows)
    starts = sorted(n.geometry.x for n in items)
    ends = sorted(n.geometry.x + n.geometry.width for n in items)
    candidates = sorted({(xs[i - 1] + xs[i]) / 2 for i in range(1, len(xs)) if xs[i] - xs[i - 1] >= 1})
    if not candidates:
        return []

    def left(x: float) -> int:
        return prefix[bisect_left(xs, x)]

    def cost(x: float) -> int:
        crossing = bisect_left(lows, x) - bisect_right(highs, x)
        through = bisect_left(starts, x) - bisect_right(ends, x)
        return crossing + _BOX_PENALTY * through

    first = math.ceil(total / capacity)
    best: List[float] = []
    for count in range(first, first + 40):
        step = total / count
        cuts: List[float] = []
        done = 0
        for index in range(1, count):
            target = step * index
            pool = [c for c in candidates if left(c) > done]
            near = [c for c in pool if abs(left(c) - target) <= _CUT_WINDOW * step]
            if not (near or pool):
                break
            pick = min(near or pool, key=lambda c: (cost(c), abs(left(c) - target)))
            cuts.append(pick)
            done = left(pick)
        bounds = [0, *(left(c) for c in cuts), total]
        sizes = [b - a for a, b in zip(bounds, bounds[1:])]
        best = cuts
        if len(cuts) == count - 1 and max(sizes) <= capacity:
            return cuts
    return best


def _route(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode) -> List[Tuple[float, float]]:
    """Путь линии draw.io: от центра к центру через её изломы."""
    def centre(n: ProcessNode) -> Tuple[float, float]:
        return n.geometry.x + n.geometry.width / 2, n.geometry.y + n.geometry.height / 2
    return [centre(src), *((float(p.x), float(p.y)) for p in edge.points), centre(tgt)]


def _crossing(route: Sequence[Tuple[float, float]], x: float, first: bool) -> Optional[Tuple[int, float]]:
    """Где путь пересекает вертикаль ``x``: ``(номер отрезка, y)``, первое или последнее."""
    indices = range(len(route) - 1) if first else range(len(route) - 2, -1, -1)
    for i in indices:
        (ax, ay), (bx, by) = route[i], route[i + 1]
        if ax != bx and (ax - x) * (bx - x) <= 0:
            return i, ay + (by - ay) * (x - ax) / (bx - ax)
    return None


def _points(values: Sequence[Tuple[float, float]]) -> List[ProcessEdgePoint]:
    return [ProcessEdgePoint(x=int(round(px)), y=int(round(py))) for px, py in values]


def _step_suffix(node: ProcessNode) -> str:
    match = _STEP_NUMBER_RE.match(node.name or '') if node.type in TASK_NODE_TYPES else None
    return f', шаг {match.group(1)}' if match else ''


def _lane_at(lanes: Sequence[ProcessNode], x: float, y: float) -> Optional[str]:
    """Дорожка под точкой; из перекрывающихся — последняя, как у вложения фигур."""
    found = None
    for lane in lanes:
        g = lane.geometry
        if g.x <= x <= g.x + g.width and g.y <= y <= g.y + g.height:
            found = lane.id
    return found


def _link_node(nid: str, name: str, at: Tuple[float, float], lane_id: Optional[str]) -> ProcessNode:
    half = _LINK_SIDE // 2
    return ProcessNode(
        id=nid, name=name, type='intermediateMessageEvent', category=None, laneId=lane_id,
        slaMinutes=None, style='ellipse;perimeter=ellipsePerimeter;',
        geometry=Geometry(x=int(round(at[0])) - half, y=int(round(at[1])) - half,
                          width=_LINK_SIDE, height=_LINK_SIDE))


def _place_events(events: Sequence[ProcessNode], shapes: Sequence[ProcessNode],
                  outward: Dict[str, int], lane_by_id: Dict[str, ProcessNode]) -> None:
    """События-переходы части не ложатся на фигуры, друг на друга и не вылезают из дорожки.

    Разрез проходит между центрами фигур, и шлюз, от которого уходит линия,
    бывает шире промежутка: переход, поставленный на разрезе, лёг бы на ромб.
    Такой переход отходит за фигуру наружу части (``outward``: +1 — вправо,
    −1 — влево). Точка, где линия пересекла разрез, бывает и на самой черте
    между дорожками: круг, наполовину торчащий из дорожки, студия рисует
    поверх соседней.
    """
    boxes = [n.geometry for n in shapes if n.type != 'textAnnotation']

    def touches(g: Geometry, o: Geometry, gap: int) -> bool:
        return (min(g.x + g.width, o.x + o.width) - max(g.x, o.x) > -gap
                and min(g.y + g.height, o.y + o.height) - max(g.y, o.y) > -gap)

    placed: List[Geometry] = []
    for node in sorted(events, key=lambda n: (n.geometry.x, n.geometry.y)):
        g = node.geometry
        for _ in range(50):
            hit = next((b for b in boxes if touches(g, b, 4)), None)
            if hit is not None:
                g.x = hit.x + hit.width + 8 if outward.get(node.id, 1) > 0 else hit.x - _LINK_SIDE - 8
                continue
            if any(touches(g, o, 4) for o in placed):
                g.y += _LINK_SIDE + 8
                continue
            break
        lane = lane_by_id.get(node.laneId or '')
        if lane is not None:
            top = lane.geometry.y + 2
            g.y = max(top, min(g.y, lane.geometry.y + lane.geometry.height - _LINK_SIDE - 2))
        placed.append(g)


def _cut(framed: BusinessProcess, items: Sequence[ProcessNode], lanes: Sequence[ProcessNode],
         cuts: Sequence[float]) -> List[BusinessProcess]:
    count = len(cuts) + 1
    centre = {n.id: n.geometry.x + n.geometry.width / 2 for n in items}
    part_of = {n.id: bisect_right(cuts, centre[n.id]) for n in items}
    node_by_id = {n.id: n for n in items}
    lane_by_id = {lane.id: lane for lane in lanes}

    nodes: List[List[ProcessNode]] = [[] for _ in range(count)]
    edges: List[List[ProcessEdge]] = [[] for _ in range(count)]
    events: List[List[ProcessNode]] = [[] for _ in range(count)]
    lanes_needed: List[Set[str]] = [set() for _ in range(count)]
    copies: List[Dict[str, str]] = [{} for _ in range(count)]
    catches: List[Dict[Tuple[str, int], ProcessNode]] = [{} for _ in range(count)]
    #: Куда отходит переход, если лёг на фигуру: наружу своей части.
    outward: Dict[str, int] = {}
    for node in items:
        nodes[part_of[node.id]].append(node)

    for e in framed.edges:
        if e.kind == 'annotationLine' or not e.sourceId or not e.targetId or e.sourceId == e.targetId:
            continue
        src_lane, tgt_lane = e.sourceId in lane_by_id, e.targetId in lane_by_id
        if src_lane or tgt_lane:
            # Линия к полосе едет с фигурой, от которой идёт; какая из них
            # связь, а какая оформление, выгрузка решает сама, как и для целой
            # карты (``plan_pool_links``).
            lane_id, other = (e.sourceId, e.targetId) if src_lane else (e.targetId, e.sourceId)
            if src_lane and tgt_lane or other not in part_of:
                continue
            edges[part_of[other]].append(e)
            lanes_needed[part_of[other]].add(lane_id)
            continue
        if e.sourceId not in part_of or e.targetId not in part_of:
            continue
        sp, tp = part_of[e.sourceId], part_of[e.targetId]
        if sp == tp:
            edges[sp].append(e)
            continue
        src, tgt = node_by_id[e.sourceId], node_by_id[e.targetId]

        if e.kind == 'association' or src.type in ARTIFACT_NODE_TYPES or tgt.type in ARTIFACT_NODE_TYPES:
            # Цилиндр системы, документ или примечание идут в часть своего шага
            # копией: переход «в часть N» для пунктира к цилиндру бессмыслен.
            if src.type in ARTIFACT_NODE_TYPES and tgt.type not in ARTIFACT_NODE_TYPES:
                part, artifact, end = tp, src, 'sourceId'
            else:
                part, artifact, end = sp, tgt, 'targetId'
            copy_id = copies[part].get(artifact.id)
            if copy_id is None:
                copy_id = copies[part][artifact.id] = f'{artifact.id}~{part + 1}'
                nodes[part].append(artifact.model_copy(update={'id': copy_id}))
            edges[part].append(e.model_copy(update={'id': f'{e.id}~{part + 1}', end: copy_id}))
            continue

        # Линия через разрез: «в часть N» у источника, «из части M» у приёмника.
        route = _route(e, src, tgt)
        exit_x = cuts[sp] if tp > sp else cuts[sp - 1]
        entry_x = cuts[tp - 1] if tp > sp else cuts[tp]
        out = _crossing(route, exit_x, first=True)
        into = _crossing(route, entry_x, first=False)
        out_at = (exit_x, out[1] if out else route[0][1])
        in_at = (entry_x, into[1] if into else route[-1][1])
        before = route[1:out[0] + 1] if out else []
        after = route[into[0] + 1:-1] if into else []

        label = (e.name or e.condition or '').strip()
        label_point = None
        if label and e.drawioLabelPoint:
            label_point = (e.drawioLabelPoint[0], e.drawioLabelPoint[1])
        elif label and e.drawioLabel:
            label_point = drawio_label_point(route, e.drawioLabel)
        # Подпись остаётся той половине, где её нарисовал аналитик.
        label_out = label_point is None or (label_point[0] - exit_x) * (route[0][0] - exit_x) >= 0

        throw = _link_node(f'{e.id}~to', f'в часть {tp + 1}{_step_suffix(tgt)}', out_at,
                           _lane_at(lanes, *out_at))
        events[sp].append(throw)
        outward[throw.id] = 1 if tp > sp else -1
        edges[sp].append(e.model_copy(update={
            'id': f'{e.id}~out', 'targetId': throw.id,
            'points': _points(before), 'drawioPoints': None, 'targetPoint': None,
            'entryX': None, 'entryY': None,
            'name': e.name if label_out else '', 'condition': e.condition if label_out else None,
            'labelLines': e.labelLines if label_out else None, 'drawioLabel': None,
            'drawioLabelPoint': list(label_point) if label_out and label_point else None,
        }))

        catch = catches[tp].get((e.targetId, sp))
        if catch is not None:
            # В один шаг из той же части пришло несколько линий — один вход на всех.
            catch.name = f'из части {sp + 1}'
            continue
        catch = catches[tp][(e.targetId, sp)] = _link_node(
            f'{e.id}~from', f'из части {sp + 1}{_step_suffix(src)}', in_at, _lane_at(lanes, *in_at))
        events[tp].append(catch)
        outward[catch.id] = -1 if tp > sp else 1
        edges[tp].append(e.model_copy(update={
            'id': f'{e.id}~in', 'sourceId': catch.id,
            'points': _points(after), 'drawioPoints': None, 'sourcePoint': None,
            'exitX': None, 'exitY': None,
            'name': '' if label_out else e.name, 'condition': None if label_out else e.condition,
            'labelLines': None if label_out else e.labelLines, 'drawioLabel': None,
            'drawioLabelPoint': None if label_out or not label_point else list(label_point),
        }))

    parts: List[BusinessProcess] = []
    for part in range(count):
        _place_events(events[part], nodes[part], outward, lane_by_id)
        members = [*nodes[part], *events[part]]
        x0 = min(n.geometry.x for n in members) - _LANE_LEFT_ROOM
        x1 = max(n.geometry.x + n.geometry.width for n in members) + _LANE_RIGHT_ROOM
        needed = {n.laneId for n in members if n.laneId} | lanes_needed[part]
        part_lanes: List[ProcessNode] = []
        rename: Dict[str, str] = {}
        for lane in lanes:
            g = lane.geometry
            low, high = max(g.x, x0), min(g.x + g.width, x1)
            name = lane.name
            if high - low < _MIN_LANE_WIDTH:
                if lane.id not in needed:
                    continue
                # Полоса участника, к которой идёт пунктир, лежит за краем
                # части: она встаёт у ближайшего края, своей ширины, а в имени
                # — часть, где она нарисована. По этой пометке склейка
                # (``pmm_glue``) возвращает копию к полосе на её месте.
                width = min(g.width, x1 - x0)
                low = min(max(g.x, x0), x1 - width)
                high = low + width
                if low != g.x or width != g.width:
                    home = bisect_right(cuts, g.x + g.width / 2) + 1
                    name = f'{lane.name} (часть {home})'
            rename[lane.id] = f'{lane.id}~{part + 1}'
            part_lanes.append(lane.model_copy(update={
                'id': rename[lane.id], 'drawioGeometry': None, 'name': name,
                'geometry': Geometry(x=int(low), y=g.y, width=int(high - low), height=g.height)}))
        members = [n.model_copy(update={'laneId': rename[n.laneId]}) if n.laneId in rename else n
                   for n in members]
        part_edges = [
            e.model_copy(update={'sourceId': rename.get(e.sourceId, e.sourceId),
                                 'targetId': rename.get(e.targetId, e.targetId)})
            for e in edges[part]
        ]
        parts.append(framed.model_copy(update={
            'id': f'{framed.id}~{part + 1}', 'nodes': members, 'lanes': part_lanes,
            'edges': part_edges, 'subProcesses': [], 'variants': [],
        }))
    return parts


def link_pix_types(process: BusinessProcess) -> Dict[str, str]:
    """Элементы студии для событий-переходов части (``~to`` — бросок, ``~from`` — приём)."""
    out: Dict[str, str] = {}
    for node in process.nodes:
        if node.type == 'intermediateMessageEvent' and node.id.endswith('~to'):
            out[node.id] = THROW_LINK
        elif node.type == 'intermediateMessageEvent' and node.id.endswith('~from'):
            out[node.id] = CATCH_LINK
    return out


def _unique_name(name: str, used: Set[str]) -> str:
    """Имя дочерней карты: оно же имя части ZIP, без символов, которых там не бывает."""
    clean = re.sub(r'\s+', ' ', _UNSAFE_NAME_RE.sub(' ', name or '')).strip(' .')[:120].rstrip(' .')
    clean = clean or 'Карта'
    candidate, index = clean, 2
    while candidate.lower() in used:
        candidate = f'{clean} ({index})'
        index += 1
    used.add(candidate.lower())
    return candidate
