"""Карта платформы -> файл draw.io (``.drawio``), который импорт читает обратно той же картой.

Эталон вида карты — draw.io: аналитики рисуют в нём, и модель хранит всё, что
оттуда пришло, — id ячеек, стили, рамки, изломы, подписи. Выгрузка кладёт это
на место, а не рисует карту заново: открытый в draw.io файл выглядит так, как
его нарисовали, а импорт (``drawio_parser``) проходит по нему тем же путём.

Чего в draw.io нет, выгрузка добавляет так, как это сделал бы аналитик:

* **время шага** — значками-часами рядом с шагом (4-ILOVA), а не цифрой в
  подписи: импорт снимает такие значки и переносит их время в ST/WT ближайшего
  шага (``_apply_duration_badges``), поэтому значок ставится туда, где ближайшим
  окажется именно его шаг;
* **пул** — рамкой вокруг своих дорожек: сама рамка в модели не хранится,
  только ссылка на неё у дорожек;
* **данные платформы** — роль, система, категория, код, потенциал RPA, цена
  запуска робота, описание, ставка дорожки — свойствами фигуры ``sqb_*``
  (в draw.io: «Изменить данные»). Только там, где разбор рисунка их не
  восстанавливает: свойство, записанное «на всякий случай», перебивало бы
  правку, сделанную в draw.io (перенёс шаг в другую дорожку — а роль осталась
  от прежней). Поэтому выгрузка двухпроходная: сначала рисунок, затем его
  разбор и сравнение с картой, и свойства — по найденным расхождениям.
"""
from __future__ import annotations

import html
import json
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import quoteattr

from app.models.process import (
    TASK_NODE_TYPES,
    BusinessProcess,
    Geometry,
    ProcessEdge,
    ProcessNode,
)
from app.services.diagnostics import is_generated_name
from app.services.drawio_parser import BADGE_ATTACH_RADIUS, _style_float, _style_map, duration_minutes, parse_drawio_xml

#: Свойства фигуры, которые разбор читает поверх рисунка (``_apply_platform_data``).
DATA_PREFIX = 'sqb_'

#: Поля шага, которые переносятся свойствами, и как их записать строкой.
NODE_DATA_FIELDS = (
    'name', 'type', 'code', 'category', 'role', 'system', 'automationPotential', 'costPerExecution',
    'description', 'inputArtifacts', 'outputArtifacts', 'formFields', 'slaMinutes',
)

#: Значок часов у шага — фигура BPMN-таймер с подписью времени под ней.
BADGE_STYLE = ('shape=mxgraph.bpmn.event;symbol=timer;outline=standard;html=1;'
               'verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;'
               'aspect=fixed;perimeter=ellipsePerimeter;')
BADGE_SIZE = 20

#: Подпись связи отдельной ячейкой — так её пишет сам draw.io, когда подпись тащат.
EDGE_LABEL_STYLE = 'edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];'

#: Стиль фигуры, у которой его нет: самые старые импорты стиль не сохраняли, и
#: без него draw.io нарисовал бы прямоугольник, а импорт принял бы цилиндр за шаг.
_EVENT = 'html=1;verticalLabelPosition=bottom;verticalAlign=top;align=center;perimeter=ellipsePerimeter;aspect=fixed;'
_GATEWAY = 'shape=mxgraph.bpmn.gateway2;html=1;verticalLabelPosition=bottom;verticalAlign=top;align=center;'
_ARTIFACT = 'html=1;labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;'
STYLE_FOR_TYPE = {
    'startEvent': 'shape=mxgraph.bpmn.event;outline=standard;symbol=general;' + _EVENT,
    'endEvent': 'shape=mxgraph.bpmn.event;outline=end;symbol=terminate2;' + _EVENT,
    'intermediateTimerEvent': 'shape=mxgraph.bpmn.event;outline=catching;symbol=timer;' + _EVENT,
    'intermediateMessageEvent': 'shape=mxgraph.bpmn.event;outline=catching;symbol=message;' + _EVENT,
    'exclusiveGateway': _GATEWAY + 'gwType=exclusive;',
    'parallelGateway': _GATEWAY + 'gwType=parallel;',
    'inclusiveGateway': _GATEWAY + 'gwType=inclusive;',
    'complexGateway': _GATEWAY + 'gwType=complex;',
    'dataStore': 'shape=datastore;' + _ARTIFACT,
    'dataObject': 'shape=mxgraph.bpmn.data2;' + _ARTIFACT,
    # Не «заметка»: заметку без связей вне дорожек импорт снимает как легенду.
    # Рамку вне дорожек он делает примечанием-заголовком, в дорожке тип
    # довозит свойство ``sqb_type``.
    'textAnnotation': 'rounded=0;whiteSpace=wrap;html=1;',
    'serviceTask': 'shape=mxgraph.bpmn.task2;taskMarker=service;whiteSpace=wrap;html=1;',
    'subProcess': 'shape=mxgraph.bpmn.task2;isLoopSub=1;whiteSpace=wrap;html=1;',
}
TASK_STYLE = 'rounded=1;whiteSpace=wrap;html=1;'
LANE_STYLE = 'swimlane;html=1;horizontal=0;startSize=30;'


def _style(node: ProcessNode) -> str:
    if node.style:
        return node.style
    if node.type == 'lane':
        return LANE_STYLE
    return STYLE_FOR_TYPE.get(node.type, TASK_STYLE)


#: Рамка пула, восстановленная по его дорожкам: заголовок слева, как в draw.io.
POOL_STYLE = 'swimlane;html=1;horizontal=0;startSize=20;'
POOL_HEADER = 20


# ── Страницы ───────────────────────────────────────────────────────────────

def _pages(process: BusinessProcess) -> List[Tuple[str, BusinessProcess]]:
    """Страницы файла: основная первой, затем части процесса, затем варианты.

    Основной импорт берёт первую непустую страницу — или сводную «JAMI», где
    бы она ни стояла, — поэтому основная карта и стоит первой.
    """
    main_name = process.mainPageName or process.name or 'Страница 1'
    return ([(main_name, process)]
            + [(p.name, p) for p in process.subProcesses]
            + [(p.name, p) for p in process.variants])


def _with_time(name: str, node: ProcessNode) -> str:
    """Подпись события со временем: у события время — часть подписи («Kutish vaqti 60 min»).

    У шага время едет значком часов рядом (``_badges``). Слова времени, которые
    в имени уже есть, второй раз не пишем. Имя, кончающееся числом, отделяется
    точкой посередине: «… 60 120 min» импорт прочёл бы как «60 120» минут.
    """
    if node.type in TASK_NODE_TYPES or not (node.slaMeasured and node.slaMinutes):
        return name
    if node.slaText:
        if node.slaText in name:
            return name
        time = node.slaText
    elif duration_minutes(name) is not None:
        return name
    else:
        time = f'{node.slaMinutes} min'
    if not name:
        return time
    return f'{name} · {time}' if name[-1].isdigit() else f'{name} {time}'


def page_keys(pages: Sequence[Tuple[str, BusinessProcess]]) -> List[Tuple[str, int]]:
    """Ключ страницы — имя и номер среди одноимённых.

    В файлах банка бывают страницы с одинаковыми именами («Investit kredit»,
    «Харид»); по одному имени такие страницы слипались в одну.
    """
    seen: Dict[str, int] = {}
    keys = []
    for name, _page in pages:
        seen[name] = seen.get(name, 0) + 1
        keys.append((name, seen[name]))
    return keys


def _diagram_id(index: int) -> str:
    return f'page-{index}'


# ── Геометрия ──────────────────────────────────────────────────────────────

def _drawn(node: ProcessNode) -> Geometry:
    """Рамка так, как её нарисовал аналитик: поправленная импортом — по ``drawioGeometry``."""
    return node.drawioGeometry or node.geometry


def _num(value: float) -> str:
    """Число без потерь: импорт читает его обратно тем же ``float``."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _geometry_xml(x: float, y: float, w: float, h: float) -> str:
    return f'<mxGeometry x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" as="geometry" />'


def _label(text: str, style: str) -> str:
    """Подпись для ``value``: при ``html=1`` draw.io читает её как HTML."""
    return html.escape(text, quote=False) if 'html=1' in (style or '').replace(' ', '') else text


def _cell(cell_id: str, value: str, style: str, parent: str, inner: str, *, vertex: bool = True,
          extra: str = '', data: Optional[Dict[str, str]] = None, link: Optional[str] = None) -> str:
    """Ячейка; со свойствами или ссылкой — в обёртке ``<object>``, как пишет draw.io."""
    kind = 'vertex="1"' if vertex else 'edge="1"'
    if not data and not link:
        return (f'<mxCell id={quoteattr(cell_id)} value={quoteattr(value)} style={quoteattr(style)} '
                f'{kind} parent={quoteattr(parent)}{extra}>{inner}</mxCell>')
    attrs = ''.join(f' {key}={quoteattr(val)}' for key, val in sorted((data or {}).items()))
    if link:
        attrs += f' link={quoteattr(link)}'
    return (f'<object id={quoteattr(cell_id)} label={quoteattr(value)}{attrs}>'
            f'<mxCell style={quoteattr(style)} {kind} parent={quoteattr(parent)}{extra}>{inner}</mxCell>'
            f'</object>')


# ── Пулы и дорожки ─────────────────────────────────────────────────────────

def _pools(page: BusinessProcess) -> Dict[str, Tuple[float, float, float, float]]:
    """Рамки пулов: дорожки ссылаются на родителя, которого среди дорожек нет."""
    lane_ids = {lane.id for lane in page.lanes}
    groups: Dict[str, List[ProcessNode]] = {}
    for lane in page.lanes:
        parent = lane.laneId or '1'
        if parent not in ('0', '1') and parent not in lane_ids:
            groups.setdefault(parent, []).append(lane)
    frames = {}
    for pool_id, members in groups.items():
        left = min(l.geometry.x for l in members) - POOL_HEADER
        top = min(l.geometry.y for l in members)
        right = max(l.geometry.x + l.geometry.width for l in members)
        bottom = max(l.geometry.y + l.geometry.height for l in members)
        frames[pool_id] = (left, top, right - left, bottom - top)
    return frames


# ── Значки времени ─────────────────────────────────────────────────────────

def _badge_texts(node: ProcessNode) -> List[Tuple[str, bool]]:
    """Подписи значков шага: (текст, это ожидание). Подписи с карты — её же словами."""
    out: List[Tuple[str, bool]] = []
    if node.slaMeasured and node.slaMinutes:
        texts = node.slaText.split(' + ') if node.slaText else [f'{node.slaMinutes} min']
        out += [(t, False) for t in texts]
    if node.waitMinutes:
        texts = node.waitText.split(' + ') if node.waitText else [f'{node.waitMinutes} min']
        # Ожидание импорт узнаёт по словам «Kutish vaqti» на значке.
        out += [(t if 'kutish' in t.lower() or 'wait' in t.lower() or 'ожидани' in t.lower()
                 else f'Kutish vaqti {t}', True) for t in texts]
    return out


def _badge_centres(box: Geometry, count: int) -> List[List[Tuple[float, float]]]:
    """Места под ``count`` значков: у правого нижнего угла шага, иначе у середины низа."""
    right, bottom = box.x + box.width, box.y + box.height
    cx = box.x + box.width / 2
    step = BADGE_SIZE + 4
    return [
        [(right - 6 - i * step, bottom - 6) for i in range(count)],
        [(cx + (i - (count - 1) / 2) * step, bottom - 6) for i in range(count)],
        [(cx + (i - (count - 1) / 2) * step, box.y + box.height / 2) for i in range(count)],
    ]


def _nearest_task(point: Tuple[float, float], tasks: Sequence[Tuple[str, Geometry]]) -> Optional[str]:
    """Тот же выбор, что у импорта: ближайший центр шага в пределах радиуса."""
    best, best_dist = None, BADGE_ATTACH_RADIUS
    for task_id, g in tasks:
        dist = math.hypot(point[0] - (g.x + g.width / 2), point[1] - (g.y + g.height / 2))
        if dist < best_dist:
            best, best_dist = task_id, dist
    return best


def _badges(page: BusinessProcess) -> List[Tuple[ProcessNode, str, Tuple[float, float]]]:
    """(шаг, подпись, центр значка) — так, чтобы импорт отдал значок именно этому шагу."""
    tasks = [(n.id, _drawn(n)) for n in page.nodes if n.type in TASK_NODE_TYPES]
    out = []
    for node in page.nodes:
        if node.type not in TASK_NODE_TYPES:
            continue
        texts = _badge_texts(node)
        if not texts:
            continue
        box = _drawn(node)
        for centres in _badge_centres(box, len(texts)):
            if all(_nearest_task(c, tasks) == node.id for c in centres):
                break
        out += [(node, text, centre) for (text, _wait), centre in zip(texts, centres)]
    return out


# ── Связи ──────────────────────────────────────────────────────────────────

def _label_text(edge: ProcessEdge, style: str) -> str:
    lines = (edge.labelLines or '').split('\n') if edge.labelLines else [edge.name or '']
    if 'html=1' in (style or '').replace(' ', ''):
        return '<br>'.join(html.escape(line, quote=False) for line in lines)
    return '\n'.join(lines)


def _child_label_matches(edge: ProcessEdge) -> bool:
    """Подпись была отдельной ячейкой-потомком связи (со смещением).

    Импорт по-разному читает положение подписи на самой связи и на ячейке-
    потомке: у потомка смещение входит в ``labelX``/``labelY``. Какой из двух
    записей была подпись, видно по самим числам — их и повторяем.
    """
    raw = edge.drawioLabel
    if not raw or len(raw) < 4 or not (raw[2] or raw[3]):
        return False
    x, y, ox, oy = raw[:4]
    want_x = x + ox * 0.005 if ox else x
    return (edge.labelX is not None and abs(edge.labelX - want_x) < 1e-6
            and edge.labelY is not None and abs(edge.labelY - (y + oy)) < 1e-6)


def _edge_label_mode(edge: ProcessEdge) -> str:
    """Где стоит подпись: ``free`` — надписью рядом, ``child`` — ячейкой связи, ``own`` — на связи.

    Кегль подписи импорт берёт со стиля той ячейки, где она стоит: кегль,
    которого нет в стиле самой связи, был у отдельной ячейки.
    """
    if not edge.name:
        return 'none'
    if edge.drawioLabelPoint:
        return 'free'
    own_font = _style_float(_style_map(edge.style or ''), 'fontsize')
    if _child_label_matches(edge) or (edge.labelFontSize and edge.labelFontSize != own_font):
        return 'child'
    return 'own'


def _edge_label_cells(edge: ProcessEdge) -> str:
    mode = _edge_label_mode(edge)
    style = EDGE_LABEL_STYLE + (f'fontSize={_num(edge.labelFontSize)};' if edge.labelFontSize else '')
    if mode == 'child':
        raw = edge.drawioLabel
        if raw and len(raw) >= 4:
            x, y, ox, oy = raw[:4]
        else:
            x, y, ox, oy = edge.labelX, edge.labelY, 0, 0
        attrs = ''.join(f' {k}="{_num(v)}"' for k, v in (('x', x), ('y', y)) if v is not None)
        offset = f'<mxPoint x="{_num(ox)}" y="{_num(oy)}" as="offset" />' if (ox or oy) else ''
        geo = f'<mxGeometry{attrs} relative="1" as="geometry">{offset}</mxGeometry>'
        return (f'<mxCell id={quoteattr(edge.id + "-label")} value={quoteattr(_label_text(edge, style))} '
                f'style={quoteattr(style)} vertex="1" connectable="0" parent={quoteattr(edge.id)}>{geo}</mxCell>')
    if mode == 'free':
        # Надпись у линии сама по себе: импорт отдаёт её ближайшей безымянной
        # связи, и её место становится местом подписи.
        lx, ly = edge.drawioLabelPoint[:2]
        return (f'<mxCell id={quoteattr(edge.id + "-label")} value={quoteattr(_label_text(edge, style))} '
                f'style={quoteattr(style)} vertex="1" connectable="0" parent="1">'
                f'{_geometry_xml(lx - 1, ly - 1, 2, 2)}</mxCell>')
    return ''


def _edge_value(edge: ProcessEdge) -> str:
    return _label_text(edge, edge.style or '') if _edge_label_mode(edge) == 'own' else ''


def _edge_geometry(edge: ProcessEdge) -> str:
    parts: List[str] = []
    attrs = ''
    if edge.labelX is not None and _edge_label_mode(edge) != 'child':
        attrs += f' x="{_num(edge.labelX)}"'
    if edge.labelY is not None and _edge_label_mode(edge) != 'child':
        attrs += f' y="{_num(edge.labelY)}"'
    raw = edge.drawioLabel if _edge_label_mode(edge) == 'own' else None
    if edge.sourcePoint:
        parts.append(f'<mxPoint x="{edge.sourcePoint.x}" y="{edge.sourcePoint.y}" as="sourcePoint" />')
    if edge.targetPoint:
        parts.append(f'<mxPoint x="{edge.targetPoint.x}" y="{edge.targetPoint.y}" as="targetPoint" />')
    points = edge.drawioPoints if edge.drawioPoints is not None else edge.points
    if points:
        parts.append('<Array as="points">' + ''.join(
            f'<mxPoint x="{p.x}" y="{p.y}" />' for p in points) + '</Array>')
    if raw and len(raw) >= 4 and (raw[2] or raw[3]):
        parts.append(f'<mxPoint x="{_num(raw[2])}" y="{_num(raw[3])}" as="offset" />')
    return f'<mxGeometry relative="1"{attrs} as="geometry">{"".join(parts)}</mxGeometry>'


# ── Страница целиком ───────────────────────────────────────────────────────

def _page_xml(page: BusinessProcess, page_links: Dict[str, str],
              data: Dict[str, Dict[str, str]]) -> str:
    cells: List[str] = ['<mxCell id="0" />', '<mxCell id="1" parent="0" />']
    pools = _pools(page)
    origin: Dict[str, Tuple[float, float]] = {'1': (0.0, 0.0)}
    for pool_id, (x, y, w, h) in pools.items():
        cells.append(_cell(pool_id, '', POOL_STYLE, '1', _geometry_xml(x, y, w, h)))
        origin[pool_id] = (x, y)

    # Дорожки — сверху вниз по вложенности: родитель раньше потомка.
    lane_by_id = {lane.id: lane for lane in page.lanes}
    placed: set = set()

    def place_lane(lane: ProcessNode) -> None:
        if lane.id in placed:
            return
        parent = lane.laneId if lane.laneId in pools or lane.laneId in lane_by_id else '1'
        if parent in lane_by_id:
            place_lane(lane_by_id[parent])
        px, py = origin[parent]
        g = lane.geometry
        name = '' if is_generated_name(lane.name) else lane.name
        cells.append(_cell(lane.id, _label(name, _style(lane)), _style(lane), parent,
                           _geometry_xml(g.x - px, g.y - py, g.width, g.height), data=data.get(lane.id)))
        origin[lane.id] = (g.x, g.y)
        placed.add(lane.id)

    for lane in page.lanes:
        place_lane(lane)

    for node in page.nodes:
        parent = node.laneId if node.laneId in lane_by_id else '1'
        px, py = origin[parent]
        g = _drawn(node)
        # Имя, придуманное импортом («Документ», «Условие»), на карте не
        # написано. Кроме примечания: пустую текстовую ячейку импорт снимает
        # как оформление, и примечание пропало бы с карты.
        name = '' if is_generated_name(node.name) and node.type != 'textAnnotation' else node.name
        name = _with_time(name, node)
        link = page_links.get(node.calledSubProcessId or '') if node.type == 'subProcess' else None
        cells.append(_cell(node.id, _label(name, _style(node)), _style(node), parent,
                           _geometry_xml(g.x - px, g.y - py, g.width, g.height),
                           data=data.get(node.id), link=link))

    # Значок часов — потомок своего шага: в draw.io он двигается вместе с шагом.
    # Соседом шага он оставался бы на месте, и сдвинутый на полсотни пикселей
    # шаг терял бы своё время — импорт отдаёт значок шагу в радиусе 130 px.
    for index, (node, text, (cx, cy)) in enumerate(_badges(page)):
        g = _drawn(node)
        cells.append(_cell(f'{node.id}-time-{index}', _label(text, BADGE_STYLE), BADGE_STYLE, node.id,
                           _geometry_xml(cx - BADGE_SIZE / 2 - g.x, cy - BADGE_SIZE / 2 - g.y,
                                         BADGE_SIZE, BADGE_SIZE)))

    for edge in page.edges:
        ends = ''.join(f' {attr}={quoteattr(value)}' for attr, value in
                       (('source', edge.sourceId), ('target', edge.targetId)) if value)
        cells.append(_cell(edge.id, _edge_value(edge), edge.style or '', '1', _edge_geometry(edge),
                           vertex=False, extra=ends, data=data.get(edge.id)))
        label = _edge_label_cells(edge)
        if label:
            cells.append(label)

    return ('<mxGraphModel grid="1" gridSize="10" guides="1" connect="1" arrows="1" fold="1" '
            'page="0" math="0" shadow="0"><root>' + ''.join(cells) + '</root></mxGraphModel>')


def _mxfile(process: BusinessProcess, data: Dict[str, Dict[str, Dict[str, str]]]) -> str:
    pages = _pages(process)
    page_links = {page.id: f'data:page/id,{_diagram_id(i)}' for i, (_name, page) in enumerate(pages)}
    diagrams = ''.join(
        f'<diagram id="{_diagram_id(i)}" name={quoteattr(name)}>'
        f'{_page_xml(page, page_links, data.get(_diagram_id(i), {}))}</diagram>'
        for i, (name, page) in enumerate(pages))
    return f'<mxfile host="SQB Process Hub" type="device">{diagrams}</mxfile>'


# ── Свойства платформы ─────────────────────────────────────────────────────

def _text(value) -> Optional[str]:
    """Значение поля строкой свойства; пустое — ``None``."""
    if value is None or value == '' or value == []:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _field(node: ProcessNode, field: str):
    value = getattr(node, field)
    if field == 'formFields':
        return [f.model_dump(mode='json') for f in value or []]
    return value


def _node_data(want: ProcessNode, got: Optional[ProcessNode]) -> Dict[str, str]:
    """Свойства шага, которые разбор рисунка не восстановил."""
    out: Dict[str, str] = {}
    for field in NODE_DATA_FIELDS:
        if field == 'slaMinutes' and want.slaMeasured:
            continue  # время с карты едет значком
        if field == 'costPerExecution' and want.category != 'rpa_bot':
            continue  # человеку цену считает ставка дорожки при сохранении
        mine = _field(want, field)
        theirs = _field(got, field) if got is not None else None
        if (mine or None) != (theirs or None):
            out[DATA_PREFIX + field] = _text(mine) or ''
    return out


def _platform_data(process: BusinessProcess, back: BusinessProcess) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Свойства по страницам: {id страницы файла: {id ячейки: {свойство: значение}}}."""
    # Основная страница — первая и в файле, и в разборе; остальные — по имени
    # и номеру среди одноимённых.
    theirs = _pages(back)[1:]
    back_pages = dict(zip(page_keys(theirs), (page for _name, page in theirs)))
    mine = _pages(process)
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for index, ((name, page), key) in enumerate(zip(mine, [None, *page_keys(mine[1:])])):
        got_page = back if index == 0 else back_pages.get(key)
        got_nodes = {n.id: n for n in (got_page.nodes if got_page else [])}
        got_lanes = {l.id: l for l in (got_page.lanes if got_page else [])}
        got_edges = {e.id: e for e in (got_page.edges if got_page else [])}
        cells: Dict[str, Dict[str, str]] = {}
        for node in page.nodes:
            found = _node_data(node, got_nodes.get(node.id))
            if found:
                cells[node.id] = found
        for lane in page.lanes:
            got = got_lanes.get(lane.id)
            if lane.hourlyRate is not None and (got is None or got.hourlyRate != lane.hourlyRate):
                cells[lane.id] = {DATA_PREFIX + 'hourlyRate': str(lane.hourlyRate)}
        for edge in page.edges:
            got = got_edges.get(edge.id)
            found: Dict[str, str] = {}
            if (edge.probability or 100) != ((got.probability if got else 100) or 100):
                found[DATA_PREFIX + 'probability'] = str(edge.probability)
            if (edge.condition or None) != ((got.condition if got else None) or None):
                found[DATA_PREFIX + 'condition'] = edge.condition or ''
            # Причалы связи импорт читает со стиля; у связи, собранной платформой
            # (склейка шин, холст), они бывают только в модели.
            for field in ('exitX', 'exitY', 'entryX', 'entryY'):
                mine = getattr(edge, field)
                if got is not None and mine != getattr(got, field):
                    found[DATA_PREFIX + field] = '' if mine is None else repr(float(mine))
            # Свободный конец связи импорт притягивает к фигуре под ним. Если в
            # карте он свободен, а разбор его притянул, — оставить свободным.
            if got is not None and not edge.sourceId and got.sourceId:
                found[DATA_PREFIX + 'freeSource'] = '1'
            if got is not None and not edge.targetId and got.targetId:
                found[DATA_PREFIX + 'freeTarget'] = '1'
            # Вид связи импорт выводит из рисунка. Обрывок без стрелок, который
            # разбор признал оформлением, в файле прицеплен к обеим фигурам — и
            # без свойства снова стал бы переходом.
            if got is not None and edge.kind != got.kind:
                found[DATA_PREFIX + 'kind'] = edge.kind
            if found:
                cells[edge.id] = found
        if cells:
            out[_diagram_id(index)] = cells
    return out


def generate_drawio_xml(process: BusinessProcess) -> str:
    """Файл draw.io карты: рисунок и, где нужно, свойства платформы."""
    plain = _mxfile(process, {})
    back = parse_drawio_xml(plain, f'{process.fileName or process.name}.drawio')
    data = _platform_data(process, back)
    return _mxfile(process, data) if data else plain


def iter_pages(process: BusinessProcess) -> Iterable[Tuple[str, BusinessProcess]]:
    """Страницы в порядке файла — для проверок и сравнения туда-обратно."""
    return _pages(process)
