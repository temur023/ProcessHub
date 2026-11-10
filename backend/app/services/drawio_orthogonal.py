"""Ломаная связи ``orthogonalEdgeStyle`` ровно так, как её строит draw.io.

Перенос ``mxEdgeStyle.OrthConnector`` и ``mxEdgeStyle.SegmentConnector`` вместе с
расчётом концов из ``mxGraphView`` (закреплённая точка ``exitX``/``entryX`` и
свободный конец на контуре фигуры) и тремя контурами ``mxPerimeter``, которые
встречаются на картах банка: прямоугольник, эллипс (события) и ромб (шлюзы).
Источник — jgraph/drawio (Apache 2.0).

Зачем. Эталон вида карты PIX — draw.io: аналитик видит линию такой, какой её
строит редактор, а не наш поиск пути с обходом фигур. Пока «ортогональные»
связи трассировал наш поиск, линия выходила из шага не с той грани и делала
петлю там, где в draw.io два излома. Порт сверен с ломаными, которые сам
draw.io считает на десяти картах банка.

Имена и порядок действий повторяют исходник: так расхождение с draw.io легче
найти построчным сравнением.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]

# mxConstants.DIRECTION_MASK_*
WEST, NORTH, SOUTH, EAST = 1, 2, 4, 8
ALL = 15

ORTH_BUFFER = 10
_DIR_VECTORS = [[-1, 0], [0, -1], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 0]]
_ROUTE_PATTERNS = [
    [[513, 2308, 2081, 2562], [513, 1090, 514, 2184, 2114, 2561],
     [513, 1090, 514, 2564, 2184, 2562],
     [513, 2308, 2561, 1090, 514, 2568, 2308]],
    [[514, 1057, 513, 2308, 2081, 2562], [514, 2184, 2114, 2561],
     [514, 2184, 2562, 1057, 513, 2564, 2184],
     [514, 1057, 513, 2568, 2308, 2561]],
    [[1090, 514, 1057, 513, 2308, 2081, 2562], [2114, 2561],
     [1090, 2562, 1057, 513, 2564, 2184],
     [1090, 514, 1057, 513, 2308, 2561, 2568]],
    [[2081, 2562], [1057, 513, 1090, 514, 2184, 2114, 2561],
     [1057, 513, 1090, 514, 2184, 2562, 2564],
     [1057, 2561, 1090, 514, 2568, 2308]],
]
_SIDE_MASK = 480
_CENTER_MASK = 512
_SOURCE_MASK = 1024
_TARGET_MASK = 2048

#: Маркер по умолчанию у draw.io: у конца — стрелка, у начала — ничего.
_DEFAULT_MARKER_SIZE = 6


def style_dict(style: Optional[str]) -> Dict[str, str]:
    """Стиль draw.io как словарь; ключи — с регистром, как в draw.io."""
    out: Dict[str, str] = {}
    for part in (style or '').split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            out[key.strip()] = value.strip()
    return out


def _round1(value: float) -> float:
    """``Math.round(v * 10) / 10``: у JavaScript половина округляется вверх."""
    return math.floor(value * 10 + 0.5) / 10


def perimeter_kind(style: Optional[str]) -> str:
    """Контур фигуры по её стилю: ``rectangle``, ``ellipse`` или ``rhombus``."""
    s = style_dict(style)
    perimeter = s.get('perimeter', '')
    first = (style or '').split(';', 1)[0].strip()
    if perimeter == 'ellipsePerimeter' or (not perimeter and first == 'ellipse'):
        return 'ellipse'
    if perimeter == 'rhombusPerimeter' or (not perimeter and first == 'rhombus'):
        return 'rhombus'
    return 'rectangle'


def _intersection(x0, y0, x1, y1, x2, y2, x3, y3) -> Optional[Point]:
    """``mxUtils.intersection``: точка пересечения двух отрезков или ``None``."""
    denom = ((y3 - y2) * (x1 - x0)) - ((x3 - x2) * (y1 - y0))
    if denom == 0:
        return None
    ua = (((x3 - x2) * (y0 - y2)) - ((y3 - y2) * (x0 - x2))) / denom
    ub = (((x1 - x0) * (y0 - y2)) - ((y1 - y0) * (x0 - x2))) / denom
    if 0.0 <= ua <= 1.0 and 0.0 <= ub <= 1.0:
        return x0 + ua * (x1 - x0), y0 + ua * (y1 - y0)
    return None


def _rectangle(bounds: Box, nxt: Point, orthogonal: bool) -> Point:
    x, y, w, h = bounds
    cx, cy = x + w / 2, y + h / 2
    dx, dy = nxt[0] - cx, nxt[1] - cy
    alpha = math.atan2(dy, dx)
    pi, pi2 = math.pi, math.pi / 2
    beta = pi2 - alpha
    t = math.atan2(h, w)
    if alpha < -pi + t or alpha > pi - t:
        px, py = x, cy - w * math.tan(alpha) / 2
    elif alpha < -t:
        px, py = cx - h * math.tan(beta) / 2, y
    elif alpha < t:
        px, py = x + w, cy + w * math.tan(alpha) / 2
    else:
        px, py = cx + h * math.tan(beta) / 2, y + h
    if orthogonal:
        if x <= nxt[0] <= x + w:
            px = nxt[0]
        elif y <= nxt[1] <= y + h:
            py = nxt[1]
        if nxt[0] < x:
            px = x
        elif nxt[0] > x + w:
            px = x + w
        if nxt[1] < y:
            py = y
        elif nxt[1] > y + h:
            py = y + h
    return px, py


def _ellipse(bounds: Box, nxt: Point, orthogonal: bool) -> Point:
    x, y, w, h = bounds
    a, b = w / 2, h / 2
    cx, cy = x + a, y + b
    px, py = nxt
    dx, dy = int(px - cx), int(py - cy)
    if dx == 0 and dy != 0:
        return cx, cy + b * dy / abs(dy)
    if dx == 0 and dy == 0:
        return px, py
    if orthogonal:
        if y <= py <= y + h:
            ty = py - cy
            inner = a * a * (1 - (ty * ty) / (b * b))
            tx = math.sqrt(inner) if inner > 0 else 0.0
            if px <= x:
                tx = -tx
            return cx + tx, py
        if x <= px <= x + w:
            tx = px - cx
            inner = b * b * (1 - (tx * tx) / (a * a))
            ty = math.sqrt(inner) if inner > 0 else 0.0
            if py <= y:
                ty = -ty
            return px, cy + ty
    d = dy / dx
    hh = cy - d * cx
    e = a * a * d * d + b * b
    f = -2 * cx * e
    g = a * a * d * d * cx * cx + b * b * cx * cx - a * a * b * b
    det = math.sqrt(max(f * f - 4 * e * g, 0.0))
    x1, x2 = (-f + det) / (2 * e), (-f - det) / (2 * e)
    y1, y2 = d * x1 + hh, d * x2 + hh
    dist1 = math.hypot(x1 - px, y1 - py)
    dist2 = math.hypot(x2 - px, y2 - py)
    return (x1, y1) if dist1 < dist2 else (x2, y2)


def _rhombus(bounds: Box, nxt: Point, orthogonal: bool) -> Optional[Point]:
    x, y, w, h = bounds
    cx, cy = x + w / 2, y + h / 2
    px, py = nxt
    if cx == px:
        return (cx, y) if cy > py else (cx, y + h)
    if cy == py:
        return (x, cy) if cx > px else (x + w, cy)
    tx, ty = cx, cy
    if orthogonal:
        if x <= px <= x + w:
            tx = px
        elif y <= py <= y + h:
            ty = py
    if px < cx:
        if py < cy:
            return _intersection(px, py, tx, ty, cx, y, x, cy)
        return _intersection(px, py, tx, ty, cx, y + h, x, cy)
    if py < cy:
        return _intersection(px, py, tx, ty, cx, y, x + w, cy)
    return _intersection(px, py, tx, ty, cx, y + h, x + w, cy)


def perimeter_point(bounds: Box, kind: str, nxt: Point, orthogonal: bool) -> Point:
    """``mxGraphView.getPerimeterPoint``: точка контура в сторону ``nxt``."""
    x, y, w, h = bounds
    if w <= 0 and h <= 0:
        return x + w / 2, y + h / 2
    if kind == 'ellipse':
        point = _ellipse(bounds, nxt, orthogonal)
    elif kind == 'rhombus':
        point = _rhombus(bounds, nxt, orthogonal)
    else:
        point = _rectangle(bounds, nxt, orthogonal)
    return point if point is not None else (x + w / 2, y + h / 2)


def connection_point(bounds: Box, kind: str, fx: float, fy: float,
                     dx: float = 0.0, dy: float = 0.0, perimeter: bool = True) -> Point:
    """``mxGraph.getConnectionPoint``: закреплённый конец ``exitX``/``entryX``."""
    x, y, w, h = bounds
    point = (x + fx * w + dx, y + fy * h + dy)
    if perimeter:
        point = perimeter_point(bounds, kind, point, False)
    return point


def jetty_size(style: Dict[str, str], source: bool) -> float:
    """``mxEdgeStyle.getJettySize``."""
    value = style.get('sourceJettySize' if source else 'targetJettySize',
                      style.get('jettySize', ORTH_BUFFER))
    if value == 'auto':
        marker = style.get('startArrow' if source else 'endArrow', 'none' if source else 'classic')
        if marker != 'none':
            try:
                size = float(style.get('startSize' if source else 'endSize', _DEFAULT_MARKER_SIZE))
            except ValueError:
                size = _DEFAULT_MARKER_SIZE
            return max(2, math.ceil((size + ORTH_BUFFER) / ORTH_BUFFER)) * ORTH_BUFFER
        return 2 * ORTH_BUFFER
    try:
        return float(value)
    except (TypeError, ValueError):
        return ORTH_BUFFER


def _reverse(constraint: int) -> int:
    """``mxUtils.reversePortConstraints``."""
    result = (constraint & WEST) << 3
    result |= (constraint & NORTH) << 1
    result |= (constraint & SOUTH) >> 1
    result |= (constraint & EAST) >> 3
    return result


def _port_constraint(style: Dict[str, str], source: bool) -> int:
    raw = style.get('sourcePortConstraint' if source else 'targetPortConstraint')
    if not raw:
        return ALL
    value = 0
    for part in raw.lower().split(','):
        value |= {'west': WEST, 'north': NORTH, 'south': SOUTH, 'east': EAST}.get(part.strip(), 0)
    return value or ALL


def segment_connector(pts: List[Optional[Point]], source: Optional[Box], target: Optional[Box],
                      hints: Sequence[Point], result: list) -> None:
    """``mxEdgeStyle.SegmentConnector`` при масштабе 1.

    ``result`` уже начинается с закреплённого начала (или ``None``) — так его
    передаёт ``mxGraphView.updatePoints``, — и излом ближе пикселя к началу
    не добавляется.
    """
    tol = 1
    temp: List[List[float]] = []
    last_pushed = result[0] if result else None
    horizontal = True
    hint: Optional[List[float]] = None

    def push(point: List[float]) -> None:
        nonlocal last_pushed
        point[0], point[1] = _round1(point[0]), _round1(point[1])
        if last_pushed is None or abs(last_pushed[0] - point[0]) >= tol or abs(last_pushed[1] - point[1]) >= 1:
            result.append([point[0], point[1]])
            last_pushed = point

    pt: Optional[List[float]] = None
    if pts[0] is None and source is not None:
        pt = [source[0] + source[2] / 2, source[1] + source[3] / 2]
    elif pts[0] is not None:
        pt = [pts[0][0], pts[0][1]]
    last = len(pts) - 1
    pe = None

    if hints:
        hs = [[h[0], h[1]] for h in hints]
        if pt is not None:
            if abs(hs[0][0] - pt[0]) < tol:
                hs[0][0] = pt[0]
            if abs(hs[0][1] - pt[1]) < tol:
                hs[0][1] = pt[1]
        pe = pts[last]
        if pe is not None:
            if abs(hs[-1][0] - pe[0]) < tol:
                hs[-1][0] = pe[0]
            if abs(hs[-1][1] - pe[1]) < tol:
                hs[-1][1] = pe[1]
        hint = hs[0]
        current_term = source
        current_pt = pts[0]
        current_hint = hint
        if current_pt is not None:
            current_term = None
        for i in range(2):
            fixed_vert = current_pt is not None and current_pt[0] == current_hint[0]
            fixed_hoz = current_pt is not None and current_pt[1] == current_hint[1]
            in_hoz = current_term is not None and current_term[1] <= current_hint[1] <= current_term[1] + current_term[3]
            in_vert = current_term is not None and current_term[0] <= current_hint[0] <= current_term[0] + current_term[2]
            hoz_chan = fixed_hoz or (current_pt is None and in_hoz)
            vert_chan = fixed_vert or (current_pt is None and in_vert)
            if not (i == 0 and ((hoz_chan and vert_chan) or (fixed_vert and fixed_hoz))):
                if current_pt is not None and not fixed_hoz and not fixed_vert and (in_hoz or in_vert):
                    horizontal = not in_hoz
                    break
                if vert_chan or hoz_chan:
                    horizontal = hoz_chan
                    if i == 1:
                        horizontal = hoz_chan if len(hs) % 2 == 0 else vert_chan
                    break
            current_term = target
            current_pt = pts[last]
            if current_pt is not None:
                current_term = None
            # В JavaScript пустой список даёт здесь undefined, а не ошибку.
            current_hint = hs[-1] if hs else None
            if fixed_vert and fixed_hoz:
                hs = hs[1:]
        if horizontal and ((pts[0] is not None and pts[0][1] != hint[1]) or
                           (pts[0] is None and source is not None and
                            (hint[1] < source[1] or hint[1] > source[1] + source[3]))):
            temp.append([pt[0], hint[1]])
        elif not horizontal and ((pts[0] is not None and pts[0][0] != hint[0]) or
                                 (pts[0] is None and source is not None and
                                  (hint[0] < source[0] or hint[0] > source[0] + source[2]))):
            temp.append([hint[0], pt[1]])
        if horizontal:
            pt[1] = hint[1]
        else:
            pt[0] = hint[0]
        for h in hs:
            horizontal = not horizontal
            hint = h
            if horizontal:
                pt[1] = hint[1]
            else:
                pt[0] = hint[0]
            temp.append([pt[0], pt[1]])
    else:
        hint = pt
        horizontal = True

    pt = None if pts[last] is None else [pts[last][0], pts[last][1]]
    if pt is None and target is not None:
        pt = [target[0] + target[2] / 2, target[1] + target[3] / 2]
    if pt is not None and hint is not None:
        if horizontal and ((pts[last] is not None and pts[last][1] != hint[1]) or
                           (pts[last] is None and target is not None and
                            (hint[1] < target[1] or hint[1] > target[1] + target[3]))):
            temp.append([pt[0], hint[1]])
        elif not horizontal and ((pts[last] is not None and pts[last][0] != hint[0]) or
                                 (pts[last] is None and target is not None and
                                  (hint[0] < target[0] or hint[0] > target[0] + target[2]))):
            temp.append([hint[0], pt[1]])

    def contains(box: Box, p: List[float]) -> bool:
        return box[0] <= p[0] <= box[0] + box[2] and box[1] <= p[1] <= box[1] + box[3]

    if source is None or source is not target:
        if pts[0] is None and source is not None:
            while temp and contains(source, temp[0]):
                temp.pop(0)
        if pts[last] is None and target is not None:
            while temp and contains(target, temp[-1]):
                temp.pop()
    for p in temp:
        push(p)
    if pe is not None and result and result[-1] is not None and abs(pe[0] - result[-1][0]) <= tol and abs(pe[1] - result[-1][1]) <= tol:
        result.pop()
        if result and result[-1] is not None:
            result[-1] = [result[-1][0], result[-1][1]]
            if abs(result[-1][0] - pe[0]) < tol:
                result[-1][0] = pe[0]
            if abs(result[-1][1] - pe[1]) < tol:
                result[-1][1] = pe[1]


def orth_connector(pts: List[Optional[Point]], source: Optional[Box], target: Optional[Box],
                   hints: Sequence[Point], style: Dict[str, str], result: list) -> None:
    """``mxEdgeStyle.OrthConnector`` при масштабе 1 и без поворота фигур."""
    pts = [None if p is None else (_round1(p[0]), _round1(p[1])) for p in pts]
    source = None if source is None else tuple(_round1(v) for v in source)
    target = None if target is None else tuple(_round1(v) for v in target)
    p0, pe = pts[0], pts[-1]
    sx, sy, sw, sh = source if source is not None else (p0[0], p0[1], 1, 1)
    tx, ty, tw, th = target if target is not None else (pe[0], pe[1], 1, 1)
    source_buffer = jetty_size(style, True)
    target_buffer = jetty_size(style, False)
    if source is not None and target == source:
        target_buffer = max(source_buffer, target_buffer)
        source_buffer = target_buffer
    total_buffer = target_buffer + source_buffer
    too_short = False
    if p0 is not None and pe is not None:
        ddx, ddy = pe[0] - p0[0], pe[1] - p0[1]
        too_short = ddx * ddx + ddy * ddy < total_buffer * total_buffer
    if too_short or hints:
        segment_connector(pts, source, target, hints, result)
        return

    port_constraint = [ALL, ALL]
    if source is not None:
        port_constraint[0] = _port_constraint(style, True)
    if target is not None:
        port_constraint[1] = _port_constraint(style, False)
    if sw == 0 or sh == 0 or tw == 0 or th == 0:
        return

    direction = [0, 0]
    geo = [[sx, sy, sw, sh], [tx, ty, tw, th]]
    buffer = [source_buffer, target_buffer]
    limits = [[0.0] * 9 for _ in range(2)]
    for i in range(2):
        limits[i][1] = geo[i][0] - buffer[i]
        limits[i][2] = geo[i][1] - buffer[i]
        limits[i][4] = geo[i][0] + geo[i][2] + buffer[i]
        limits[i][8] = geo[i][1] + geo[i][3] + buffer[i]

    source_cx = geo[0][0] + geo[0][2] / 2.0
    source_cy = geo[0][1] + geo[0][3] / 2.0
    target_cx = geo[1][0] + geo[1][2] / 2.0
    target_cy = geo[1][1] + geo[1][3] / 2.0
    dx = source_cx - target_cx
    dy = source_cy - target_cy
    quad = 0
    if dx < 0:
        quad = 2 if dy < 0 else 1
    elif dy <= 0:
        quad = 3
        if dx == 0:
            quad = 2

    current_term = p0 if source is not None else None
    constraint = [[0.5, 0.5], [0.5, 0.5]]
    if source is None:
        constraint[0] = [0, 0]
    if target is None:
        constraint[1] = [0, 0]
    for i in range(2):
        if current_term is not None:
            constraint[i][0] = (current_term[0] - geo[i][0]) / geo[i][2]
            if abs(current_term[0] - geo[i][0]) <= 1:
                direction[i] = WEST
            elif abs(current_term[0] - geo[i][0] - geo[i][2]) <= 1:
                direction[i] = EAST
            constraint[i][1] = (current_term[1] - geo[i][1]) / geo[i][3]
            if abs(current_term[1] - geo[i][1]) <= 1:
                direction[i] = NORTH
            elif abs(current_term[1] - geo[i][1] - geo[i][3]) <= 1:
                direction[i] = SOUTH
        current_term = pe if target is not None else None

    source_top = geo[0][1] - (geo[1][1] + geo[1][3])
    source_left = geo[0][0] - (geo[1][0] + geo[1][2])
    source_bottom = geo[1][1] - (geo[0][1] + geo[0][3])
    source_right = geo[1][0] - (geo[0][0] + geo[0][2])
    separations = [0.0] * 5
    separations[1] = max(source_left - total_buffer, 0)
    separations[2] = max(source_top - total_buffer, 0)
    separations[4] = max(source_bottom - total_buffer, 0)
    separations[3] = max(source_right - total_buffer, 0)

    hor_pref = [WEST if source_left >= source_right else EAST, 0]
    vert_pref = [NORTH if source_top >= source_bottom else SOUTH, 0]
    hor_pref[1] = _reverse(hor_pref[0])
    vert_pref[1] = _reverse(vert_pref[0])
    preferred_horiz = source_left if source_left >= source_right else source_right
    preferred_vert = source_top if source_top >= source_bottom else source_bottom
    ordering = [[0, 0], [0, 0]]
    order_set = False
    for i in range(2):
        if direction[i] != 0:
            continue
        if (hor_pref[i] & port_constraint[i]) == 0:
            hor_pref[i] = _reverse(hor_pref[i])
        if (vert_pref[i] & port_constraint[i]) == 0:
            vert_pref[i] = _reverse(vert_pref[i])
        ordering[i][0] = vert_pref[i]
        ordering[i][1] = hor_pref[i]
    if preferred_vert > 0 and preferred_horiz > 0:
        if (hor_pref[0] & port_constraint[0]) > 0 and (vert_pref[1] & port_constraint[1]) > 0:
            ordering = [[hor_pref[0], vert_pref[0]], [vert_pref[1], hor_pref[1]]]
            order_set = True
        elif (vert_pref[0] & port_constraint[0]) > 0 and (hor_pref[1] & port_constraint[1]) > 0:
            ordering = [[vert_pref[0], hor_pref[0]], [hor_pref[1], vert_pref[1]]]
            order_set = True
    if preferred_vert > 0 and not order_set:
        ordering = [[vert_pref[0], hor_pref[0]], [vert_pref[1], hor_pref[1]]]
        order_set = True
    if preferred_horiz > 0 and not order_set:
        ordering = [[hor_pref[0], vert_pref[0]], [hor_pref[1], vert_pref[1]]]
        order_set = True

    for i in range(2):
        if direction[i] != 0:
            continue
        if (ordering[i][0] & port_constraint[i]) == 0:
            ordering[i][0] = ordering[i][1]
        pref = ordering[i][0] & port_constraint[i]
        pref |= (ordering[i][1] & port_constraint[i]) << 8
        pref |= (ordering[1 - i][i] & port_constraint[i]) << 16
        pref |= (ordering[1 - i][1 - i] & port_constraint[i]) << 24
        if (pref & 0xF) == 0:
            pref = pref << 8
        if (pref & 0xF00) == 0:
            pref = (pref & 0xF) | pref >> 8
        if (pref & 0xF0000) == 0:
            pref = (pref & 0xFFFF) | ((pref & 0xF000000) >> 8)
        direction[i] = pref & 0xF
        if port_constraint[i] in (WEST, NORTH, EAST, SOUTH):
            direction[i] = port_constraint[i]

    source_index = 3 if direction[0] == EAST else direction[0]
    target_index = 3 if direction[1] == EAST else direction[1]
    source_index -= quad
    target_index -= quad
    if source_index < 1:
        source_index += 4
    if target_index < 1:
        target_index += 4
    pattern = _ROUTE_PATTERNS[source_index - 1][target_index - 1]

    way = [[0.0, 0.0] for _ in range(12)]
    way[0][0], way[0][1] = geo[0][0], geo[0][1]
    if direction[0] == WEST:
        way[0][0] -= source_buffer
        way[0][1] += constraint[0][1] * geo[0][3]
    elif direction[0] == SOUTH:
        way[0][0] += constraint[0][0] * geo[0][2]
        way[0][1] += geo[0][3] + source_buffer
    elif direction[0] == EAST:
        way[0][0] += geo[0][2] + source_buffer
        way[0][1] += constraint[0][1] * geo[0][3]
    elif direction[0] == NORTH:
        way[0][0] += constraint[0][0] * geo[0][2]
        way[0][1] -= source_buffer

    current = 0
    last_orientation = 0 if (direction[0] & (EAST | WEST)) > 0 else 1
    initial_orientation = last_orientation
    for step in pattern:
        next_direction = step & 0xF
        direction_index = 3 if next_direction == EAST else next_direction
        direction_index += quad
        if direction_index > 4:
            direction_index -= 4
        vector = _DIR_VECTORS[direction_index - 1]
        orientation = 0 if direction_index % 2 > 0 else 1
        if orientation != last_orientation:
            current += 1
            way[current][0] = way[current - 1][0]
            way[current][1] = way[current - 1][1]
        tar = (step & _TARGET_MASK) > 0
        sou = (step & _SOURCE_MASK) > 0
        side = (step & _SIDE_MASK) >> 5
        side = side << quad
        if side > 0xF:
            side = side >> 4
        center = (step & _CENTER_MASK) > 0
        if (sou or tar) and side < 9:
            sou_tar = 0 if sou else 1
            if center and orientation == 0:
                limit = geo[sou_tar][0] + constraint[sou_tar][0] * geo[sou_tar][2]
            elif center:
                limit = geo[sou_tar][1] + constraint[sou_tar][1] * geo[sou_tar][3]
            else:
                limit = limits[sou_tar][side]
            if orientation == 0:
                delta = (limit - way[current][0]) * vector[0]
                if delta > 0:
                    way[current][0] += vector[0] * delta
            else:
                delta = (limit - way[current][1]) * vector[1]
                if delta > 0:
                    way[current][1] += vector[1] * delta
        elif center:
            way[current][0] += vector[0] * abs(separations[direction_index] / 2)
            way[current][1] += vector[1] * abs(separations[direction_index] / 2)
        if current > 0 and way[current][orientation] == way[current - 1][orientation]:
            current -= 1
        else:
            last_orientation = orientation

    for i in range(current + 1):
        if i == current:
            target_orientation = 0 if (direction[1] & (EAST | WEST)) > 0 else 1
            same = 0 if target_orientation == initial_orientation else 1
            if same != (current + 1) % 2:
                break
        result.append([_round1(way[i][0]), _round1(way[i][1])])
    # Повторы соседних точек убираются — вместе с началом, если первый излом
    # совпал с ним.
    index = 1
    while index < len(result):
        a, b = result[index - 1], result[index]
        if a is None or b is None or a[0] != b[0] or a[1] != b[1]:
            index += 1
        else:
            del result[index]


def orthogonal_route(
    style: Optional[str],
    source: Box,
    target: Box,
    source_kind: str,
    target_kind: str,
    hints: Sequence[Point] = (),
) -> List[Point]:
    """Полная ломаная связи — начало, изломы, конец — как её рисует draw.io.

    ``mxGraphView.updateEdgeState``: сначала закреплённые концы
    (``exitX``/``exitY``, ``entryX``/``entryY``), затем изломы стиля, затем
    свободные концы на контуре фигуры — сперва у цели, потом у источника.
    """
    s = style_dict(style)

    def fixed(box: Box, kind: str, prefix: str) -> Optional[Point]:
        fx, fy = s.get(prefix + 'X'), s.get(prefix + 'Y')
        if fx is None or fy is None:
            return None
        try:
            return connection_point(box, kind, float(fx), float(fy),
                                    float(s.get(prefix + 'Dx', 0) or 0), float(s.get(prefix + 'Dy', 0) or 0),
                                    s.get(prefix + 'Perimeter', '1') != '0')
        except ValueError:
            return None

    p0 = fixed(source, source_kind, 'exit')
    pe = fixed(target, target_kind, 'entry')
    result: list = [None if p0 is None else [p0[0], p0[1]]]
    orth_connector([p0, pe], source, target, list(hints), s, result)
    points: List[Optional[Point]] = [None if p is None else (p[0], p[1]) for p in result] + [pe]
    if pe is None:
        nxt = points[-2] if len(points) >= 2 and points[-2] is not None else (
            source[0] + source[2] / 2, source[1] + source[3] / 2)
        pe = perimeter_point(target, target_kind, nxt, True)
        points[-1] = pe
    if p0 is None:
        nxt = points[1] if points[1] is not None else (target[0] + target[2] / 2, target[1] + target[3] / 2)
        p0 = perimeter_point(source, source_kind, nxt, True)
        points[0] = p0
    return [p for p in points if p is not None]
