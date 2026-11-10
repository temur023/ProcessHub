"""Время и деньги карты: сколько процесс длится и во что обходится.

Время считается двумя числами, потому что одно врёт:

* **трудоёмкость** — сумма времени всех шагов. Честна к затратам труда, но у
  карты с развилкой завышает длительность: по XOR проходит одна ветка, а не обе;
* **длительность** — самый долгий путь от старта до финиша. У XOR это самая
  долгая ветка, у параллельного шлюза — самая долгая из идущих одновременно.
  Петля доработки («вернуть на исправление») проходится один раз: путь по
  кругу бесконечен, а цикл в графе — не план процесса, а возможность.

Деньги — время шага × ставка часа сотрудника его дорожки: своя ставка дорожки
в этой карте, иначе ставка роли из справочника (``role_rates``), иначе ставка
по умолчанию. Робот PIX RPA денег
за время не стоит: у него цена запуска (``costPerExecution``), как и при
импорте. Ставка по умолчанию — та же, по которой импорт всегда считал
себестоимость (1932 сум/мин), поэтому без заданных ставок цифры не меняются.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from app.services import role_rates
from app.models.process import (
    FLOW_NODE_TYPES,
    TASK_NODE_TYPES,
    LaneStats,
    ProcessEdge,
    ProcessNode,
    ProcessStats,
)

#: Ставка часа по умолчанию, сум/ч: 1932 сум/мин — ставка ФОТ, по которой
#: импорт draw.io считает себестоимость шага.
DEFAULT_HOURLY_RATE = 1932 * 60

#: Имя строки разбивки для шагов вне дорожек.
NO_LANE_NAME = 'Без дорожки'


def lane_rate_source(lane: Optional[ProcessNode]) -> Tuple[int, str]:
    """Ставка дорожки и откуда она: ``lane`` — своя в карте, ``role`` — справочник ролей, ``default``."""
    if lane is not None and lane.hourlyRate is not None and lane.hourlyRate >= 0:
        return lane.hourlyRate, 'lane'
    role = role_rates.rate_for(lane.name) if lane is not None else None
    if role is not None:
        return role, 'role'
    return DEFAULT_HOURLY_RATE, 'default'


def lane_rate(lane: Optional[ProcessNode]) -> int:
    return lane_rate_source(lane)[0]


def step_cost(node: ProcessNode, rate: int) -> int:
    """Себестоимость одного выполнения шага."""
    if node.category == 'rpa_bot':
        return max(0, node.costPerExecution or 0)
    return round((node.slaMinutes or 0) * rate / 60)


def _is_step(node: ProcessNode) -> bool:
    return node.type in TASK_NODE_TYPES


def _wait_of(node: ProcessNode) -> int:
    """Ожидание фигуры, мин.

    У шага — его WT. У таймера — время, только если оно написано на карте:
    любой фигуре модель ставит условные 30 минут, и безымянные часы иначе
    добавляли бы к процессу полчаса, которых никто не рисовал.
    """
    if _is_step(node):
        return max(0, node.waitMinutes or 0)
    if node.type == 'intermediateTimerEvent' and (node.slaText or node.slaMeasured):
        return max(0, node.slaMinutes or node.waitMinutes or 0)
    return 0


def _duration_of(node: ProcessNode) -> int:
    work = max(0, node.slaMinutes or 0) if _is_step(node) else 0
    return work + _wait_of(node)


def apply_costs(nodes: Sequence[ProcessNode], lanes: Sequence[ProcessNode]) -> None:
    """Записывает шагам себестоимость по ставке их дорожки.

    Себестоимость шага — производная от времени и ставки: поменяли время в
    карточке или ставку дорожки — сохранённая цифра обязана поменяться тоже,
    иначе регламент и аналитика читают устаревшую. Робот не трогается: его
    цена запуска задаётся руками.
    """
    by_id = {lane.id: lane for lane in lanes}
    for node in nodes:
        if _is_step(node) and node.category != 'rpa_bot':
            node.costPerExecution = step_cost(node, lane_rate(by_id.get(node.laneId or '')))


def _longest_path(
    nodes: Sequence[ProcessNode],
    edges: Sequence[ProcessEdge],
) -> Tuple[int, List[str]]:
    """Самый долгий путь потока управления: (минуты, фигуры по порядку).

    Обратные дуги (петли доработки) снимаются обходом в глубину от стартов, и
    на оставшемся ациклическом графе путь ищется динамикой по топологическому
    порядку. Обход итеративный: цепочка в 1600 фигур уронила бы рекурсию.
    """
    flow = {n.id: n for n in nodes if n.type in FLOW_NODE_TYPES}
    succ: Dict[str, List[str]] = {nid: [] for nid in flow}
    indegree: Dict[str, int] = {nid: 0 for nid in flow}
    for e in edges:
        if e.kind != 'sequenceFlow' or e.sourceId not in flow or e.targetId not in flow:
            continue
        succ[e.sourceId].append(e.targetId)
        indegree[e.targetId] += 1
    starts = [nid for nid, n in flow.items() if n.type == 'startEvent']
    if not starts:
        starts = [nid for nid in flow if indegree[nid] == 0]
    if not starts:
        return 0, []

    # Обход в глубину: дуга в фигуру, которая ещё на стеке, — обратная.
    state: Dict[str, int] = {}  # 1 — на стеке, 2 — пройдена
    order: List[str] = []       # обратный топологический порядок
    dag: Dict[str, List[str]] = {}
    for root in starts:
        if root in state:
            continue
        state[root] = 1
        stack: List[Tuple[str, int]] = [(root, 0)]
        while stack:
            nid, i = stack[-1]
            nexts = succ[nid]
            if i < len(nexts):
                stack[-1] = (nid, i + 1)
                nxt = nexts[i]
                mark = state.get(nxt)
                if mark == 1:
                    continue  # петля доработки
                dag.setdefault(nid, []).append(nxt)
                if mark is None:
                    state[nxt] = 1
                    stack.append((nxt, 0))
            else:
                state[nid] = 2
                order.append(nid)
                stack.pop()

    # Самый долгий путь, НАЧИНАЮЩИЙСЯ в фигуре: идём от стоков к стартам.
    best: Dict[str, int] = {}
    after: Dict[str, Optional[str]] = {}
    for nid in order:
        tail, pick = 0, None
        for nxt in dag.get(nid, []):
            if best[nxt] > tail or pick is None:
                tail, pick = best[nxt], nxt
        best[nid] = _duration_of(flow[nid]) + tail
        after[nid] = pick
    start = max(starts, key=lambda s: best.get(s, 0))
    path: List[str] = []
    cur: Optional[str] = start
    while cur is not None:
        path.append(cur)
        cur = after.get(cur)
    return best.get(start, 0), path


def compute_stats(
    nodes: Sequence[ProcessNode],
    edges: Sequence[ProcessEdge],
    lanes: Sequence[ProcessNode],
) -> ProcessStats:
    by_lane = {lane.id: lane for lane in lanes}
    steps = [n for n in nodes if _is_step(n)]

    rows: Dict[Optional[str], LaneStats] = {}
    for lane in lanes:
        rate, source = lane_rate_source(lane)
        rows[lane.id] = LaneStats(
            laneId=lane.id, laneName=lane.name, hourlyRate=rate,
            rateIsDefault=source == 'default', rateSource=source,
            stepCount=0, workMinutes=0, waitMinutes=0, costUzs=0,
        )
    for node in nodes:
        lane = by_lane.get(node.laneId or '')
        key = lane.id if lane else None
        row = rows.get(key)
        if row is None:
            row = rows[key] = LaneStats(
                laneId=None, laneName=NO_LANE_NAME, hourlyRate=DEFAULT_HOURLY_RATE,
                rateIsDefault=True, stepCount=0, workMinutes=0, waitMinutes=0, costUzs=0,
            )
        row.waitMinutes += _wait_of(node)
        if _is_step(node):
            row.stepCount += 1
            row.workMinutes += max(0, node.slaMinutes or 0)
            row.costUzs += step_cost(node, row.hourlyRate)

    path_minutes, path = _longest_path(nodes, edges)
    flow = {n.id: n for n in nodes}
    return ProcessStats(
        stepCount=len(steps),
        unmeasuredStepCount=sum(1 for n in steps if not n.slaMeasured),
        noSystemStepCount=sum(1 for n in steps if not n.system),
        guessedSystemStepCount=sum(1 for n in steps if n.system and n.systemGuessed),
        workMinutes=sum(r.workMinutes for r in rows.values()),
        waitMinutes=sum(r.waitMinutes for r in rows.values()),
        pathMinutes=path_minutes,
        pathStepCount=sum(1 for nid in path if _is_step(flow[nid])),
        pathNodeIds=path,
        costUzs=sum(r.costUzs for r in rows.values()),
        defaultHourlyRate=DEFAULT_HOURLY_RATE,
        # Пустая строка «Без дорожки» не нужна; дорожки — даже пустые: им тоже
        # задают ставку.
        lanes=[r for key, r in rows.items() if key is not None or r.stepCount or r.waitMinutes],
    )
