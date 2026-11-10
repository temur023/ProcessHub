"""Приведение геометрии карты к читаемому виду после импорта draw.io.

draw.io прощает то, чего не прощают BPMN-редакторы и PIX Процессная студия:

* фигура события объявлена ``aspect=fixed`` — редактор рисует круг по меньшей
  стороне, а рамка может быть прямоугольной (80×50). Импортёр рисует эллипс, и
  значок таймера («часы») внутри него растягивается и вылезает за круг;
* подпись шага в draw.io свободно выходит за рамку, а bpmn.io и студия обрезают
  её по фигуре и кладут поверх маркера задачи в левом верхнем углу — читать
  текст становится невозможно;
* хранилище данных может лежать поверх шага: в draw.io оно уходит на задний
  план, в выгрузке — перекрывает подпись.

Модуль правит именно геометрию — состав узлов и связей не меняется, чтобы
модель процесса осталась ровно такой, какой её нарисовал аналитик.

Клиентский двойник — ``app/src/lib/layout.ts``.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    TASK_NODE_TYPES,
    ProcessEdge,
    ProcessNode,
)

#: Кегль подписи фигуры в bpmn.io и в Процессной студии.
FONT_SIZE = 12.0
#: Межстрочный интервал bpmn.io (1.2 кегля).
LINE_HEIGHT = FONT_SIZE * 1.2
#: Горизонтальные и вертикальные поля подписи внутри фигуры.
PAD_X = 12.0
PAD_Y = 10.0
#: Маркер задачи (человечек/шестерёнка) bpmn.io рисует в полосе 12–26 px от
#: верхней грани. Подпись центрируется по высоте и по ширине, поэтому увести
#: первую строку из-под маркера можно только запасом высоты — по 22 px сверху
#: и снизу от текстового блока.
MARKER_BAND = 44.0

#: Минимальный зазор до соседней фигуры, который сохраняем при расширении.
GAP_X = 24.0
GAP_Y = 16.0

#: Насколько шаг вообще разрешено расширять — иначе карта «поплывёт».
MAX_TASK_WIDTH = 260
MAX_TASK_HEIGHT = 220

#: Диаметр события: стандарт BPMN — 36 px, крупнее 56 выглядит непропорционально.
MIN_EVENT_SIDE = 36
MAX_EVENT_SIDE = 56
MIN_GATEWAY_SIDE = 40
MAX_GATEWAY_SIDE = 60

_EVENT_TYPES = (
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
)
_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')

#: Ширина символа в долях кегля (Arial/Helvetica, латиница и кириллица).
_NARROW = set(" iljtfrI.,:;|!'`()[]{}/\\-")
_WIDE = set('mwMWШЩЮЫФ@%')


def text_width(text: str, font_size: float = FONT_SIZE) -> float:
    """Ширина строки в пикселях — оценка без обращения к движку шрифтов."""
    total = 0.0
    for ch in text or '':
        if ch == ' ':
            total += 0.28
        elif ch in _NARROW:
            total += 0.32
        elif ch in _WIDE:
            total += 0.86
        elif ch.isdigit():
            total += 0.56
        elif ch.isupper():
            total += 0.68
        else:
            total += 0.55
    return total * font_size


def wrapped_line_count(text: str, available_px: float, font_size: float = FONT_SIZE) -> int:
    """Сколько строк займёт подпись при переносе по словам."""
    words = (text or '').split()
    if not words:
        return 1
    if available_px <= 0:
        return len(words)
    space = text_width(' ', font_size)
    lines = 1
    cursor = 0.0
    for word in words:
        w = text_width(word, font_size)
        if cursor and cursor + space + w > available_px:
            lines += 1
            cursor = w
        else:
            cursor += (space if cursor else 0.0) + w
        # Слово длиннее строки переносится посимвольно.
        while cursor > available_px and w > available_px:
            lines += 1
            cursor -= available_px
            w -= available_px
    return lines


def label_height(text: str, box_width: float, marker: bool) -> float:
    """Высота, которой хватит подписи внутри фигуры заданной ширины."""
    lines = wrapped_line_count(text, box_width - PAD_X)
    return lines * LINE_HEIGHT + PAD_Y + (MARKER_BAND if marker else 0.0)


def _boxes_overlap(a: ProcessNode, b: ProcessNode, gap_x: float, gap_y: float) -> bool:
    ag, bg = a.geometry, b.geometry
    return (
        ag.x - gap_x < bg.x + bg.width
        and bg.x - gap_x < ag.x + ag.width
        and ag.y - gap_y < bg.y + bg.height
        and bg.y - gap_y < ag.y + ag.height
    )


def _free_space(
    node: ProcessNode,
    others: Iterable[ProcessNode],
    lane: Optional[ProcessNode],
    axis: str,
) -> Tuple[float, float]:
    """Свободное место слева/справа (``axis='x'``) или сверху/снизу от фигуры.

    Соседями считаются только фигуры, перекрывающиеся с узлом по другой оси:
    шаг из соседнего ряда расширению не мешает.
    """
    g = node.geometry
    if axis == 'x':
        low, high = float(g.x), float(g.x + g.width)
        cross_low, cross_high = float(g.y), float(g.y + g.height)
        gap = GAP_X
    else:
        low, high = float(g.y), float(g.y + g.height)
        cross_low, cross_high = float(g.x), float(g.x + g.width)
        gap = GAP_Y

    before, after = 1e9, 1e9
    for other in others:
        if other.id == node.id:
            continue
        og = other.geometry
        if axis == 'x':
            o_low, o_high = float(og.x), float(og.x + og.width)
            o_cross_low, o_cross_high = float(og.y), float(og.y + og.height)
        else:
            o_low, o_high = float(og.y), float(og.y + og.height)
            o_cross_low, o_cross_high = float(og.x), float(og.x + og.width)
        # Мешает росту только тот, кто перекрывается по ДРУГОЙ оси. Соседа,
        # разведённого по перпендикуляру, рост вдоль этой оси не задевает —
        # с зазором в проверке шаг переставал расти из-за фигуры сбоку.
        if o_cross_high <= cross_low or o_cross_low >= cross_high:
            continue
        if o_high <= low:
            before = min(before, low - o_high - gap)
        elif o_low >= high:
            after = min(after, o_low - high - gap)
        else:
            # Фигуры уже пересекаются — в эту сторону не растём.
            before = min(before, 0.0)
            after = min(after, 0.0)

    if lane is not None:
        lg = lane.geometry
        if axis == 'x':
            before = min(before, low - lg.x)
            after = min(after, lg.x + lg.width - high)
        else:
            before = min(before, low - lg.y)
            after = min(after, lg.y + lg.height - high)

    return (max(before, 0.0), max(after, 0.0))


def _grow(node: ProcessNode, extra: float, axis: str, before: float, after: float) -> None:
    """Расширяет фигуру симметрично, перекладывая нехватку на свободную сторону."""
    take_before = min(extra / 2, before)
    take_after = min(extra - take_before, after)
    take_before = min(extra - take_after, before)
    g = node.geometry
    if axis == 'x':
        g.x = int(round(g.x - take_before))
        g.width = int(round(g.width + take_before + take_after))
    else:
        g.y = int(round(g.y - take_before))
        g.height = int(round(g.height + take_before + take_after))


def square_up_events(nodes: List[ProcessNode]) -> List[str]:
    """Событиям и шлюзам возвращает квадратную рамку.

    В draw.io такие фигуры объявлены ``aspect=fixed`` и рисуются по меньшей
    стороне; прямоугольная рамка (80×50) — это просто место под подпись сбоку.
    Импортёр про ``aspect`` не знает и растягивает круг в эллипс, а значок
    внутри (часы таймера, конверт сообщения) вылезает за контур.
    """
    touched: List[str] = []
    for node in nodes:
        if node.type in _EVENT_TYPES:
            lo, hi = MIN_EVENT_SIDE, MAX_EVENT_SIDE
        elif node.type in _GATEWAY_TYPES:
            lo, hi = MIN_GATEWAY_SIDE, MAX_GATEWAY_SIDE
        else:
            continue
        g = node.geometry
        side = int(round(min(max(min(g.width, g.height), lo), hi)))
        if side == g.width and side == g.height:
            continue
        g.x = int(round(g.x + (g.width - side) / 2))
        g.y = int(round(g.y + (g.height - side) / 2))
        g.width = side
        g.height = side
        touched.append(node.id)
    return touched


def _room_for_pins(
    room: Tuple[float, float],
    pins: Sequence[ProcessNode],
    lo: float,
    hi: float,
    axis: str,
) -> Tuple[float, float]:
    """Урезает запас роста на то, насколько пришпиленный артефакт торчит за грань.

    Документ поедет вместе с гранью, за которую держится, поэтому место ему
    нужно заранее. Иначе шаг вырастает вплотную к соседу, ехать документу
    становится некуда, и он остаётся под разросшейся рамкой — связь тогда
    обходит шаг кругом.
    """
    before, after = room
    if pins:
        spans = [
            (pin.geometry.x, pin.geometry.x + pin.geometry.width) if axis == 'x'
            else (pin.geometry.y, pin.geometry.y + pin.geometry.height)
            for pin in pins
        ]
        before -= max([lo - p_lo for p_lo, _ in spans] + [0.0])
        after -= max([p_hi - hi for _, p_hi in spans] + [0.0])
    return max(before, 0.0), max(after, 0.0)


def fit_labels(
    nodes: List[ProcessNode],
    lanes: List[ProcessNode],
    pinned: Optional[Dict[str, List[ProcessNode]]] = None,
) -> List[str]:
    """Расширяет шаги и примечания так, чтобы подпись помещалась в рамку.

    Сначала пробуем добрать ширину — строк становится меньше, и карта остаётся
    такой же высоты. Если места по горизонтали нет, добираем высоту. Рост
    ограничен свободным местом до соседней фигуры и границами дорожки, поэтому
    шаги не наезжают друг на друга.

    ``pinned`` — артефакты, приколотые к шагу пунктиром. Они поедут вместе с
    гранью, поэтому место под них резервируем заранее.
    """
    lane_by_id = {lane.id: lane for lane in lanes}
    fitted: List[str] = []

    targets = [
        n for n in nodes
        if n.type in TASK_NODE_TYPES or n.type == 'textAnnotation'
    ]
    # Крупные фигуры расширяем первыми: им сложнее найти место.
    targets.sort(key=lambda n: -len(n.name or ''))

    for node in targets:
        is_note = node.type == 'textAnnotation'
        marker = not is_note
        lane = lane_by_id.get(node.laneId or '')
        g = node.geometry
        if label_height(node.name, g.width, marker) <= g.height:
            continue

        pins = (pinned or {}).get(node.id, ())
        before_x, after_x = _room_for_pins(
            _free_space(node, nodes, lane, 'x'), pins, g.x, g.x + g.width, 'x')
        # Примечанию потолок ширины ставит его дорожка, а не константа. У шага
        # ширину надо ограничивать: он вытянется на полкарты и разорвёт ряд.
        # Примечание же вытянуться не может — рост прекращается, как только
        # очередная строка перестаёт убираться, — зато абзац в 337 символов
        # внутри полосы участника иначе не помещается ни при какой высоте:
        # полоса высотой 80 px даёт место шести строкам по ширине 370 px и
        # пяти по ширине всей полосы, а константа в 340 px запрещала рост
        # вовсе, потому что фигура и так была шире неё.
        max_width = (g.width + before_x + after_x) if is_note else MAX_TASK_WIDTH
        room_x = min(before_x + after_x, max(max_width - g.width, 0))

        # Расширяем, только пока это убирает строку: подпись в одну строку от
        # лишней ширины не выигрывает, а фигура зря съедает место соседей.
        lines = wrapped_line_count(node.name, g.width - PAD_X)
        add_x = 0.0
        probe = 0.0
        step = 10.0
        while probe < room_x and lines > 1:
            probe = min(probe + step, room_x)
            probe_lines = wrapped_line_count(node.name, g.width + probe - PAD_X)
            if probe_lines >= lines:
                continue
            add_x, lines = probe, probe_lines
            if label_height(node.name, g.width + add_x, marker) <= g.height:
                break
        if add_x > 0:
            _grow(node, add_x, 'x', before_x, after_x)

        need_h = label_height(node.name, g.width, marker)
        if need_h > g.height:
            max_height = MAX_TASK_HEIGHT if not is_note else MAX_TASK_HEIGHT
            before_y, after_y = _room_for_pins(
                _free_space(node, nodes, lane, 'y'), pins, g.y, g.y + g.height, 'y')
            room_y = min(before_y + after_y, max(max_height - g.height, 0))
            _grow(node, min(need_h - g.height, room_y), 'y', before_y, after_y)

        fitted.append(node.id)
    return fitted


def _fits(box: Tuple[int, int, int, int], lane: Optional[ProcessNode]) -> bool:
    if lane is None:
        return True
    x, y, w, h = box
    lg = lane.geometry
    return lg.x <= x and lg.y <= y and x + w <= lg.x + lg.width and y + h <= lg.y + lg.height


def separate_artifacts(nodes: List[ProcessNode], lanes: List[ProcessNode]) -> List[str]:
    """Убирает наложение артефакта на шаг.

    В draw.io хранилище данных уходит на задний план, и наложение аналитику не
    мешает. В выгрузке порядок отрисовки другой — цилиндр ложится поверх
    подписи шага. Сдвигаем артефакт, шаг не трогаем, и только туда, где он
    останется внутри своей дорожки и никого не заденет.
    """
    lane_by_id = {lane.id: lane for lane in lanes}
    flow = [n for n in nodes if n.type not in ARTIFACT_NODE_TYPES and n.type != 'lane']
    moved: List[str] = []

    for artifact in nodes:
        if artifact.type not in ARTIFACT_NODE_TYPES or artifact.type == 'textAnnotation':
            continue
        step = next((s for s in flow if _boxes_overlap(artifact, s, 0, 0)), None)
        if step is None:
            continue

        ag, sg = artifact.geometry, step.geometry
        lane = lane_by_id.get(artifact.laneId or '')

        # Смещение считаем до полного выхода за грань шага, а не по величине
        # пересечения: цилиндр может лежать целиком внутри шага, и тогда сдвиг
        # «на ширину пересечения» его не освобождает.
        gap = 8
        up = (ag.y + ag.height) - sg.y + gap
        down = (sg.y + sg.height) - ag.y + gap
        left = (ag.x + ag.width) - sg.x + gap
        right = (sg.x + sg.width) - ag.x + gap
        candidates = [
            (ag.x, ag.y - up, ag.width, ag.height),
            (ag.x, ag.y + down, ag.width, ag.height),
            (ag.x - left, ag.y, ag.width, ag.height),
            (ag.x + right, ag.y, ag.width, ag.height),
        ]
        # Сначала — куда двигать ближе.
        candidates.sort(key=lambda c: abs(c[0] - ag.x) + abs(c[1] - ag.y))

        others = [n for n in nodes if n.id != artifact.id and n.type != 'lane']
        for x, y, w, h in candidates:
            if not _fits((x, y, w, h), lane):
                continue
            clash = any(
                min(x + w, o.geometry.x + o.geometry.width) - max(x, o.geometry.x) > 0
                and min(y + h, o.geometry.y + o.geometry.height) - max(y, o.geometry.y) > 0
                for o in others
            )
            if clash:
                continue
            ag.x, ag.y = int(x), int(y)
            moved.append(artifact.id)
            break
    return moved


#: Дальше этого от грани шага артефакт уже не считается пришпиленным к ней, px.
#:
#: Документ аналитик ставит вплотную к углу шага: по картам банка зазор от
#: грани держится в пределах 4–25 px. Фигура, стоящая дальше, живёт сама по
#: себе, и таскать её за рамкой шага незачем.
DOCK_REACH = 32.0

#: Доли переноса, которые пробуем по очереди. Место рядом с шагом занимает
#: подросший сосед, и на полный сдвиг артефакт иногда не пролезает; лучше
#: подвинуть его насколько получится, чем оставить под рамкой.
_CARRY_SHARES = (1.0, 0.9, 0.75, 0.5)


def _box_distance(a: ProcessNode, b: ProcessNode) -> float:
    """Зазор между рамками по той оси, где он больше."""
    ag, bg = a.geometry, b.geometry
    dx = max(bg.x - (ag.x + ag.width), ag.x - (bg.x + bg.width), 0.0)
    dy = max(bg.y - (ag.y + ag.height), ag.y - (bg.y + bg.height), 0.0)
    return max(dx, dy)


#: Допуск на долю причала: аналитик ставит якорь мышью, и вместо ровного нуля
#: в стиле оказывается 0.003.
_EDGE_TOLERANCE = 0.05


def _on_edge(fraction: Optional[float]) -> bool:
    """Причал стоит на грани рамки, а не посередине."""
    if fraction is None:
        return False
    return fraction < _EDGE_TOLERANCE or fraction > 1 - _EDGE_TOLERANCE


def pinned_artifacts(
    nodes: Sequence[ProcessNode],
    edges: Sequence[ProcessEdge],
) -> Dict[str, List[ProcessNode]]:
    """Артефакты, приколотые к шагу: связаны с ним пунктиром и стоят вплотную.

    Такой документ аналитик двигает вместе с шагом, и укладка обязана вести
    себя так же: рамка шага не имеет права ни наехать на него, ни уехать от
    него. Фигура, стоящая поодаль, живёт сама по себе — её не трогаем.
    """
    by_id = {node.id: node for node in nodes}
    pinned: Dict[str, List[ProcessNode]] = {}
    for edge in edges:
        if edge.kind != 'association':
            continue
        ends = (by_id.get(edge.sourceId), by_id.get(edge.targetId))
        artifact = next(
            (n for n in ends
             if n is not None and n.type in ARTIFACT_NODE_TYPES and n.type != 'textAnnotation'),
            None,
        )
        step = next((n for n in ends if n is not None and n.type in TASK_NODE_TYPES), None)
        if artifact is None or step is None:
            continue
        if _box_distance(artifact, step) > DOCK_REACH:
            continue
        # Держим только тот приём, которым аналитик рисует документ у шага:
        # линия выходит из ГОРИЗОНТАЛЬНОЙ грани документа (низ или верх) и
        # входит в ВЕРТИКАЛЬНУЮ грань шага (левая или правая) — то самое
        # колено. Прочие сочетания причалов трассировка и так перекладывает
        # по-своему, и таскать за шагом фигуру там не за чем.
        if artifact.id == edge.sourceId:
            side_artifact, side_step = edge.exitY, edge.entryX
        else:
            side_artifact, side_step = edge.entryY, edge.exitX
        if not _on_edge(side_artifact) or not _on_edge(side_step):
            continue
        pinned.setdefault(step.id, []).append(artifact)
    return pinned


def _axis_shift(
    lo: float,
    hi: float,
    old: Tuple[float, float],
    new: Tuple[float, float],
) -> float:
    """На сколько сдвинуть артефакт вдоль оси вслед за ближней гранью шага.

    За грань артефакт держится, только если он за неё выступает: тогда рост
    рамки в эту сторону его и накрывает. Фигура, которая по этой оси целиком
    внутри шага (документ ровно над серединой шага), не держится ни за левую
    грань, ни за правую — она едет за серединой, то есть при симметричном
    росте стоит на месте.
    """
    old_lo, old_hi = old
    new_lo, new_hi = new
    if lo < old_lo:
        return new_lo - old_lo
    if hi > old_hi:
        return new_hi - old_hi
    return (new_lo + new_hi - old_lo - old_hi) / 2


def carry_pinned_artifacts(
    nodes: List[ProcessNode],
    lanes: List[ProcessNode],
    edges: Sequence[ProcessEdge],
    pinned: Dict[str, List[ProcessNode]],
    before: Dict[str, Tuple[float, float, float, float]],
) -> List[str]:
    """Переносит приколотый артефакт вслед за гранью шага, которую растянули.

    Документ аналитик ставит вплотную к верхнему левому углу шага, и пунктир
    рисует коротким коленом: вниз от низа документа и вправо в левую грань.
    ``fit_labels`` растит шаг ОТ ЦЕНТРА — левая грань уезжает влево, верхняя
    вверх, — а документ остаётся на месте, и колено ломается. Либо шаг
    накрывает документ собой, и связь идёт в обход всей рамки; либо точка входа
    оказывается вровень с низом документа, и от колена остаётся чёрточка в три
    пикселя. И то и другое на карте выглядит криво, хотя в draw.io все такие
    связи нарисованы одинаково.

    Двигаем артефакт ровно на столько же, на сколько уехала ближняя к нему
    грань: привязка, которую нарисовал аналитик, сохраняется. Излом связи при
    этом снимаем: draw.io хранит в нём положение того же колена, посчитанное для
    прежних рамок, и после переезда он указывает туда, где фигуры уже нет —
    вместо колена рисуется лесенка. Колено считается заново по причалам, ровно
    как это делает сам ``elbowEdgeStyle``.
    """
    lane_by_id = {lane.id: lane for lane in lanes}
    by_id = {node.id: node for node in nodes}
    bends: Dict[str, List[ProcessEdge]] = {}
    for edge in edges:
        if edge.kind == 'association' and edge.points:
            for end in (edge.sourceId, edge.targetId):
                bends.setdefault(end, []).append(edge)
    carried: List[str] = []

    for step_id, artifacts in pinned.items():
        step = by_id.get(step_id)
        old = before.get(step_id)
        if step is None or old is None:
            continue
        ox, oy, ow, oh = old
        sg = step.geometry
        for artifact in artifacts:
            g = artifact.geometry
            dx = _axis_shift(g.x, g.x + g.width, (ox, ox + ow), (sg.x, sg.x + sg.width))
            dy = _axis_shift(g.y, g.y + g.height, (oy, oy + oh), (sg.y, sg.y + sg.height))
            if not dx and not dy:
                continue

            lane = lane_by_id.get(artifact.laneId or '')
            place = None
            for share in _CARRY_SHARES:
                x = int(round(g.x + dx * share))
                y = int(round(g.y + dy * share))
                if not _fits((x, y, g.width, g.height), lane):
                    continue
                clash = any(
                    other.id != artifact.id and other.type != 'lane'
                    and min(x + g.width, other.geometry.x + other.geometry.width)
                    - max(x, other.geometry.x) > 0
                    and min(y + g.height, other.geometry.y + other.geometry.height)
                    - max(y, other.geometry.y) > 0
                    for other in nodes
                )
                if not clash:
                    place = (x, y, dx * share, dy * share)
                    break
            if place is None:
                continue
            x, y, dx, dy = place
            for edge in bends.get(artifact.id, ()):
                edge.points = []
            g.x, g.y = x, y
            carried.append(artifact.id)
    return carried


def _label_overflows(node: ProcessNode) -> bool:
    """Подпись не помещается в рамку фигуры."""
    marker = node.type != 'textAnnotation'
    return label_height(node.name, node.geometry.width, marker) > node.geometry.height + 0.5


def normalize_layout(
    nodes: List[ProcessNode],
    lanes: List[ProcessNode],
    edges: Sequence[ProcessEdge] = (),
) -> Dict[str, List[str]]:
    """Полный проход нормализации. Возвращает, что именно пришлось поправить.

    Артефакты разводим дважды: до подгонки подписей — чтобы наложение не
    считалось «нет свободного места» и шаг мог расшириться, и после — потому
    что подросший шаг может задеть соседний цилиндр. Между этими проходами
    подтягиваем приколотые артефакты за рамкой их шага.
    """
    squared = square_up_events(nodes)
    moved = separate_artifacts(nodes, lanes)
    pinned = pinned_artifacts(nodes, edges)
    before = {
        n.id: (float(n.geometry.x), float(n.geometry.y),
               float(n.geometry.width), float(n.geometry.height))
        for n in nodes
    }
    touched = fit_labels(nodes, lanes, pinned)
    moved += [nid for nid in carry_pinned_artifacts(nodes, lanes, edges, pinned, before)
              if nid not in moved]
    moved += [nid for nid in separate_artifacts(nodes, lanes) if nid not in moved]
    # Расширить фигуру удаётся не всегда: соседи стоят вплотную, и места не
    # остаётся. Такие разделяем, иначе отчёт сообщает «расширены под подпись»
    # про фигуру, у которой подпись по-прежнему не помещается.
    by_id = {n.id: n for n in nodes}
    cramped = [nid for nid in touched if nid in by_id and _label_overflows(by_id[nid])]
    fitted = [nid for nid in touched if nid not in cramped]
    return {'squared': squared, 'fitted': fitted, 'moved': moved, 'cramped': cramped}


#: Подписи событий, шлюзов и артефактов bpmn.io рисует ВНЕ фигуры и переносит
#: по узкой рамке в 90 px. Длинное имя шлюза превращалось в столбец из семи
#: строк, который ложился на соседние шаги и на подписи связей.
MIN_EXTERNAL_LABEL_WIDTH = 90
MAX_EXTERNAL_LABEL_WIDTH = 220
#: Сколько строк подписи считаем приемлемыми, прежде чем расширять рамку.
EXTERNAL_LABEL_TARGET_LINES = 2
EXTERNAL_LABEL_GAP = 6.0
#: Пустое поле над первой и под последней строкой в рамке подписи, px.
#:
#: Рамку bpmn.io верстает на 4 px выше строк (``lines * LINE_HEIGHT + 4``), и
#: текст стоит посередине. Поле не печатается: разделитель дорожек, прошедший
#: по нему, текст не перечёркивает. И оно же повторяет draw.io: там подпись
#: над фигурой печатается с отступом ``spacing`` в 2 px, так что рамка,
#: поставленная вплотную к фигуре, кладёт текст ровно туда, где его видит
#: аналитик.
LABEL_TEXT_INSET = 2
#: Шаг, которым подпись отходит от фигуры, когда её место занято.
LABEL_SLIDE_STEP = 4

#: Типы, подпись которых импортёр выносит за пределы фигуры.
EXTERNAL_LABEL_TYPES = _EVENT_TYPES + _GATEWAY_TYPES + ('dataStore', 'dataObject')

Box = Tuple[int, int, int, int]


def style_map(style: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (style or '').split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            out[key.strip().lower()] = value.strip().lower()
    return out


def _overlap_area(box: Box, other: Box) -> float:
    ax, ay, aw, ah = box
    bx, by, bw, bh = other
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return dx * dy if dx > 0 and dy > 0 else 0.0


#: Во сколько раз дороже наехать на «запретное» место, чем на обычное.
#:
#: Препятствия не равны между собой. Заехать подписью на пустую часть шага —
#: терпимо, её ещё можно прочесть. Заехать на полосу с названием дорожки —
#: нельзя: там уже напечатан повёрнутый текст, и две надписи складываются в
#: нечитаемую кашу. Пока вес был общий, выбор мог предпочесть заголовок
#: дорожки чуть большему перекрытию с фигурой.
_FORBIDDEN_WEIGHT = 1000.0

#: Перекрытие, которое ещё не считается наложением, px².
#:
#: Требовать ровно ноль — слишком строго. Линии связей входят в расчёт не
#: волосяной чертой, а «коридором» в ``SEGMENT_THICKNESS`` пикселей с каждой
#: стороны, и подпись, зацепившая такой коридор уголком в пару пикселей, до
#: самой линии не достаёт. Пока порога не было, «Ha» под шлюзом уезжала на
#: 24 px в сторону из-за нахлёста в 10 px² на пунктир соседнего хранилища:
#: место ровно на своей линии проигрывало оторванному, но идеально чистому.
#:
#: Столько же (40 px²) считает незаметным для глаза проверка выгрузки в
#: ``tests/test_bpmn_label_placement.py``: это меньше половины буквы.
LABEL_TOUCH_AREA = 40.0


def choose_label_box(
    candidates: List[Box],
    obstacles: Iterable[Box],
    forbidden: Iterable[Box] = (),
) -> Box:
    """Первая позиция подписи, которая ни на что заметно не наезжает.

    Если свободной нет, берём наименее конфликтную: подпись всё равно должна
    где-то стоять, но пусть перекрывает как можно меньше. Перекрытие с
    ``forbidden`` считается на три порядка дороже обычного — туда подпись
    попадёт, только если других мест нет вовсе.

    С ``forbidden`` сравниваются строки подписи, а не вся рамка: запретна
    полоса для ТЕКСТА, а пустое поле рамки ничего не печатает. Пока
    сравнивалась рамка, разделитель, проходящий в паре пикселей над буквами,
    считался перечёркивающим подпись, и «EHA» уезжала от цилиндра в соседнюю
    дорожку.
    """
    # Перекрыть хоть одну позицию может только то, что заходит в их общую
    # рамку: остальное даёт ноль, и сравнивать с ним каждую позицию незачем.
    # На карте в 1600 фигур занятых мест — тысячи, и полный перебор занимал
    # треть раскладки. Порядок оставшихся тот же, поэтому суммы — и выбор —
    # совпадают до бита. Края рамки берутся теми же действиями, что в
    # ``_overlap_area`` (x + width), по рамкам позиций и по строкам текста:
    # что задевает хоть одну, задевает и общую рамку, без потерь на округлении.
    # Лишнее в отбор попасть может (оно даст свой честный ноль), нужное — нет.
    texts = [(x, y + LABEL_TEXT_INSET, w, h - 2 * LABEL_TEXT_INSET) for x, y, w, h in candidates]
    spans = [*candidates, *texts]
    if spans:
        left = min(s[0] for s in spans)
        top = min(s[1] for s in spans)
        right = max(s[0] + s[2] for s in spans)
        bottom = max(s[1] + s[3] for s in spans)
        obstacles = [o for o in obstacles
                     if o[0] < right and o[1] < bottom and o[0] + o[2] > left and o[1] + o[3] > top]
        forbidden = [o for o in forbidden
                     if o[0] < right and o[1] < bottom and o[0] + o[2] > left and o[1] + o[3] > top]
    else:
        obstacles = list(obstacles)
        forbidden = list(forbidden)
    best: Optional[Box] = None
    best_area: Optional[float] = None
    for box in candidates:
        x, y, width, height = box
        text = (x, y + LABEL_TEXT_INSET, width, height - 2 * LABEL_TEXT_INSET)
        # Как только позиция проиграла лучшей из уже проверенных, досчитывать
        # её перекрытия незачем: на карте в сотни фигур перебор «каждый
        # кандидат против каждого препятствия» занимал секунды.
        area = 0.0
        for obstacle in forbidden:
            area += _FORBIDDEN_WEIGHT * _overlap_area(text, obstacle)
            if best_area is not None and area >= best_area:
                break
        else:
            for obstacle in obstacles:
                area += _overlap_area(box, obstacle)
                if best_area is not None and area >= best_area:
                    break
        if area <= LABEL_TOUCH_AREA:
            return box
        if best_area is None or area < best_area:
            best, best_area = box, area
    return best if best is not None else candidates[0]


def label_size(text: str) -> Tuple[int, int]:
    """Рамка подписи: по тексту, а не по фиксированной ширине.

    Короткому «To'liq» рамка в 90 px не нужна — на плотной карте лишние 55 px
    ровно и приводят к тому, что подпись ветки ложится на соседний шаг.
    Длинную подпись расширяем, пока она не уляжется в пару строк.
    """
    snug = int(round(text_width(text) + 10))
    if snug <= MAX_EXTERNAL_LABEL_WIDTH:
        return max(snug, 32), int(round(LINE_HEIGHT + 4))
    width = MIN_EXTERNAL_LABEL_WIDTH
    while (
        width < MAX_EXTERNAL_LABEL_WIDTH
        and wrapped_line_count(text, width) > EXTERNAL_LABEL_TARGET_LINES
    ):
        width += 10
    lines = wrapped_line_count(text, width)
    return width, int(round(lines * LINE_HEIGHT + 4))


def rendered_label_size(text: str) -> Tuple[int, int]:
    """Рамка подписи такой, какой её НАРИСУЕТ bpmn.io.

    Ширину из ``BPMNLabel`` импортёр не читает: выносную подпись и подпись
    связи он верстает по собственной рамке в 90 px. Объявленные 56×119 он
    рисует как 87×53, объявленные 200×18 — как 90×54; ни одна подпись на 22
    картах банка не вышла шире 90 px. Пока резерв расходился с рисунком,
    проверка пересечений считала не ту рамку: подпись «свободно» вставала
    туда, где нарисованная накрывает соседний шаг.

    Раньше здесь перебирались вёрстки — по ширине фигуры, по тексту, узкие в
    три и четыре строки. В draw.io аналитик действительно ставит под листом
    узкий столбик, но в выгрузке этот перебор ни на что не влияет: что бы мы
    ни объявили, bpmn.io нарисует свою рамку.
    """
    width = min(int(round(text_width(text) + 10)), MIN_EXTERNAL_LABEL_WIDTH)
    lines = wrapped_line_count(text, MIN_EXTERNAL_LABEL_WIDTH)
    return max(width, 32), int(round(lines * LINE_HEIGHT + 4))


def node_label_candidates(node: ProcessNode) -> List[Box]:
    """Места выносной подписи фигуры, из которых выбирает раскладка.

    Подпись ИТ-системы и документа — только на стороне, заданной в draw.io
    (под цилиндром), с зазором или вплотную. Сдвиги вбок и отход дальше
    уводили «EHA» от её цилиндра: на картах банка так стояла каждая седьмая
    подпись, и одни системы были подписаны под фигурой, другие — в стороне.
    Линия, что проходит под подписью, её не перечёркивает: холст кладёт под
    текст фон. Событиям и шлюзам сдвиги оставлены — их подпись стоит на пути
    связей, и уйти с линии ей важнее.
    """
    candidates = external_label_candidates(node)
    if node.type in ('dataStore', 'dataObject'):
        return candidates[:2]
    return candidates


def external_label_candidates(node: ProcessNode) -> List[Box]:
    """Позиции выносной подписи — в порядке предпочтения.

    Первой идёт та, которую выбрал аналитик в draw.io: стиль фигуры хранит
    ``labelPosition`` и ``verticalLabelPosition``, и на карте банка они
    расставлены не случайно — подпись таймера уведена влево, чтобы не лечь на
    вертикальную связь, а подпись хранилища данных поднята над цилиндром.
    Импортёр про эти стили не знает и кладёт всё под фигуру.
    """
    text = node.name or ''
    g = node.geometry
    gap = EXTERNAL_LABEL_GAP
    smap = style_map(node.style)
    horizontal = smap.get('labelposition', 'center')
    vertical = smap.get('verticallabelposition', 'bottom')

    variants = [rendered_label_size(text)]

    def at(width: int, height: int, space: float = gap) -> Dict[str, Box]:
        center_x = int(round(g.x + g.width / 2 - width / 2))
        middle_y = int(round(g.y + g.height / 2 - height / 2))
        return {
            'below': (center_x, int(round(g.y + g.height + space)), width, height),
            'above': (center_x, int(round(g.y - space - height)), width, height),
            'left': (int(round(g.x - space - width)), middle_y, width, height),
            'right': (int(round(g.x + g.width + space)), middle_y, width, height),
        }

    if horizontal == 'left':
        first = 'left'
    elif horizontal == 'right':
        first = 'right'
    elif vertical == 'top':
        first = 'above'
    else:
        first = 'below'
    # Рамка у подписи одна — та, которой её нарисует bpmn.io, — поэтому
    # перебираются только места, а не вёрстки.
    opposite = {'below': 'above', 'above': 'below', 'left': 'right', 'right': 'left'}[first]
    order: List[Box] = []
    for width, height in variants:
        order.append(at(width, height)[first])
    # Если место с обычным зазором занято, подпись сперва ПРИДВИГАЕТСЯ к фигуре:
    # рамка вплотную — это текст ровно там, где его печатает draw.io (см.
    # ``LABEL_TEXT_INSET``). На «Asosiy hisob» цилиндры стоят в 18–27 px под
    # границей дорожки: с зазором разделитель задевает буквы, вплотную —
    # проходит выше.
    for width, height in variants:
        order.append(at(width, height, 0)[first])
    # Подпись, сдвинутая вдоль своей же грани, всё ещё читается как подпись
    # этой фигуры; уведённая на другую сторону — уже нет. Поэтому сдвиг вдоль
    # стороны аналитика идёт раньше смены стороны: под цилиндром места на всю
    # ширину подписи нет почти никогда, но полшага левее оно есть.
    #
    # Пока противоположная сторона стояла сразу за стороной аналитика, занятое
    # место над цилиндром отправляло «EHA» ПОД цилиндр. На карте от этого одни
    # системы подписаны сверху, другие снизу, подпись оказывается ближе к
    # чужой фигуре, чем к своей, и аналитик читает её как подпись соседа.
    for width, height in variants:
        bx, by, bw, bh = at(width, height)[first]
        if first in ('below', 'above'):
            shift = int(round(gap + width / 2))
            order.extend([(bx - shift, by, bw, bh), (bx + shift, by, bw, bh)])
        else:
            # У боковой подписи «вдоль грани» — это вверх и вниз.
            shift = int(round(gap + height))
            order.extend([(bx, by - shift, bw, bh), (bx, by + shift, bw, bh)])
    # Если занят и сдвиг вдоль грани, отходим от фигуры ДАЛЬШЕ по той же
    # стороне. Чаще всего мешает не фигура, а разделитель дорожек: он проходит
    # вплотную над цилиндром, и ровное место под цилиндром выигрывало у своей
    # стороны. Отход выше разделителя снимает спор, не меняя стороны.
    #
    # Отходим мелким шагом, а не сразу на высоту подписи: разделителю хватает
    # нескольких пикселей, чтобы уйти из строк. Прыжок на целую «полку» ставил
    # «EHA» в 34 px от цилиндра, посреди соседней дорожки. Вторая полка
    # остаётся — для случаев, когда над фигурой стоит чужой шаг.
    for width, height in variants:
        bx, by, bw, bh = at(width, height)[first]
        step = int(round(gap + height + 4)) if first in ('below', 'above') else int(round(gap + width))
        for away in [*range(LABEL_SLIDE_STEP, step + 1, LABEL_SLIDE_STEP), step * 2]:
            if first == 'above':
                order.append((bx, by - away, bw, bh))
            elif first == 'below':
                order.append((bx, by + away, bw, bh))
            elif first == 'left':
                order.append((bx - away, by, bw, bh))
            else:
                order.append((bx + away, by, bw, bh))
    for width, height in variants:
        order.append(at(width, height)[opposite])
    for width, height in variants:
        boxes = at(width, height)
        shift = int(round(gap + width / 2))
        for name in ('below', 'above'):
            bx, by, bw, bh = boxes[name]
            order.extend([(bx - shift, by, bw, bh), (bx + shift, by, bw, bh)])
    for width, height in variants:
        boxes = at(width, height)
        order.extend([boxes['below'], boxes['above'], boxes['right'], boxes['left']])

    # Запасные позиции по диагоналям и на дальних «полках»: на плотной карте
    # четырёх сторон не хватает, и подпись садилась на соседнюю фигуру или в
    # полосу с названием дорожки. Полок три, а не одна: заголовок дорожки
    # теперь запретная зона, и подписи, которых он выталкивает, нужно куда-то
    # деть — иначе они лягут друг на друга.
    for width, height in variants:
        center_x = int(round(g.x + g.width / 2 - width / 2))
        middle_y = int(round(g.y + g.height / 2 - height / 2))
        above_y = int(round(g.y - gap - height))
        below_y = int(round(g.y + g.height + gap))
        step = int(round(gap + height + 4))
        side = int(round(gap + width / 2))
        for shelf in (1, 2, 3):
            dy = step * shelf
            order.extend([
                (center_x, below_y + dy, width, height),
                (center_x, above_y - dy, width, height),
                (center_x - side, below_y + dy - step, width, height),
                (center_x + side, below_y + dy - step, width, height),
                (center_x - side, above_y - dy + step, width, height),
                (center_x + side, above_y - dy + step, width, height),
            ])
        # И вбок, дальше от фигуры: у узкого события места сверху и снизу
        # может не быть вовсе.
        for shelf in (1, 2):
            dx = int(round((gap + width) * shelf))
            order.extend([
                (int(round(g.x - dx - width)), middle_y, width, height),
                (int(round(g.x + g.width + dx)), middle_y, width, height),
            ])

    seen: List[Box] = []
    for box in order:
        if box not in seen:
            seen.append(box)
    return seen


def node_obstacles(nodes: Iterable[ProcessNode], skip_id: str = '') -> List[Box]:
    return [
        (n.geometry.x, n.geometry.y, n.geometry.width, n.geometry.height)
        for n in nodes
        if n.id != skip_id and n.type != 'lane'
    ]


#: Отступы подписи от линии связи: вплотную, затем в стороне.
#: Кольца отступов подписи связи от линии. Три, а не два: заголовок дорожки
#: стал запретной зоной, и подписи, которых он выталкивает, должны иметь куда
#: отойти — иначе они ложатся друг на друга.
EDGE_LABEL_GAPS = (4, 26, 58)

#: Доли длины ломаной, около которых можно поставить подпись связи. Середина
#: предпочтительна, но на плотной карте там бывает занято — тогда подпись
#: сдвигается вдоль своей же линии, а не садится на чужую фигуру.
EDGE_LABEL_FRACTIONS = (0.5, 0.4, 0.6, 0.28, 0.72, 0.15, 0.85, 0.08, 0.92)


def _point_at(route: Sequence[Tuple[int, int]], fraction: float) -> Tuple[float, float, bool]:
    """Точка на ломаной по доле её длины и ориентация отрезка в этом месте."""
    lengths = [
        abs(b[0] - a[0]) + abs(b[1] - a[1])
        for a, b in zip(route, route[1:])
    ]
    total = sum(lengths)
    if total <= 0:
        return float(route[0][0]), float(route[0][1]), False
    target = total * fraction
    for (a, b), seg in zip(zip(route, route[1:]), lengths):
        if seg <= 0:
            continue
        if target <= seg:
            t = target / seg
            return (
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                abs(b[1] - a[1]) > abs(b[0] - a[0]),
            )
        target -= seg
    a, b = route[-2], route[-1]
    return float(b[0]), float(b[1]), abs(b[1] - a[1]) > abs(b[0] - a[0])


def edge_label_candidates(route: Sequence[Tuple[int, int]], text: str) -> List[Box]:
    """Позиции подписи связи вдоль ломаной.

    Первой идёт та, которую выбрал аналитик в draw.io: подпись стоит ровно на
    линии, а не рядом с ней. В исходнике карты «Ha» и «Yo'q» — это ``edgeLabel``
    с перпендикулярным смещением в 0–3 px, то есть по центру связи; редактор
    печатает под ними фон цвета холста, и линия видимо прерывается.

    Раньше выгрузка ставила подпись в стороне — на 13 px над горизонтальным
    отрезком и на 24 px правее вертикального. Формально текст ничем не
    перечёркнут, но у шлюза с двумя ветками по такой подписи уже не понять, к
    какой из линий она относится: она висит между ними.

    Смещённые позиции остаются запасными: если подпись по центру линии
    накрывает чужую фигуру, лучше отвести её вбок, чем спрятать чужой шаг.
    """
    sizes = [rendered_label_size(text)]
    points = list(route)
    if not points:
        w, h = sizes[0]
        return [(0, 0, w, h)]
    if len(points) == 1:
        points = points * 2

    out: List[Box] = []
    for width, height in sizes:
        for fraction in EDGE_LABEL_FRACTIONS:
            cx, cy, vertical = _point_at(points, fraction)
            x = int(round(cx - width / 2))
            y = int(round(cy - height / 2))
            # По центру линии — как в draw.io.
            out.append((x, y, width, height))
            # Два кольца отступов: сперва вплотную к линии, потом на ширину
            # фигуры в стороне. На тесной карте место у самой линии занято
            # соседним шагом, и подпись садилась ему прямо на текст.
            for gap in EDGE_LABEL_GAPS:
                above: Box = (x, int(round(cy - height - gap)), width, height)
                below: Box = (x, int(round(cy + gap)), width, height)
                right: Box = (int(round(cx + gap + 4)), y, width, height)
                left: Box = (int(round(cx - gap - 4 - width)), y, width, height)
                out.extend([right, left, above, below] if vertical else [above, below, right, left])

    seen: List[Box] = []
    for box in out:
        if box not in seen:
            seen.append(box)
    return seen


#: Толщина «коридора» линии связи при разведении подписей. Меньше — подпись
#: липнет к линии, больше — её некуда поставить на плотной карте.
SEGMENT_THICKNESS = 3


def segment_boxes(route: Iterable[Tuple[int, int]]) -> List[Box]:
    """Отрезки ломаной как узкие прямоугольники — препятствия для подписей.

    Без них подпись ветки шлюза ложится ровно на вертикальную связь и читается
    как перечёркнутая.
    """
    points = list(route)
    boxes: List[Box] = []
    half = SEGMENT_THICKNESS
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        boxes.append((
            int(left - half), int(top - half),
            int(right - left + 2 * half), int(bottom - top + 2 * half),
        ))
    return boxes
