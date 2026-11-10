"""Ортогональная трассировка связей — как рисует draw.io.

draw.io по умолчанию ведёт связи стилем ``edgeStyle=orthogonalEdgeStyle``:
линия выходит из фигуры перпендикулярно её грани и идёт только по осям.
В самом файле при этом хранятся лишь те изломы, которые аналитик подвинул
руками, — всё остальное редактор достраивает при отрисовке.

Если выгружать в BPMN только «точка выхода → точка входа», схема в bpmn.io
превращается в паутину диагоналей. Этот модуль восстанавливает ту ломаную,
которую показывает draw.io, и используется обоими экспортёрами.
"""
from __future__ import annotations

import heapq
import math
from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import GATEWAY_NODE_TYPES, Geometry, ProcessEdge, ProcessNode
from app.services.drawio_orthogonal import perimeter_kind

Point = Tuple[float, float]
Direction = Tuple[int, int]
#: Причалы связи: доли ``exitX, exitY, entryX, entryY`` по граням её концов.
Anchors = Tuple[float, float, float, float]

#: Длина перпендикулярного «уса» от грани фигуры, px. draw.io использует 20.
STUB = 20.0
_EPS = 0.5


def _origin(node: ProcessNode, placed: Optional[Dict[str, Tuple[int, int]]]) -> Tuple[float, float]:
    if placed and node.id in placed:
        x, y = placed[node.id]
        return float(x), float(y)
    return float(node.geometry.x), float(node.geometry.y)


def anchor_point(
    node: ProcessNode,
    frac_x: float,
    frac_y: float,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> Point:
    ox, oy = _origin(node, placed)
    return (ox + node.geometry.width * frac_x, oy + node.geometry.height * frac_y)


def _center(node: ProcessNode, placed: Optional[Dict[str, Tuple[int, int]]]) -> Point:
    return anchor_point(node, 0.5, 0.5, placed)


#: События — кружки: контур у них эллипс, как у ``ellipsePerimeter`` в draw.io.
_EVENT_TYPES = ('startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent')


def outline_kind(node: ProcessNode) -> str:
    """Контур фигуры: ``rectangle``, ``rhombus`` (шлюз) или ``ellipse`` (событие).

    По стилю draw.io (``perimeter=rhombusPerimeter``), а у фигуры без стиля —
    по типу: шлюз, нарисованный на холсте платформы, тоже ромб.
    """
    kind = perimeter_kind(node.style)
    if kind != 'rectangle':
        return kind
    if node.type in GATEWAY_NODE_TYPES:
        return 'rhombus'
    if node.type in _EVENT_TYPES:
        return 'ellipse'
    return 'rectangle'


def _onto_outline(end: Point, before: Point, node: ProcessNode,
                  placed: Optional[Dict[str, Tuple[int, int]]]) -> Point:
    """Конец связи — на настоящий контур ромба или круга, вдоль последнего отрезка.

    Трассировка ставит конец на рамку фигуры. Для шага это и есть контур, а у
    шлюза рамка — пустой угол вокруг ромба: линия, вошедшая в рамку не в
    вершине, обрывается в воздухе, и стрелка выглядит не подключённой к шлюзу.
    Бывает и наоборот: изломы из draw.io приводят конец на пару пикселей
    внутрь кружка события, и стрелка прячется под контуром.
    draw.io в таком случае продлевает отрезок до контура (``rhombusPerimeter``
    и ``ellipsePerimeter`` с ортогональной подводкой) — так и делаем: конец
    едет по оси последнего отрезка, линия остаётся прямой.
    """
    kind = outline_kind(node)
    if kind == 'rectangle':
        return end
    ox, oy = _origin(node, placed)
    hw, hh = node.geometry.width / 2, node.geometry.height / 2
    cx, cy = ox + hw, oy + hh
    if hw <= 0 or hh <= 0:
        return end
    horizontal = abs(end[1] - before[1]) < _EPS and abs(end[0] - before[0]) >= _EPS
    vertical = abs(end[0] - before[0]) < _EPS and abs(end[1] - before[1]) >= _EPS
    if not (horizontal or vertical):
        return end
    # Смещение поперёк отрезка и полуоси: вдоль какой оси едем и какая её мерит.
    off, half_across, half_along = (end[1] - cy, hh, hw) if horizontal else (end[0] - cx, hw, hh)
    ratio = abs(off) / half_across
    if ratio > 1 + 1e-9:
        return end  # отрезок проходит мимо контура — трогать нечего
    # Отрезок по самому краю рамки касается ромба в вершине — туда конец и ведём.
    reach = half_along * ((1 - ratio) if kind == 'rhombus' else math.sqrt(1 - ratio * ratio))
    # И внутрь (конец в пустом углу рамки), и наружу (конец зашёл под контур
    # кружка на пару пикселей): draw.io ставит его ровно на контур.
    if horizontal:
        return (float(round(cx - reach if before[0] < cx else cx + reach)), end[1])
    return (end[0], float(round(cy - reach if before[1] < cy else cy + reach)))


def _ends_on_outline(route: List[Point], src: ProcessNode, tgt: ProcessNode,
                     placed: Optional[Dict[str, Tuple[int, int]]]) -> List[Point]:
    """Оба конца ломаной — на контур своей фигуры (см. ``_onto_outline``)."""
    if len(route) < 2:
        return route
    out = list(route)
    out[0] = _onto_outline(out[0], out[1], src, placed)
    out[-1] = _onto_outline(out[-1], out[-2], tgt, placed)
    return out


#: Последний отрезок короче этого и поперёк предыдущего — ступенька, а не поворот.
END_JOG = 4.0


def _no_end_jog(route: List[Point]) -> List[Point]:
    """Ступенька в пару пикселей у конца — на середину предыдущего отрезка, как в draw.io.

    Связь из шага, чей правый край на высоте 1601, в вершину шлюза на 1600
    кончалась вертикальным хвостиком в пиксель: наконечник ложится по
    последнему отрезку, и стрелка у шлюза смотрела вверх. По всем картам банка
    так кончалась 161 связь. draw.io ту же разницу берёт посередине прямого
    участка, и в фигуру линия входит в своём направлении.
    """
    if len(route) < 3:
        return route
    a, b, c = route[-3], route[-2], route[-1]
    jog = abs(c[0] - b[0]) + abs(c[1] - b[1])
    if not _EPS <= jog < END_JOG:
        return route
    if abs(a[1] - b[1]) < _EPS and abs(b[0] - c[0]) < _EPS:
        mid = float(round((a[0] + b[0]) / 2))
        moved = [*route[:-2], (mid, a[1]), (mid, c[1]), c]
    elif abs(a[0] - b[0]) < _EPS and abs(b[1] - c[1]) < _EPS:
        mid = float(round((a[1] + b[1]) / 2))
        moved = [*route[:-2], (a[0], mid), (c[0], mid), c]
    else:
        return route
    # Предыдущий отрезок в пиксель даёт середину в его конце — без повторов.
    return [pt for i, pt in enumerate(moved) if i == 0 or pt != moved[i - 1]]


def _dominant_axis(src: ProcessNode, tgt: ProcessNode, placed) -> Direction:
    """Направление от источника к цели по преобладающей оси."""
    sx, sy = _center(src, placed)
    tx, ty = _center(tgt, placed)
    if abs(tx - sx) >= abs(ty - sy):
        return (1, 0) if tx >= sx else (-1, 0)
    return (0, 1) if ty >= sy else (0, -1)


def _side_direction(frac_x: Optional[float], frac_y: Optional[float]) -> Optional[Direction]:
    """Внешняя нормаль грани, на которой сидит якорь draw.io."""
    if frac_x is not None:
        if frac_x <= 0.0:
            return (-1, 0)
        if frac_x >= 1.0:
            return (1, 0)
    if frac_y is not None:
        if frac_y <= 0.0:
            return (0, -1)
        if frac_y >= 1.0:
            return (0, 1)
    return None


def exit_direction(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed=None,
                   frac: Optional[Tuple[Optional[float], Optional[float]]] = None) -> Direction:
    """``frac`` подменяет якорь из стиля draw.io — им пользуется разведение
    причалов, когда двигает конец связи вдоль грани."""
    fx, fy = frac if frac is not None else (edge.exitX, edge.exitY)
    side = _side_direction(fx, fy)
    if side:
        return side
    if edge.points:
        # Первый заданный аналитиком излом задаёт ось выхода.
        sx, sy = _center(src, placed)
        px, py = float(edge.points[0].x), float(edge.points[0].y)
        if abs(px - sx) >= abs(py - sy):
            return (1, 0) if px >= sx else (-1, 0)
        return (0, 1) if py >= sy else (0, -1)
    return _dominant_axis(src, tgt, placed)


def entry_direction(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed=None,
                    frac: Optional[Tuple[Optional[float], Optional[float]]] = None) -> Direction:
    """Направление ДВИЖЕНИЯ линии в момент входа в целевую фигуру."""
    fx, fy = frac if frac is not None else (edge.entryX, edge.entryY)
    side = _side_direction(fx, fy)
    if side:
        return (-side[0], -side[1])
    if edge.points:
        tx, ty = _center(tgt, placed)
        px, py = float(edge.points[-1].x), float(edge.points[-1].y)
        if abs(tx - px) >= abs(ty - py):
            return (1, 0) if tx >= px else (-1, 0)
        return (0, 1) if ty >= py else (0, -1)
    return _dominant_axis(src, tgt, placed)


def _simplify(points: Sequence[Point]) -> List[Point]:
    """Убирает дубли и точки, лежащие внутри прямого отрезка."""
    out: List[Point] = []
    for pt in points:
        if out and abs(out[-1][0] - pt[0]) < _EPS and abs(out[-1][1] - pt[1]) < _EPS:
            continue
        out.append(pt)
    cleaned: List[Point] = []
    for i, pt in enumerate(out):
        if 0 < i < len(out) - 1:
            prev, nxt = out[i - 1], out[i + 1]
            # Точка лишняя, только если лежит ВНУТРИ прямого отрезка. Разворот
            # (сосед по одну сторону и по другую совпадают) внутренней точкой
            # не является: это вершина обхода, и убрать её значит стереть
            # излом, который аналитик нарисовал специально. На картах банка так
            # терялась почти треть его изломов.
            same_x = abs(prev[0] - pt[0]) < _EPS and abs(pt[0] - nxt[0]) < _EPS
            same_y = abs(prev[1] - pt[1]) < _EPS and abs(pt[1] - nxt[1]) < _EPS
            between_y = min(prev[1], nxt[1]) - _EPS <= pt[1] <= max(prev[1], nxt[1]) + _EPS
            between_x = min(prev[0], nxt[0]) - _EPS <= pt[0] <= max(prev[0], nxt[0]) + _EPS
            if (same_x and between_y) or (same_y and between_x):
                continue
        cleaned.append(pt)
    return cleaned


#: Короче этого отрезок в ломаной не оставляем: студия рисует связи типа
#: ``step``, и отрезок в два-три пикселя виден не как поворот, а как дефект
#: отрисовки.
#:
#: Порог выбран по выгрузке самой студии (``tests/fixtures/sap.pmm``): там
#: самый короткий настоящий отрезок — 6 px, а преобладают 16–23 px. Берём 8 —
#: на ступень строже эталона, но того же порядка: правило должно убирать
#: зазубрины, а не перекладывать ломаную, которую аналитик нарисовал сам.
MIN_SEGMENT = 8.0


def _faces_overlap(
    src: ProcessNode,
    tgt: ProcessNode,
    horizontal: bool,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[float]:
    """Середина общего участка встречных граней или ``None``, если его нет.

    Две фигуры, стоящие друг напротив друга, соединяются одной прямой, если
    их грани перекрываются: точку входа и выхода достаточно поставить на
    середину перекрытия. Ровно это и делает аналитик, когда тянет линию от
    шага к шагу, — и ровно это ломалось после приведения фигур к канону
    студии: шлюз стал на 10 px больше, его центр разошёлся с центром соседней
    задачи, и на прямой линии появилась лесенка в пять пикселей.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    if horizontal:
        lo = max(sy, ty)
        hi = min(sy + src.geometry.height, ty + tgt.geometry.height)
    else:
        lo = max(sx, tx)
        hi = min(sx + src.geometry.width, tx + tgt.geometry.width)
    # Впритык грани не считаем: линия прошла бы по самому краю фигуры.
    return (lo + hi) / 2 if hi - lo >= MIN_SEGMENT else None


def _straighten(
    start: Point,
    end: Point,
    d0: Direction,
    d1: Direction,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Tuple[Point, Point]:
    """Ставит оба конца на одну ось, если фигуры стоят друг напротив друга."""
    if d0 != d1:
        return start, end
    horizontal = d0[0] != 0
    shared = _faces_overlap(src, tgt, horizontal, placed)
    if shared is None:
        return start, end
    if horizontal:
        return (start[0], shared), (end[0], shared)
    return (shared, start[1]), (shared, end[1])


def _face_span(
    node: Optional[ProcessNode],
    axis: int,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[Tuple[float, float]]:
    """Отрезок, в пределах которого конец связи может скользить по грани."""
    if node is None:
        return None
    ox, oy = _origin(node, placed)
    if axis == 0:
        return (ox, ox + node.geometry.width)
    return (oy, oy + node.geometry.height)


def _is_orthogonal(points: Sequence[Point]) -> bool:
    """Каждый отрезок ломаной идёт строго по оси."""
    return all(
        abs(b[0] - a[0]) < _EPS or abs(b[1] - a[1]) < _EPS
        for a, b in zip(points, points[1:])
    )


def _shift(pts: List[Point], indices: Tuple[int, int], axis: int, value: float) -> bool:
    """Двигает пару точек на общую линию, если это не сделает ломаную косой.

    Проверка обязательна: связи в студии имеют тип ``step``, и косой отрезок в
    них выглядит поломкой. Пока сдвиг применялся без проверки, снятие ступенек
    у ломаной с разворотом рождало два десятка диагоналей на двенадцати картах.
    """
    before = [pts[k] for k in indices]
    for k in indices:
        point = list(pts[k])
        point[axis] = value
        pts[k] = (point[0], point[1])
    if _is_orthogonal(pts):
        return True
    for k, point in zip(indices, before):
        pts[k] = point
    return False


def _collapse_collinear_runs(points: Sequence[Point]) -> List[Point]:
    """Оставляет от прямого участка ломаной только его концы.

    Трассировка складывает маршрут из уса, обхода и причала, и на общей прямой
    их концы иногда идут вразнобой: линия уходит на двадцать пикселей вправо,
    возвращается на шестнадцать и снова идёт вправо. Ход туда-обратно по ОДНОЙ
    прямой ничего не рисует — отрезок ложится на уже проведённый, — но у
    пунктира сбивается фаза, и вместо ровной цепочки точек выходит рваная
    полоса. На коротких связях «листочек ↔ шаг» из неё видно два-три штриха,
    а в draw.io там ровная пунктирная линия. По 22 картам банка так испорчены
    1358 связей из 5239.

    Убирать безопасно: концы участка лежат внутри уже нарисованного отрезка,
    поэтому новая линия — подмножество прежней и задеть чужую фигуру не может.
    Настоящий обход («вверх, вбок, вниз») под правило не попадает: его вершины
    на одной прямой не лежат.
    """
    pts = [(float(x), float(y)) for x, y in points]
    # Проход повторяем до неподвижной точки: схлопнув один участок, ``_simplify``
    # смыкает соседние отрезки, и на общей прямой оказывается уже следующая
    # пара — за один проход такую цепочку не разобрать.
    for _ in range(len(pts)):
        if len(pts) < 3:
            break
        out: List[Point] = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = i + 1
            horizontal = abs(pts[j][1] - pts[i][1]) < _EPS
            vertical = abs(pts[j][0] - pts[i][0]) < _EPS
            while j + 1 < len(pts) and (
                (horizontal and abs(pts[j + 1][1] - pts[i][1]) < _EPS)
                or (vertical and abs(pts[j + 1][0] - pts[i][0]) < _EPS)
            ):
                j += 1
            out.append(pts[j])
            i = j
        out = _simplify(out)
        # Чистка не имеет права уничтожить связь: у ломаной, оба конца которой
        # свелись в одну точку, отдавать нечего, и выгрузка подставила бы
        # заглушку в углу карты вместо линии.
        if len(out) < 2 or out == pts:
            break
        pts = out
    return pts


def _drop_micro_jogs(
    points: Sequence[Point],
    src: Optional[ProcessNode] = None,
    tgt: Optional[ProcessNode] = None,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[Point]:
    """Убирает ступеньки короче ``MIN_SEGMENT``, не ломая ортогональность.

    Ступенька — это короткий отрезок между двумя параллельными, разошедшимися
    на пару пикселей. Убрать её, подвинув саму ступеньку, нельзя: соседний
    отрезок станет косым. Сдвигать надо соседний отрезок целиком — на линию
    второго.

    Двигаем сначала тот из соседей, что не упирается в фигуру. Если упираются
    оба (короткая ломаная в четыре точки), конец сдвигаем ВДОЛЬ грани, на
    которой он стоит, и только в её пределах: точка примыкания при этом
    остаётся на фигуре, а линия выпрямляется. Уйти с грани нельзя — связь
    отошла бы от фигуры, а это худший из дефектов отрисовки.
    """
    pts: List[Point] = [(float(x), float(y)) for x, y in points]
    for _ in range(len(pts)):
        jog = None
        for i in range(1, len(pts) - 2):
            a, b = pts[i], pts[i + 1]
            length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            if _EPS <= length < MIN_SEGMENT:
                jog = i
                break
        if jog is None:
            break

        i = jog
        a, b = pts[i], pts[i + 1]
        horizontal = abs(b[0] - a[0]) > abs(b[1] - a[1])
        axis = 0 if horizontal else 1

        if i - 1 > 0 and _shift(pts, (i - 1, i), axis, b[axis]):
            pass                           # сдвинули левый отрезок на линию b
        elif i + 2 < len(pts) - 1 and _shift(pts, (i + 1, i + 2), axis, a[axis]):
            pass                           # или правый — на линию a
        else:
            # Оба соседа упираются в фигуры: скользим концом вдоль грани.
            moved = False
            for indices, node, value in (
                ((0, 1), src, b[axis]),
                ((len(pts) - 2, len(pts) - 1), tgt, a[axis]),
            ):
                span = _face_span(node, axis, placed)
                if span is None or not (span[0] - _EPS <= value <= span[1] + _EPS):
                    continue
                if indices[0] > i or indices[1] < i + 1:
                    for k in indices:
                        pt = list(pts[k])
                        pt[axis] = value
                        pts[k] = (pt[0], pt[1])
                    moved = True
                    break
            if not moved:
                break

        pts = _simplify(pts)
    return pts


# ── Обход фигур ─────────────────────────────────────────────────────────────

#: Насколько линия ОБХОДИТ чужую фигуру: на столько отступают линии сетки от
#: её краёв. Это запас на аккуратность, а не допуск.
_CLEARANCE = 10.0

#: Насколько глубоко линия может зайти в фигуру, прежде чем это считается
#: пересечением, px.
#:
#: Разделять这 два числа обязательно, и раньше они были одним. Проверка сжимала
#: рамку на весь ``_CLEARANCE``, то есть закрывала глаза на линию, врезавшуюся
#: в фигуру на девять пикселей: у значка длительности в 48 px «неприкосновенной»
#: оставалась середина 28×28, у шлюза в 60 px — 40×40. Трассировщик считал такие
#: маршруты чистыми и не искал обход, а на карте линия шла прямо по фигуре.
#: Здесь нужен допуск на округление, и только он.
_PIERCE_TOLERANCE = 1.0

#: Сторона ячейки сетки, по которой ищутся препятствия рядом с отрезком.
_GRID = 256.0

#: На сколько сжимается СОБСТВЕННАЯ фигура связи, прежде чем стать
#: препятствием: причал стоит на грани, и полоса вдоль неё должна остаться
#: линии доступной. Шире «уса» брать нельзя — короткая связь между соседними
#: фигурами перестала бы находиться вовсе.
_SELF_TOLERANCE = 6.0


class Obstacles:
    """Фигуры, которые линия обязана обойти, с поиском по сетке.

    Проверять каждый отрезок против всех фигур карты нельзя: на карте банка их
    девятьсот, связей семьсот, и перебор становится квадратичным. Фигуры
    раскладываются по сетке с крупной ячейкой, и отрезок сверяется только с
    теми, что попали в его ячейки.
    """

    __slots__ = ('_cells', '_boxes', '_owner', '_own')

    def __init__(self, boxes: Sequence[Tuple[str, Tuple[float, float, float, float]]],
                 owners: Optional[Dict[str, str]] = None):
        self._boxes: Dict[str, Tuple[float, float, float, float]] = dict(boxes)
        #: Ключ рамки -> фигура, которой она принадлежит. Значок длительности
        #: принадлежит своему шагу: связь, идущая В этот шаг, проходит рядом со
        #: значком по праву, и считать это пересечением нельзя.
        self._owner: Dict[str, str] = dict(owners or {})
        #: Собственные фигуры трассируемой связи — см. ``with_own``.
        self._own: Tuple[Tuple[float, float, float, float], ...] = ()
        self._cells: Dict[Tuple[int, int], List[str]] = {}
        for key, (x, y, w, h) in self._boxes.items():
            for cx in range(int(x // _GRID), int((x + w) // _GRID) + 1):
                for cy in range(int(y // _GRID), int((y + h) // _GRID) + 1):
                    self._cells.setdefault((cx, cy), []).append(key)

    def with_own(self, boxes: Sequence[Tuple[float, float, float, float]]) -> 'Obstacles':
        """Те же препятствия плюс собственные фигуры одной связи.

        Концы связи исключены из проверки: причал стоит на грани, и без этого
        любая линия считалась бы пробившей свои же фигуры. Но исключение было
        сплошным, и трассировщик спокойно вёл линию НАСКВОЗЬ собственный шаг —
        на двенадцати картах банка так шли 68 связей из 1787. Здесь те же
        рамки возвращаются в проверку сжатыми на ``_SELF_TOLERANCE``: у грани
        линии по-прежнему можно всё, а через середину — уже нет.

        Общий индекс карты не копируется: он один на все связи.
        """
        clone = Obstacles.__new__(Obstacles)
        clone._boxes = self._boxes
        clone._owner = self._owner
        clone._cells = self._cells
        clone._own = tuple(boxes)
        return clone

    def _near(self, a: Point, b: Point) -> List[str]:
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        keys: List[str] = []
        seen = set()
        for cx in range(int(lo_x // _GRID), int(hi_x // _GRID) + 1):
            for cy in range(int(lo_y // _GRID), int(hi_y // _GRID) + 1):
                for key in self._cells.get((cx, cy), ()):
                    if key not in seen:
                        seen.add(key)
                        keys.append(key)
        return keys

    def hits(self, route: Sequence[Point], exclude: Sequence[str]) -> int:
        """Сколько раз ломаная входит внутрь чужой или своей же фигуры."""
        skip = set(exclude)
        count = 0
        for a, b in zip(route, route[1:]):
            for key in self._near(a, b):
                if key in skip or self._owner.get(key) in skip:
                    continue
                if _segment_in_box(a, b, self._boxes[key]):
                    count += 1
            for box in self._own:
                if _segment_in_box(a, b, box):
                    count += 1
        return count

    def box(self, key: str) -> Optional[Tuple[float, float, float, float]]:
        return self._boxes.get(key)

    def within(
        self,
        area: Tuple[float, float, float, float],
        exclude: Sequence[str] = (),
    ) -> List[Tuple[float, float, float, float]]:
        """Рамки, попавшие в окрестность, кроме принадлежащих концам связи."""
        ax, ay, aw, ah = area
        skip = set(exclude)
        out: List[Tuple[float, float, float, float]] = []
        for key, (x, y, w, h) in self._boxes.items():
            if key in skip or self._owner.get(key) in skip:
                continue
            if x > ax + aw or x + w < ax or y > ay + ah or y + h < ay:
                continue
            out.append((x, y, w, h))
        for x, y, w, h in self._own:
            if x > ax + aw or x + w < ax or y > ay + ah or y + h < ay:
                continue
            out.append((x, y, w, h))
        return out


def build_obstacles(
    nodes: Sequence[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    skip_types: Sequence[str] = ('lane',),
    extra: Sequence[Tuple[str, str, Tuple[float, float, float, float]]] = (),
) -> Obstacles:
    """Препятствия для трассировки: все фигуры карты, кроме контейнеров.

    Дорожка и пул препятствиями не считаются: связь между дорожками обязана их
    пересекать, это нормальный ход потока. Обходить надо шаги, шлюзы, события
    и артефакты — то, поверх чего линия читается как ошибка.
    """
    skip = set(skip_types)
    boxes = []
    for node in nodes:
        if node.type in skip:
            continue
        x, y = _origin(node, placed)
        boxes.append((node.id, (x, y, float(node.geometry.width), float(node.geometry.height))))
    # ``extra`` — фигуры, которых нет в модели, но которые появятся на карте:
    # значки длительности рисует сам экспортёр. Трассировщик обязан знать о них
    # так же, как о шагах, иначе линия пройдёт сквозь часы. Каждая такая рамка
    # приходит с хозяином — узлом, которому она принадлежит.
    owners: Dict[str, str] = {}
    for key, owner, box in extra:
        boxes.append((key, box))
        owners[key] = owner
    return Obstacles(boxes, owners)


class Corridors:
    """Отрезки, уже занятые проложенными связями.

    Две линии, легшие на одну прямую, на карте выглядят одной: вторая просто
    исчезает под первой, и схема врёт — связи не видно, хотя она есть. Поэтому
    маршруты прокладываются по очереди, и каждый следующий знает, где прошли
    предыдущие: общий коридор ему дороже, и при равных прочих он отходит на
    соседнюю линию сетки.

    Это не запрет: пересечь чужую линию можно и нужно, речь только о ДЛИННОМ
    совпадении. Поэтому штраф считается по длине наложения.
    """

    __slots__ = ('_h', '_v')

    def __init__(self) -> None:
        #: y -> занятые интервалы по x (горизонтальные отрезки) и наоборот.
        self._h: Dict[int, List[Tuple[float, float]]] = {}
        self._v: Dict[int, List[Tuple[float, float]]] = {}

    def occupy(self, route: Sequence[Point]) -> None:
        for a, b in zip(route, route[1:]):
            if abs(a[1] - b[1]) < _EPS:
                self._h.setdefault(int(round(a[1])), []).append(
                    (min(a[0], b[0]), max(a[0], b[0])))
            elif abs(a[0] - b[0]) < _EPS:
                self._v.setdefault(int(round(a[0])), []).append(
                    (min(a[1], b[1]), max(a[1], b[1])))

    def overlap(self, route: Sequence[Point]) -> float:
        """Суммарная длина, на которой ломаная лежит поверх уже проложенных."""
        total = 0.0
        for a, b in zip(route, route[1:]):
            if abs(a[1] - b[1]) < _EPS:
                bucket, lo, hi = self._h.get(int(round(a[1]))), min(a[0], b[0]), max(a[0], b[0])
            elif abs(a[0] - b[0]) < _EPS:
                bucket, lo, hi = self._v.get(int(round(a[0]))), min(a[1], b[1]), max(a[1], b[1])
            else:
                continue
            for start, end in bucket or ():
                total += max(0.0, min(hi, end) - max(lo, start))
        return total


#: Во сколько раз пиксель общего коридора «дороже» пикселя своего пути.
#: Ломаная охотно удлиняется, лишь бы не слиться с соседней, но не любой ценой:
#: крюк вокруг половины карты хуже, чем сто пикселей рядом с чужой линией.
_CORRIDOR_WEIGHT = 3.0


def _segment_in_box(a: Point, b: Point, box: Tuple[float, float, float, float]) -> bool:
    """Отрезок заходит внутрь рамки глубже допуска на округление."""
    x, y, w, h = box
    x += _PIERCE_TOLERANCE
    y += _PIERCE_TOLERANCE
    w -= 2 * _PIERCE_TOLERANCE
    h -= 2 * _PIERCE_TOLERANCE
    if w <= 0 or h <= 0:
        return False
    lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
    lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
    return not (hi_x < x or lo_x > x + w or hi_y < y or lo_y > y + h)


def _faces_straight(start: Point, d0: Direction, end: Point, d1: Direction) -> bool:
    """Концы смотрят навстречу, стоят на одной линии, и цель впереди.

    Ровно этого добивается ``_straighten``: связь между такими концами —
    один отрезок, и так её рисует draw.io.
    """
    if d0 != d1:
        return False
    if d0[0]:
        return abs(start[1] - end[1]) < _EPS and (end[0] - start[0]) * d0[0] > _EPS
    return abs(start[0] - end[0]) < _EPS and (end[1] - start[1]) * d0[1] > _EPS


def _candidate_routes(start: Point, d0: Direction, end: Point, d1: Direction) -> List[List[Point]]:
    """Несколько допустимых ортогональных маршрутов между теми же концами.

    Первый — классический «ус, колено, ус» из draw.io; остальные разводят
    колено в стороны, чтобы обойти то, что стоит на пути. Все начинаются и
    кончаются в тех же точках на гранях: конец связи двигать нельзя.
    """
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)
    horizontal_exit = d0[0] != 0
    horizontal_entry = d1[0] != 0

    routes: List[List[Point]] = []

    def add(middle: List[Point]) -> None:
        routes.append(_simplify([start, a, *middle, b, end]))

    # Заготовка «ус — колено — ус» вырождается, когда зазор между фигурами
    # короче двух усов: линия проскакивает мимо цели на 20 px, возвращается на
    # 30 назад и снова идёт вперёд. При зазоре в полсотни пикселей это ещё
    # незаметно, а пунктир «цилиндр ↔ шаг» — как раз десяток пикселей, и на
    # карте выходил зигзаг нулевой высоты. Дальше за него брался обход
    # препятствий и уводил связь крюком за верхнюю грань шага. Прямой отрезок
    # ставим ПЕРВЫМ: у него ноль изломов и наименьшая длина, поэтому выбор по
    # стоимости берёт его, как только он допустим.
    if _faces_straight(start, d0, end, d1):
        routes.append([start, end])

    # Насколько далеко уводится обходной коридор. Несколько расстояний — чтобы
    # обойти и узкий шаг, и высокую дорожку с фигурами: одного отступа хватает
    # не всегда, а перебирать их дешевле, чем строить видимость по всей карте.
    detours = (40.0, 90.0, 160.0, 260.0)

    if horizontal_exit and horizontal_entry:
        for frac in (0.5, 0.25, 0.75, 0.12, 0.88):
            mid_x = a[0] + (b[0] - a[0]) * frac
            add([(mid_x, a[1]), (mid_x, b[1])])
        # Обход поверху и понизу: колено уводится за пределы обеих фигур.
        for gap in detours:
            add([(a[0], min(a[1], b[1]) - gap), (b[0], min(a[1], b[1]) - gap)])
            add([(a[0], max(a[1], b[1]) + gap), (b[0], max(a[1], b[1]) + gap)])
    elif not horizontal_exit and not horizontal_entry:
        for frac in (0.5, 0.25, 0.75, 0.12, 0.88):
            mid_y = a[1] + (b[1] - a[1]) * frac
            add([(a[0], mid_y), (b[0], mid_y)])
        for gap in detours:
            add([(min(a[0], b[0]) - gap, a[1]), (min(a[0], b[0]) - gap, b[1])])
            add([(max(a[0], b[0]) + gap, a[1]), (max(a[0], b[0]) + gap, b[1])])
    elif horizontal_exit:
        add([(b[0], a[1])])
        add([(a[0], b[1]), (b[0], b[1])])
        # Через промежуточный коридор: сначала вбок, потом по вертикали и снова
        # вбок. Нужен, когда прямое колено упирается в соседнюю фигуру.
        for gap in detours:
            for lane_y in (min(a[1], b[1]) - gap, max(a[1], b[1]) + gap):
                add([(a[0], lane_y), (b[0], lane_y)])
    else:
        add([(a[0], b[1])])
        add([(b[0], a[1]), (b[0], b[1])])
        for gap in detours:
            for lane_x in (min(a[0], b[0]) - gap, max(a[0], b[0]) + gap):
                add([(lane_x, a[1]), (lane_x, b[1])])
    return routes


#: Крюк короче этого считаем допустимым: обход препятствия честно ходит назад.
_UTURN_TOLERANCE = 40.0


def _doubles_back(route: Sequence[Point]) -> bool:
    """Ломаная уходит в одну сторону и заметно возвращается обратно.

    Так выглядит связь, у которой ус направлен прочь от цели: линия делает
    двадцать пикселей вправо, разворачивается и идёт влево через собственную
    фигуру. На карте это читается как поворот на 180°, и аналитик не понимает,
    куда ведёт связь.

    Считаем по каждой оси отдельно: если ход вперёд и ход назад сопоставимы,
    значит линия ходила и вернулась.

    Правило намеренно грубое: под него попадает и честный обход «вверх, вбок,
    вниз», у которого возврат по одной оси компенсирован продвижением по
    другой. Отделять одно от другого пробовали — развороты выросли с 19 до 27,
    а линий сквозь фигуры стало больше: обход, за который никто не штрафует,
    трассировщик начинает выбирать и там, где он не нужен. Цена ошибки здесь
    невелика: пересечение стоит дороже разворота, поэтому обход, которым
    действительно обходят фигуру, правило не отменяет.
    """
    for axis in (0, 1):
        steps = [b[axis] - a[axis] for a, b in zip(route, route[1:])
                 if abs(b[axis] - a[axis]) > _EPS]
        if not steps:
            continue
        forward = sum(s for s in steps if s > 0)
        backward = -sum(s for s in steps if s < 0)
        detour = min(forward, backward)
        if detour > _UTURN_TOLERANCE and detour > abs(forward - backward) * 0.5:
            return True
    return False


def _route_cost(route: Sequence[Point], obstacles: Optional[Obstacles],
                exclude: Sequence[str],
                corridors: Optional[Corridors] = None) -> Tuple[int, int, int, float]:
    """Чем маршрут хуже: пересечения, разворот, изломы, длина — в этом порядке.

    Разворот стоит сразу после пересечений: линия, ушедшая от цели и
    вернувшаяся, читается как ошибка чертежа. Уступает он только обходу — если
    вернуться назад можно лишь так, это лучше, чем пройти сквозь чужой шаг.

    Совпадение с уже проложенными связями входит в последний член: линия,
    легшая поверх соседней, не ошибка модели, но на карте её не видно.
    """
    pierced = obstacles.hits(route, exclude) if obstacles is not None else 0
    length = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:]))
    if corridors is not None:
        length += _CORRIDOR_WEIGHT * corridors.overlap(route)
    return (pierced, int(_doubles_back(route)), max(len(route) - 2, 0), length)


# ── Трассировка в обход препятствий ─────────────────────────────────────────
#
# Перебор готовых форм («колено посередине», «обход поверху на 90 px») закрывает
# простые случаи и упирается в сложные: если между фигурами стоят три чужих
# шага, ни одна заготовка не подойдёт. Здесь строится настоящий маршрут.
#
# ПОЧЕМУ СЕТКА ХАНАНА, А НЕ ПИКСЕЛЬНАЯ. Ортогональный путь оптимальной длины
# всегда можно провести по линиям, проходящим через края препятствий: между
# двумя соседними краями поворачивать негде и незачем. Поэтому сетка строится
# из координат самих фигур — десятки линий вместо тысяч пикселей, и поиск по
# ней идёт за миллисекунды даже на карте в девятьсот фигур.
#
# Сетка локальная: берутся только препятствия рядом с самой связью. Строить её
# по всей карте бессмысленно — путь всё равно не уходит за пределы окрестности,
# а размер сетки растёт квадратично.

#: Насколько окрестность связи шире её собственной рамки, px. Меньше — и обход
#: вокруг крупного шага не поместится в сетку.
_NEIGHBOURHOOD = 320.0

#: Больше этого числа препятствий в окрестности — сетка становится слишком
#: большой, и поиск дороже пользы. Такие связи остаются на заготовках.
_MAX_LOCAL_OBSTACLES = 48

#: Штраф за поворот в стоимости пути. Ломаная из двух колен читается лучше, чем
#: из шести той же длины, поэтому поворот стоит как 60 px пути.
_BEND_PENALTY = 60.0


def _grid_lines(values: Sequence[float], limit: Tuple[float, float]) -> List[float]:
    """Уникальные координаты сетки внутри окрестности, по возрастанию."""
    low, high = limit
    seen = sorted({round(v, 1) for v in values if low - 1 <= v <= high + 1})
    return seen


def _blocked_segments(xs: Sequence[float], ys: Sequence[float],
                      boxes: Sequence[Tuple[float, float, float, float]]) -> Tuple[bytearray, bytearray]:
    """Какие отрезки сетки задевают препятствия — один раз на всю сетку.

    Поиск пути проверял каждый шаг по сетке против всех препятствий
    окрестности, и один и тот же отрезок — десятки раз: на карте в 1600 фигур
    это двадцать миллионов проверок и половина всего времени раскладки. Здесь
    каждое препятствие само отмечает отрезки, которые задевает, — по тому же
    правилу, что и ``_segment_in_box`` (те же допуск и нестрогие границы),
    поэтому найденные пути не меняются.

    ``horizontal[i * len(ys) + j]`` — отрезок от ``(xs[i], ys[j])`` до
    ``(xs[i + 1], ys[j])``; ``vertical[i * (len(ys) - 1) + j]`` — от
    ``(xs[i], ys[j])`` до ``(xs[i], ys[j + 1])``.
    """
    nx, ny = len(xs), len(ys)
    horizontal = bytearray(max(nx - 1, 0) * ny)
    vertical = bytearray(nx * max(ny - 1, 0))
    for x, y, w, h in boxes:
        # Та же арифметика, что в _segment_in_box: граница — x + w после сдвига.
        x += _PIERCE_TOLERANCE
        y += _PIERCE_TOLERANCE
        w -= 2 * _PIERCE_TOLERANCE
        h -= 2 * _PIERCE_TOLERANCE
        if w <= 0 or h <= 0:
            continue
        right, bottom = x + w, y + h
        col_lo, col_hi = bisect_left(xs, x), bisect_right(xs, right)   # x ≤ xs[i] ≤ right
        row_lo, row_hi = bisect_left(ys, y), bisect_right(ys, bottom)  # y ≤ ys[j] ≤ bottom
        # Горизонтальный отрезок задет, если xs[i + 1] ≥ x, xs[i] ≤ right и y ≤ ys[j] ≤ bottom.
        for i in range(max(col_lo - 1, 0), min(col_hi, nx - 1)):
            base = i * ny
            for j in range(row_lo, row_hi):
                horizontal[base + j] = 1
        # Вертикальный — если x ≤ xs[i] ≤ right, ys[j + 1] ≥ y и ys[j] ≤ bottom.
        for i in range(col_lo, col_hi):
            base = i * (ny - 1)
            for j in range(max(row_lo - 1, 0), min(row_hi, ny - 1)):
                vertical[base + j] = 1
    return horizontal, vertical


def _astar_route(
    start: Point,
    d0: Direction,
    end: Point,
    d1: Direction,
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
) -> Optional[List[Point]]:
    """Кратчайший ортогональный путь, не задевающий чужие фигуры.

    Возвращает ``None``, если пути нет или окрестность слишком плотная: тогда
    вызывающий остаётся на заготовке. Пустой результат лучше кривого — связь
    без маршрута студия проведёт сама.
    """
    if obstacles is None:
        return None

    lo_x = min(start[0], end[0]) - _NEIGHBOURHOOD
    hi_x = max(start[0], end[0]) + _NEIGHBOURHOOD
    lo_y = min(start[1], end[1]) - _NEIGHBOURHOOD
    hi_y = max(start[1], end[1]) + _NEIGHBOURHOOD
    boxes = obstacles.within((lo_x, lo_y, hi_x - lo_x, hi_y - lo_y), exclude)
    if not boxes or len(boxes) > _MAX_LOCAL_OBSTACLES:
        return None

    # Ус: первый шаг обязан уйти по своей оси, иначе линия отойдёт от фигуры
    # вбок прямо на её границе.
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)

    xs = [a[0], b[0]]
    ys = [a[1], b[1]]
    for x, y, w, h in boxes:
        xs.extend((x - _CLEARANCE - 1, x + w + _CLEARANCE + 1))
        ys.extend((y - _CLEARANCE - 1, y + h + _CLEARANCE + 1))
    xs = _grid_lines(xs, (lo_x, hi_x))
    ys = _grid_lines(ys, (lo_y, hi_y))
    if not xs or not ys:
        return None
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}
    ax, ay = round(a[0], 1), round(a[1], 1)
    bx, by = round(b[0], 1), round(b[1], 1)
    if ax not in xi or ay not in yi or bx not in xi or by not in yi:
        return None

    start_cell = (xi[ax], yi[ay])
    goal_cell = (xi[bx], yi[by])
    blocked_h, blocked_v = _blocked_segments(xs, ys, boxes)
    rows = len(ys)

    def point(cell: Tuple[int, int]) -> Point:
        return (xs[cell[0]], ys[cell[1]])

    def heuristic(cell: Tuple[int, int]) -> float:
        px, py = point(cell)
        return abs(px - b[0]) + abs(py - b[1])

    # Состояние — клетка плюс ось, по которой в неё пришли: без этого поворот
    # не отличить от продолжения прямой, и штрафовать его нечем.
    open_heap: List[Tuple[float, float, Tuple[int, int], int]] = [
        (heuristic(start_cell), 0.0, start_cell, 0 if d0[0] else 1)
    ]
    best: Dict[Tuple[Tuple[int, int], int], float] = {(start_cell, 0 if d0[0] else 1): 0.0}
    came: Dict[Tuple[Tuple[int, int], int], Optional[Tuple[Tuple[int, int], int]]] = {
        (start_cell, 0 if d0[0] else 1): None
    }
    goal_axis = 0 if d1[0] else 1
    found: Optional[Tuple[Tuple[int, int], int]] = None

    while open_heap:
        _f, g, cell, axis = heapq.heappop(open_heap)
        if best.get((cell, axis), math.inf) < g - _EPS:
            continue
        if cell == goal_cell and axis == goal_axis:
            found = (cell, axis)
            break
        cx, cy = cell
        for naxis, ncell in (
            (0, (cx - 1, cy)), (0, (cx + 1, cy)),
            (1, (cx, cy - 1)), (1, (cx, cy + 1)),
        ):
            if not (0 <= ncell[0] < len(xs) and 0 <= ncell[1] < len(ys)):
                continue
            if naxis == 0:
                if blocked_h[min(cx, ncell[0]) * rows + cy]:
                    continue
            elif blocked_v[cx * (rows - 1) + min(cy, ncell[1])]:
                continue
            here, there = point(cell), point(ncell)
            step = abs(there[0] - here[0]) + abs(there[1] - here[1])
            cost = g + step + (_BEND_PENALTY if naxis != axis else 0.0)
            key = (ncell, naxis)
            if cost < best.get(key, math.inf) - _EPS:
                best[key] = cost
                came[key] = (cell, axis)
                heapq.heappush(open_heap, (cost + heuristic(ncell), cost, ncell, naxis))

    if found is None:
        return None

    path: List[Point] = []
    node: Optional[Tuple[Tuple[int, int], int]] = found
    while node is not None:
        path.append(point(node[0]))
        node = came[node]
    path.reverse()
    return _simplify([start, *path, end])


#: Оси, по которым можно попробовать увести связь, если прямой путь перекрыт.
_ALL_DIRECTIONS: Tuple[Direction, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _anchor_for(node: ProcessNode, direction: Direction,
                placed: Optional[Dict[str, Tuple[int, int]]], leaving: bool) -> Point:
    """Точка на грани, из которой линия уходит (или в которую приходит)."""
    if leaving:
        fx, fy = 0.5 + direction[0] * 0.5, 0.5 + direction[1] * 0.5
    else:
        fx, fy = 0.5 - direction[0] * 0.5, 0.5 - direction[1] * 0.5
    return anchor_point(node, fx, fy, placed)


def _vertical_link(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[Direction]:
    """Ось «сверху вниз», если фигуры стоят одна над другой и пересекаются по X.

    ``None`` — когда они разнесены вбок и вертикальная связь была бы кривее
    горизонтальной, а также когда грани СОПРИКАСАЮТСЯ: вертикальной связи там
    не остаётся длины вовсе, оба причала садятся в одну точку, и заготовка
    маршрута складывается в разворот на 180°. У такой пары единственное, что
    ещё описывает видимую линию, — причалы, расставленные аналитиком, и мы
    оставляем их в силе.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    overlap = min(sx + src.geometry.width, tx + tgt.geometry.width) - max(sx, tx)
    if overlap < MIN_SEGMENT:
        return None
    if ty - (sy + src.geometry.height) > _EPS:
        return (0, 1)
    if sy - (ty + tgt.geometry.height) > _EPS:
        return (0, -1)
    return None


def _horizontal_link(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[Direction]:
    """Ось «слева направо», если фигуры стоят бок о бок и пересекаются по Y.

    Зеркало ``_vertical_link`` по второй оси. Цилиндр базы аналитик ставит не
    только НАД шагом, но и СБОКУ от него — тогда в draw.io из боковой грани
    цилиндра в боковую грань шага идёт такая же короткая чёрточка. Общее
    правило выбирает ось по большему смещению центров, и у пары, разнесённой
    вбок на десяток пикселей при полном перекрытии по Y, оно уводило линию
    вверх, за верхнюю грань шага, и возвращало обратно: на карте выходил крюк
    там, где в draw.io прямая.

    Соприкасающиеся грани исключены по той же причине, что и в двойнике:
    связи там не остаётся длины.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    overlap = min(sy + src.geometry.height, ty + tgt.geometry.height) - max(sy, ty)
    if overlap < MIN_SEGMENT:
        return None
    if tx - (sx + src.geometry.width) > _EPS:
        return (1, 0)
    if sx - (tx + tgt.geometry.width) > _EPS:
        return (-1, 0)
    return None



def _stub_reverses(start: Point, d0: Direction, end: Point) -> bool:
    """Ус уходит в сторону, противоположную цели.

    Так бывает, когда в стиле draw.io прописан якорь («выходить вправо»), а
    цель на самом деле слева: линия делает ус на 20 px вправо, разворачивается
    и идёт назад по себе же — прямо через фигуру, из которой только что вышла.
    На карте это выглядит как узел из слипшихся линий, и именно на такие места
    жалуются аналитики.

    Разворот считается настоящим, только если цель ЯВНО позади: сдвиг по
    перпендикуляру не должен перевешивать. Иначе под правило попадёт обычная
    связь к соседу сбоку, у которой ус чуть-чуть заходит за край, и разворотов
    станет больше, а не меньше — проверено замером.
    """
    along = (end[0] - start[0]) * d0[0] + (end[1] - start[1]) * d0[1]
    across = abs((end[0] - start[0]) * d0[1] - (end[1] - start[1]) * d0[0])
    return along < -STUB and abs(along) > across



def _unwind_bent_route(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
    bends: Sequence[Point],
    d0: Direction,
    d1: Direction,
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
    fallback: List[Point],
) -> List[Point]:
    """Перебирает грани выхода и входа, пока ломаная не перестанет разворачиваться.

    Изломы аналитика остаются на месте — меняется только то, из какой грани
    линия выходит и в какую входит. Разворот почти всегда родом отсюда: ус
    уходит прочь от первого излома и тут же возвращается, пересекая фигуру, из
    которой вышел.
    """
    best = fallback
    best_cost = _route_cost(fallback, obstacles, exclude)
    for alt0 in _ALL_DIRECTIONS:
        for alt1 in _ALL_DIRECTIONS:
            start = _anchor_for(src, alt0, placed, leaving=True)
            end = _anchor_for(tgt, alt1, placed, leaving=False)
            route = _route_through_bends([start, *bends, end], alt0, alt1)
            cost = _route_cost(route, obstacles, exclude)
            if cost < best_cost:
                best, best_cost = route, cost
            if best_cost[:2] == (0, 0):
                return best
    return best


def _reroute_through_bends(
    start: Point,
    d0: Direction,
    bends: Sequence[Point],
    end: Point,
    d1: Direction,
    obstacles: Obstacles,
    exclude: Sequence[str],
    fallback: List[Point],
) -> List[Point]:
    """Маршрут по изломам аналитика, но в обход чужих фигур.

    Сначала пробуем сохранить его рисунок: каждый участок между соседними
    изломами прокладывается заново с обходом, а сами изломы остаются на месте.
    Если и так линия кого-то задевает — строим путь целиком заново, уже без
    изломов. Хуже потерять форму, чем оставить линию поперёк чужого шага.
    """
    stitched: List[Point] = [start]
    ok = True
    waypoints = [start, *bends, end]
    for index, (a, b) in enumerate(zip(waypoints, waypoints[1:])):
        first = index == 0
        last = index == len(waypoints) - 2
        leg_d0 = d0 if first else _axis_towards(a, b)
        leg_d1 = d1 if last else _axis_towards(a, b)
        leg = _astar_route(a, leg_d0, b, leg_d1, obstacles, exclude)
        if leg is None:
            # Прямой отрезок между изломами почти всегда косой, а связи в
            # студии имеют тип step: возвращаем колено, а не диагональ.
            elbow = (b[0], a[1]) if leg_d0[0] else (a[0], b[1])
            leg = _simplify([a, elbow, b])
        if _route_cost(leg, obstacles, exclude)[0]:
            ok = False
            break
        stitched.extend(leg[1:])
    if ok:
        cleaned = _simplify(stitched)
        if _route_cost(cleaned, obstacles, exclude)[0] == 0:
            return cleaned

    whole = _astar_route(start, d0, end, d1, obstacles, exclude)
    if whole is not None and _route_cost(whole, obstacles, exclude)[0] == 0:
        return whole
    return fallback


def _axis_towards(a: Point, b: Point) -> Direction:
    """Ось, по которой естественно идти от одной точки к другой."""
    if abs(b[0] - a[0]) >= abs(b[1] - a[1]):
        return (1, 0) if b[0] >= a[0] else (-1, 0)
    return (0, 1) if b[1] >= a[1] else (0, -1)


def _best_route(
    start: Point,
    d0: Direction,
    end: Point,
    d1: Direction,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
    corridors: Optional[Corridors] = None,
) -> List[Point]:
    """Лучший маршрут: сначала по естественным граням, потом — в обход.

    Порядок важен. Сперва пробуем ту пару граней, которую диктует взаимное
    положение фигур: именно так связь нарисована в draw.io, и менять грань без
    нужды значит перерисовывать схему за аналитика. И только если по ней линия
    всё равно проходит сквозь чужую фигуру, перебираем остальные грани — уйти с
    правой грани на нижнюю лучше, чем прошить соседний шаг насквозь.
    """
    best = min(_candidate_routes(start, d0, end, d1),
               key=lambda r: _route_cost(r, obstacles, exclude, corridors))
    if obstacles is None:
        return best
    if _route_cost(best, obstacles, exclude)[:2] == (0, 0):
        return best

    # Заготовки не подошли — ищем путь по-настоящему.
    found = _astar_route(start, d0, end, d1, obstacles, exclude)
    if found is not None and _route_cost(found, obstacles, exclude)[0] == 0:
        return found

    best_cost = _route_cost(best, obstacles, exclude, corridors)
    for alt0 in _ALL_DIRECTIONS:
        for alt1 in _ALL_DIRECTIONS:
            if (alt0, alt1) == (d0, d1):
                continue
            alt_start = _anchor_for(src, alt0, placed, leaving=True)
            alt_end = _anchor_for(tgt, alt1, placed, leaving=False)
            for route in _candidate_routes(alt_start, alt0, alt_end, alt1):
                cost = _route_cost(route, obstacles, exclude, corridors)
                # Смена грани — уступка, и берём её только за меньшее число
                # пересечений, а не за пару пикселей длины.
                if cost[0] < best_cost[0] or (cost[0] == best_cost[0] and cost < best_cost):
                    best, best_cost = route, cost
        if best_cost[0] == 0:
            break
    return best


def _route_without_bends(start: Point, d0: Direction, end: Point, d1: Direction) -> List[Point]:
    """Классическая ломаная draw.io: ус — колено — ус."""
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)
    horizontal_exit = d0[0] != 0
    horizontal_entry = d1[0] != 0

    middle: List[Point] = []
    if horizontal_exit and horizontal_entry:
        mid_x = (a[0] + b[0]) / 2
        middle = [(mid_x, a[1]), (mid_x, b[1])]
    elif not horizontal_exit and not horizontal_entry:
        mid_y = (a[1] + b[1]) / 2
        middle = [(a[0], mid_y), (b[0], mid_y)]
    elif horizontal_exit:
        middle = [(b[0], a[1])]
    else:
        middle = [(a[0], b[1])]

    return _simplify([start, a, *middle, b, end])


def _route_through_bends(points: Sequence[Point], d0: Direction, d1: Direction) -> List[Point]:
    """Проводит ортогональную ломаную через изломы, заданные в draw.io."""
    out: List[Point] = [points[0]]
    last = len(points) - 1
    for i in range(1, len(points)):
        prev, cur = out[-1], points[i]
        aligned = abs(prev[0] - cur[0]) < _EPS or abs(prev[1] - cur[1]) < _EPS
        if aligned:
            out.append(cur)
            continue
        if i == last:
            # Финальный отрезок должен входить в фигуру по своей оси.
            elbow = (prev[0], cur[1]) if d1[0] != 0 else (cur[0], prev[1])
        elif i == 1:
            elbow = (cur[0], prev[1]) if d0[0] != 0 else (prev[0], cur[1])
        else:
            prev2 = out[-2]
            was_horizontal = abs(prev2[1] - prev[1]) < _EPS
            elbow = (cur[0], prev[1]) if was_horizontal else (prev[0], cur[1])
        out.append(elbow)
        out.append(cur)
    return _simplify(out)


def _snap_to_pixel_grid(points: Sequence[Point]) -> List[Point]:
    """Округляет ломаную к целым и добивает выравнивание по осям.

    Ортогональность считалась в дробных координатах; после округления соседние
    точки могут разойтись на пиксель, и отрезок становится «почти
    горизонтальным» — в bpmn.io это видно как едва заметный скос.
    """
    snapped: List[Point] = [(float(round(x)), float(round(y))) for x, y in points]
    for i in range(1, len(snapped)):
        prev, cur = snapped[i - 1], snapped[i]
        dx, dy = abs(cur[0] - prev[0]), abs(cur[1] - prev[1])
        if dx == 0 or dy == 0:
            continue
        if dy <= 1:
            snapped[i] = (cur[0], prev[1])
        elif dx <= 1:
            snapped[i] = (prev[0], cur[1])
    return _simplify(snapped)


def _own_boxes(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> List[Tuple[float, float, float, float]]:
    """Собственные фигуры связи, сжатые на допуск у грани."""
    out: List[Tuple[float, float, float, float]] = []
    for node in (src, tgt):
        ox, oy = _origin(node, placed)
        w = float(node.geometry.width) - 2 * _SELF_TOLERANCE
        h = float(node.geometry.height) - 2 * _SELF_TOLERANCE
        if w > 0 and h > 0:
            out.append((ox + _SELF_TOLERANCE, oy + _SELF_TOLERANCE, w, h))
    return out


def _push_out(point: Point, node: ProcessNode, side: Direction,
              placed: Optional[Dict[str, Tuple[int, int]]]) -> Point:
    """Выносит излом, оказавшийся ВНУТРИ своей же фигуры, за её грань.

    Изломы аналитик расставлял по исходной геометрии draw.io, а канонизация
    потом раздвигает шаг под его подпись (``layout.fit_labels``): на картах
    банка размер меняют 873 фигуры из 1662. Излом, лежавший в двадцати
    пикселях правее шага, после расширения оказывается внутри него — и линия
    выходит из грани, разворачивается и идёт назад сквозь собственную фигуру.
    На карте это читается как «связь подходит к шагу изнутри», и жалуются
    именно на это.

    Выносим по нормали той грани, на которой стоит причал: аналитик ставил
    первый излом сразу за той стороной, из которой ведёт линию. Отступ — тот
    же «ус» в 20 px, что рисует сам draw.io, поэтому ломаная встаёт туда же,
    где она была до расширения фигуры.
    """
    ox, oy = _origin(node, placed)
    w, h = float(node.geometry.width), float(node.geometry.height)
    x, y = point
    if not (ox < x < ox + w and oy < y < oy + h):
        return point
    if side[0] > 0:
        return (ox + w + STUB, y)
    if side[0] < 0:
        return (ox - STUB, y)
    if side[1] > 0:
        return (x, oy + h + STUB)
    return (x, oy - STUB)


def _has_author_bends(edge: ProcessEdge) -> bool:
    """Точки связи — изломы аналитика, которые надо повторить.

    У ``elbowEdgeStyle`` это не так: mxGraph хранит там КОНТРОЛЬНУЮ точку,
    которая лишь двигает единственное колено вдоль своей оси, а проходить через
    неё линия не обязана. Мы вели ломаную через все точки подряд, и вместо
    одного колена выходила лесенка в четыре отрезка, два из которых в пару
    пикселей. Сплошную линию это ещё не портит, а пунктир «цилиндр ↔ шаг» на
    таких отрезках рассыпается на оторванные чёрточки.

    Ограничиваемся ассоциациями: у потока управления изломы аналитик ставит
    осознанно и обводит ими чужие фигуры — перерисовывать их за него незачем.
    """
    if not edge.points:
        return False
    return not (edge.kind == 'association' and 'elbow' in (edge.edgeStyle or '').lower())


def _bends_of(
    edge: ProcessEdge,
    src: ProcessNode,
    tgt: ProcessNode,
    d0: Direction,
    d1: Direction,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> List[Point]:
    """Изломы аналитика, вынесенные из собственных фигур связи."""
    entry_side = (-d1[0], -d1[1])
    out: List[Point] = []
    for p in edge.points:
        point = (float(p.x), float(p.y))
        point = _push_out(point, src, d0, placed)
        point = _push_out(point, tgt, entry_side, placed)
        out.append(point)
    return out


def _resolve_ends(
    edge: ProcessEdge,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    anchors: Optional[Anchors] = None,
) -> Tuple[Point, Point, Direction, Direction, bool]:
    """Причалы связи, оси выхода/входа и признак «сведено в общий створ».

    Вынесено из ``orthogonal_waypoints``, потому что считать это надо дважды:
    один раз — чтобы развести причалы, сошедшиеся в одной точке грани
    (``spread_dock_anchors``), второй — чтобы проложить по ним ломаную.
    Разойтись два расчёта не имеют права: развели бы одно, а нарисовали другое.

    ``anchors`` — доли, назначенные разведением. Они уже получены этой же
    функцией, поэтому пересчитывать по ним ни грань, ни створ не нужно: точка
    встанет туда же, только сдвинутая вдоль грани.
    """
    # draw.io допускает якорь чуть за гранью фигуры (entryY=-0.017), и линия
    # там начинается «в воздухе». Импортёр ждёт точку на фигуре, поэтому долю
    # прижимаем к грани.
    def _frac(value: Optional[float]) -> Optional[float]:
        return None if value is None else min(1.0, max(0.0, value))

    given = anchors is not None
    raw = anchors if given else (edge.exitX, edge.exitY, edge.entryX, edge.entryY)
    exit_x, exit_y, entry_x, entry_y = (_frac(v) for v in raw)

    d0 = exit_direction(edge, src, tgt, placed, (exit_x, exit_y))
    d1 = entry_direction(edge, src, tgt, placed, (entry_x, entry_y))

    # Пунктир к базе данных аналитик рисует одинаково: цилиндр стоит НАД шагом
    # (или СБОКУ от него), и связь идёт короткой прямой в ближнюю грань. Общее
    # правило выбирает ось по большему смещению центров, и при малейшем сдвиге
    # по второй оси линия уходила не из той грани и заворачивала — на карте это
    # выглядит петлёй там, где в draw.io прямая чёрточка.
    if not given and edge.kind == 'association' and not _has_author_bends(edge):
        axis = _vertical_link(src, tgt, placed) or _horizontal_link(src, tgt, placed)
        if axis is not None:
            d0 = d1 = axis
            # Ось поменяли — значит и якорь обязан переехать на ту грань, из
            # которой линия теперь выходит. Иначе конец остаётся на боковой
            # грани, а ус уходит вниз, и линия отрывается от фигуры.
            exit_x = exit_y = entry_x = entry_y = None

    # Если якорь не задан в стиле, берём середину грани, из которой выходим.
    if exit_x is None or exit_y is None:
        exit_x = 0.5 + d0[0] * 0.5
        exit_y = 0.5 + d0[1] * 0.5
    if entry_x is None or entry_y is None:
        entry_x = 0.5 - d1[0] * 0.5
        entry_y = 0.5 - d1[1] * 0.5

    start = anchor_point(src, exit_x, exit_y, placed)
    end = anchor_point(tgt, entry_x, entry_y, placed)

    # Якорь из стиля draw.io бывает противоречит расположению фигур. Доверяем
    # ему, пока он не разворачивает линию назад: связь, уходящая усом прочь от
    # цели, возвращается по себе же и пересекает собственную фигуру.
    # Ус, направленный прочь от первого излома, разворачивает линию так же, как
    # и прочь от цели: у связи с изломами проверяем по тому, куда аналитик её
    # повёл, у прямой — по второму концу.
    bends = _bends_of(edge, src, tgt, d0, d1, placed) if _has_author_bends(edge) else []
    bend_first = bends[0] if bends else None
    bend_last = bends[-1] if bends else None
    if _stub_reverses(start, d0, bend_first or end):
        d0 = _axis_towards(start, bend_first) if bend_first else _dominant_axis(src, tgt, placed)
        start = anchor_point(src, 0.5 + d0[0] * 0.5, 0.5 + d0[1] * 0.5, placed)
    # Второй конец сверяем с УЖЕ исправленным первым: если начало переехало,
    # разворот считается от него.
    if _stub_reverses(end, (-d1[0], -d1[1]), bend_last or start):
        d1 = _axis_towards(bend_last, end) if bend_last else _dominant_axis(src, tgt, placed)
        end = anchor_point(tgt, 0.5 - d1[0] * 0.5, 0.5 - d1[1] * 0.5, placed)

    # Изломов аналитик не ставил — значит и лесенки в ломаной быть не должно:
    # если фигуры стоят друг напротив друга, связь прямая. Створ считается
    # здесь, а не в трассировке, чтобы разведение причалов видело те же точки,
    # по которым линия и пойдёт: иначе оно двигало бы уже выровненный конец.
    aligned = False
    if not given and not _has_author_bends(edge):
        moved = _straighten(start, end, d0, d1, src, tgt, placed)
        aligned = moved != (start, end)
        start, end = moved
    return start, end, d0, d1, aligned


# ── Разведение причалов ─────────────────────────────────────────────────────

#: Ближе этого два причала на одной грани читаются как один, px.
#:
#: Ровно столько же длится «ус» связи (``STUB``): пока причалы ближе, усы
#: перекрываются и линии выходят из фигуры одной. Замер по двенадцати картам
#: банка: при 20 px слипшихся причалов остаётся 4, при 15 px — 29, при 24 px и
#: больше их снова девять, потому что причал упирается в поля грани и не
#: переезжает вовсе.
ANCHOR_MIN_GAP = 20.0

#: Куда пробуем отодвинуть причал вдоль грани, если его место занято, px.
#: Шаг равен зазору: любой перенос сразу даёт законное место, если оно
#: свободно, и лесенка не мельчит.
ANCHOR_STEPS = (0.0, 20.0, -20.0, 40.0, -40.0, 60.0, -60.0, 80.0, -80.0)

#: Доля грани, в пределах которой причал ещё может ездить: у самого угла он
#: смотрится оторвавшимся от фигуры.
ANCHOR_SPREAD = 0.86


def _dock_fractions(node: ProcessNode, side: Direction, along: float,
                    placed: Optional[Dict[str, Tuple[int, int]]]) -> Tuple[float, float]:
    """Доли причала, стоящего на грани ``side`` в точке ``along`` вдоль неё.

    Доля вдоль грани не доводится до нуля и единицы: ровно по этим значениям
    ``_side_direction`` опознаёт грань, и причал у нижнего левого угла был бы
    прочитан как стоящий на ЛЕВОЙ грани. Сдвиг здесь меньше пикселя.
    """
    ox, oy = _origin(node, placed)
    if side[0]:
        span = max(float(node.geometry.height), 1.0)
        return (1.0 if side[0] > 0 else 0.0, min(0.999, max(0.001, (along - oy) / span)))
    span = max(float(node.geometry.width), 1.0)
    return (min(0.999, max(0.001, (along - ox) / span)), 1.0 if side[1] > 0 else 0.0)


def _dock_span(node: ProcessNode, side: Direction,
               placed: Optional[Dict[str, Tuple[int, int]]]) -> Tuple[float, float]:
    """Отрезок грани с полями, в пределах которого причалу разрешено ездить."""
    ox, oy = _origin(node, placed)
    lo, hi = (oy, oy + node.geometry.height) if side[0] else (ox, ox + node.geometry.width)
    mid = (lo + hi) / 2
    half = (hi - lo) / 2 * ANCHOR_SPREAD
    return mid - half, mid + half


def spread_dock_anchors(
    links: Sequence[Tuple[ProcessEdge, Optional[ProcessNode], Optional[ProcessNode]]],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    routes: Optional[Dict[str, Sequence[Point]]] = None,
) -> Dict[str, Anchors]:
    """Разводит причалы связей, сошедшиеся в одной точке грани.

    Середина грани — самое естественное место для причала, поэтому входящая и
    исходящая связи шага садятся на неё обе. Дальше их усы совпадают, маршруты
    идут по одному коридору, и на карте вместо двух связей видна одна: поток
    теряется, а вместе с ним и половина смысла схемы. Холст разводит такие
    причалы сам (``canvas-routing.ts:spreadAnchors``), выгрузка — нет, и в
    BPMN уезжала слипшаяся пара.

    Двигаем причал только ВДОЛЬ грани: линия остаётся на той же стороне
    фигуры, и ход маршрута не меняется. Место занимают по старшинству: сперва
    причалы, заданные аналитиком в draw.io, затем съехавшие ради прямой линии
    (``_straighten``), и лишь потом поставленные по умолчанию — на середину.

    ``routes`` — уже проложенные ломаные. Если они переданы, грань и точка
    причала берутся из них, а не считаются заново: в обход чужой фигуры
    трассировщик иногда уводит связь на другую грань, и разведение, сделанное
    по исходной грани, к готовой карте отношения не имеет.

    Возвращает доли только для тех связей, у которых конец действительно
    сдвинулся: остальные трассируются ровно как раньше.
    """
    # Конец связи: узел, точка вдоль грани, внешняя нормаль, старшинство.
    docks: Dict[str, List[Tuple[ProcessNode, float, Direction, int]]] = {}
    order: List[str] = []
    for edge, src, tgt in links:
        if src is None or tgt is None:
            continue
        route = (routes or {}).get(edge.id)
        if route is not None and len(route) >= 2:
            start, end = tuple(route[0]), tuple(route[-1])
            d0 = _axis_towards(start, tuple(route[1]))
            d1 = _axis_towards(tuple(route[-2]), end)
            aligned = True
        else:
            start, end, d0, d1, aligned = _resolve_ends(edge, src, tgt, placed)
        entry_side = (-d1[0], -d1[1])
        rank = 1 if aligned else 2
        docks[edge.id] = [
            (src, start[1] if d0[0] else start[0], d0,
             0 if _side_direction(edge.exitX, edge.exitY) == d0 else rank),
            (tgt, end[1] if entry_side[0] else end[0], entry_side,
             0 if _side_direction(edge.entryX, edge.entryY) == entry_side else rank),
        ]
        order.append(edge.id)

    groups: Dict[Tuple[str, int, int], List[Tuple[str, int]]] = {}
    for edge_id in order:
        for side_index, (node, _along, side, _rank) in enumerate(docks[edge_id]):
            # Шлюз и событие причалы не разводят: у ромба и круга грань — одна
            # точка (вершина), и связи, как в draw.io, сходятся в ней. Причал,
            # сдвинутый вдоль рамки, оказывался в пустом углу вокруг ромба.
            if outline_kind(node) != 'rectangle':
                continue
            groups.setdefault((node.id, side[0], side[1]), []).append((edge_id, side_index))

    moved: Dict[str, List[Optional[float]]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: docks[m[0]][m[1]][3])
        taken: List[float] = []
        for edge_id, side_index in members:
            node, along, side, rank = docks[edge_id][side_index]
            if rank == 0:
                taken.append(along)
                continue
            lo, hi = _dock_span(node, side, placed)
            picked = along
            for step in ANCHOR_STEPS:
                probe = along + step
                # Поля грани ограничивают, куда причал можно ПЕРЕНЕСТИ. Там,
                # где он уже стоит, он законен и без них: створ (``_straighten``)
                # ставит конец связи и вплотную к углу, и отодвигать его оттуда
                # только ради полей значило бы ломать прямую линию.
                if step and (probe < lo or probe > hi):
                    continue
                if all(abs(t - probe) >= ANCHOR_MIN_GAP for t in taken):
                    picked = probe
                    break
            taken.append(picked)
            if abs(picked - along) < _EPS:
                continue
            fx, fy = _dock_fractions(node, side, picked, placed)
            slot = moved.setdefault(edge_id, [None, None, None, None])
            slot[2 * side_index] = fx
            slot[2 * side_index + 1] = fy

    # У сдвинутой связи фиксируем ОБА конца: несдвинутый описываем той же
    # долей, которую посчитала бы трассировка, — иначе она пересчитает его
    # заново и вернёт себе право свести связь в общий створ.
    out: Dict[str, Anchors] = {}
    for edge_id, slot in moved.items():
        values: List[float] = []
        for side_index in range(2):
            node, along, side, _rank = docks[edge_id][side_index]
            fx, fy = slot[2 * side_index], slot[2 * side_index + 1]
            if fx is None or fy is None:
                fx, fy = _dock_fractions(node, side, along, placed)
            values.extend((fx, fy))
        out[edge_id] = (values[0], values[1], values[2], values[3])
    return out


Box = Tuple[float, float, float, float]


def _box_contains(box: Box, x: float, y: float) -> bool:
    return box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]


def _js_round(value: float) -> float:
    """``Math.round`` из JavaScript: половина округляется вверх, а не к чётному."""
    return float(math.floor(value + 0.5))


def _rectangle_perimeter(box: Box, toward: Point) -> Point:
    """Точка на рамке фигуры в сторону ``toward`` — ``mxPerimeter.RectanglePerimeter``
    из draw.io с ортогональной подводкой, как у «локтя»."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    alpha = math.atan2(toward[1] - cy, toward[0] - cx)
    beta = math.pi / 2 - alpha
    t = math.atan2(h, w)
    if alpha < -math.pi + t or alpha > math.pi - t:
        px, py = x, cy - w * math.tan(alpha) / 2
    elif alpha < -t:
        px, py = cx - h * math.tan(beta) / 2, y
    elif alpha < t:
        px, py = x + w, cy + w * math.tan(alpha) / 2
    else:
        px, py = cx + h * math.tan(beta) / 2, y + h
    if x <= toward[0] <= x + w:
        px = toward[0]
    elif y <= toward[1] <= y + h:
        py = toward[1]
    if toward[0] < x:
        px = x
    elif toward[0] > x + w:
        px = x + w
    if toward[1] < y:
        py = y
    elif toward[1] > y + h:
        py = y + h
    return px, py


def drawio_elbow_route(
    edge: ProcessEdge,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[Point]:
    """Ломаная «локтя» (``elbowEdgeStyle``) ровно так, как её строит draw.io.

    Перенос ``mxEdgeStyle.ElbowConnector`` с ``SideToSide`` / ``TopToBottom``
    и расчёта концов из ``mxGraphView`` (jgraph/drawio, Apache 2.0). Точка
    связи у «локтя» — не излом, а контрольная точка: она лишь сдвигает
    единственное колено. Раньше такую связь прокладывал наш поиск пути с
    обходом фигур, и пунктир «цилиндр ↔ шаг» выходил не таким, каким его
    видит аналитик в draw.io. Эталон — draw.io, поэтому здесь его же
    алгоритм, только на итоговых рамках фигур.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    source: Box = (sx, sy, float(src.geometry.width), float(src.geometry.height))
    target: Box = (tx, ty, float(tgt.geometry.width), float(tgt.geometry.height))

    def fixed(box: Box, fx: Optional[float], fy: Optional[float]) -> Optional[Point]:
        if fx is None or fy is None:
            return None
        fx, fy = min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))
        return box[0] + box[2] * fx, box[1] + box[3] * fy

    p0 = fixed(source, edge.exitX, edge.exitY)
    pe = fixed(target, edge.entryX, edge.entryY)
    pt = (float(edge.points[0].x), float(edge.points[0].y)) if edge.points else None

    # ElbowConnector: по какой оси ставить колено — решают рамки фигур.
    if pt is not None:
        vertical = (pt[1] < min(source[1], target[1])
                    or pt[1] > max(source[1] + source[3], target[1] + target[3]))
        horizontal = (pt[0] < min(source[0], target[0])
                      or pt[0] > max(source[0] + source[2], target[0] + target[2]))
    else:
        vertical = max(source[0], target[0]) == min(source[0] + source[2], target[0] + target[2])
        horizontal = (not vertical and max(source[1], target[1])
                      == min(source[1] + source[3], target[1] + target[3]))
    elbow_vertical = 'elbow=vertical' in (edge.style or '').replace(' ', '').lower()

    # Закреплённый конец draw.io заменяет фигурой-точкой.
    s: Box = (p0[0], p0[1], 0.0, 0.0) if p0 else source
    e: Box = (pe[0], pe[1], 0.0, 0.0) if pe else target
    result: List[Point] = []

    def push(x: float, y: float) -> None:
        if not _box_contains(e, x, y) and not _box_contains(s, x, y):
            result.append((x, y))

    if not horizontal and (vertical or elbow_vertical):
        # TopToBottom
        top, bottom = max(s[1], e[1]), min(s[1] + s[3], e[1] + e[3])
        x = pt[0] if pt is not None and s[0] <= pt[0] <= s[0] + s[2] else s[0] + s[2] / 2
        y = pt[1] if pt is not None else _js_round(bottom + (top - bottom) / 2)
        push(x, y)
        x = pt[0] if pt is not None and e[0] <= pt[0] <= e[0] + e[2] else e[0] + e[2] / 2
        push(x, y)
        if len(result) == 1:
            if pt is not None:
                push(pt[0], y)
            else:
                left, right = max(s[0], e[0]), min(s[0] + s[2], e[0] + e[2])
                result.append((left + (right - left) / 2, y))
    else:
        # SideToSide
        left, right = max(s[0], e[0]), min(s[0] + s[2], e[0] + e[2])
        x = pt[0] if pt is not None else _js_round(right + (left - right) / 2)
        y1, y2 = s[1] + s[3] / 2, e[1] + e[3] / 2
        if pt is not None:
            if s[1] <= pt[1] <= s[1] + s[3]:
                y1 = pt[1]
            if e[1] <= pt[1] <= e[1] + e[3]:
                y2 = pt[1]
        push(x, y1)
        push(x, y2)
        if len(result) == 1:
            if pt is not None:
                push(x, pt[1])
            else:
                top, bottom = max(s[1], e[1]), min(s[1] + s[3], e[1] + e[3])
                result.append((x, top + (bottom - top) / 2))

    # Свободные концы — на рамке, в сторону соседней точки (сперва цель).
    if pe is None:
        toward = result[-1] if result else (p0 or (source[0] + source[2] / 2, source[1] + source[3] / 2))
        pe = _rectangle_perimeter(target, toward)
    if p0 is None:
        p0 = _rectangle_perimeter(source, result[0] if result else pe)
    return _simplify([p0, *result, pe])


def orthogonal_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    obstacles: Optional[Obstacles] = None,
    corridors: Optional[Corridors] = None,
    anchors: Optional[Anchors] = None,
) -> List[Point]:
    """Полная ломаная связи в абсолютных координатах карты.

    ``corridors`` копит уже проложенные связи: маршрут, повторяющий чужой,
    получает штраф и при равных прочих уходит на соседнюю линию сетки. Без
    этого две линии ложатся одна поверх другой и вторая пропадает с карты.

    ``anchors`` приходит из ``spread_dock_anchors``: доли причалов, разведённых
    по грани. Сводить такую связь в общий створ уже нельзя — выравнивание
    вернуло бы оба конца на середину грани, ровно откуда их и развели.
    """
    if src is None or tgt is None:
        return []

    # Пунктир «данные ↔ шаг» в draw.io — «локоть»: он рисуется алгоритмом
    # самого редактора, без обхода фигур. Аналитик так его и видит.
    if edge.kind == 'association' and (edge.edgeStyle or '').lower() == 'elbowedgestyle':
        return _collapse_collinear_runs(_snap_to_pixel_grid(drawio_elbow_route(edge, src, tgt, placed)))

    if obstacles is not None:
        obstacles = obstacles.with_own(_own_boxes(src, tgt, placed))

    start, end, d0, d1, _aligned = _resolve_ends(edge, src, tgt, placed, anchors)

    if _has_author_bends(edge):
        # Изломы аналитика — его решение, как обвести схему, и по умолчанию мы
        # его повторяем. Но линия, идущая сквозь чужой шаг, читается как
        # ошибка независимо от того, кто её так нарисовал: если маршрут по
        # изломам задевает фигуру, к которой связь не подключена, он
        # перестраивается — сначала по тем же изломам, но с обходом между
        # ними, и лишь потом целиком заново.
        bends = _bends_of(edge, src, tgt, d0, d1, placed)
        route = _route_through_bends([start, *bends, end], d0, d1)
        exclude = (src.id, tgt.id)
        if _doubles_back(route):
            route = _unwind_bent_route(
                src, tgt, placed, bends, d0, d1, obstacles, exclude, route)
        if obstacles is not None and _route_cost(route, obstacles, exclude)[0]:
            route = _reroute_through_bends(
                start, d0, bends, end, d1, obstacles, exclude, route)
    else:
        exclude = (src.id, tgt.id)
        route = _best_route(
            start, d0, end, d1, src, tgt, placed, obstacles, exclude, corridors)
    route = _collapse_collinear_runs(
        _drop_micro_jogs(_snap_to_pixel_grid(route), src, tgt, placed))
    route = _straight_short_association(edge, src, tgt, placed, start, end, route)
    return _ends_on_outline(_no_end_jog(route), src, tgt, placed)


#: Длиннее этого прямой отрезок читается как перекос схемы, а не как связка, px.
#:
#: Пунктир «артефакт ↔ шаг» короткий по самой своей природе: аналитик ставит
#: цилиндр вплотную к шагу, которому тот принадлежит. Порог отсекает редкую
#: связь через полкарты — её лучше провести по осям, даже ступеньками.
STRAIGHT_ASSOCIATION_SPAN = 120.0


def _leaves_own_pair(
    route: Sequence[Point],
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> bool:
    """Ломаная выходит за рамку, общую для двух своих фигур.

    Для пунктира «артефакт ↔ шаг» это всегда дефект: цилиндр стоит вплотную к
    шагу, и обходить между ними нечего. Так выглядит крюк, которым обход
    препятствий уводит линию за верхнюю грань шага и возвращает сверху.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    lo_x = min(sx, tx) - MIN_SEGMENT
    hi_x = max(sx + src.geometry.width, tx + tgt.geometry.width) + MIN_SEGMENT
    lo_y = min(sy, ty) - MIN_SEGMENT
    hi_y = max(sy + src.geometry.height, ty + tgt.geometry.height) + MIN_SEGMENT
    return any(not (lo_x <= x <= hi_x and lo_y <= y <= hi_y) for x, y in route)


def _straight_short_association(
    edge: ProcessEdge,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
    start: Point,
    end: Point,
    route: List[Point],
) -> List[Point]:
    """Короткий пунктир «артефакт ↔ шаг» — одним отрезком, как в draw.io.

    Связь без ``edgeStyle`` редактор ведёт прямой линией, и у цилиндра,
    стоящего наискось от шага или вплотную к нему, это единственный опрятный
    вид. Ортогонально между причалами, разнесёнными на четыре пикселя по одной
    оси и на полсотни по другой, ложится только лесенка с отрезками в те же
    четыре пикселя. Сплошную линию это ещё не портит, а пунктир рассыпается
    совсем: штрих длиной в пять пикселей в такой отрезок не попадает, и вместо
    связи на карте остаются оторванные чёрточки — ровно на это и жалуются
    аналитики, сверяя выгрузку с исходником.

    Правило намеренно узкое. Оно включается, только когда ломаная УЖЕ
    испорчена — в ней есть отрезок короче ``MIN_SEGMENT`` или она уходит за
    рамку собственных фигур, — поэтому опрятные ортогональные подводки,
    которых большинство, остаются как были.
    """
    if edge.kind != 'association' or edge.edgeStyle or edge.points:
        return route
    if len(route) < 3:
        return route
    if abs(end[0] - start[0]) > STRAIGHT_ASSOCIATION_SPAN:
        return route
    if abs(end[1] - start[1]) > STRAIGHT_ASSOCIATION_SPAN:
        return route
    cramped = any(
        _EPS <= max(abs(b[0] - a[0]), abs(b[1] - a[1])) < MIN_SEGMENT
        for a, b in zip(route, route[1:])
    )
    if not cramped and not _leaves_own_pair(route, src, tgt, placed):
        return route
    return [route[0], route[-1]]


def point_stub(stub_id: str, x: float, y: float) -> ProcessNode:
    """Точечная «фигура» для трассировки конца связи, висящего на полосе."""
    return ProcessNode(
        id=stub_id,
        name='',
        type='textAnnotation',
        geometry=Geometry(x=int(round(x)) - 1, y=int(round(y)) - 1, width=2, height=2),
    )


def message_flow_endpoints(
    edge: ProcessEdge,
    node: ProcessNode,
    lane: ProcessNode,
    lane_is_source: bool,
) -> Tuple[ProcessNode, ProcessNode]:
    """Пара фигур для трассировки связи «шаг ↔ полоса внешнего участника».

    Полоса тянется на всю ширину карты, поэтому её центр как якорь не годится:
    линия ушла бы через полсхемы. Берём точку, которую нарисовал аналитик
    (свободный конец в draw.io), иначе — проекцию шага на ближайшую грань полосы.

    Причал в обоих случаях прижимается к рамке полосы, потому что связь обязана
    начинаться на фигуре, а не рядом с ней. Два разных промаха это лечит одним
    правилом. Первый: шаг стоит правее или левее полосы, и «центр шага по X»
    оказывается за её краем (в «1.3» — на 330 px). Второй: у связи, оба конца
    которой привязаны к фигурам, в файле остаются старые ``mxPoint`` от той
    поры, когда конец висел в пустоте; сам draw.io их не рисует, а мы брали их
    за причал — в «1.53» линия начиналась в 4557 px от своей полосы.
    """
    free = edge.sourcePoint if lane_is_source else edge.targetPoint
    if free is not None:
        x, y = float(free.x), float(free.y)
    else:
        x = node.geometry.x + node.geometry.width / 2
        lane_bottom = lane.geometry.y + lane.geometry.height
        y = float(lane_bottom if node.geometry.y >= lane_bottom else lane.geometry.y)
    g = lane.geometry
    stub = point_stub(
        f'{edge.id}__lane',
        min(max(x, g.x), g.x + g.width),
        min(max(y, g.y), g.y + g.height),
    )
    return (stub, node) if lane_is_source else (node, stub)
