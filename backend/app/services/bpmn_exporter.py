"""OMG BPMN 2.0 + BPMNDI для PIX Процессной студии / Processet / Camunda.

Экспортёр держит инвариант валидности схемы, а не просто перекладывает узлы:

* стартовое событие не может иметь входящих переходов, конечное — исходящих;
  узлы, нарушающие это, понижаются до промежуточных событий (нормализация);
* хранилища данных и документы (2-ILOVA: Artefaktlar) выгружаются как
  ``dataStoreReference`` / ``dataObjectReference`` и соединяются
  ``association``, а не ``sequenceFlow`` — по спецификации поток управления
  к артефакту вести нельзя;
* порядок элементов внутри ``bpmn:process`` соблюдает XSD-последовательность
  ``laneSet* , flowElement* , artifact*``;
* ``flowNodeRef`` дорожки перечисляет только узлы потока: артефакты
  элементами потока не являются;
* дорожка без шагов — это внешняя сторона (клиент), а не зона ответственности:
  она выгружается отдельным участником-«чёрным ящиком», а пунктир к ней —
  ``messageFlow``. Дорожка без ``flowNodeRef`` импортёру не нужна, и раньше
  такая строка вместе со всеми связями к ней пропадала из схемы;
* дорожки замощают пул по вертикали и делят с ним ширину: импортёр рисует
  геометрию как есть, и разрыв между полосами виден на схеме.
"""
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    GATEWAY_NODE_TYPES,
    TASK_NODE_TYPES,
    BusinessProcess,
    CanvasLayout,
    LabelBox,
    ProcessEdge,
    ProcessNode,
)
from app.services.diagnostics import is_generated_name
from app.services.drawio_parser import duration_badge_minutes
from app.services.map_layout import pool_bounds, split_external_lanes, stack_lanes
from app.services.edge_routing import (
    Anchors,
    Obstacles,
    build_obstacles,
    message_flow_endpoints,
    orthogonal_waypoints,
    point_stub,
    spread_dock_anchors,
)
from app.services.layout import (
    EXTERNAL_LABEL_TYPES,
    FONT_SIZE,
    Box,
    choose_label_box,
    edge_label_candidates,
    node_label_candidates,
    label_size,
    node_obstacles,
    segment_boxes,
    wrapped_line_count,
)

_NCNAME = re.compile(r'^[A-Za-z_][A-Za-z0-9._-]*$')

_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')


def escape_xml(value) -> str:
    if value is None:
        return ''
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


def _safe_id(raw: str, prefix: str, used: Dict[str, str], taken: set) -> str:
    """Выдаёт уникальный xsd:ID и запоминает, во что превратился исходный.

    Раньше функция начиналась с возврата уже выданного значения по исходной
    строке. Для одной и той же сущности это было безобидно, но каждая сущность
    и так спрашивает идентификатор ровно один раз, а совпадение исходных строк
    у РАЗНЫХ сущностей возвращало им общий id. Так рождались дубликаты, из-за
    которых студия отвергала файл целиком:

    * маркер длительности шага ``A`` просит ``Duration_A`` — и получает id
      фигуры, которая в исходнике уже называлась ``Duration_A``;
    * две фигуры с одинаковым ``id`` в склеенном draw.io.

    Поэтому кэш больше не отдаёт готовый ответ, а только ведёт журнал
    соответствий: каждый вызов проходит через ``taken`` и получает своё имя.
    """
    original = raw or ''
    candidate = original if _NCNAME.match(original) else ''
    if not candidate:
        cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', original)
        if not cleaned or not re.match(r'^[A-Za-z_]', cleaned):
            cleaned = f'{prefix}_{cleaned}' if cleaned else prefix
        candidate = cleaned
    base = candidate
    n = 2
    while candidate in taken:
        candidate = f'{base}_{n}'
        n += 1
    used[original] = candidate
    taken.add(candidate)
    return candidate


def iso_duration(minutes: Optional[int]) -> str:
    """Минуты -> ISO-8601 длительность для timerEventDefinition."""
    value = max(1, int(minutes or 0) or 1)
    if value >= 1440 and value % 1440 == 0:
        return f'P{value // 1440}D'
    if value >= 60 and value % 60 == 0:
        return f'PT{value // 60}H'
    return f'PT{value}M'


#: Диаметр значка события в BPMNDI: bpmn.io и Процессная студия рисуют 36 px.
EVENT_SIDE = 36

#: Отступ значка длительности от правого края шага, px.
_DURATION_INSET = 20

#: Запас на дыхание вокруг чужой фигуры при выборе места для значка, px.
_DURATION_GAP = 6


def duration_label(minutes: Optional[float]) -> str:
    """Человекочитаемая длительность: «45 мин», «2 ч», «1 ч 30 мин», «2 дн»."""
    value = int(round(minutes or 0))
    if value <= 0:
        return ''
    if value < 60:
        return f'{value} мин'
    hours, rest = divmod(value, 60)
    if rest:
        return f'{hours} ч {rest} мин'
    if hours >= 24 and hours % 24 == 0:
        return f'{hours // 24} дн'
    return f'{hours} ч'


def step_duration_text(node: ProcessNode) -> str:
    """Подпись под часами у шага: время операции и, если есть, ожидание.

    Пусто, если время не снято с карты. Импорт подставляет правдоподобную
    длительность по категории шага, когда в подписи её нет, — для расчёта SLA
    это нужно, но нарисовать её значком часов значит выдать догадку за замер.
    В draw.io у такого шага часов нет, и на карте студии их быть не должно.
    """
    if not node.slaMeasured:
        return ''
    st = drawn_duration(node.slaText, node.slaMinutes)
    wt = drawn_duration(node.waitText, node.waitMinutes)
    # Подпись часов ожидания на карте сама говорит, что это ожидание
    # («Kutish vaqti 30 min»); «ожидание» дописываем только к пересчитанному.
    if wt and wt != node.waitText:
        wt = f'ожидание {wt}'
    if st and wt:
        return f'{st} · {wt}'
    return wt or st


def drawn_duration(text: Optional[str], minutes: Optional[float]) -> str:
    """Время словами карты draw.io, пока оно не разошлось с числом платформы.

    «60 min» на часах шага — это то, что видит аналитик; «1 ч» там не написано
    нигде. Но время можно поправить на платформе, и тогда старая подпись
    называла бы уже неверное число: такое время пишем пересчитанным.
    """
    value = int(round(minutes or 0))
    if text and value > 0:
        # Разбираем тем же правилом, что и значок на карте: голое «10» внутри
        # часов — тоже минуты. Подписи нескольких часов шага сложены через «+».
        parts = [duration_badge_minutes('symbol=timer', part) for part in text.split(' + ')]
        if None not in parts and sum(max(1, int(round(p))) for p in parts) == value:
            return text
    return duration_label(minutes)


def map_label(node: ProcessNode) -> str:
    """Подпись фигуры на карте студии. Эталон — то, как она выглядит в draw.io.

    Отличий от ``node.name`` два, и оба взяты с исходной карты.

    Подставленное платформой имя на карту не идёт. Безымянный шлюз аналитик
    рисует пустым ромбом — вопрос стоит на ветках («Ha» / «Yo'q»), — а мы
    писали в него «Условие». На карте банка таких ромбов 71 из 220: семь
    десятков надписей, которых в эталоне нет и которые в студии ложатся
    поверх соседних фигур. То же у артефактов: чистый лист без подписи мы
    называли «Документ», а пустой цилиндр — «Информационная система». По
    двадцати двум картам банка это 372 надписи, которых в draw.io нет.
    У задач подпись оставляем: пустой прямоугольник хуже условного имени,
    по нему шаг не найти ни в регламенте, ни в отчёте.

    Событию-ожиданию возвращаем время. В draw.io подпись набрана в две строки
    («Kutish vaqti» и «15 min»), импорт разбирает вторую строку в минуты и
    убирает из имени — в студии оставалось голое «Kutish vaqti» без единой
    цифры. На карту пишем так же, как нарисовано в эталоне.
    """
    name = (node.name or '').strip()
    # Подставленное имя не идёт на карту ни у кого, кроме шагов: безымянный
    # старт в draw.io — пустой круг, а мы подписывали его «Старт».
    if node.type not in TASK_NODE_TYPES and is_generated_name(name):
        return ''
    # У стартовых часов время пишем только словами их подписи: условные 5 минут,
    # которые импорт ставит любому старту, на карте не написаны.
    timed = node.type == 'intermediateTimerEvent' or (is_timer_event(node) and bool(node.slaText))
    if timed and name and not any(c.isdigit() for c in name):
        minutes = drawn_duration(node.slaText, node.slaMinutes or node.waitMinutes)
        if minutes:
            # В draw.io время стоит второй строкой, и от первой её нередко
            # отделяет двоеточие. Склеив в одну строку, получили бы
            # «Kutish vaqti : 30 мин» — с висящим знаком посреди подписи.
            return f'{name.rstrip(" :-—·")} {minutes}'
    return name


def is_timer_event(node: ProcessNode) -> bool:
    """Событие-часы: промежуточный таймер или старт, нарисованный часами.

    Часы в начале процесса («Kutish vaqti / 1 440 min» в полосе клиента) —
    стартовое событие-таймер; простым стартом без часов оно становилось в
    .bpmn, а в подписи обоих форматов терялось время.
    """
    if node.type == 'intermediateTimerEvent':
        return True
    return node.type == 'startEvent' and 'symbol=timer' in (node.style or '').lower()


class DurationMarker(NamedTuple):
    """Значок часов у шага: граничный таймер плюс подпись со временем.

    На карте draw.io время операции нарисовано мелкой фигурой-таймером в углу
    шага, и холст банка показывает его там же. В BPMN это время жило только в
    ``documentation``: в Процессной студии карта открывалась без единой цифры,
    и сотруднику приходилось сверяться с регламентом отдельно.

    Некрывающий (``cancelActivity="false"``) граничный таймер — единственная
    конструкция BPMN, которую импортёры рисуют ровно там, где значок стоит на
    исходной карте: на границе фигуры. Поток она не меняет — у события нет
    исходящих переходов, оно только помечает длительность.
    """

    node: ProcessNode
    marker_id: str
    text: str
    minutes: int
    cx: int
    cy: int


def _marker_candidates(node: ProcessNode) -> List[Box]:
    """Куда поставить часы: сначала правый нижний угол, потом остальные.

    Первым идёт то место, где значок рисует аналитик в draw.io, — на карте без
    тесноты ничего не меняется. Остальные углы нужны, когда шаги стоят вплотную:
    значок в 36 px висит на половину за гранью шага, и у соседа, отстоящего на
    два десятка пикселей, часы верхнего шага ложатся прямо на текст нижнего.
    """
    geo = node.geometry
    half = EVENT_SIDE // 2
    # Узкий шаг значком не разрезать пополам: у него часы встают по центру
    # грани, у обычного — у её угла.
    inset_x = min(_DURATION_INSET, geo.width / 2)
    inset_y = min(_DURATION_INSET, geo.height / 2)
    right, left = geo.x + geo.width - inset_x, geo.x + inset_x
    bottom, top = geo.y + geo.height - inset_y, geo.y + inset_y
    centres = [
        (right, geo.y + geo.height),
        (left, geo.y + geo.height),
        (right, geo.y),
        (left, geo.y),
        (geo.x + geo.width, bottom),
        (geo.x, bottom),
        (geo.x + geo.width, top),
        (geo.x, top),
    ]
    return [(int(round(cx)) - half, int(round(cy)) - half, EVENT_SIDE, EVENT_SIDE)
            for cx, cy in centres]


def duration_markers(
    flow_nodes: List[ProcessNode],
    id_of: Dict[str, str],
    used: Dict[str, str],
    taken: set,
    neighbours: Sequence[ProcessNode] = (),
) -> List[DurationMarker]:
    """Значки длительности для шагов, где проставлено ST или WT.

    Граничное событие BPMN разрешено только у активности, поэтому события и
    шлюзы значка не получают: их время и так уходит в ``timerEventDefinition``
    самого события.

    ``neighbours`` — все фигуры карты. Значок объезжает по границе своего шага
    те из них, что стоят вплотную, и уже поставленные часы соседей: иначе на
    плотном участке двое часов и две подписи сходятся в одну кляксу.
    """
    markers: List[DurationMarker] = []
    placed: List[Box] = []
    for node in flow_nodes:
        if node.type not in TASK_NODE_TYPES:
            continue
        text = step_duration_text(node)
        if not text:
            continue
        # Чужие фигуры считаем крупнее, чем они есть: часы, вставшие вплотную
        # к соседнему шагу, формально его не задевают, а читаются как его часть.
        crowd = [(x - _DURATION_GAP, y - _DURATION_GAP,
                  w + 2 * _DURATION_GAP, h + 2 * _DURATION_GAP)
                 for x, y, w, h in node_obstacles(neighbours, skip_id=node.id)]
        box = choose_label_box(_marker_candidates(node), crowd + placed)
        placed.append(box)
        markers.append(DurationMarker(
            node=node,
            marker_id=_safe_id(f'Duration_{id_of[node.id]}', 'Duration', used, taken),
            text=text,
            minutes=int(node.slaMinutes or node.waitMinutes or 0),
            cx=box[0] + EVENT_SIDE // 2,
            cy=box[1] + EVENT_SIDE // 2,
        ))
    return markers


def marker_box(marker: DurationMarker) -> Box:
    """Рамка самого значка: круг события сидит на границе шага."""
    half = EVENT_SIDE // 2
    return (marker.cx - half, marker.cy - half, EVENT_SIDE, EVENT_SIDE)


def _duration_label_candidates(marker: DurationMarker) -> List[Box]:
    """Куда положить время: под часами, затем правее, левее, выше и по углам.

    Четырёх сторон на плотной карте не хватает: если все они заняты, выбор
    падает на «наименее конфликтную» позицию, и время печатается поверх текста
    самого шага. Диагонали и вторая полка дают запас.
    """
    width, height = label_size(marker.text)
    half = EVENT_SIDE // 2
    cx, cy = marker.cx, marker.cy
    near = half + 4
    far = half + 6 + height
    return [
        (cx - width // 2, cy + half + 2, width, height),
        (cx + near, cy - height // 2, width, height),
        (cx - near - width, cy - height // 2, width, height),
        (cx - width // 2, cy - half - 2 - height, width, height),
        (cx + near, cy + half + 2, width, height),
        (cx - near - width, cy + half + 2, width, height),
        (cx + near, cy - half - 2 - height, width, height),
        (cx - near - width, cy - half - 2 - height, width, height),
        (cx - width // 2, cy + far, width, height),
        (cx - width // 2, cy - far - height, width, height),
    ]


def _band_boxes(x: int, y: int, width: int, height: int, header: int) -> List[Box]:
    """Заголовок дорожки и её рамка — места, куда подпись класть нельзя.

    В полосе слева bpmn.io печатает повёрнутое название дорожки, а по контуру
    рисует линию. Подпись, попавшая туда, ложится либо поверх названия, либо
    ровно на разделитель между дорожками — ровно то, что видно в выгрузке.
    """
    b = LANE_BORDER
    return [
        (x, y, header, height),
        (x, y - b, width, 2 * b),
        (x, y + height - b, width, 2 * b),
    ]


def _duration_marker_xml(marker: DurationMarker, attached_to: str) -> List[str]:
    return [
        f'    <bpmn:boundaryEvent id="{escape_xml(marker.marker_id)}"'
        f' name="{escape_xml(marker.text)}"'
        f' attachedToRef="{escape_xml(attached_to)}" cancelActivity="false">',
        '      <bpmn:timerEventDefinition>',
        '        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">'
        f'{iso_duration(marker.minutes)}</bpmn:timeDuration>',
        '      </bpmn:timerEventDefinition>',
        '    </bpmn:boundaryEvent>',
    ]


def _edge_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    obstacles: Optional[Obstacles] = None,
    anchors: Optional[Anchors] = None,
) -> List[Tuple[int, int]]:
    """Ортогональная ломаная — та же, что рисует draw.io.

    Раньше сюда шла только пара «точка выхода — точка входа», и в bpmn.io
    схема выглядела диагональной паутиной. ``obstacles`` даёт трассировщику
    список чужих фигур: без него колено ставится посередине и линия уходит
    сквозь то, что там стоит. ``anchors`` — разведённые причалы.
    """
    route = orthogonal_waypoints(edge, src, tgt, None, obstacles, None, anchors)
    if len(route) < 2:
        return [(100, 100), (250, 100)]
    return [(int(round(x)), int(round(y))) for x, y in route]


def normalize_event_types(
    flow_nodes: List[ProcessNode],
    edges: List[ProcessEdge],
) -> Dict[str, str]:
    """Приводит степени событий к требованиям BPMN 2.0.

    Возвращает карту ``id -> эффективный тип``. Исходная модель не меняется:
    понижение типа нужно только для выгрузки.

    * startEvent с входящим переходом -> intermediateThrowEvent;
    * endEvent с исходящим переходом  -> intermediateThrowEvent.
    """
    incoming: Set[str] = set()
    outgoing: Set[str] = set()
    for e in edges:
        if e.kind != 'sequenceFlow':
            continue
        if e.targetId:
            incoming.add(e.targetId)
        if e.sourceId:
            outgoing.add(e.sourceId)

    effective: Dict[str, str] = {}
    for node in flow_nodes:
        kind = node.type
        if kind == 'startEvent' and node.id in incoming:
            kind = 'intermediateThrowEvent'
        elif kind == 'endEvent' and node.id in outgoing:
            kind = 'intermediateThrowEvent'
        effective[node.id] = kind
    return effective


def _node_tag(node: ProcessNode, effective_type: str) -> str:
    if effective_type == 'startEvent':
        return 'bpmn:startEvent'
    if effective_type == 'endEvent':
        return 'bpmn:endEvent'
    if effective_type == 'intermediateThrowEvent':
        return 'bpmn:intermediateThrowEvent'
    if effective_type in ('intermediateTimerEvent', 'intermediateMessageEvent'):
        return 'bpmn:intermediateCatchEvent'
    if effective_type == 'exclusiveGateway':
        return 'bpmn:exclusiveGateway'
    if effective_type == 'parallelGateway':
        return 'bpmn:parallelGateway'
    if effective_type == 'inclusiveGateway':
        return 'bpmn:inclusiveGateway'
    if effective_type == 'complexGateway':
        return 'bpmn:complexGateway'
    if effective_type == 'subProcess':
        return 'bpmn:subProcess'
    if effective_type == 'dataStore':
        return 'bpmn:dataStoreReference'
    if effective_type == 'dataObject':
        return 'bpmn:dataObjectReference'
    if effective_type == 'textAnnotation':
        return 'bpmn:textAnnotation'
    if effective_type == 'serviceTask' or node.category == 'rpa_bot':
        return 'bpmn:serviceTask'
    if effective_type == 'task':
        return 'bpmn:task'
    return 'bpmn:userTask'


def _node_documentation(node: ProcessNode) -> str:
    bits: List[str] = []
    if node.code:
        bits.append(f'Code: {node.code}')
    if node.role:
        bits.append(f'Role: {node.role}')
    if node.laneName and node.laneName != node.role:
        bits.append(f'Lane: {node.laneName}')
    if node.system:
        bits.append(f'System: {node.system}')
    if node.slaMinutes:
        bits.append(f'ST: {node.slaMinutes} min')
    if node.waitMinutes:
        bits.append(f'WT: {node.waitMinutes} min')
    if node.inputArtifacts:
        bits.append(f"In: {', '.join(node.inputArtifacts)}")
    if node.outputArtifacts:
        bits.append(f"Out: {', '.join(node.outputArtifacts)}")
    if node.category:
        bits.append(f'Category: {node.category}')
    if node.automationPotential:
        bits.append(f'RPA potential: {node.automationPotential}%')
    return '; '.join(bits)


#: Сколько строк повёрнутой подписи помещается в заголовочную полосу.
#: Полоса — 30 px, строка при кегле 12 — 14.4 px: две влезают, третья уже нет.
_BAND_LINES = 2


def band_label(name: str, height: float, font_size: float = FONT_SIZE,
               lines: int = _BAND_LINES) -> str:
    """Название дорожки, укороченное до того, что влезет в её полосу.

    Импортёр печатает название дорожки повёрнутым в полосе шириной 30 px, а
    длиной строки ему служит высота самой дорожки. Число 30 зашито в
    отрисовщике — атрибутом его не поменять, и длинное название расползается
    за пределы полосы: на карте «Akkreditiv ochish» подпись пула вылезала на
    64 px левее пула, поверх соседних фигур. Ровно на это жалуются как на
    «тексты слева выходят за рамки».

    Поэтому в ``name`` уезжает то, что помещается, а полное название остаётся
    в ``documentation`` — сотрудник видит его в свойствах дорожки, и при
    обратном чтении оно не потеряно.

    Влезет или нет — считаем ПО ЧИСЛУ СТРОК, а не по общей ширине текста.
    Отрисовщик переносит подпись по словам и жадно: длинное слово не влезает в
    остаток строки и целиком уезжает на следующую, поэтому строка кончается
    раньше края. Мерка «общая ширина ≤ двум длинам строки» этого не видит: у
    дорожки «Bank tarmoqlarini muvofiqlashtirish xizmati bosh menejer (MXD)»
    укороченное название проходило по ширине и всё равно ложилось в три
    строки — та самая подпись, которая вылезает за полосу.
    """
    text = (name or '').strip()
    if not text:
        return text
    room = max(float(height) - 8.0, 24.0)
    if wrapped_line_count(text, room, font_size) <= lines:
        return text
    # Режем по границе слова: обрубок посреди слова читается как опечатка.
    kept: List[str] = []
    for word in text.split():
        if kept and wrapped_line_count(' '.join(kept + [word]) + '…', room, font_size) > lines:
            break
        kept.append(word)
    trimmed = ' '.join(kept).rstrip(' ,.;:-')
    # Одно слово длиннее двух строк словами не укоротить — дорезаем буквами.
    while trimmed and wrapped_line_count(trimmed + '…', room, font_size) > lines:
        trimmed = trimmed[:-1].rstrip()
    return f'{trimmed}…' if trimmed else text[:1] + '…'


#: Ширина заголовочной полосы пула в BPMNDI (bpmn.io рисует ровно 30 px).
POOL_HEADER = 30
#: Полоса с названием дорожки внутри пула — bpmn.io рисует её той же ширины.
LANE_HEADER = 30
#: Полутолщина рамки дорожки: подпись, севшая на разделитель, читается
#: перечёркнутой.
#:
#: Разделитель — тонкая линия, и запретна ровно она. Полоса в 8 px держала
#: текст на 4 px от линии: цилиндр ближе 30 px к границе дорожки не мог
#: подписаться у себя, и подпись уезжала в соседнюю дорожку.
LANE_BORDER = 1

#: Типы, которым BPMN разрешает быть концом messageFlow (InteractionNode).
_INTERACTION_TYPES = (
    'task', 'userTask', 'serviceTask', 'subProcess',
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
)


def _union_bounds(nodes: Iterable[ProcessNode], header: int = POOL_HEADER) -> Tuple[int, int, int, int]:
    items = list(nodes)
    if not items:
        return (40, 40, 800, 200)
    min_x = min(n.geometry.x for n in items)
    min_y = min(n.geometry.y for n in items)
    max_x = max(n.geometry.x + n.geometry.width for n in items)
    max_y = max(n.geometry.y + n.geometry.height for n in items)
    x = min_x - header
    y = min_y
    return (x, y, max(max_x - x, 80), max(max_y - y, 80))


class DiagramParts(NamedTuple):
    """Что из модели попадает на схему выгрузки и в каком качестве."""

    all_nodes: List[ProcessNode]
    flow_nodes: List[ProcessNode]
    artifact_nodes: List[ProcessNode]
    lanes: List[ProcessNode]
    external_lanes: List[ProcessNode]
    message_flows: List[ProcessEdge]
    edges: List[ProcessEdge]


def diagram_parts(process: BusinessProcess) -> DiagramParts:
    """Узлы, дорожки пула, внешние участники и связи схемы — одним правилом.

    Нужно двоим: выгрузке, которая пишет схему в файл, и холсту, который
    рисует ту же схему на экране (``canvas_layout``).
    """
    all_nodes = [n for n in process.nodes if n.type != 'lane']
    flow_nodes = [n for n in all_nodes if n.type not in ARTIFACT_NODE_TYPES]
    artifact_nodes = [n for n in all_nodes if n.type in ARTIFACT_NODE_TYPES]
    lanes, external_lanes = split_external_lanes(list(process.lanes or []), flow_nodes)
    lane_by_id = {lane.id: lane for lane in lanes + external_lanes}
    node_by_id = {n.id: n for n in all_nodes}
    valid_ids = set(node_by_id)

    # Точки контакта с внешним участником: один конец — его полоса, другой —
    # фигура процесса. В BPMN это messageFlow внутри collaboration.
    #
    # Второй конец бывает и шлюзом: аналитик ведёт пунктир от полосы клиента
    # прямо к ромбу — ответ участника и есть развилка. Спецификация концом
    # messageFlow шлюз не признаёт (InteractionNode — это пул, активность и
    # событие), но выбрасывать связь из-за этого нельзя: на 22 картах банка
    # так пропадали 84 пунктира. Такая связь идёт ассоциацией — тем же
    # приёмом, каким ниже выгружается пунктир к хранилищу.
    #
    # Пунктир к дорожке своего же пула — тоже контакт (в полосе «Mijoz» стоят
    # старт и завершение, но стрелки к её границе — это обмен с клиентом).
    # messageFlow внутри одного пула спецификация запрещает, поэтому ниже он
    # выгружается ассоциацией к дорожке: линия остаётся там, где её нарисовал
    # аналитик.
    message_flows = [
        e for e in process.edges
        if e.kind == 'messageFlow'
        and (
            (e.sourceId in lane_by_id and node_by_id.get(e.targetId or '') is not None)
            or (e.targetId in lane_by_id and node_by_id.get(e.sourceId or '') is not None)
        )
    ]
    # Висячие связи и оформительские линии draw.io в схему не идут: у
    # annotationLine хотя бы один конец не опирается на шаг процесса.
    # Петля из фигуры в саму себя тоже не идёт: спецификация её не допускает,
    # а PIX Процессная студия из-за одной такой линии отказывается открыть всю
    # карту («Connector source and target node cannot be the same»).
    edges = [
        e for e in process.edges
        if e.kind != 'annotationLine'
        and e.sourceId in valid_ids and e.targetId in valid_ids
        and e.sourceId != e.targetId
    ]
    return DiagramParts(all_nodes, flow_nodes, artifact_nodes, lanes, external_lanes,
                        message_flows, edges)


class DiagramLayout(NamedTuple):
    """Геометрия схемы, которую выгрузка пишет в BPMNDI, а холст рисует."""

    #: Рамка пула ``(x, y, ширина, высота)``; без дорожек — нули.
    pool: Tuple[int, int, int, int]
    routes: List[Tuple[ProcessEdge, List[Tuple[int, int]]]]
    edge_labels: Dict[str, Box]
    node_labels: Dict[str, Box]
    marker_labels: Dict[str, Box]
    #: Занятые и запретные места после расстановки: по ним холст ставит
    #: подписи линий, которых в выгрузке нет (оформительских).
    taken: List[Box]
    forbidden: List[Box]
    obstacles: Obstacles


def layout_diagram(
    all_nodes: List[ProcessNode],
    lanes: List[ProcessNode],
    external_lanes: List[ProcessNode],
    edges: List[ProcessEdge],
    message_flows: List[ProcessEdge],
    markers: List[DurationMarker],
    use_collab: bool,
) -> DiagramLayout:
    """Ломаные связей и места подписей — один расчёт на выгрузку и на холст.

    Раньше у холста был свой трассировщик и своя расстановка подписей, и карта
    на экране расходилась с файлом: по замеру приёмки на «overnight», «counter
    guarantee» и «Akkreditiv ochish» форма линий отличалась у 17–31 % связей, а
    подписи веток шлюза на холсте ложились друг на друга. Теперь и файл, и холст
    берут линии и подписи отсюда.
    """
    contact_lane_by_id = {lane.id: lane for lane in lanes + external_lanes}
    node_by_id = {n.id: n for n in all_nodes}
    if use_collab:
        # Пул охватывает дорожки и узлы; полосы внешних участников — отдельные
        # пулы и в его границы не входят.
        # Пул — ровно объединение своих дорожек. Раньше сюда входили ещё и
        # узлы, и если хоть один стоял выше первой дорожки, пул начинался
        # на 120-260 px выше неё: сверху оставалась полоса, не принадлежащая
        # ни одной дорожке, и импортёр раскладывал полосы по-своему.
        stacked = pool_bounds(lanes)
        pool = stacked if stacked else _union_bounds(lanes + all_nodes)
    else:
        pool = (0, 0, 0, 0)
    px, py, pw, ph = pool

    # ── Подписи ────────────────────────────────────────────────────────────
    # Позиции считаем ДО отрисовки: подпись шлюза, подпись связи и подпись
    # соседнего события претендуют на одно и то же место под фигурой, и
    # разводить их можно, только зная все занятые прямоугольники сразу.
    # Препятствия считаются один раз на схему: дорожки и пулы в них не входят,
    # связь между дорожками обязана их пересекать.
    # Значок длительности рисует сам экспортёр, в модели его нет. Пока
    # трассировщик о нём не знал, линии шли прямо по часам — в PMM на это
    # приходилась половина всех пересечений. Хозяин у рамки свой шаг: связь,
    # идущая в этот шаг, проходит рядом со значком по праву.
    route_obstacles = build_obstacles(
        all_nodes,
        extra=[(marker.marker_id, marker.node.id, marker_box(marker))
               for marker in markers],
    )
    links: List[Tuple[ProcessEdge, Optional[ProcessNode], Optional[ProcessNode]]] = [
        (edge, node_by_id.get(edge.sourceId or ''), node_by_id.get(edge.targetId or ''))
        for edge in edges
    ]
    for edge in message_flows:
        lane_is_source = edge.sourceId in contact_lane_by_id
        lane = contact_lane_by_id[edge.sourceId if lane_is_source else edge.targetId]
        peer = node_by_id[edge.targetId if lane_is_source else edge.sourceId]
        links.append((edge, *message_flow_endpoints(edge, peer, lane, lane_is_source)))
    # Трассируем дважды. Первый проход показывает, какими гранями связи пойдут
    # на самом деле: обходя чужую фигуру, маршрут иногда меняет грань, и
    # причал, разведённый по исходной, оказывается не на той стороне. Второй
    # проход идёт уже по разведённым причалам.
    #
    # Причалы разводятся по всем связям сразу: поодиночке каждая ломаная
    # выглядит правильной, а вместе они ложатся друг на друга.
    probe = {
        edge.id: _edge_waypoints(edge, src_node, tgt_node, route_obstacles)
        for edge, src_node, tgt_node in links
    }
    anchors = spread_dock_anchors(links, routes=probe)
    routes: List[Tuple[ProcessEdge, List[Tuple[int, int]]]] = [
        (edge, _edge_waypoints(edge, src_node, tgt_node, route_obstacles, anchors.get(edge.id)))
        for edge, src_node, tgt_node in links
    ]

    taken_boxes: List[Tuple[int, int, int, int]] = node_obstacles(all_nodes)
    # Заголовки дорожек и пулов — не просто занятое место, а запретное: в них
    # уже напечатано повёрнутое название, и вторая надпись поверх складывается
    # с ним в нечитаемую кашу. Держим их отдельным списком с большим весом.
    forbidden_boxes: List[Tuple[int, int, int, int]] = []
    if use_collab:
        forbidden_boxes.append((px, py, POOL_HEADER, ph))
        for lane in external_lanes:
            g = lane.geometry
            forbidden_boxes.extend(_band_boxes(g.x, g.y, g.width, g.height, POOL_HEADER))
    for lane in lanes:
        g = lane.geometry
        lane_x = px if use_collab else g.x
        lane_w = max(pw, 80) if use_collab else g.width
        forbidden_boxes.extend(_band_boxes(lane_x, g.y, lane_w, g.height, LANE_HEADER))
    # Часы у шага занимают место на карте так же, как фигура: подпись связи,
    # положенная на них, скрывает цифру.
    taken_boxes.extend(marker_box(m) for m in markers)
    # Линии связей — тоже препятствие: подпись, положенная на ЧУЖУЮ связь,
    # читается как перечёркнутая. Свои линии и узел, и сама связь помнят
    # отдельно: подпись цилиндра или листа обязана стоять под фигурой, а
    # собственный пунктир выходит ровно оттуда и загораживал ей единственное
    # правильное место — подпись уезжала вбок и на карте отрывалась от своей
    # фигуры. У связи то же самое, только сильнее: в draw.io «Ha» и «Yo'q»
    # стоят ровно на своей линии, и запрет садиться на неё выталкивал их в
    # промежуток между ветками шлюза.
    own_segments: Dict[str, List[Tuple[int, int, int, int]]] = {}
    edge_segments: Dict[str, List[Tuple[int, int, int, int]]] = {}
    for edge, route in routes:
        boxes = segment_boxes(route)
        taken_boxes.extend(boxes)
        edge_segments[edge.id] = boxes
        for end in (edge.sourceId, edge.targetId):
            if end:
                own_segments.setdefault(end, []).extend(boxes)
    edge_label_of: Dict[str, Tuple[int, int, int, int]] = {}
    for edge, route in routes:
        text = (edge.name or edge.condition or '').strip()
        if not text:
            continue
        mine = set(edge_segments[edge.id])
        around = [b for b in taken_boxes if b not in mine]
        box = choose_label_box(
            edge_label_candidates(route, text), around, forbidden_boxes)
        edge_label_of[edge.id] = box
        taken_boxes.append(box)

    node_label_of: Dict[str, Tuple[int, int, int, int]] = {}
    for node in all_nodes:
        # Смотрим на подпись, которая реально уедет в схему: у безымянного
        # шлюза она пустая, и место под неё резервировать не за чем.
        if node.type not in EXTERNAL_LABEL_TYPES or not map_label(node).strip():
            continue
        # Сама фигура своей подписи не мешает: подпись события печатается
        # вплотную к нему и обязана иметь право стоять рядом. Её собственные
        # связи — тоже: они выходят из фигуры туда же, где стоит подпись.
        mine = {(node.geometry.x, node.geometry.y, node.geometry.width, node.geometry.height)}
        mine.update(own_segments.get(node.id, ()))
        around = [b for b in taken_boxes if b not in mine]
        box = choose_label_box(node_label_candidates(node), around, forbidden_boxes)
        node_label_of[node.id] = box
        taken_boxes.append(box)

    # Время под часами кладём последним: оно уступает место подписям связей
    # и событий, а не наоборот — цифру читают, подойдя к конкретному шагу.
    marker_label_of: Dict[str, Box] = {}
    for marker in markers:
        box = choose_label_box(_duration_label_candidates(marker), taken_boxes, forbidden_boxes)
        marker_label_of[marker.marker_id] = box
        taken_boxes.append(box)

    return DiagramLayout(
        pool=(px, py, pw, ph),
        routes=routes,
        edge_labels=edge_label_of,
        node_labels=node_label_of,
        marker_labels=marker_label_of,
        taken=taken_boxes,
        forbidden=forbidden_boxes,
        obstacles=route_obstacles,
    )


def _canvas_ends(
    edge: ProcessEdge,
    node_by_id: Dict[str, ProcessNode],
    lane_by_id: Dict[str, ProcessNode],
) -> Tuple[Optional[ProcessNode], Optional[ProcessNode]]:
    """Концы линии, которой нет в выгрузке, — для трассировки на холсте.

    Такие линии аналитик всё-таки нарисовал: разделитель этапов с концом в
    пустоте, пунктир в дорожку с шагами. Выгрузка их пропускает, а холст обязан
    показать — тем же трассировщиком, что и остальные.
    """
    def end(node_id: Optional[str], free, suffix: str, other: Optional[ProcessNode]):
        if node_id in node_by_id:
            return node_by_id[node_id]
        if free is not None:
            return point_stub(f'{edge.id}__{suffix}', free.x, free.y)
        lane = lane_by_id.get(node_id or '')
        if lane is not None and other is not None:
            lane_is_source = suffix == 'src'
            ends = message_flow_endpoints(edge, other, lane, lane_is_source)
            return ends[0] if lane_is_source else ends[1]
        return None

    src_node = node_by_id.get(edge.sourceId or '')
    tgt_node = node_by_id.get(edge.targetId or '')
    src = end(edge.sourceId, edge.sourcePoint, 'src', tgt_node)
    tgt = end(edge.targetId, edge.targetPoint, 'tgt', src_node or src)
    if src is None and tgt is not None and edge.sourceId in lane_by_id:
        src = end(edge.sourceId, None, 'src', tgt)
    return src, tgt


def canvas_layout(process: BusinessProcess) -> CanvasLayout:
    """Линии и подписи карты для холста — тем же расчётом, что пишет .bpmn.

    Отличие одно: выгрузка перед расчётом укладывает дорожки в сплошную стопку
    (``stack_lanes``), а холст рисует карту в координатах draw.io. Поэтому
    считается по геометрии самой страницы. Замер на 13 картах банка: ломаные
    холста и файла совпадают по числу изломов и по x у всех связей — сдвиг
    полос меняет только высоту вертикальных участков.

    Линии, которых в выгрузке нет (оформительские, петли), трассируются тем же
    трассировщиком после основных и подписываются на оставшихся местах.
    """
    parts = diagram_parts(process)
    id_of = {n.id: n.id for n in parts.flow_nodes}
    markers = duration_markers(parts.flow_nodes, id_of, {}, set(), parts.all_nodes)
    layout = layout_diagram(
        parts.all_nodes, parts.lanes, parts.external_lanes, parts.edges,
        parts.message_flows, markers, bool(parts.lanes) or bool(parts.external_lanes),
    )
    routes: Dict[str, List[List[int]]] = {
        edge.id: [[int(x), int(y)] for x, y in route] for edge, route in layout.routes if len(route) >= 2
    }
    texts = {edge.id: (edge.name or edge.condition or '').strip() for edge in process.edges}
    edge_labels: Dict[str, LabelBox] = {
        edge_id: LabelBox(x=box[0], y=box[1], width=box[2], height=box[3], text=texts.get(edge_id, ''))
        for edge_id, box in layout.edge_labels.items()
    }

    node_by_id = {n.id: n for n in parts.all_nodes}
    lane_by_id = {lane.id: lane for lane in process.lanes or []}
    taken = list(layout.taken)
    for edge in process.edges:
        if edge.id in routes:
            continue
        src, tgt = _canvas_ends(edge, node_by_id, lane_by_id)
        route = [
            (int(round(x)), int(round(y)))
            for x, y in orthogonal_waypoints(edge, src, tgt, None, layout.obstacles)
        ]
        if len(route) < 2:
            continue
        routes[edge.id] = [[x, y] for x, y in route]
        boxes = segment_boxes(route)
        taken.extend(boxes)
        if texts.get(edge.id):
            mine = set(boxes)
            box = choose_label_box(
                edge_label_candidates(route, texts[edge.id]),
                [b for b in taken if b not in mine], layout.forbidden)
            edge_labels[edge.id] = LabelBox(
                x=box[0], y=box[1], width=box[2], height=box[3], text=texts[edge.id])
            taken.append(box)

    node_labels = {
        node_id: LabelBox(x=box[0], y=box[1], width=box[2], height=box[3],
                          text=map_label(node_by_id[node_id]))
        for node_id, box in layout.node_labels.items()
    }
    durations = {marker.node.id: marker.text for marker in markers}
    return CanvasLayout(routes=routes, edgeLabels=edge_labels, nodeLabels=node_labels,
                        durations=durations)


def generate_bpmn_xml(process: BusinessProcess) -> str:
    # Дорожки укладываются в сплошную стопку ДО всего остального, вместе со
    # своим содержимым. По спецификации дорожка — раздел пула, а не отдельный
    # прямоугольник на холсте: щель или нахлёст между полосами импортёр
    # вынужден исправлять сам, и в Процессной студии меньшие дорожки уходят
    # под большие. См. ``map_layout.stack_lanes``.
    process = stack_lanes(process)

    used: Dict[str, str] = {}
    taken: set = set()

    # Идентификаторы процессов, которые дадут страницы-подпроцессы. Нужны до
    # сборки: на них ссылается `calledElement` шагов основной карты.
    called_process_of: Dict[str, str] = {
        page.id: sub_page_process_id(index)
        for index, page in enumerate(process.subProcesses or [], 1)
    }

    proc_id = _safe_id(
        f"Process_{re.sub(r'[^A-Za-z0-9_]', '_', process.passport.code or 'SQB')}",
        'Process',
        used,
        taken,
    )
    def_id = _safe_id(f"Definitions_{process.id}", 'Definitions', used, taken)
    diag_id = _safe_id(f'Diagram_{proc_id}', 'Diagram', used, taken)
    plane_id = _safe_id(f'Plane_{proc_id}', 'Plane', used, taken)
    collab_id = _safe_id(f'Collaboration_{proc_id}', 'Collaboration', used, taken)
    participant_id = _safe_id(f'Participant_{proc_id}', 'Participant', used, taken)
    lane_set_id = _safe_id(f'LaneSet_{proc_id}', 'LaneSet', used, taken)

    parts = diagram_parts(process)
    all_nodes, flow_nodes, artifact_nodes = parts.all_nodes, parts.flow_nodes, parts.artifact_nodes
    lanes, external_lanes = parts.lanes, parts.external_lanes
    external_by_id = {lane.id: lane for lane in external_lanes}
    node_by_id = {n.id: n for n in all_nodes}
    message_flows, edges = parts.message_flows, parts.edges

    lane_ids = {lane.id for lane in lanes}

    def _peer_type(edge: ProcessEdge) -> str:
        lane_is_source = edge.sourceId in external_by_id or edge.sourceId in lane_ids
        return node_by_id[edge.targetId if lane_is_source else edge.sourceId].type

    #: Контакты, которым спецификация запрещает быть messageFlow: второй конец
    #: не InteractionNode (шлюз) или полоса — дорожка своего же пула.
    contact_associations = {
        e.id for e in message_flows
        if _peer_type(e) not in _INTERACTION_TYPES
        or e.sourceId in lane_ids or e.targetId in lane_ids
    }

    #: Примечания, написанные внутри полосы участника: id примечания -> id полосы.
    #:
    #: Такое примечание обязано лежать в процессе САМОГО участника, а не в
    #: процессе банка. Импортёр вкладывает фигуру в тот пул, которому
    #: принадлежит её процесс, и примечание из полосы «Sport maydoni», отданное
    #: процессу банка, оказывалось под чужим пулом: в bpmn.io текст был виден
    #: сквозь заливку соседнего пула бледно-серым, а строка, не поместившаяся в
    #: рамку, торчала под ним чёрным.
    participant_notes = {
        n.id: n.laneId
        for n in artifact_nodes
        if n.type == 'textAnnotation' and n.laneId in external_by_id
    }

    # messageFlow между двумя шагами одного пула спецификация запрещает: карта
    # SQB — один пул, поэтому такие связи выгружаются как поток управления.
    sequence_edges = [e for e in edges if e.kind != 'association']
    association_edges = [e for e in edges if e.kind == 'association']

    # ── Связь «шаг ↔ документ / база» — связь с данными внутри шага ─────────
    #
    # По спецификации документ и база подключаются к шагу не ассоциацией, а
    # ``dataInputAssociation`` / ``dataOutputAssociation``, вложенными в сам
    # шаг. PIX принимает только их: обычную ассоциацию к документу или базе
    # его импорт выбрасывает молча (проба на PIX банка, PIX Процессы 2.3.2),
    # и все системы с документами отваливались от шагов. Наконечник у связи с
    # данными рисуют и PIX, и bpmn.io, выключить его из файла нечем — это цена
    # за то, что связь вообще доезжает до студии.
    #
    # Ассоциацией остаётся связь с данными, второй конец которой не шаг
    # (событие, шлюз): вложить связь с данными туда спецификация не даёт.
    _data_types = ('dataStore', 'dataObject')
    node_type_of = {n.id: n.type for n in all_nodes}
    #: Шаг -> его связи с данными: ``('in', связь)`` из данных в шаг,
    #: ``('out', связь)`` из шага в данные.
    data_links: Dict[str, List[Tuple[str, ProcessEdge]]] = {}
    #: Оставшиеся ассоциации с данными — без наконечника, как в draw.io.
    #: Пунктир к текстовому примечанию сохраняет направление как было.
    directionless: Set[str] = set()
    for edge in association_edges:
        s_type = node_type_of.get(edge.sourceId or '')
        t_type = node_type_of.get(edge.targetId or '')
        if s_type in _data_types and t_type in TASK_NODE_TYPES:
            data_links.setdefault(edge.targetId, []).append(('in', edge))
        elif t_type in _data_types and s_type in TASK_NODE_TYPES:
            data_links.setdefault(edge.sourceId, []).append(('out', edge))
        elif s_type in _data_types or t_type in _data_types:
            directionless.add(edge.id)
    nested_links = {edge.id for links in data_links.values() for _way, edge in links}

    effective_type = normalize_event_types(flow_nodes, sequence_edges)

    id_of: Dict[str, str] = {}
    for n in flow_nodes:
        id_of[n.id] = _safe_id(n.id, 'Node', used, taken)
    for n in artifact_nodes:
        id_of[n.id] = _safe_id(n.id, 'Artifact', used, taken)
    for lane in lanes:
        id_of[lane.id] = _safe_id(lane.id, 'Lane', used, taken)
    external_process_of: Dict[str, str] = {}
    for lane in external_lanes:
        id_of[lane.id] = _safe_id(f'Participant_{lane.id}', 'Participant', used, taken)
        external_process_of[lane.id] = _safe_id(
            f'Process_{lane.id}', 'Process', used, taken
        )
    for edge in edges:
        id_of[edge.id] = _safe_id(edge.id, 'Flow', used, taken)
    for edge in message_flows:
        id_of[edge.id] = _safe_id(edge.id, 'MessageFlow', used, taken)

    markers = duration_markers(flow_nodes, id_of, used, taken, all_nodes)
    marker_of = {m.node.id: m for m in markers}

    incoming_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    outgoing_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    for edge in sequence_edges:
        if edge.targetId in incoming_by_node:
            incoming_by_node[edge.targetId].append(edge.id)
        if edge.sourceId in outgoing_by_node:
            outgoing_by_node[edge.sourceId].append(edge.id)

    use_collab = bool(lanes) or bool(external_lanes)
    plane_ref = collab_id if use_collab else proc_id
    process_name = escape_xml(process.passport.name or process.name or 'Business process')
    comment = process_name.replace('--', '—')

    xml: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        '  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
        '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
        '  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
        f'  id="{escape_xml(def_id)}"',
        '  targetNamespace="http://bpmn.io/schema/bpmn"',
        '  exporter="SQB Process Hub"',
        '  exporterVersion="2.0">',
        '',
        f'  <!-- Process map: {comment} -->',
    ]

    if use_collab:
        # ── Пул банка идёт без имени ────────────────────────────────────────
        #
        # Импортёр печатает имя пула повёрнутым в полосе заголовка слева, и эта
        # полоса стоит ЛЕВЕЕ полос дорожек: на карте получалось два столбца
        # подписей — имя процесса в одном, названия подразделений в другом,
        # сдвинутые вправо. В draw.io столбец один: заголовок карты аналитик
        # пишет плашкой сверху, а слева стоят только подразделения.
        #
        # Само название процесса не теряется: оно остаётся в ``bpmn:process``,
        # оттуда его и читает обратный разбор.
        xml.append(f'  <bpmn:collaboration id="{escape_xml(collab_id)}">')
        xml.append(
            f'    <bpmn:participant id="{escape_xml(participant_id)}" '
            f'name="" processRef="{escape_xml(proc_id)}" />'
        )
        # Внешний участник ссылается на собственный пустой процесс. Без
        # processRef импортёр считает пул свёрнутым и печатает имя по центру
        # полосы: на карте шириной 4620 px подпись уезжала за экран, и строка
        # выглядела безымянной. С процессом пул раскрыт, и имя стоит в шапке
        # слева — там же, где имена дорожек.
        for lane in external_lanes:
            shown = band_label(lane.name, lane.geometry.height)
            head = (
                f'    <bpmn:participant id="{escape_xml(id_of[lane.id])}" '
                f'name="{escape_xml(shown)}" '
                f'processRef="{escape_xml(external_process_of[lane.id])}"'
            )
            if shown == (lane.name or '').strip():
                xml.append(head + ' />')
            else:
                # Подпись обрезана по ширине полосы заголовка; полное название
                # кладём в документацию — иначе при обратном чтении карта
                # теряет имя внешнего участника, оставляя многоточие.
                xml.append(head + '>')
                xml.append(
                    f'      <bpmn:documentation>{escape_xml(lane.name)}</bpmn:documentation>')
                xml.append('    </bpmn:participant>')
        # Порядок детей collaboration задан схемой BPMN 2.0: участники, потоки
        # сообщений, и только потом артефакты. PIX разбирает файл строго по
        # схеме: ассоциация, вставшая между потоками сообщений, валит импорт
        # целиком — «Файл не является диаграммой BPMN 2.0» (проба на
        # PIX банка, PIX Процессы 2.3.2). Поэтому ассоциации — отдельным
        # проходом после всех потоков.
        for edge in message_flows:
            if edge.id in contact_associations:
                continue
            name = (edge.name or edge.condition or '').strip()
            name_attr = f' name="{escape_xml(name)}"' if name else ''
            xml.append(
                f'    <bpmn:messageFlow id="{escape_xml(id_of[edge.id])}"{name_attr}'
                f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
                f' targetRef="{escape_xml(id_of[edge.targetId])}" />'
            )
        for edge in message_flows:
            if edge.id not in contact_associations:
                continue
            xml.append(
                f'    <bpmn:association id="{escape_xml(id_of[edge.id])}"'
                f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
                f' targetRef="{escape_xml(id_of[edge.targetId])}"'
                ' associationDirection="One" />'
            )
        xml.append('  </bpmn:collaboration>')
        xml.append('')

    xml.append(
        # processType и isExecutable проставлены явно: значение по умолчанию
        # («None») стандарт разрешает опустить, но профиль выгрузки для PIX
        # требует обоих атрибутов в тексте файла. Основной процесс — Private:
        # это внутренняя оркестровка банка, а не публичный интерфейс.
        f'  <bpmn:process id="{escape_xml(proc_id)}" name="{process_name}"'
        f' processType="Private" isExecutable="true">'
    )

    # ── 1. laneSet (только узлы потока) ─────────────────────────────────────
    if lanes:
        xml.append(f'    <bpmn:laneSet id="{escape_xml(lane_set_id)}">')
        for lane in lanes:
            shown = band_label(lane.name, lane.geometry.height)
            xml.append(
                f'      <bpmn:lane id="{escape_xml(id_of[lane.id])}" name="{escape_xml(shown)}">'
            )
            if shown != (lane.name or '').strip():
                xml.append(
                    f'        <bpmn:documentation>{escape_xml(lane.name)}</bpmn:documentation>')
            for child in flow_nodes:
                if child.laneId != lane.id:
                    continue
                xml.append(
                    f'        <bpmn:flowNodeRef>{escape_xml(id_of[child.id])}</bpmn:flowNodeRef>'
                )
                # Значок длительности — тоже узел потока: дорожка без
                # ссылки на него импортируется без часов у своих шагов.
                marker = marker_of.get(child.id)
                if marker is not None:
                    xml.append(
                        '        <bpmn:flowNodeRef>'
                        f'{escape_xml(marker.marker_id)}</bpmn:flowNodeRef>'
                    )
            xml.append('      </bpmn:lane>')
        xml.append('    </bpmn:laneSet>')

    # ── 2. Узлы потока ──────────────────────────────────────────────────────
    for node in flow_nodes:
        nid = escape_xml(id_of[node.id])
        kind = effective_type[node.id]
        tag = _node_tag(node, kind)
        name_attr = f' name="{escape_xml(map_label(node))}"'
        extras: List[str] = []
        # Свёрнутый подпроцесс, за которым стоит отдельная страница файла, — по
        # спецификации не `subProcess`: у того содержимое лежит внутри него же.
        # Вызов чужого процесса — это `callActivity`.
        called = called_process_of.get(node.calledSubProcessId or '')
        if kind == 'subProcess' and called:
            tag = 'bpmn:callActivity'
            extras.append(f' calledElement="{escape_xml(called)}"')
        if kind in _GATEWAY_TYPES:
            if len(outgoing_by_node.get(node.id, [])) > 1:
                extras.append(' gatewayDirection="Diverging"')
            elif len(incoming_by_node.get(node.id, [])) > 1:
                extras.append(' gatewayDirection="Converging"')

        children: List[str] = []
        doc = _node_documentation(node)
        if doc:
            children.append(f'      <bpmn:documentation>{escape_xml(doc)}</bpmn:documentation>')
        for edge_id in incoming_by_node.get(node.id, []):
            children.append(f'      <bpmn:incoming>{escape_xml(id_of[edge_id])}</bpmn:incoming>')
        for edge_id in outgoing_by_node.get(node.id, []):
            children.append(f'      <bpmn:outgoing>{escape_xml(id_of[edge_id])}</bpmn:outgoing>')
        if kind == 'intermediateTimerEvent' or (kind == 'startEvent' and is_timer_event(node)):
            children.append('      <bpmn:timerEventDefinition>')
            children.append(
                '        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">'
                f'{iso_duration(node.slaMinutes)}</bpmn:timeDuration>'
            )
            children.append('      </bpmn:timerEventDefinition>')
        elif kind == 'intermediateMessageEvent':
            children.append('      <bpmn:messageEventDefinition />')

        links = data_links.get(node.id, [])
        inputs = [edge for way, edge in links if way == 'in']
        if inputs:
            # Приёмник входной связи — свойство шага: bpmn.io заводит под это
            # один служебный ``property`` на шаг, так же и здесь.
            holder = escape_xml(_safe_id(f'Property_{node.id}', 'Property', used, taken))
            children.append(f'      <bpmn:property id="{holder}" name="__targetRef_placeholder" />')
            for edge in inputs:
                children.append(f'      <bpmn:dataInputAssociation id="{escape_xml(id_of[edge.id])}">')
                children.append(f'        <bpmn:sourceRef>{escape_xml(id_of[edge.sourceId])}</bpmn:sourceRef>')
                children.append(f'        <bpmn:targetRef>{holder}</bpmn:targetRef>')
                children.append('      </bpmn:dataInputAssociation>')
        for way, edge in links:
            if way == 'out':
                children.append(f'      <bpmn:dataOutputAssociation id="{escape_xml(id_of[edge.id])}">')
                children.append(f'        <bpmn:targetRef>{escape_xml(id_of[edge.targetId])}</bpmn:targetRef>')
                children.append('      </bpmn:dataOutputAssociation>')

        extra = ''.join(extras)
        if children:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra}>')
            xml.extend(children)
            xml.append(f'    </{tag}>')
        else:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra} />')

        marker = marker_of.get(node.id)
        if marker is not None:
            xml.extend(_duration_marker_xml(marker, nid))

    # ── 3. Артефакты-элементы потока: хранилища и документы ─────────────────
    for node in artifact_nodes:
        if node.type == 'textAnnotation':
            continue
        tag = _node_tag(node, node.type)
        xml.append(
            f'    <{tag} id="{escape_xml(id_of[node.id])}" name="{escape_xml(map_label(node))}" />'
        )

    # ── 4. Переходы ─────────────────────────────────────────────────────────
    for edge in sequence_edges:
        eid = escape_xml(id_of[edge.id])
        name = edge.name or edge.condition or ''
        name_attr = f' name="{escape_xml(name)}"' if name else ''
        src_attr = f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
        tgt_attr = f' targetRef="{escape_xml(id_of[edge.targetId])}"'
        expr = (edge.condition or edge.name or '').strip()
        if expr:
            xml.append(f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr}>')
            xml.append(
                '      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
                f'{escape_xml(expr)}</bpmn:conditionExpression>'
            )
            xml.append('    </bpmn:sequenceFlow>')
        else:
            xml.append(f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr} />')

    # ── 5. Артефакты по XSD идут последними: примечания и ассоциации ────────
    for node in artifact_nodes:
        if node.type != 'textAnnotation' or node.id in participant_notes:
            continue
        xml.append(f'    <bpmn:textAnnotation id="{escape_xml(id_of[node.id])}">')
        xml.append(f'      <bpmn:text>{escape_xml(map_label(node))}</bpmn:text>')
        xml.append('    </bpmn:textAnnotation>')

    for edge in association_edges:
        if edge.id in nested_links:
            continue
        # ``None`` — единственное значение, при котором импортёр рисует
        # пунктир без наконечника; связь с хранилищем идёт только так.
        direction = 'None' if edge.id in directionless else 'One'
        xml.append(
            f'    <bpmn:association id="{escape_xml(id_of[edge.id])}"'
            f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
            f' targetRef="{escape_xml(id_of[edge.targetId])}"'
            f' associationDirection="{direction}" />'
        )

    xml.append('  </bpmn:process>')
    xml.append('')

    for lane in external_lanes:
        head = (
            # Внешняя сторона — «чёрный ящик»: поведения у неё в файле нет,
            # видна только граница взаимодействия, поэтому Public.
            f'  <bpmn:process id="{escape_xml(external_process_of[lane.id])}" '
            f'name="{escape_xml(lane.name)}" processType="Public" isExecutable="false"'
        )
        notes = [n for n in artifact_nodes if participant_notes.get(n.id) == lane.id]
        if not notes:
            xml.append(head + ' />')
            continue
        xml.append(head + '>')
        for node in notes:
            xml.append(f'    <bpmn:textAnnotation id="{escape_xml(id_of[node.id])}">')
            xml.append(f'      <bpmn:text>{escape_xml(map_label(node))}</bpmn:text>')
            xml.append('    </bpmn:textAnnotation>')
        xml.append('  </bpmn:process>')
    if external_lanes:
        xml.append('')

    # ── Диаграмма ───────────────────────────────────────────────────────────
    xml.append(f'  <bpmndi:BPMNDiagram id="{escape_xml(diag_id)}">')
    xml.append(
        f'    <bpmndi:BPMNPlane id="{escape_xml(plane_id)}" bpmnElement="{escape_xml(plane_ref)}">'
    )

    # Линии и подписи считаются одним расчётом — тем же, которым их рисует
    # холст платформы (см. ``layout_diagram``).
    layout = layout_diagram(all_nodes, lanes, external_lanes, edges, message_flows, markers, use_collab)
    px, py, pw, ph = layout.pool

    if use_collab:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(participant_id)}_di" '
            f'bpmnElement="{escape_xml(participant_id)}" isHorizontal="true">'
        )
        xml.append(f'        <dc:Bounds x="{px}" y="{py}" width="{pw}" height="{ph}" />')
        xml.append('      </bpmndi:BPMNShape>')
        for lane in external_lanes:
            xml.append(
                f'      <bpmndi:BPMNShape id="{escape_xml(id_of[lane.id])}_di" '
                f'bpmnElement="{escape_xml(id_of[lane.id])}" isHorizontal="true">'
            )
            xml.append(
                f'        <dc:Bounds x="{lane.geometry.x}" y="{lane.geometry.y}" '
                f'width="{lane.geometry.width}" height="{lane.geometry.height}" />'
            )
            xml.append('      </bpmndi:BPMNShape>')

    for lane in lanes:
        # Полосы обязаны тайлиться внутри пула: иначе импортёр рисует их
        # уступами, а часть карты оказывается за пределами дорожек. По X и
        # ширине полоса притягивается к пулу, по Y и высоте берётся из стопки,
        # уложенной в ``map_layout.stack_lanes``, — там они уже сплошные.
        #
        # По X дорожка занимает пул целиком, вместе с его полосой заголовка:
        # имени у пула нет, печатать в ней нечего, а отступ в 30 px сдвигал бы
        # названия подразделений вправо от полос внешних участников — на карте
        # выходило два столбца подписей вместо одного.
        lane_x = px if use_collab else lane.geometry.x
        lane_w = max(pw, 80) if use_collab else lane.geometry.width
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[lane.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[lane.id])}" isHorizontal="true">'
        )
        xml.append(
            f'        <dc:Bounds x="{lane_x}" y="{lane.geometry.y}" '
            f'width="{lane_w}" height="{lane.geometry.height}" />'
        )
        xml.append('      </bpmndi:BPMNShape>')

    routes = layout.routes
    edge_label_of = layout.edge_labels
    node_label_of = layout.node_labels
    marker_label_of = layout.marker_labels

    def _label_xml(box: Tuple[int, int, int, int], indent: str) -> List[str]:
        lx, ly, lw, lh = box
        return [
            f'{indent}<bpmndi:BPMNLabel>',
            f'{indent}  <dc:Bounds x="{lx}" y="{ly}" width="{lw}" height="{lh}" />',
            f'{indent}</bpmndi:BPMNLabel>',
        ]

    for node in all_nodes:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[node.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[node.id])}">'
        )
        xml.append(
            f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" '
            f'width="{node.geometry.width}" height="{node.geometry.height}" />'
        )
        # Подпись события, шлюза и артефакта импортёр рисует вне фигуры и
        # переносит по рамке в 90 px. Без явных границ длинное имя шлюза
        # превращается в столбец, накрывающий соседние шаги и подписи связей.
        if node.id in node_label_of:
            xml.extend(_label_xml(node_label_of[node.id], '        '))
        xml.append('      </bpmndi:BPMNShape>')

        marker = marker_of.get(node.id)
        if marker is not None:
            mx, my, mw, mh = marker_box(marker)
            xml.append(
                f'      <bpmndi:BPMNShape id="{escape_xml(marker.marker_id)}_di" '
                f'bpmnElement="{escape_xml(marker.marker_id)}">'
            )
            xml.append(f'        <dc:Bounds x="{mx}" y="{my}" width="{mw}" height="{mh}" />')
            xml.extend(_label_xml(marker_label_of[marker.marker_id], '        '))
            xml.append('      </bpmndi:BPMNShape>')

    for edge, route in routes:
        xml.append(
            f'      <bpmndi:BPMNEdge id="{escape_xml(id_of[edge.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[edge.id])}">'
        )
        for x, y in route:
            xml.append(f'        <di:waypoint x="{x}" y="{y}" />')
        if edge.id in edge_label_of:
            xml.extend(_label_xml(edge_label_of[edge.id], '        '))
        xml.append('      </bpmndi:BPMNEdge>')

    xml.append('    </bpmndi:BPMNPlane>')
    xml.append('  </bpmndi:BPMNDiagram>')
    xml.append('</bpmn:definitions>')
    return _merge_sub_pages('\n'.join(xml), list(process.subProcesses or []))


def sub_page_process_id(index: int) -> str:
    """Идентификатор процесса страницы-подпроцесса в общей выгрузке.

    Считается по номеру страницы, а не по её паспорту, чтобы основная карта
    могла сослаться на него из ``calledElement`` ещё до того, как страница
    будет собрана.
    """
    return f'Process_PAGE{index}'


def prefixed_page(page: BusinessProcess, index: int) -> BusinessProcess:
    """Копия страницы с идентификаторами, не пересекающимися с другими.

    Шаг, скопированный аналитиком с одной страницы на другую, сохраняет в
    draw.io СВОЙ идентификатор: `lq9nNoXpgytnnWjg0dKN-141` есть и на «Копия
    SOTIB OLISH», и на «JAMI». В одном файле выгрузки два элемента с одним
    `id` — уже не BPMN, поэтому перед сборкой каждой странице выдаётся свой
    префикс.
    """
    copy = page.model_copy(deep=True)
    prefix = f'p{index}_'
    for shape in [*copy.nodes, *copy.lanes]:
        shape.id = prefix + shape.id
        if shape.laneId:
            shape.laneId = prefix + shape.laneId
    for edge in copy.edges:
        edge.id = prefix + edge.id
        if edge.sourceId:
            edge.sourceId = prefix + edge.sourceId
        if edge.targetId:
            edge.targetId = prefix + edge.targetId
    # Паспорт задаёт идентификатор процесса — подменяем его на предсказуемый.
    copy.passport.code = f'PAGE{index}'
    copy.subProcesses = []
    return copy


#: Элементы, которые страница-подпроцесс приносит в общий ``<bpmn:definitions>``.
_MERGED_TOP_LEVEL = ('bpmn:collaboration', 'bpmn:process', 'bpmndi:BPMNDiagram')


def _definition_children(xml: str) -> List[str]:
    """Верхнеуровневые блоки собранного документа — как есть, текстом.

    Разбирать и собирать XML заново нельзя: выгрузку читают и глазами, и
    построчными проверками, а повторная сериализация переставила бы атрибуты и
    съела бы отступы. Документ мы печатаем сами, поэтому границы блоков здесь
    известны точно: открывающий тег занимает одну строку, закрывающий стоит на
    том же отступе.
    """
    out: List[str] = []
    lines = xml.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        tag = next((t for t in _MERGED_TOP_LEVEL if stripped.startswith(f'<{t}')), None)
        if tag is None:
            index += 1
            continue
        block = [lines[index]]
        if not stripped.endswith('/>'):
            closing = f'</{tag}>'
            while index + 1 < len(lines) and lines[index].strip() != closing:
                index += 1
                block.append(lines[index])
        out.append('\n'.join(block))
        index += 1
    return out


def _merge_sub_pages(main_xml: str, pages: List[BusinessProcess]) -> str:
    """Дописывает в выгрузку процессы и схемы остальных страниц файла.

    Каждая страница собирается тем же генератором, что и основная карта, и её
    процесс со схемой переезжает в общий ``<bpmn:definitions>``. Спецификация
    это разрешает прямо: определений процессов в файле может быть сколько
    угодно, а у каждого своя ``BPMNDiagram``. Импортёр показывает первую —
    основную карту, — а остальные открываются по выбору диаграммы.
    """
    if not pages:
        return main_xml
    blocks: List[str] = []
    for index, page in enumerate(pages, 1):
        blocks.extend(_definition_children(generate_bpmn_xml(prefixed_page(page, index))))
    if not blocks:
        return main_xml
    closing = '</bpmn:definitions>'
    head = main_xml.rstrip()
    diagram_at = head.find('\n  <bpmndi:BPMNDiagram')
    if not head.endswith(closing) or diagram_at < 0:
        return main_xml
    # Схема BPMN 2.0 требует сперва все корневые элементы, потом все схемы.
    # Процессы страниц, дописанные после схемы основной карты, PIX не
    # принимает вовсе: «Файл не является диаграммой BPMN 2.0».
    roots = [b for b in blocks if not b.lstrip().startswith('<bpmndi:BPMNDiagram')]
    diagrams = [b for b in blocks if b.lstrip().startswith('<bpmndi:BPMNDiagram')]
    return (head[:diagram_at] + '\n' + '\n'.join(roots) + head[diagram_at:-len(closing)]
            + '\n'.join(diagrams) + '\n' + closing)
