"""PIX Process Studio native package (.pmm = ZIP of three XML parts).

Структура пакета воспроизводит выгрузку самой PIX Процессной студии:

    main.xml               — манифест частей (<Types><Override .../></Types>)
    pm/configuration.xml   — каталог свойств и нотаций студии
    pm/maps/<slug>.xml     — сама карта (<Map> с <node> и <connector>)

Ключевые соглашения формата, сверенные с выгрузкой самой студии
(``tests/fixtures/sap.pmm`` — карта, сделанная и сохранённая в PIX):

* узлы внутри дорожки (`horizontalRoad`) позиционируются ОТНОСИТЕЛЬНО дорожки,
  сама дорожка — в абсолютных координатах карты;
* подпись связи хранится в атрибуте ``Text``, а не ``label``
  (``label`` студия игнорирует — подписи шлюзов теряются);
* список ``waypoint`` — это ПОЛНАЯ ломаная, включая точки на границе исходного
  и целевого узла, а не только промежуточные изломы; ломаную задаём для каждой
  связи — без неё студия трассирует сама и на плотной карте кладёт линии
  поверх соседних;
* ``sourcePoint``/``targetPoint`` — необязательные индексы якорей: задаём их
  для тех граней, чей номер эталон называет однозначно, для остальных
  опускаем, и студия выбирает точку примыкания сама (в своей выгрузке она
  опускает ``sourcePoint`` у 30 связей из 50);
* стиль линии — атрибут ``lineStyle`` у самой связи, а не дочерний элемент:
  дочернего ``<lineStyle>`` в выгрузке студии нет ни разу;
* маркеры концов берутся парой к стилю линии (см. ``_line_decoration``):
  незнакомый маркер студия отбрасывает вместе со связью.

В отличие от BPMN-выгрузки, здесь НЕ применяется нормализация степеней
событий: ``.pmm`` — это рисунок карты, и узел должен выглядеть так, как его
нарисовал аналитик. Валидную модель даёт экспорт в ``.bpmn``.
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from app.models.process import (
    GATEWAY_NODE_TYPES,
    TASK_NODE_TYPES,
    BusinessProcess,
    Geometry,
    ProcessEdge,
    ProcessEdgePoint,
    ProcessNode,
)
from app.services.bpmn_exporter import (
    band_label,
    map_label,
    prefixed_page,
    step_duration_text,
)
from app.services.drawio_orthogonal import orthogonal_route, perimeter_kind
from app.services.map_layout import split_external_lanes
from app.services.layout import (
    style_map,
    text_width,
    wrapped_line_count,
)
from app.services.edge_routing import (
    MIN_SEGMENT,
    Anchors,
    Corridors,
    Obstacles,
    _collapse_collinear_runs,
    _drop_micro_jogs,
    _snap_to_pixel_grid,
    build_obstacles,
    drawio_elbow_route,
    message_flow_endpoints,
    orthogonal_waypoints,
    point_stub,
    spread_dock_anchors,
)

_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_NS_XSD = 'http://www.w3.org/2001/XMLSchema'
_PIX_NS = uuid.UUID('8b2e0c5a-4d71-4f3a-9c1e-6a7f0d2b9e11')

_CONFIGURATION_PATH = Path(__file__).resolve().parent.parent / 'resources' / 'pix_configuration.xml'

#: Элемент нотации, по которому опознаётся набор BPMN в каталоге студии.
_BPMN_PROBE_ELEMENT = 'gateway_xor'
#: Куда падает тип, которого в каталоге студии не оказалось.
_FALLBACK_ELEMENT = 'task'
#: Как саму нотацию подписывает студия в атрибуте ``<Map notation="…">``.
#: В каталоге она объявлена как ``BPMN``, но в собственной выгрузке студии
#: (``tests/fixtures/sap.pmm``) стоит строчное ``bpmn`` — пишем ровно так же,
#: чтобы не расходиться с эталоном на первом же атрибуте карты.
_MAP_NOTATION = 'bpmn'


@lru_cache(maxsize=1)
def bpmn_notation() -> Tuple[str, frozenset]:
    """Каноническое имя BPMN-нотации в каталоге студии и её пригодные элементы.

    Имя берём из самого каталога, а не пишем константой: по нему ищутся
    типы фигур, и незнакомый тип валит импорт целиком («Notation element not
    found (Parameter 'type')»). В карту, однако, уезжает не оно, а
    ``_MAP_NOTATION``: регистр имени студия не различает, а пишет строчными.

    ПОЧЕМУ НЕ ВСЕ ЭЛЕМЕНТЫ ПОДРЯД. Пригодным считается только тот, у которого в
    каталоге проставлена категория (``type``): «Задачи», «Шлюзы», «События»…
    В нотации BPMN её нет ровно у одного элемента из 91 — ``input``
    («Текст»). Студия, встретив такую фигуру, идёт за её категорией, не
    находит и падает с сообщением, где параметр так и назван — ``'type'``.
    Отсюда и правило: имени в каталоге мало, нужна категория.
    """
    root = ET.fromstring(_CONFIGURATION_PATH.read_text(encoding='utf-8'))
    for notation in root.iter('notation'):
        names = {e.get('name') for e in notation.findall('element') if e.get('name')}
        if _BPMN_PROBE_ELEMENT not in names:
            continue
        usable = {
            e.get('name') for e in notation.findall('element')
            if e.get('name') and e.get('type')
        }
        return notation.get('name') or 'BPMN', frozenset(usable)
    raise ValueError('В каталоге PIX нет нотации BPMN')


def notation_categories(config: ET.Element, notation: str) -> Dict[str, str]:
    """Категория каждого элемента нотации («Задачи», «Шлюзы», «Участники»…).

    Каталог студии делит элементы на группы, и по группе видно, чему положено
    содержать вложенные фигуры: дорожка и пул — «Участники».
    """
    return {
        e.get('name'): e.get('type') or ''
        for n in config.iter('notation') if n.get('name') == notation
        for e in n.findall('element') if e.get('name')
    }


def pix_element(kind: str) -> str:
    """Тип фигуры, который студия точно знает и умеет разложить по категориям.

    Незнакомый тип валит импорт всего пакета, поэтому лучше отдать обычную
    задачу: карта откроется, а о подмене аналитик уже предупреждён отчётом
    о качестве импорта. Тип без категории в каталоге (см. ``bpmn_notation``)
    незнакомому равносилен — студия спотыкается на нём так же.
    """
    return kind if kind in bpmn_notation()[1] else _FALLBACK_ELEMENT

#: Ширина заголовочной плашки карты; в эталоне PIX — 1988 px.
_TITLE_POOL_WIDTH = 1988
_TITLE_POOL_HEIGHT = 90

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h', 'і': 'i', 'ї': 'i', 'є': 'e',
}


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


def _pix_id(raw: str) -> str:
    return str(uuid.uuid5(_PIX_NS, raw or 'node'))


def transliterate(text: str) -> str:
    """Кириллица -> латиница, чтобы имя карты оставалось читаемым в студии.

    Имена файлов из Telegram/macOS приходят в разложенном виде (NFD): «ў» —
    это «у» + комбинирующая бреве. Без нормализации диакритика превращалась бы
    в подчёркивание посреди слова.
    """
    text = unicodedata.normalize('NFC', text or '')
    out: List[str] = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        lower = ch.lower()
        if lower in _TRANSLIT:
            mapped = _TRANSLIT[lower]
            out.append(mapped.upper() if ch.isupper() and mapped else mapped)
        else:
            out.append(ch)
    return ''.join(out)


def map_slug(process: BusinessProcess) -> str:
    """Имя карты: человекочитаемое имя процесса, а не служебный код паспорта.

    Имя части в ZIP и атрибут ``Map name`` совпадают — как в выгрузке PIX.
    """
    for candidate in (process.passport.name, process.name, process.passport.code):
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', transliterate(candidate or '')).strip('_')
        slug = re.sub(r'_{2,}', '_', slug)[:60].strip('_')
        if len(slug) >= 3:
            return slug
    return 'map'


def pix_type(node: ProcessNode) -> str:
    """Тип узла в словаре нотации BPMN Процессной студии (pm/configuration.xml).

    Результат обязательно сверяется с каталогом: студия не открывает пакет
    целиком, если встретит хоть один тип, которого в её нотации нет.
    """
    return pix_element(_pix_type_raw(node))


def _pix_type_raw(node: ProcessNode) -> str:
    style = (node.style or '').lower()
    kind = node.type

    if kind == 'startEvent':
        if 'symbol=timer' in style:
            return 'start_event_timer'
        if 'symbol=message' in style:
            return 'start_event_message'
        return 'start_event_none'
    if kind == 'endEvent':
        if 'terminate' in style:
            return 'end_event_terminate'
        if 'symbol=message' in style:
            return 'end_event_message'
        return 'end_event_none'
    if kind == 'intermediateTimerEvent':
        return 'intermediate_event_catch_timer'
    if kind == 'intermediateMessageEvent':
        return 'intermediate_event_catch_message'
    if kind == 'exclusiveGateway':
        return 'gateway_xor'
    if kind == 'parallelGateway':
        return 'gateway_parallel'
    if kind == 'inclusiveGateway':
        return 'gateway_or'
    if kind == 'complexGateway':
        return 'gateway_complex'
    if kind == 'subProcess':
        return 'sub_process'
    if kind == 'dataStore':
        return 'dataStorage'
    if kind == 'dataObject':
        return 'dataObject'
    if kind == 'textAnnotation':
        # Точного двойника примечанию в нотации студии нет: элемента-выноски
        # там не объявлено вовсе. Элемент «Текст» (``input``) подошёл бы по
        # смыслу, но в нотации BPMN этой студии его нет («Notation element not
        # found», PIX банка).
        #
        # Текст на картах PIX пишут «Пустым пулом» — рамкой с надписью: так в
        # ``tests/fixtures/sap.pmm`` подписаны и заголовок, и сноска, и пунктир
        # от такой рамки к шагу студия принимает. «Объект данных», которым
        # примечание выгружалось раньше, она рисует листом с загнутым углом, и
        # абзац текста превращался в огромный лист.
        return 'emptyPool'
    # Все действия — плоская «Задача». Студия рисует у пользовательской задачи
    # человечка, у сервисной — шестерёнку, и рисует их В ЛЕВОМ ВЕРХНЕМ УГЛУ,
    # прямо поверх первых букв подписи: «1.Eksport akkreditivi…» превращается
    # в «👤Eksport akkreditivi…». В draw.io, который для нас эталон вида карты,
    # у шагов иконок нет вовсе — только рамка с текстом.
    #
    # Различие «ручная / роботизированная» при этом не теряется: оно едет в
    # регламент и в выгрузку BPMN, где тип шага задан элементом, а не значком.
    return 'task'


# ── Время шага в свойствах фигуры ───────────────────────────────────────────
#
# Студия хранит время не подписью на карте, а свойством фигуры: дочерний узел
# ``<Properties>`` внутри ``<node>``, значения — в формате .NET ``TimeSpan``.
# Пока платформа его не писала, панель свойств шага показывала «000д 00ч 00м
# 00с» даже там, где на карте стояли часы с цифрой.

#: Множитель к минутам для каждой единицы, какой её пишут аналитики банка.
#: Языка три (русский, узбекский, английский), и сокращают все по-своему.
_TIME_UNITS: Tuple[Tuple[Tuple[str, ...], float], ...] = (
    (('d', 'day', 'days', 'дн', 'день', 'дня', 'дней', 'сут', 'сутки', 'kun'), 1440.0),
    (('h', 'hr', 'hrs', 'hour', 'hours', 'ч', 'час', 'часа', 'часов', 'soat'), 60.0),
    (('m', 'min', 'mins', 'minute', 'minutes',
      'м', 'мин', 'минут', 'минута', 'минуты', 'daq', 'daqiqa'), 1.0),
    (('s', 'sec', 'secs', 'second', 'seconds', 'с', 'сек', 'секунд', 'soniya'), 1.0 / 60.0),
)

#: Число с необязательной дробной частью и разделителем тысяч, следом —
#: необязательная единица. «15», «15m», «1.5h», «1 440 мин», «2 ч».
_TIME_TOKEN_RE = re.compile(
    r'(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?)\s*'
    r'([A-Za-zА-Яа-яЁё\u2018\u2019\'`]*)',
    re.IGNORECASE,
)


def _unit_factor(unit: str) -> Optional[float]:
    """Множитель единицы к минутам. Пустая единица — это минуты."""
    cleaned = unit.strip().strip('.').lower()
    if not cleaned:
        return 1.0
    for names, factor in _TIME_UNITS:
        if cleaned in names:
            return factor
    return None


def parse_duration_minutes(raw: Optional[str]) -> Optional[float]:
    """Длительность из произвольной строки в минутах, или ``None``.

    Понимает то, что аналитики действительно пишут в draw.io: голое число
    (это минуты), число с единицей на любом из трёх языков, дробное значение
    («1.5h», «1,5 ч») и разделитель тысяч («1 440 мин»). Составные записи
    вида «1 ч 30 мин» складываются.

    ``None`` возвращается на всём, что разобрать нельзя, — тег со значением
    «мусор» в файл уходить не должен.
    """
    if raw is None:
        return None
    text = str(raw).replace('\u00a0', ' ').replace('\u202f', ' ').strip()
    if not text:
        return None
    # Отрицательная длительность — это не «минус пять минут», а опечатка.
    # Знак стоит вне числа, и без этой проверки он просто отбрасывался.
    if '-' in text or '\u2212' in text:
        return None

    total = 0.0
    matched = False
    for match in _TIME_TOKEN_RE.finditer(text):
        number, unit = match.group(1), match.group(2)
        factor = _unit_factor(unit)
        if factor is None:
            # Единица есть, но незнакомая: это не длительность, а что-то
            # другое («3 shtuk»). Молча считать её минутами нельзя.
            return None
        value = float(number.replace(' ', '').replace(',', '.'))
        total += value * factor
        matched = True
    if not matched or total <= 0:
        return None
    return total


def minutes_to_timespan(minutes: Optional[float]) -> Optional[str]:
    """Минуты в .NET ``TimeSpan``, или ``None``, если времени нет.

    Формат «hh:mm:ss», а от суток и больше — «d.hh:mm:ss». Это не украшение:
    ``TimeSpan.Parse("24:00:00")`` в .NET падает, часы обязаны лежать в 0..23,
    а сутки выносятся отдельным полем через точку. Сейчас в картах банка
    максимум 960 минут, но правило должно быть верным и для более длинных.
    """
    if minutes is None:
        return None
    total_seconds = int(round(float(minutes) * 60))
    if total_seconds <= 0:
        return None
    days, rest = divmod(total_seconds, 86400)
    hours, rest = divmod(rest, 3600)
    mins, secs = divmod(rest, 60)
    stamp = f'{hours:02d}:{mins:02d}:{secs:02d}'
    return f'{days}.{stamp}' if days else stamp


def parse_time_to_timespan(raw: Optional[str]) -> Optional[str]:
    """Сырая строка из draw.io -> .NET ``TimeSpan``, или ``None``."""
    return minutes_to_timespan(parse_duration_minutes(raw))


#: Как свойства времени называются в файле карты.
#:
#: Имена — не наши, а из каталога студии ``pix_configuration.xml``: панель
#: свойств шага строится по нему, и ключ, которого в каталоге нет, приезжает
#: «теневым атрибутом» — файл открывается, значение хранится, а в интерфейсе
#: его не видно. Имён ``ProcessingTime`` / ``WaitingTime`` в каталоге нет ни
#: одного, поэтому написанное так время до панели не доходило.
#:
#: Шаблонов времени в каталоге три, и «Время процесса» из них ДВА — с одной
#: подписью, но разной ролью:
#:
#:     vremya_protsessa     Время процесса    обычный шаблон -> «Основные»
#:     system_process_time  Время процесса    defaultProperty -> «Системные»
#:     vremya_ozhidaniya    Время ожидания    обычный шаблон -> «Основные»
#:
#: Поле «Время процесса» в разделе «Системные» панели шага — это
#: ``system_process_time``. Формат сверен с выгрузкой самой PIX после того, как
#: время шагу проставили руками (PIX банка, 2026-09-22)::
#:
#:     <prop name="Время процесса" inner_name="system_process_time" value="7200">
#:       <PropertyId>…guid…</PropertyId>
#:     </prop>
#:
#: Значение — секунды. Прежний блок ``<Properties><Property …/>`` студия молча
#: отбрасывала, поэтому шаги и открывались с «000д 00ч 00м 00с». Свойство
#: доходит, только если в пакете лежит ТЕКУЩИЙ каталог студии
#: (``resources/pix_configuration.xml``): со старым каталогом на 21 шаблон
#: импорт терял время без единого слова.
#:
#: На картах уровней 1–6 к «Задаче» в нотации BPMN привязаны и обычные шаблоны
#: ``vremya_protsessa`` и ``vremya_ozhidaniya`` (раздел «Основные»), на уровнях
#: 7–8 — ни одного. Поэтому время шага пишется в оба «Времени процесса», а
#: ожидание — в ``vremya_ozhidaniya``: студия хранит их на любом уровне
#: (PIX банка, 2026-09-23: шаблон 9 пришёл со значениями 1800 и 2700), а
#: показывает там, где шаблон привязан.
#:
#: Шаблон студия ищет по ``name`` и берёт первый с таким именем. «Время
#: процесса» зовутся оба шаблона, и под этим именем значение попадало только в
#: системный — «Основные → Время процесса» оставалось пустым. Обычный шаблон
#: поэтому пишется под своим ключом (PIX банка, 2026-09-24, уровень 3, проба
#: P16: ``name="vremya_protsessa"`` дошло, «Время процесса» — нет).
_PROCESS_TIME_PROPERTIES = (('Время процесса', 'system_process_time'),
                            ('vremya_protsessa', 'vremya_protsessa'))
_WAIT_TIME_PROPERTY = ('Время ожидания', 'vremya_ozhidaniya')


def node_properties_xml(node: ProcessNode, indent: str) -> List[str]:
    """Свойства времени — у шага выполнение и ожидание, у таймера его время.

    Время выполнения пишется только шагу, у которого оно замерено в draw.io:
    подставленное импортом умолчание в панель свойств не идёт, иначе догадка
    выглядит замером. Ожидание импорт не подставляет никогда, поэтому пишется
    любое ненулевое.

    Таймер между шагами («60 min», «960 min») получает своё время в
    ``system_process_time``: в панели таймера студия показывает ровно это поле
    «Время процесса», и без значения там стояло «000д 00ч 00м 00с».
    Предупреждение «шаблон отвязан от таймера» студия давала со старым
    каталогом на 21 шаблон; с её текущим каталогом шаблон у таймера есть.

    Подпроцессу (``sub_process``) время не пишется: шаблон к нему не
    привязан, и импорт предупреждал «Шаблон свойств 'Время процесса' отвязан
    от 'sub_process'», отбрасывая значения (PIX банка, 2026-09-24, уровень 7).
    Его время на карте видно по часам.
    """
    values: List[Tuple[Tuple[str, str], int]] = []
    process_seconds = int(round(float(node.slaMinutes or 0) * 60)) if node.slaMeasured else 0
    if node.type == 'intermediateTimerEvent':
        if process_seconds > 0:
            values.append((_PROCESS_TIME_PROPERTIES[0], process_seconds))
    elif node.type not in TASK_NODE_TYPES or pix_type(node) == 'sub_process':
        return []
    else:
        if process_seconds > 0:
            values += [(template, process_seconds) for template in _PROCESS_TIME_PROPERTIES]
        wait_seconds = int(round(float(node.waitMinutes or 0) * 60))
        if wait_seconds > 0:
            values.append((_WAIT_TIME_PROPERTY, wait_seconds))
    lines: List[str] = []
    for (display, inner), seconds in values:
        # Идентификатор значения стабилен от выгрузки к выгрузке: один и тот
        # же шаг даёт один и тот же файл.
        property_id = uuid.uuid5(uuid.NAMESPACE_URL, f'pix-property:{node.id}:{inner}')
        lines += [
            f'{indent}  <prop name="{display}" inner_name="{inner}" value="{seconds}">',
            f'{indent}    <PropertyId>{property_id}</PropertyId>',
            f'{indent}  </prop>',
        ]
    return lines

#: Кегль подписи, которую студия печатает СНАРУЖИ фигуры.
_EXTERNAL_LABEL_FONT = 16.0
#: Шире этого студия переносит подпись по словам.
_EXTERNAL_LABEL_MAX_WIDTH = 180.0
#: Зазор между фигурой и её подписью, px.
_EXTERNAL_LABEL_GAP = 4.0
#: Сбоку от события студия центрирует подпись в коробке 100 px, отступив от
#: круга 4 px: центр текста — в 54 px от круга, и «60 min» стояло в 32 px
#: (PIX банка, 2026-09-25, проба P19). Короткую подпись подбиваем
#: неразрывными пробелами с дальней стороны (``side_label_text``), и видимый
#: текст встаёт в 6 px от круга.
_SIDE_LABEL_CENTRE = 54.0
_SIDE_LABEL_BOX = 100.0
_SIDE_LABEL_GAP = 6.0
#: Ширина неразрывного пробела в подписи студии при кегле 14 (проба P21).
_NBSP_WIDTH = 3.5
_EXTERNAL_LABEL_ASIDE = _SIDE_LABEL_GAP


def side_label_text(text: str, side: str) -> str:
    """Подпись события сбоку, подбитая пробелами так, чтобы стоять у круга.

    Студия центрирует её в коробке, и пробелы с дальней стороны сдвигают
    видимый текст к кругу. Пробелы неразрывные: обычный студия схлопнула бы, а
    по пробелу внутри «60 min» перенесла бы строку. Подпись, которой места уже
    не хватает, остаётся как есть.
    """
    if side not in ('Left', 'Right') or not text:
        return text
    width = text_width(text, 14.0)
    count = int((2 * (_SIDE_LABEL_CENTRE - _SIDE_LABEL_GAP) - width) // _NBSP_WIDTH)
    if count <= 0 or width + count * _NBSP_WIDTH > _SIDE_LABEL_BOX:
        return text
    body, pad = text.replace(' ', ' '), ' ' * count
    return pad + body if side == 'Left' else body + pad


def _external_label_box(
    box: Tuple[float, float, float, float],
    text: str,
    placement: str,
) -> Tuple[float, float, float, float]:
    """Куда ляжет подпись, напечатанная над фигурой, под ней или сбоку."""
    x, y, w, h = box
    width = min(max(text_width(text, _EXTERNAL_LABEL_FONT) + 8, 40.0),
                _EXTERNAL_LABEL_MAX_WIDTH)
    lines = wrapped_line_count(text, width, _EXTERNAL_LABEL_FONT)
    height = lines * _EXTERNAL_LABEL_FONT * 1.25 + 4
    left = x + w / 2 - width / 2
    if placement == 'Top':
        return (left, y - height - _EXTERNAL_LABEL_GAP, width, height)
    if placement == 'Left':
        return (x - width - _EXTERNAL_LABEL_ASIDE, y + h / 2 - height / 2, width, height)
    if placement == 'Right':
        return (x + w + _EXTERNAL_LABEL_ASIDE, y + h / 2 - height / 2, width, height)
    return (left, y + h + _EXTERNAL_LABEL_GAP, width, height)


def _boxes_touch(a, b) -> bool:
    return (min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > 2
            and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > 2)


def _route_crosses(route: Sequence[Tuple[float, float]], box: Tuple[float, float, float, float]) -> bool:
    """Проходит ли ортогональная ломаная сквозь рамку (не по её краю)."""
    x0, y0, x1, y1 = box[0], box[1], box[0] + box[2], box[1] + box[3]
    for (ax, ay), (bx, by) in zip(route, route[1:]):
        if abs(ay - by) < 0.5 and y0 < ay < y1 and min(ax, bx) < x1 and max(ax, bx) > x0:
            return True
        if abs(ax - bx) < 0.5 and x0 < ax < x1 and min(ay, by) < y1 and max(ay, by) > y0:
            return True
    return False


#: Куда уводится подпись события, если снизу её пересекает своя же линия.
_EVENT_ASIDE = ('Left', 'Right')


def event_label_sides(
    flow: Sequence[ProcessNode],
    placed: Dict[str, Tuple[int, int]],
    edges: Sequence[ProcessEdge],
) -> Dict[str, str]:
    """Сторона подписи каждого события ожидания; пустая строка — снизу.

    Сторону задал аналитик (``_event_label_placement``). Но в draw.io время
    под часами стоит прямо на уходящей вниз линии и читается только благодаря
    белой подложке (``labelBackgroundColor``); у подписи в студии подложки
    нет, и линия шла сквозь «120 min» — у 193 таймеров из 233. Такую подпись
    уводим вбок: влево, иначе вправо — туда, где нет ни фигур, ни линий.
    Чисто нигде — берём сторону с наименьшим числом помех, при равенстве
    ту, что нарисована.
    """
    by_id = {n.id: n for n in flow}
    boxes = {
        n.id: (float(x), float(y), float(n.geometry.width), float(n.geometry.height))
        for n in flow for x, y in [placed.get(n.id, (n.geometry.x, n.geometry.y))]
    }
    routes = [
        polyline(edge, by_id[edge.sourceId], by_id[edge.targetId], placed)
        for edge in edges if edge.sourceId in by_id and edge.targetId in by_id
    ]
    sides: Dict[str, str] = {}
    for node in flow:
        if node.type not in _EVENT_LABEL_TYPES:
            continue
        side, text = _event_label_placement(node), map_label(node)

        def hindrances(candidate: str) -> int:
            box = _external_label_box(boxes[node.id], text, candidate or 'Bottom')
            return (sum(1 for route in routes if _route_crosses(route, box))
                    + sum(1 for key, other in boxes.items() if key != node.id and _boxes_touch(box, other)))

        if not side and text and hindrances(''):
            side = min(('', *_EVENT_ASIDE), key=hindrances)
        sides[node.id] = side
    return sides


def choose_label_placements(
    flow: Sequence[ProcessNode],
    placed: Dict[str, Tuple[int, int]],
    event_sides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Сверху или снизу печатать подпись каждой фигуры.

    Подпись шлюза и имя базы данных студия рисует ВНЕ фигуры, и место для неё
    мы выбрать не можем — только сторону. Ставить всем «сверху» нельзя: два
    соседних ромба с длинными вопросами накладывают подписи друг на друга,
    ровно как на карте банка. В draw.io аналитик разводит их сам — один вопрос
    над ромбом, другой под ним.

    Считаем обе стороны и берём ту, где меньше столкновений: сначала с уже
    расставленными подписями, потом с фигурами. При равенстве — сверху, как в
    эталоне.
    """
    boxes: Dict[str, Tuple[float, float, float, float]] = {}
    for node in flow:
        x, y = placed.get(node.id, (node.geometry.x, node.geometry.y))
        boxes[node.id] = (float(x), float(y),
                          float(node.geometry.width), float(node.geometry.height))

    shapes = list(boxes.values())
    chosen: Dict[str, str] = {}
    # Сторону подписи таймера задал аналитик, и она уже занята: не зная её,
    # «EHA» над цилиндром ложилась на «960 min» слева от таймера (1.54).
    taken: List[Tuple[float, float, float, float]] = [
        _external_label_box(boxes[n.id], map_label(n),
                            (event_sides or {}).get(n.id, _event_label_placement(n)) or 'Bottom')
        for n in flow if n.type in _EVENT_LABEL_TYPES and map_label(n)
    ]
    # Сначала длинные подписи: им труднее найти свободную сторону.
    for node in sorted(flow, key=lambda n: -len(map_label(n))):
        if node.type not in _EXTERNAL_LABEL_TYPES:
            continue
        text = map_label(node)
        if not text:
            continue
        best_side, best_cost, best_box = 'Top', None, None
        for side in ('Top', 'Bottom'):
            candidate = _external_label_box(boxes[node.id], text, side)
            cost = (
                sum(1 for other in taken if _boxes_touch(candidate, other)),
                sum(1 for other in shapes if _boxes_touch(candidate, other)),
            )
            if best_cost is None or cost < best_cost:
                best_side, best_cost, best_box = side, cost, candidate
        chosen[node.id] = best_side
        taken.append(best_box)
    return chosen


#: Фигуры, которым сторону подписи выбираем перебором «сверху/снизу».
#:
#: Подпись промежуточного события студия тоже печатает снаружи, но сторону у
#: него не ищем — её задал аналитик, см. ``_event_label_placement``.
_EXTERNAL_LABEL_TYPES = (*GATEWAY_NODE_TYPES, 'dataStore', 'dataObject')


#: Куда студия кладёт подпись шлюза.
#:
#: В эталонной выгрузке студии стоит ``Left``, и платформа повторяла её. На
#: плотной карте банка это не работает: слева от ромба стоит предыдущий шаг, и
#: вопрос («Banknotalar muomalaga yaroqlimi») ложится прямо на его текст. В
#: draw.io аналитик ставит вопрос НАД ромбом — там свободно всегда, потому что
#: поток идёт по горизонтали.
#:
#: Оговорка честная: значение ``Top`` эталоном не подтверждено, в выгрузках
#: студии встречается только ``Left``. Проверяется пробой
#: ``test_08_pmm_label_placement_top.pmm``; если студия его не примет, здесь
#: достаточно вернуть ``Left``.
_GATEWAY_LABEL_PLACEMENT = 'Top'


#: События, подпись которых студия печатает снаружи круга.
_EVENT_LABEL_TYPES = ('intermediateTimerEvent', 'intermediateMessageEvent')


def _event_label_placement(node: ProcessNode) -> str:
    """С какой стороны круга печатать подпись события ожидания.

    Время в круг диаметром 48 px не влезает, и студия печатает его снаружи. В
    своём эталоне (``tests/fixtures/sap.pmm``) сторону она не задаёт вовсе —
    у ``intermediate_event_catch_timer`` стоит только ``label``. Под кругом
    время ставить нельзя: связь входит в часы сверху и выходит снизу, и
    подпись оказывается перечёркнутой линией.

    Сторону берём у аналитика, а не выбираем сами: на картах банка у 103
    таймеров из 127 стоит ``labelPosition=left``, у 10 — ``right``, ещё у трёх
    подпись поднята над кругом. Где он оставил её снизу, атрибут не пишем:
    снизу студия ставит подпись и без нас.

    Оговорка честная: эталоном подтверждено одно значение — ``Left``, и только
    у ромба. ``Top`` платформа уже пишет шлюзам, ``Bottom`` — хранилищам,
    ``Right`` здесь появляется впервые. Если студия не примет атрибут у
    события, достаточно вернуть отсюда пустую строку.
    """
    smap = style_map(node.style)
    horizontal = smap.get('labelposition', 'center')
    if horizontal == 'left':
        return 'Left'
    if horizontal == 'right':
        return 'Right'
    if smap.get('verticallabelposition', 'bottom') == 'top':
        return 'Top'
    return ''


def _node_extra(node: ProcessNode, placement: str = _GATEWAY_LABEL_PLACEMENT) -> str:
    if node.type in GATEWAY_NODE_TYPES:
        return f' labelPlacement="{placement}" font_size="16"'
    if node.type in _EVENT_LABEL_TYPES:
        # Сторона события приходит из ``event_label_sides``; пустая — снизу.
        return f' labelPlacement="{placement}"' if placement else ''
    # Имя системы аналитик пишет над цилиндром — «iABS», «EHA». Сбоку оно
    # налезает на соседний шаг, и на карте не понять, к чему относится.
    if node.type in ('dataStore', 'dataObject'):
        return f' labelPlacement="{placement}"'
    if node.type in TASK_NODE_TYPES:
        return f' font_size="{int(round(task_font_size(node)))}"'
    return ''


def _node_xml(
    node_type: str,
    nid: str,
    label: str,
    x: int,
    y: int,
    w: int,
    h: int,
    extra: str = '',
    fill: str = 'var(--bg-accent-node)',
    indent: str = '  ',
    children: Sequence[str] = (),
) -> str:
    head = (
        f'{indent}<node type="{escape_xml(node_type)}" id="{escape_xml(nid)}"'
        f' label="{escape_xml(label)}" number="0"'
        f' x="{int(x)}" y="{int(y)}" width="{int(max(w, 8))}" height="{int(max(h, 8))}"'
        f' fill_color="{escape_xml(fill)}"{extra}'
    )
    if not children:
        return head + ' />'
    # Фигура со свойствами перестаёт быть самозакрывающейся. Без детей тег
    # закрываем сразу — так пишет и сама студия.
    return '\n'.join([head + '>', *children, f'{indent}</node>'])


# ── Размеры фигур Процессной студии ─────────────────────────────────────────
#
# В выгрузке самой PIX (``tests/fixtures/sap.pmm``, 66 фигур) у каждого типа
# один размер: события 48 × 48, шлюзы 60 × 60, хранилище данных 62 × 56. Это
# размеры её палитры, а не шаблон: фигуру другого размера студия рисует в той
# рамке, которую мы объявили (см. ``drawio_frames``).
_EVENT_SIDE = 48
_DATA_STORAGE_SIZE = (62, 56)


#: Диаметр значка длительности на карте PIX, px.
#:
#: Тот же, что у остальных событий (``_EVENT_SIDE``), и это не косметика:
#: студия рисует событие своим шаблоном в 48 px независимо от того, какую
#: рамку мы объявили. Прежние 24 px значили, что она дорисовывает кружок вдвое
#: больше объявленного — и он наезжал на подпись шага.
_DURATION_SIDE = _EVENT_SIDE

#: Насколько центр значка отступает от правого края шага, px. Подобран так,
#: чтобы кружок в 48 px не вылезал за правую грань: 28 − 24 = 4 px запаса.
_DURATION_INSET = 28

#: Элемент значка длительности в каталоге студии.
#:
#: Раньше здесь стоял ``intermediate_event_catch_timer`` — «промежуточное
#: событие-таймер». Формально такой узел обязан стоять В потоке, между двумя
#: шагами, поэтому студия на каждом значке писала «У элемента отсутствует
#: входящий поток управления» и столько же раз — про исходящий. На карте из
#: 279 шагов это давало под шестьсот замечаний, за которыми не было видно
#: настоящих: список «Проблемы» упирался в «99+».
#:
#: Значок длительности потоком и не является: он помечает время шага, а не
#: ожидание между шагами. Ровно для этого в BPMN есть непрерывающее граничное
#: событие — оно живёт на границе активности и связей не требует. В выгрузке
#: BPMN платформа так его и пишет (``cancelActivity="false"``), а здесь
#: расходилась с собой. Теперь тип общий для обоих форматов.
_DURATION_ELEMENT = 'boundary_non_interrupting_event_timer'


def place_duration_badges(
    flow: Sequence[ProcessNode],
    placed: Dict[str, Tuple[int, int]],
    lane_of: Dict[str, ProcessNode],
) -> Dict[str, Tuple[int, int]]:
    """Левый верхний угол часов каждого шага, в абсолютных координатах карты.

    Считается один раз на карту и обязательно одинаково для рисования и для
    трассировки: пока препятствие знал только рисовальщик, линии шли прямо
    сквозь часы (из 90 пересечений с чужими фигурами 68 приходилось на них).

    Часы стоят ЦЕЛИКОМ под шагом, у правого угла: когда кружок сидел на грани,
    верхняя половина уходила под заливку шага (пересечений с линиями и фигурами
    было 1016, стало 283). Если под шагом уже что-то стоит — таймер между
    шагами соседних дорожек, цилиндр, чужие часы, — часы уходят вбок: на угол,
    правее шага, потом так же влево. Студия рисует их кружком в 48 px, и под
    шагом они накрывали таймер (1.54, шаги 42–43). Внутри дорожки часы не
    выходят за её край: вылезший узел студия рисует поверх соседней дорожки.
    """
    frames = {
        n.id: (*placed.get(n.id, (n.geometry.x, n.geometry.y)), n.geometry.width, n.geometry.height)
        for n in flow
    }
    side, half = _DURATION_SIDE, _DURATION_SIDE // 2

    def hits(box: Tuple[float, float, float, float], other: Tuple[float, float, float, float]) -> bool:
        return (min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0]) > _BADGE_TOUCH
                and min(box[1] + box[3], other[1] + other[3]) - max(box[1], other[1]) > _BADGE_TOUCH)

    out: Dict[str, Tuple[int, int]] = {}
    for node in flow:
        if node.type not in TASK_NODE_TYPES or not step_duration_text(node):
            continue
        sx, sy, sw, sh = frames[node.id]
        # Узкий шаг значком не разрезать пополам: у него часы встают по центру
        # нижней грани, у обычного — у правого нижнего угла.
        centres = [sx + max(sw - _DURATION_INSET, sw / 2), sx + sw, sx + sw + half + 4,
                   sx + _DURATION_INSET, sx, sx - half - 4]
        lane = lane_of.get(node.id)
        spots = []
        for cx in centres:
            mx, my = int(round(cx - half)), int(round(sy + sh))
            if lane is not None:
                lx, ly = lane.geometry.x, lane.geometry.y
                mx = max(lx, min(mx, lx + max(lane.geometry.width, 80) - side))
                my = max(ly, min(my, ly + max(lane.geometry.height, 80) - side))
            spots.append((mx, my))
        others = [f for nid, f in frames.items() if nid != node.id]
        others += [(x, y, side, side) for x, y in out.values()]
        out[node.id] = next(
            (spot for spot in spots if not any(hits((*spot, side, side), f) for f in others)),
            spots[0])
    return out


#: Наложение часов на фигуру меньше этого не считается помехой, px.
_BADGE_TOUCH = 2


def duration_badge_boxes(
    badges: Dict[str, Tuple[int, int]],
) -> List[Tuple[str, str, Tuple[float, float, float, float]]]:
    """Рамки часов для трассировки: ключ, шаг-хозяин, абсолютная рамка."""
    return [
        (f'duration:{node_id}', node_id,
         (float(mx), float(my), float(_DURATION_SIDE), float(_DURATION_SIDE)))
        for node_id, (mx, my) in badges.items()
    ]


def duration_node_xml(
    node: ProcessNode,
    badges: Dict[str, Tuple[int, int]],
    indent: str,
    lane: Optional[ProcessNode] = None,
) -> Optional[str]:
    """Часы со временем шага — непрерывающее граничное событие под ним.

    Длительность показывается так же, как её рисует аналитик в draw.io: часы у
    правого нижнего угла шага, время подписью под ними. Связей у значка нет и
    быть не должно: граничное событие помечает время самой активности, а не
    переход между активностями. В BPMN-выгрузке это же время едет тем же самым
    непрерывающим граничным таймером — форматы не должны расходиться.

    Место берётся из ``place_duration_badges`` (абсолютные координаты); внутри
    дорожки узел пишется относительно неё.
    """
    if node.type not in TASK_NODE_TYPES or node.id not in badges:
        return None
    text = step_duration_text(node)
    if not text:
        return None
    mx, my = badges[node.id]
    if lane is not None:
        mx, my = mx - lane.geometry.x, my - lane.geometry.y
    return _node_xml(
        pix_element(_DURATION_ELEMENT),
        _pix_id(f'duration:{node.id}'),
        text,
        mx,
        my,
        _DURATION_SIDE,
        _DURATION_SIDE,
        indent=indent,
    )


def drawio_frames(process: BusinessProcess) -> BusinessProcess:
    """Копия процесса с рамками и изломами фигур так, как их нарисовал аналитик.

    Импорт правит геометрию под холст платформы: расширяет шаг под подпись,
    отодвигает цилиндр, снимает излом у его пунктира. На двенадцати картах
    банка так менялось 93 % шагов (120 × 80 становился 210 × 83), и карта в
    PIX расходилась с draw.io: 40 % линий уходили от нарисованных больше чем на
    30 px. Эталон вида карты PIX — draw.io, поэтому выгрузка берёт исходные
    рамки (``drawioGeometry``), а подпись шага вмещает кеглем draw.io
    (``task_font_size``).

    Шлюзы и события тоже остаются в рамке draw.io, а не приводятся к палитре
    студии (60 и 48 px): ромб 50 × 50 и таймер 40 × 40 студия так и рисует, и
    линии к ним берёт как есть (PIX банка, 2026-09-24, проба P15). Приведение
    к 60 px сдвигало конец каждой линии к шлюзу на 5 px, и точно с draw.io
    совпадало 58 % линий вместо 91 %.
    """
    if not any(n.drawioGeometry for n in process.nodes) and not any(
            e.drawioPoints is not None for e in process.edges):
        return process
    updated = process.model_copy()
    updated.nodes = [
        n.model_copy(update={'geometry': n.drawioGeometry}) if n.drawioGeometry else n
        for n in process.nodes
    ]
    updated.edges = [
        e.model_copy(update={'points': e.drawioPoints}) if e.drawioPoints is not None else e
        for e in process.edges
    ]
    return updated


#: Кегль подписи в draw.io, когда в стиле фигуры он не задан.
_DRAWIO_FONT_SIZE = 12.0

#: Кегль подписи связи в draw.io по умолчанию (defaultEdge темы редактора).
_DRAWIO_EDGE_FONT_SIZE = 11.0


def task_font_size(node: ProcessNode) -> float:
    """Кегль подписи шага — тот, которым её напечатал draw.io.

    Рамку аналитик рисовал под этот кегль (на картах банка 12, 11, 10 и 8), и
    при нём подпись в рамку помещается. Студия по умолчанию печатает 14, и
    раньше шаг приходилось раздвигать под этот кегль — у 93 % шагов, и карта
    переставала совпадать с draw.io. Кегль студия берёт из ``font_size``
    фигуры (PIX банка, 2026-09-23: 12, 10 и 8 легли в стиль шага), а выступающий
    текст печатает, как draw.io, не обрезая.
    """
    raw = style_map(node.style).get('fontsize', '')
    try:
        size = float(raw) if raw else _DRAWIO_FONT_SIZE
    except ValueError:
        size = _DRAWIO_FONT_SIZE
    return size if size > 0 else _DRAWIO_FONT_SIZE


def clamp_into_lane(child: ProcessNode, lane: ProcessNode) -> Tuple[int, int]:
    """Координаты узла относительно дорожки, зажатые в её границы.

    Узел, отнесённый к дорожке по геометрии, может выступать за её край — в
    студии он отрисовался бы поверх соседней дорожки.
    """
    lane_w = max(lane.geometry.width, 80)
    lane_h = max(lane.geometry.height, 80)
    rel_x = child.geometry.x - lane.geometry.x
    rel_y = child.geometry.y - lane.geometry.y
    rel_x = max(0, min(rel_x, lane_w - child.geometry.width))
    rel_y = max(0, min(rel_y, lane_h - child.geometry.height))
    return int(rel_x), int(rel_y)


def _overlaps_lane(node: ProcessNode, lane: ProcessNode) -> bool:
    """Задевает ли фигура дорожку площадью, а не только краем."""
    g, l = node.geometry, lane.geometry
    return (min(g.x + g.width, l.x + max(l.width, 80)) > max(g.x, l.x)
            and min(g.y + g.height, l.y + max(l.height, 80)) > max(g.y, l.y))


#: Толщина полосы с названием дорожки в студии, px. Это класс её
#: отрисовщика (``_roadTitleLine``), в файле ширины полосы нет.
_LANE_BAND = 30.0
#: Межстрочный интервал подписи дорожки в студии — доля кегля.
_LANE_LINE_HEIGHT = 0.9
#: Мельче этого название дорожки уже не читается.
_LANE_MIN_FONT = 8


def lane_title(lane: ProcessNode) -> Tuple[str, int]:
    """Название дорожки и кегль, при котором оно целиком влезает в полосу.

    Полосу в 30 px не расширить, поэтому уменьшается кегль: от кегля
    draw.io (обычно 12) вниз до 8, пока название не ляжет в полосу. При 14
    студии длинное название приходилось резать «…» — на 10 картах банка 70
    названий из 348; с подбором кегля режется одно. Кегль и интервал 0,9
    кегля студия берёт из ``font_size`` (PIX банка, 2026-09-25, проба P18).
    """
    name = (lane.name or '').strip()
    height = max(lane.geometry.height, 80)
    start = min(int(round(task_font_size(lane))), int(_DRAWIO_FONT_SIZE))
    for size in range(start, _LANE_MIN_FONT - 1, -1):
        rows = int(_LANE_BAND // (_LANE_LINE_HEIGHT * size))
        if band_label(name, height, size, rows) == name:
            return name, size
    rows = int(_LANE_BAND // (_LANE_LINE_HEIGHT * _LANE_MIN_FONT))
    return band_label(name, height, _LANE_MIN_FONT, rows), _LANE_MIN_FONT


def _text_frame_style(node: ProcessNode) -> Tuple[str, str]:
    """Заливка и прочие атрибуты рамки текста — так, как её рисует draw.io.

    Стиль ``text`` в теме draw.io — без обводки и заливки, и длинная сноска
    там свободно выходит за свою рамку (у «Sport maydoni» в 1.54 — 337
    символов в рамке 370 × 30). «Пустой пул» студия обводит, и текст вылезал из
    рамки, которой на карте аналитика нет. Обводку и заливку студия снимает
    по ``border_color``/``fill_color="transparent"`` (PIX банка, 2026-09-24,
    проба P17); где рамку нарисовал аналитик, она остаётся.

    Кегль пишется всегда: без него студия печатает 14, а draw.io — 12.
    """
    smap = style_map(node.style)
    plain_text = (node.style or '').split(';', 1)[0].strip().lower() == 'text'

    def drawn(key: str) -> bool:
        return smap.get(key, 'none' if plain_text else '') != 'none'

    extra = f' font_size="{int(round(task_font_size(node)))}"'
    if not drawn('strokecolor'):
        extra += ' border_color="transparent"'
    return ('var(--bg-accent-node)' if drawn('fillcolor') else 'transparent'), extra


def flow_node_xml(
    n: ProcessNode,
    placed: Dict[str, Tuple[int, int]],
    id_map: Dict[str, str],
    label_side: Dict[str, str],
    badges: Dict[str, Tuple[int, int]],
    indent: str,
    lane: Optional[ProcessNode] = None,
    node_type: Optional[str] = None,
) -> List[str]:
    """Строки узла карты (и часов шага) — внутри дорожки относительно неё.

    Текст — заголовок, сноска или пояснение к шагу — пишется «Пустым пулом»
    (см. ``pix_type``) со своим кеглем из draw.io; пунктир к шагу студия от
    такой рамки принимает.

    ``node_type`` — элемент студии вместо того, что дал бы ``pix_type``: так
    пишутся свёрнутый подпроцесс, раскрывающий дочернюю карту, и переходы
    между частями разрезанной карты (``pmm_tree``).
    """
    x, y = placed[n.id]
    if lane is not None:
        x, y = x - lane.geometry.x, y - lane.geometry.y
    if n.type == 'textAnnotation':
        fill, extra = _text_frame_style(n)
        return [_node_xml('emptyPool', id_map[n.id], map_label(n), x, y,
                          n.geometry.width, n.geometry.height, extra, fill=fill, indent=indent)]
    label = map_label(n)
    if n.type in _EVENT_LABEL_TYPES:
        label = side_label_text(label, label_side.get(n.id, ''))
    out = [_node_xml(
        node_type or pix_type(n), id_map[n.id], label, x, y,
        n.geometry.width, n.geometry.height,
        _node_extra(n, label_side.get(n.id, _GATEWAY_LABEL_PLACEMENT)),
        indent=indent,
        children=node_properties_xml(n, indent),
    )]
    marker = duration_node_xml(n, badges, indent, lane)
    if marker:
        out.append(marker)
    return out


def polyline(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    obstacles: Optional[Obstacles] = None,
    corridors: Optional[Corridors] = None,
    anchors: Optional[Anchors] = None,
) -> List[Tuple[float, float]]:
    """Полная ломаная связи в абсолютных координатах карты.

    Ломаную задаём ВСЕГДА, а не только когда аналитик двигал изломы руками.
    Без waypoint студия трассирует связь сама, и на плотной карте банка это
    даёт то, на что жалуются аналитики: линии идут поверх соседних связей и
    сквозь чужие фигуры. В BPMN-выгрузке ломаная передаётся целиком, и там
    схема читается — здесь должно быть так же.

    ``placed`` — фактические абсолютные координаты узлов после зажатия в
    границы дорожки; концы ломаной обязаны лежать на них, а не на исходной
    геометрии draw.io.

    Ломаная строится ортогонально (`edge_routing`): связи в PIX имеют тип
    ``step``, и диагональные изломы в них выглядели бы чужеродно.

    Линии «локтем» (``elbowEdgeStyle``) и «ортогональные»
    (``orthogonalEdgeStyle``) draw.io строит своими алгоритмами, без обхода
    фигур, и именно так их видит аналитик: на картах банка это 99 % связей.
    Они уезжают в PIX ломаной самого draw.io (``drawio_elbow_route``,
    ``drawio_orthogonal``); общий поиск пути остаётся прочим — и линиям к
    полосе участника, у которых конец ведёт ``plan_pool_links``.
    """
    style = (edge.edgeStyle or '').lower()
    if src is not None and tgt is not None and style == 'elbowedgestyle':
        route = _collapse_collinear_runs(_drop_micro_jogs(_snap_to_pixel_grid(
            drawio_elbow_route(edge, src, tgt, placed)), src, tgt, placed))
        if len(route) >= 2:
            return route
    if (src is not None and tgt is not None and style == 'orthogonaledgestyle'
            and not src.id.endswith('__lane') and not tgt.id.endswith('__lane')
            and src.id != tgt.id):
        route = _collapse_collinear_runs(_drop_micro_jogs(_snap_to_pixel_grid(orthogonal_route(
            edge.style, _frame(src, placed), _frame(tgt, placed),
            perimeter_kind(src.style), perimeter_kind(tgt.style),
            [(float(p.x), float(p.y)) for p in edge.points])), src, tgt, placed))
        if len(route) >= 2:
            return route
    route = orthogonal_waypoints(edge, src, tgt, placed, obstacles, corridors, anchors)
    if len(route) >= 2:
        return route
    # Маршрут не построился (у связи нет обеих опор или они совпали по центру):
    # отдаём хотя бы прямой отрезок между центрами. Связь без единой точки
    # студия рисует по-своему, и на плотной карте это лишний пересекающий луч.
    if src is None or tgt is None:
        return []
    def _centre(node: ProcessNode) -> Tuple[float, float]:
        x, y = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
        return x + node.geometry.width / 2, y + node.geometry.height / 2
    start, end = _centre(src), _centre(tgt)
    return [start, end] if start != end else []


def _coord(value: float) -> str:
    """Координата точки ломаной: целая — целой, дробная — как есть.

    Точка крепления PIX лежит на середине или четверти грани, и у фигуры
    нечётного размера это дробь: у шага шириной 139 середина — 69,5. Студия
    берёт ломаную как есть, только если её концы совпадают с точкой крепления
    до 0,1 px; округление к целому сдвигало конец на полпикселя, и студия
    перестраивала линию сама.
    """
    text = f'{value:.2f}'.rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


#: Оформление связи по её роду: стиль линии и маркеры концов. Снято с выгрузки
#: самой студии (``tests/fixtures/sap.pmm``), где встречаются ровно три
#: сочетания, и каждое отвечает своему понятию BPMN:
#:
#:     solid  + line   + arrowclosed — поток управления      (28 связей)
#:     dotted + line   + arrowLine   — ассоциация с артефактом (21)
#:     dashed + circle + arrowEmpty  — поток сообщений         (1)
#:
#: Писавшийся раньше ``arrow`` в выгрузке студии не встречается ни разу, а
#: незнакомый маркер она молча отбрасывает вместе со связью — пунктирные линии
#: до карты не доезжали именно поэтому.
#:
#: Сочетание берётся ЦЕЛИКОМ из этого списка. Вид связи сервер PIX выводит из
#: тройки «стиль + начало + конец» и, не узнав её, делает связь обычным
#: переходом — сплошным, со стрелкой. Так пропадал пунктир к цилиндрам: без
#: наконечника (``line`` в конце) ассоциацию PIX не узнаёт, а пунктира без
#: наконечника у него нет вовсе (проба на PIX банка, PIX Процессы 2.3.2:
#: ``dotted`` + ``none`` в конце тоже стал сплошной стрелкой).
_SEQUENCE_DECORATION = ('solid', 'line', 'arrowclosed')
_ASSOCIATION_DECORATION = ('dotted', 'line', 'arrowLine')
_MESSAGE_DECORATION = ('dashed', 'circle', 'arrowEmpty')
#: Переход, нарисованный в draw.io пунктиром: пунктир остаётся, стрелка — как у
#: перехода. Сочетание ``dashed`` + ``arrowclosed`` в выгрузке студии не
#: встречается (там пунктир есть только у сообщения), но оба значения ей
#: знакомы по отдельности. Если студия его не примет, достаточно вернуть здесь
#: ``_SEQUENCE_DECORATION``: переход станет сплошным, но останется переходом.
_DASHED_SEQUENCE_DECORATION = ('dashed', 'line', 'arrowclosed')

#: Точки крепления связи в PIX (``sourcePoint``/``targetPoint``): грань ->
#: доля вдоль грани -> номер.
#:
#: Сняты с живого клиента PIX Процессы 2.3.2: у КАЖДОЙ фигуры на каждой грани
#: ровно три точки — на четверти, середине и трёх четвертях, — у каждой свой
#: номер. Координату точки студия считает сама по рамке фигуры, а ломаную
#: из файла берёт как есть лишь при двух условиях: её концы лежат в этих
#: точках (допуск 0,1 px) и все отрезки идут по осям. Иначе она строит свою
#: ступенчатую линию и вклеивает в неё середину нашей — это и давало кашу
#: из петель у шагов. Где номер не задан, студия берёт правую середину.
#:
#: Середины граней у цели записаны так, как пишет их сама студия в своей
#: выгрузке (``tests/fixtures/sap.pmm``): левая 6, правая 4.
_PIX_HANDLE_FRACTIONS = (0.25, 0.5, 0.75)
_SOURCE_HANDLES = {
    'top': {0.25: 19, 0.5: 2, 0.75: 18},
    'bottom': {0.25: 15, 0.5: 0, 0.75: 14},
    'left': {0.25: 26, 0.5: 6, 0.75: 27},
    'right': {0.25: 22, 0.5: 4, 0.75: 23},
}
_TARGET_HANDLES = {
    'top': {0.25: 17, 0.5: 1, 0.75: 16},
    'bottom': {0.25: 21, 0.5: 3, 0.75: 20},
    'left': {0.25: 28, 0.5: 6, 0.75: 29},
    'right': {0.25: 24, 0.5: 4, 0.75: 25},
}

#: Насколько точка ломаной может отойти от грани и всё ещё считаться лежащей
#: на ней: ломаная округляется к целым и выравнивается по осям сдвигом на
#: пиксель (``edge_routing._snap_to_pixel_grid``).
_ANCHOR_TOLERANCE = 2.0


def _line_decoration(edge: ProcessEdge) -> Tuple[str, str, str]:
    """Стиль линии и маркеры её концов — по роду связи.

    Род решает всё, пунктир — только вид линии. Раньше любая пунктирная связь
    получала оформление ассоциации: переход, нарисованный в draw.io пунктиром
    со стрелкой, уезжал в студию ассоциацией без наконечника.
    """
    if edge.kind == 'messageFlow':
        return _MESSAGE_DECORATION
    if edge.kind == 'association':
        return _ASSOCIATION_DECORATION
    return _DASHED_SEQUENCE_DECORATION if edge.dashed else _SEQUENCE_DECORATION


def _anchor_side(
    node: ProcessNode,
    point: Tuple[float, float],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[str]:
    """Грань фигуры, на которой лежит конец ломаной, или ``None``.

    Сторону определяем по той же ломаной, которая уезжает в файл: якорь,
    разошедшийся с нарисованной линией, хуже отсутствующего — студия увела бы
    связь к другой грани.
    """
    ox, oy = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
    width, height = node.geometry.width, node.geometry.height
    if width <= 0 or height <= 0:
        return None
    px, py = point
    within_x = ox - _ANCHOR_TOLERANCE <= px <= ox + width + _ANCHOR_TOLERANCE
    within_y = oy - _ANCHOR_TOLERANCE <= py <= oy + height + _ANCHOR_TOLERANCE
    distance: Dict[str, float] = {}
    if within_y:
        distance['left'] = abs(px - ox)
        distance['right'] = abs(px - (ox + width))
    if within_x:
        distance['top'] = abs(py - oy)
        distance['bottom'] = abs(py - (oy + height))
    if not distance:
        return None
    side = min(distance, key=lambda k: distance[k])
    return side if distance[side] <= _ANCHOR_TOLERANCE else None


#: Точки, совпадающие до этой величины, считаются лежащими на одной оси, px.
_ALIGN_EPS = 0.01


def _frame(
    node: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Tuple[float, float, float, float]:
    """Рамка фигуры в том виде, в каком её получит студия (см. ``_node_xml``)."""
    ox, oy = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
    return ox, oy, max(node.geometry.width, 8), max(node.geometry.height, 8)


def _nearest_side(
    node: ProcessNode,
    point: Tuple[float, float],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> str:
    """Ближайшая к точке грань фигуры — для конца, не лежащего ни на одной."""
    ox, oy, width, height = _frame(node, placed)
    px, py = point
    cx, cy = min(max(px, ox), ox + width), min(max(py, oy), oy + height)
    distance = {
        'left': abs(px - ox) + abs(py - cy),
        'right': abs(px - ox - width) + abs(py - cy),
        'top': abs(py - oy) + abs(px - cx),
        'bottom': abs(py - oy - height) + abs(px - cx),
    }
    return min(distance, key=lambda k: distance[k])


def _handle_point(
    node: ProcessNode,
    side: str,
    fraction: float,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Tuple[float, float]:
    """Координаты точки крепления PIX: так её считает сама студия."""
    ox, oy, width, height = _frame(node, placed)
    if side == 'top':
        return ox + width * fraction, oy
    if side == 'bottom':
        return ox + width * fraction, oy + height
    if side == 'left':
        return ox, oy + height * fraction
    return ox + width, oy + height * fraction


def _dock_first_point(
    points: List[Tuple[float, float]],
    side: str,
    handle: Tuple[float, float],
) -> List[Tuple[float, float]]:
    """Переносит начало ломаной в точку крепления, не ломая первый отрезок.

    Точка крепления сдвигает конец только вдоль грани. Если линия уходит от
    грани перпендикулярно, а следующий её отрезок идёт вдоль грани, тот же
    сдвиг получает и первый излом: форма линии не меняется, она лишь
    смещается на несколько пикселей. Остальное выпрямляет ``_orthogonal``.
    """
    along = 1 if side in ('left', 'right') else 0
    out = [handle, *points[1:]]
    if len(points) >= 3:
        head, bend, after = points[0], points[1], points[2]
        if (abs(bend[along] - head[along]) < _ALIGN_EPS
                and abs(after[1 - along] - bend[1 - along]) < _ALIGN_EPS):
            moved = list(bend)
            moved[along] = handle[along]
            out[1] = (moved[0], moved[1])
    return out


def _orthogonal(
    points: List[Tuple[float, float]],
    source_side: str,
    target_side: str,
) -> List[Tuple[float, float]]:
    """Ломаная только из горизонталей и вертикалей: косой отрезок студия не берёт.

    Косой отрезок получает излом. У концов излом ставится так, чтобы линия
    отходила от грани и входила в неё перпендикулярно; прямая между двумя
    точками крепления на разной высоте становится ступенькой посередине.
    """
    source_across = source_side in ('left', 'right')
    target_across = target_side in ('left', 'right')
    last = len(points) - 1
    out = [points[0]]
    for index in range(1, len(points)):
        a, b = out[-1], points[index]
        if abs(a[0] - b[0]) >= _ALIGN_EPS and abs(a[1] - b[1]) >= _ALIGN_EPS:
            if index == 1 and index == last and source_across == target_across:
                if source_across:
                    middle = round((a[0] + b[0]) / 2)
                    out += [(middle, a[1]), (middle, b[1])]
                else:
                    middle = round((a[1] + b[1]) / 2)
                    out += [(a[0], middle), (b[0], middle)]
            elif index == 1:
                out.append((b[0], a[1]) if source_across else (a[0], b[1]))
            else:
                out.append((a[0], b[1]) if index != last or target_across else (b[0], a[1]))
        if (abs(out[-1][0] - b[0]) >= _ALIGN_EPS) or (abs(out[-1][1] - b[1]) >= _ALIGN_EPS):
            out.append(b)
    return out


#: Ступенька не длиннее этого не считается изломом, px.
_MICRO_STEP = 2.0


def _pix_route(
    route: List[Tuple[float, float]],
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]],
    source_port: Optional[Tuple[str, float]] = None,
    target_port: Optional[Tuple[str, float]] = None,
) -> Tuple[List[Tuple[float, float]], str]:
    """Ломаная, которую студия возьмёт как есть, и номера её точек крепления.

    ``source_port``/``target_port`` — точка, уже назначенная концу на полосе
    участника (``plan_pool_links``): её не перебираем, иначе две линии снова
    сошлись бы в одной точке.

    Концы ломаной переносятся в точки крепления PIX на тех же гранях, косые
    отрезки получают изломы. Номера пишутся ВСЕГДА: без ``sourcePoint``
    студия считает началом правую середину фигуры, и линия, выходящая снизу,
    дорисовывалась от правого края.

    Точек на грани всего три, и у шагов, стоящих на несколько пикселей не
    вровень, середины граней не совпадают: прямая между ними становится
    ступенькой в те же несколько пикселей, которая читается как поломка.
    Поэтому пары точек перебираются все: берётся пара без ступенек короче
    ``MIN_SEGMENT``, а из таких — та, что меньше всего сдвигает концы линии.
    """
    if len(route) < 2 or src is None or tgt is None:
        return route, ''
    source_side = _anchor_side(src, route[0], placed) or _nearest_side(src, route[0], placed)
    target_side = _anchor_side(tgt, route[-1], placed) or _nearest_side(tgt, route[-1], placed)
    source_fractions = target_fractions = _PIX_HANDLE_FRACTIONS
    if source_port is not None:
        source_side, source_fractions = source_port[0], (source_port[1],)
    if target_port is not None:
        target_side, target_fractions = target_port[0], (target_port[1],)
    best = None
    for source_fraction in source_fractions:
        source_point = _handle_point(src, source_side, source_fraction, placed)
        for target_fraction in target_fractions:
            target_point = _handle_point(tgt, target_side, target_fraction, placed)
            points = _dock_first_point(list(route), source_side, source_point)
            points = _dock_first_point(points[::-1], target_side, target_point)[::-1]
            points = _orthogonal(points, source_side, target_side)
            # Ступенька в один-два пикселя — дробные координаты draw.io у фигур,
            # стоящих почти вровень, — на карте не видна; увести ради неё конец
            # на четверть грани значит нарисовать заметный излом в 30 px.
            steps = sum(
                1 for a, b in zip(points[1:-2], points[2:-1])
                if _MICRO_STEP < max(abs(b[0] - a[0]), abs(b[1] - a[1])) < MIN_SEGMENT
            )
            shift = (abs(source_point[0] - route[0][0]) + abs(source_point[1] - route[0][1])
                     + abs(target_point[0] - route[-1][0]) + abs(target_point[1] - route[-1][1]))
            if best is None or (steps, shift) < best[0]:
                best = ((steps, shift), points, source_fraction, target_fraction)
    _key, points, source_fraction, target_fraction = best
    attrs = (f' sourcePoint="{_SOURCE_HANDLES[source_side][source_fraction]}"'
             f' targetPoint="{_TARGET_HANDLES[target_side][target_fraction]}"')
    return points, attrs


#: Где по умолчанию встаёт подпись ветки шлюза, % длины связи.
#:
#: Не по центру: у шлюза от одной точки расходится несколько линий, их середины
#: попадают в гущу схемы, и «Ha» с «Yo‘q» ложатся поверх соседних фигур.
#: У самого шлюза место свободно всегда — там подпись и читается, и по ней
#: сразу видно, к какой ветке она относится.
_GATEWAY_LABEL_POSITION = 20

#: Где встаёт подпись обычной связи, % длины. Столько же в выгрузке студии у
#: 42 связей из 50.
_DEFAULT_LABEL_POSITION = 50

#: Ближе к концам подпись не ставим: она налезла бы на саму фигуру.
_LABEL_POSITION_LIMITS = (5, 95)


def drawio_label_point(
    points: Sequence[Tuple[float, float]],
    geometry: Sequence[float],
) -> Optional[Tuple[float, float]]:
    """Где draw.io рисует подпись связи — порт ``mxGraphView.getPoint``.

    ``geometry`` — x (доля пути от -1 до 1), y (сдвиг поперёк отрезка) и
    смещение (dx, dy). Смещением аналитик уводит подпись пунктира к колонке
    в промежуток между колонкой и дорожками; без него текст ложился на шаги.
    """
    if len(points) < 2:
        return None
    x, y, dx, dy = geometry
    segments = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(points, points[1:])]
    total = sum(segments)
    if total <= 0:
        return None
    dist = round((x / 2 + 0.5) * total)
    length, index, segment = 0.0, 1, segments[0]
    while dist >= round(length + segment) and index < len(points) - 1:
        length += segment
        segment = segments[index]
        index += 1
    factor = 0.0 if segment == 0 else (dist - length) / segment
    (x0, y0), (x1, y1) = points[index - 1], points[index]
    nx = 0.0 if segment == 0 else (y1 - y0) / segment
    ny = 0.0 if segment == 0 else (x1 - x0) / segment
    return x0 + (x1 - x0) * factor + nx * y + dx, y0 + (y1 - y0) * factor - (ny * y - dy)


def _percent_nearest(anchor: Tuple[float, float], final: Sequence[Tuple[float, float]], percent: int) -> int:
    """Доля длины ``final`` у ближайшей к ``anchor`` точки, в процентах."""
    best, walked = None, 0.0
    for a, b in zip(final, final[1:]):
        step = abs(b[0] - a[0]) + abs(b[1] - a[1])
        span_x, span_y = b[0] - a[0], b[1] - a[1]
        t = 0.0 if step == 0 else max(0.0, min(1.0, (
            (anchor[0] - a[0]) * span_x + (anchor[1] - a[1]) * span_y) / (span_x * span_x + span_y * span_y)))
        px, py = a[0] + span_x * t, a[1] + span_y * t
        gap = abs(px - anchor[0]) + abs(py - anchor[1])
        if best is None or gap < best[0]:
            best = (gap, walked + step * t)
        walked += step
    low, high = _LABEL_POSITION_LIMITS
    return max(low, min(high, int(round(best[1] / walked * 100)))) if walked > 0 else percent


def _carried_label_position(
    drawn: Sequence[Tuple[float, float]],
    final: Sequence[Tuple[float, float]],
    percent: int,
) -> int:
    """Та же точка подписи, но в процентах длины другой ломаной.

    Подпись стоит на ``percent`` % длины ломаной ``drawn`` (как в draw.io);
    на ломаной ``final`` берётся ближайшая к этой точке и её доля длины.
    """
    def length(points):
        return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))

    total = length(drawn)
    if total <= 0 or len(final) < 2:
        return percent
    goal, walked = total * percent / 100.0, 0.0
    anchor = drawn[-1]
    for a, b in zip(drawn, drawn[1:]):
        step = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if walked + step >= goal and step > 0:
            t = (goal - walked) / step
            anchor = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            break
        walked += step
    return _percent_nearest(anchor, final, percent)


def label_position(edge: ProcessEdge, source: Optional[ProcessNode]) -> int:
    """Доля длины связи, на которой стоит её подпись, в процентах.

    Место подписи аналитик выбирает сам, растаскивая надписи так, чтобы схема
    читалась; draw.io хранит выбор в ``labelX`` — доле от -1 (начало связи) до
    +1 (конец). Раньше выгрузка это выбрасывала и ставила всем подряд 50 %, то
    есть ровно в середину линии — там, где на плотной карте гуще всего фигур.
    Студия своё расположение подписей хранит так же и в собственной выгрузке
    пишет самые разные значения (3, 7, 10, 25, 32, 56, 80).
    """
    raw = edge.labelX
    if raw is not None and -1.0 <= raw <= 1.0:
        percent = int(round((raw + 1.0) * 50.0))
    elif source is not None and source.type in GATEWAY_NODE_TYPES:
        percent = _GATEWAY_LABEL_POSITION
    else:
        percent = _DEFAULT_LABEL_POSITION
    low, high = _LABEL_POSITION_LIMITS
    return max(low, min(high, percent))


# ── Пунктир к полосе участника ──────────────────────────────────────────────
#
# Линия к полосе внешнего участника («Mijoz», «Birlashgan kasaba uyushmasi»)
# ведётся прямо в полосу — как в draw.io и как это делает импорт BPMN самой
# PIX (PIX банка, 2026-09-22). Конверты-посредники, которые ставились раньше,
# на карте аналитика не нарисованы.
#
# Главная опасность — пучок. У полосы, как у любой фигуры PIX, по три точки
# крепления на грани, а в draw.io линия входит в полосу где угодно: к полосе
# «MY.GOV.UZ» подходит двадцать пунктиров. Без номера точки студия берёт
# середину, и все они когда-то сходились в одну точку.
#
# Поэтому линия идёт, как в draw.io, почти до самой полосы, а у неё сворачивает
# к своей точке крепления:
#
# * точки раздаются линиям по порядку, слева направо — так подходы не
#   пересекаются. Обращённая к шагам грань даёт три точки, боковые — ещё по
#   три: в них линия заходит «крючком» из промежутка рядом с полосой;
# * горизонтальные подходы идут на разной высоте под полосой, через
#   ``_TRACK_GAP``: линия, которой дальше идти, — ближе к полосе и обходит
#   соседнюю поверху, поэтому подходы не ложатся один на другой;
# * общая точка достаётся двум линиям, только если точек не хватает на всех,
#   и тогда они сходятся лишь в последних пикселях у самой полосы.

#: Расстояние между параллельными подходами под полосой, px.
_TRACK_GAP = 10.0

#: Расстояние между «крючками» в промежутке рядом с полосой, px.
#:
#: Полосы участников стоят вплотную, с зазором в 40–47 px, и один зазор делят
#: крючки двух соседей: по три с каждой стороны помещаются только при шаге 8.
_HOOK_STEP = 8.0

#: Надбавка за крючок вместо прямого подхода снизу, px.
_HOOK_PENALTY = 60.0

#: Вторая линия в той же точке — только когда точек не хватает на всех.
_SHARED_PORT_PENALTY = 100_000.0


class PoolLink(NamedTuple):
    """Как линия подходит к полосе участника.

    ``meet`` — x, где линия, как в draw.io, встречает грань полосы; ``track`` —
    высота горизонтального подхода под (над) полосой; ``column`` — x вертикали
    крючка рядом с полосой. У линии, подходящей к полосе сбоку, их нет: её
    концом на полосе занимается общая трассировка.
    """
    lane_id: str
    side: str
    fraction: float
    track: Optional[float] = None
    column: Optional[float] = None
    meet: Optional[float] = None
    face: Optional[float] = None


def _facing_side(
    lane: ProcessNode,
    peer: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> str:
    """Грань полосы, обращённая к фигуре на другом конце линии."""
    lx, ly, lw, lh = _frame(lane, placed)
    px, py, pw, ph = _frame(peer, placed)
    if py >= ly + lh:
        return 'bottom'
    if py + ph <= ly:
        return 'top'
    return 'left' if px + pw / 2 < lx + lw / 2 else 'right'


def _pool_slots(facing: str) -> List[Tuple[str, float]]:
    """Точки крепления полосы по порядку обхода слева направо.

    Для линий снизу: левая грань сверху вниз, нижняя слева направо, правая
    снизу вверх. Раздав их линиям по возрастанию x, получаем подходы без
    пересечений. Для линий сверху — то же, отражённое по вертикали.
    """
    if facing == 'bottom':
        return [('left', 0.25), ('left', 0.5), ('left', 0.75),
                ('bottom', 0.25), ('bottom', 0.5), ('bottom', 0.75),
                ('right', 0.75), ('right', 0.5), ('right', 0.25)]
    if facing == 'top':
        return [('left', 0.75), ('left', 0.5), ('left', 0.25),
                ('top', 0.25), ('top', 0.5), ('top', 0.75),
                ('right', 0.25), ('right', 0.5), ('right', 0.75)]
    return [(facing, fraction) for fraction in _PIX_HANDLE_FRACTIONS]


def _hook_column(frame: Tuple[float, float, float, float], side: str,
                 fraction: float, facing: str) -> float:
    """x вертикали крючка к боковой точке.

    Ближайшая к шагам точка берёт ближнюю к полосе вертикаль: крючки
    вкладываются один в другой и не пересекаются.
    """
    x, _y, width, _h = frame
    rank = {0.75: 1, 0.5: 2, 0.25: 3}[fraction] if facing == 'bottom' else {0.25: 1, 0.5: 2, 0.75: 3}[fraction]
    return x - _HOOK_STEP * rank if side == 'left' else x + width + _HOOK_STEP * rank


def _monotone_assignment(
    cost: Sequence[Sequence[float]],
    shareable: Optional[Sequence[bool]] = None,
) -> List[int]:
    """Точка для каждой линии: по порядку линий и без пересечений, при минимуме суммы.

    Линии уже отсортированы по месту встречи с полосой; номер точки у
    следующей линии не меньше, чем у предыдущей. Повтор точки стоит
    ``_SHARED_PORT_PENALTY`` — к нему прибегаем, только когда точек не хватает,
    и только там, где ``shareable``: у крючка общей стала бы вся вертикаль.
    """
    rows, cols = len(cost), len(cost[0])
    inf = float('inf')
    best = [list(cost[0])] + [[inf] * cols for _ in range(rows - 1)]
    back = [[-1] * cols for _ in range(rows)]
    for row in range(1, rows):
        run, run_at = inf, -1
        for col in range(cols):
            can_share = shareable is None or shareable[col]
            shared = best[row - 1][col] + _SHARED_PORT_PENALTY if can_share else inf
            options = [(run, run_at), (shared, col)]
            value, came = min(options, key=lambda o: o[0])
            best[row][col] = value + cost[row][col]
            back[row][col] = came
            if best[row - 1][col] < run:
                run, run_at = best[row - 1][col], col
    col = min(range(cols), key=lambda c: best[rows - 1][c])
    out = [0] * rows
    for row in range(rows - 1, -1, -1):
        out[row] = col
        col = back[row][col]
    return out


def _spread_meets(meets: Sequence[float], low: float, high: float) -> List[float]:
    """Места встречи с полосой, разведённые не ближе ``_TRACK_GAP``.

    Две линии одного шага — запрос и ответ — в draw.io встречают полосу в одной
    точке над шагом, и их вертикали легли бы одна на другую. Скопление
    раздвигается вокруг своей середины; порядок линий не меняется.
    """
    out = list(meets)
    # Раздвинутое скопление может подойти к соседней линии — тогда они
    # сливаются в одно скопление на следующем проходе.
    for _ in range(len(out)):
        moved = False
        start = 0
        while start < len(out):
            end = start
            while end + 1 < len(out) and out[end + 1] - out[end] < _TRACK_GAP - 1e-6:
                end += 1
            if end > start:
                centre = sum(out[start:end + 1]) / (end - start + 1)
                first = centre - _TRACK_GAP * (end - start) / 2
                first = max(low, min(first, high - _TRACK_GAP * (end - start)))
                for k in range(start, end + 1):
                    out[k] = first + _TRACK_GAP * (k - start)
                moved = True
            start = end + 1
        if not moved:
            break
    return out


def _approach_levels(approaches: Sequence[Tuple[float, float]]) -> List[int]:
    """Высота подхода каждой линии: 1 — у самой полосы, 0 — подход прямой.

    ``approaches`` — пары (где линия встречает полосу, куда ей свернуть),
    отсортированные по первому. Из двух линий, идущих в одну сторону и
    перекрывающих друг друга, ближе к полосе та, которой идти дальше: она
    проходит над углом соседней, а не сквозь её вертикаль.
    """
    levels = [0] * len(approaches)
    rightward = [i for i, (d, to) in enumerate(approaches) if to > d + 0.5]
    leftward = [i for i, (d, to) in enumerate(approaches) if to < d - 0.5]
    for group, reverse in ((rightward, False), (leftward, True)):
        order = sorted(group, key=lambda i: approaches[i][0], reverse=reverse)
        for n, i in enumerate(order):
            d, _to = approaches[i]
            below = [levels[j] for j in order[:n]
                     if min(approaches[j]) < d + 0.5 and max(approaches[j]) > d - 0.5
                     or abs(approaches[j][1] - approaches[i][1]) < 0.5]
            levels[i] = 1 + max(below, default=0)
    return levels


def plan_pool_links(
    edges: Sequence[ProcessEdge],
    lane_by_id: Dict[str, ProcessNode],
    node_by_id: Dict[str, ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Dict[str, PoolLink]:
    """Точка крепления и подход для каждой линии «фигура ↔ полоса».

    Крючок ставится, только если его вертикаль не задевает соседнюю полосу,
    фигуру или крючок соседа: полосы участников стоят рядом с зазором в
    сорок пикселей, и два соседа делят один промежуток.
    """
    groups: Dict[Tuple[str, str], List[Tuple[float, ProcessEdge, ProcessNode]]] = {}
    for edge in edges:
        lane_is_source = edge.sourceId in lane_by_id
        if not lane_is_source and edge.targetId not in lane_by_id:
            continue
        lane = lane_by_id[edge.sourceId if lane_is_source else edge.targetId]
        peer = node_by_id.get(edge.targetId if lane_is_source else edge.sourceId)
        if peer is None:
            continue
        facing = _facing_side(lane, peer, placed)
        ends = message_flow_endpoints(edge, peer, lane, lane_is_source)
        stub = ends[0] if lane_is_source else ends[1]
        along = (stub.geometry.x + 1 if facing in ('bottom', 'top') else stub.geometry.y + 1)
        groups.setdefault((lane.id, facing), []).append((float(along), edge, peer))

    blockers = [_frame(n, placed) for n in [*lane_by_id.values(), *node_by_id.values()]]
    taken_columns: List[float] = []
    taken_ports: Set[Tuple[str, str, float]] = set()
    out: Dict[str, PoolLink] = {}
    for (lane_id, facing), members in sorted(
            groups.items(), key=lambda g: _frame(lane_by_id[g[0][0]], placed)[0]):
        lane = lane_by_id[lane_id]
        frame = _frame(lane, placed)
        x0, y0, width, height = frame
        members.sort(key=lambda m: m[0])
        if facing not in ('bottom', 'top'):
            slots = [s for s in _pool_slots(facing) if (lane_id, *s) not in taken_ports] or _pool_slots(facing)
            picks = _monotone_assignment([[abs(d - _handle_point(lane, *s, placed)[1]) for s in slots]
                                          for d, _e, _p in members])
            for (d, edge, _peer), pick in zip(members, picks):
                out[edge.id] = PoolLink(lane_id, *slots[pick])
                taken_ports.add((lane_id, *slots[pick]))
            continue

        face = y0 + height if facing == 'bottom' else y0
        outward = 1.0 if facing == 'bottom' else -1.0
        slots, columns = [], []
        for side, fraction in _pool_slots(facing):
            if (lane_id, side, fraction) in taken_ports:
                continue
            column = None
            if side in ('left', 'right'):
                column = _hook_column(frame, side, fraction, facing)
                # Проверяется только часть вертикали рядом с полосой: ниже её
                # грани начинается дорожка с шагами, и линии её пересекают.
                port_y = _handle_point(lane, side, fraction, placed)[1]
                lo, hi = sorted((port_y, face - outward))
                if any(abs(column - c) < _HOOK_STEP - 1 for c in taken_columns) or any(
                        bx - 2 <= column <= bx + bw + 2 and by <= hi and by + bh >= lo
                        for bx, by, bw, bh in blockers if (bx, by, bw, bh) != frame):
                    continue
            slots.append((side, fraction))
            columns.append(column)

        def cost(d: float, index: int) -> float:
            side, fraction = slots[index]
            hx, hy = _handle_point(lane, side, fraction, placed)
            if columns[index] is None:
                return abs(d - hx)
            return abs(d - columns[index]) + abs(face - hy) + _HOOK_PENALTY

        picks = _monotone_assignment([[cost(d, i) for i in range(len(slots))] for d, _e, _p in members],
                                     [c is None for c in columns])
        targets = [columns[p] if columns[p] is not None
                   else _handle_point(lane, *slots[p], placed)[0] for p in picks]
        meets = _spread_meets([d for d, _e, _p in members], x0 + 1, x0 + width - 1)
        # Точка крепления в паре шагов от места встречи — линия идёт в неё прямо,
        # как в draw.io, а не ступенькой под самой полосой.
        for i, (meet, target, pick) in enumerate(zip(meets, targets, picks)):
            near = abs(meet - target) <= 2 * _TRACK_GAP and columns[pick] is None
            free = all(abs(other - target) >= _TRACK_GAP for j, other in enumerate(meets) if j != i)
            if near and free:
                meets[i] = target
        levels = _approach_levels(list(zip(meets, targets)))
        for (_d, edge, _peer), meet, pick, level in zip(members, meets, picks, levels):
            side, fraction = slots[pick]
            out[edge.id] = PoolLink(lane_id, side, fraction,
                                    track=face + outward * _TRACK_GAP * max(level, 1),
                                    column=columns[pick], meet=meet, face=face)
            taken_ports.add((lane_id, side, fraction))
            if columns[pick] is not None:
                taken_columns.append(columns[pick])
    return out


def _dock_to_pool(
    route: List[Tuple[float, float]],
    link: PoolLink,
    lane: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> List[Tuple[float, float]]:
    """Последний участок ломаной «фигура → полоса» — к точке крепления линии.

    Ломаная до полосы остаётся той, что проложена по draw.io; обрезается она
    там, где впервые (считая от полосы) пересекает высоту своего подхода, и
    оттуда идёт по подходу к своей точке: прямо вверх (вниз) или крючком в
    боковую грань.
    """
    track = link.track
    head: Optional[List[Tuple[float, float]]] = None
    for i in range(len(route) - 2, -1, -1):
        a, b = route[i], route[i + 1]
        if min(a[1], b[1]) - _ALIGN_EPS <= track <= max(a[1], b[1]) + _ALIGN_EPS:
            head = list(route[:i + 1])
            if abs(a[1] - track) >= _ALIGN_EPS:
                head.append((a[0], track))
            break
    if head is None:
        return route
    port = _handle_point(lane, link.side, link.fraction, placed)
    if link.column is None:
        tail = [(port[0], track), port]
    else:
        tail = [(link.column, track), (link.column, port[1]), port]
    return _collapse_collinear_runs(head + tail)


def _port_anchor(port: Tuple[str, float]) -> Tuple[float, float]:
    """Доли рамки (как ``exitX``/``exitY`` draw.io) для точки крепления."""
    side, fraction = port
    return {
        'bottom': (fraction, 1.0),
        'top': (fraction, 0.0),
        'left': (0.0, fraction),
        'right': (1.0, fraction),
    }[side]


def generate_map_xml(
    process: BusinessProcess,
    parent_process_id: Optional[str] = None,
    pix_types: Optional[Dict[str, str]] = None,
    name: Optional[str] = None,
) -> Tuple[str, str]:
    """Карта студии: ``(имя карты, XML)``.

    Для дочерней карты (``pmm_tree``) ``parent_process_id`` — идентификатор
    свёрнутого подпроцесса родительской карты, который её раскрывает, ``name`` —
    имя карты в дереве студии, ``pix_types`` — узлы, которые пишутся не своим
    элементом (подпроцесс со своей картой, переходы между частями карты).
    """
    # Рамки draw.io берутся ДО всего остального: и фигуры, и концы ломаных, и
    # значки длительности обязаны считаться от одних рамок.
    process = drawio_frames(process)
    slug = name or map_slug(process)
    pix_types = pix_types or {}
    parent_attr = f' parentProcessId="{escape_xml(parent_process_id)}"' if parent_process_id else ''
    id_map: Dict[str, str] = {}
    flow = [n for n in process.nodes if n.type != 'lane']
    lanes = list(process.lanes or [])
    # Полоса без единого шага — внешний участник (клиент): она остаётся строкой
    # карты, но, в отличие от дорожки с шагами, пунктир к ней осмыслен и
    # выгружается связью, а не отбрасывается как оформление.
    _, external = split_external_lanes(lanes, [n for n in flow if n.laneId])
    node_by_id = {n.id: n for n in flow}

    for n in flow + lanes:
        id_map[n.id] = _pix_id(n.id)

    lines: List[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (
            f'<Map xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}" '
            f'name="{escape_xml(slug)}" notation="{_MAP_NOTATION}"{parent_attr} '
            'paperEnabled="false" paperType="0">'
        ),
    ]

    # ── Заголовочная плашка карты ───────────────────────────────────────────
    # Ставится, только если своей шапки у карты нет. Шапка — ни с чем не
    # связанный текст целиком над дорожками; она есть у всех карт банка, и
    # плашка с именем файла («1.54») ложилась поверх настоящего заголовка.
    title = process.passport.name or process.name or slug
    bounds_nodes = lanes or flow
    linked = {end for e in process.edges if e.kind != 'annotationLine'
              for end in (e.sourceId, e.targetId)}
    body = lanes or [n for n in flow if n.type != 'textAnnotation']
    body_top = min((n.geometry.y for n in body), default=None)
    has_own_title = body_top is not None and any(
        n.type == 'textAnnotation' and n.id not in linked
        and n.geometry.y + n.geometry.height <= body_top
        for n in flow
    )
    if bounds_nodes:
        min_x = min(n.geometry.x for n in bounds_nodes)
        min_y = min(n.geometry.y for n in bounds_nodes)
        # Плашка шире эталонной, если карта шире: узкий заголовок над картой
        # в 4600 px выглядит обрывком, а не шапкой схемы.
        title_width = max(
            _TITLE_POOL_WIDTH,
            max(n.geometry.x + n.geometry.width for n in bounds_nodes) - min_x,
        )
    else:
        min_x, min_y, title_width = 0, 120, _TITLE_POOL_WIDTH
    if not has_own_title:
        lines.append(
            _node_xml(
                'emptyPool',
                _pix_id(f'title:{process.id}'),
                title,
                min_x,
                min_y - (_TITLE_POOL_HEIGHT + 40),
                title_width,
                _TITLE_POOL_HEIGHT,
                extra=' font_size="28"',
            )
        )

    # ── Дорожки и их содержимое ─────────────────────────────────────────────
    # Фактическое абсолютное положение узла после зажатия в границы дорожки:
    # по нему строятся концы ломаных связей.
    placed: Dict[str, Tuple[int, int]] = {}
    # Дорожка, в которую узел вкладывается в файле.
    host: Dict[str, ProcessNode] = {}
    lane_by_id = {lane.id: lane for lane in lanes}
    for n in flow:
        lane = lane_by_id.get(n.laneId)
        if lane is not None:
            rel_x, rel_y = clamp_into_lane(n, lane)
            inside = (lane.geometry.x + rel_x, lane.geometry.y + rel_y) == (n.geometry.x, n.geometry.y)
            # Шаг из дорожки не выносится никогда: дорожка — его исполнитель.
            if inside or n.type in TASK_NODE_TYPES:
                host[n.id] = lane
                placed[n.id] = (lane.geometry.x + rel_x, lane.geometry.y + rel_y)
                continue
        # Событие или артефакт на черте между дорожками — так в draw.io ставят
        # таймер между шагами соседних дорожек — вталкивать внутрь нельзя: он
        # наезжает на шаг, и линия к нему заворачивает. Он остаётся, где
        # нарисован, но вкладывается в ПОСЛЕДНЮЮ из задетых дорожек: студия
        # кладёт дорожки слоями по порядку в файле, детей — на слой своей
        # дорожки плюс один, а узлы уровня карты — под все дорожки, и там
        # таймер и текстовая рамка выходили бледными (PIX банка, 2026-09-23).
        # Выступающего за край ребёнка студия при импорте не двигает.
        covering = [lane for lane in lanes if _overlaps_lane(n, lane)]
        if covering:
            host[n.id] = covering[-1]
        placed[n.id] = (n.geometry.x, n.geometry.y)

    # Сторону подписи выбираем, зная итоговые места ВСЕХ фигур: двум соседним
    # ромбам одна и та же сторона кладёт вопросы друг на друга.
    event_sides = event_label_sides(flow, placed, process.edges)
    label_side = choose_label_placements(flow, placed, event_sides)
    label_side.update(event_sides)
    # Часы шагов — тоже зная места всех фигур: под шагом их место может быть
    # занято, и тогда они уходят вбок.
    badges = place_duration_badges(flow, placed, host)

    for lane in lanes:
        children = [n for n in flow if n.id in host and host[n.id].id == lane.id]
        title, title_size = lane_title(lane)
        lines.append(
            f'  <node type="horizontalRoad" id="{escape_xml(id_map[lane.id])}"'
            f' label="{escape_xml(title)}" number="0"'
            f' x="{lane.geometry.x}" y="{lane.geometry.y}"'
            f' width="{max(lane.geometry.width, 80)}" height="{max(lane.geometry.height, 80)}"'
            f' fill_color="var(--bg-accent-road-node)" font_size="{title_size}">'
        )
        for n in children:
            lines += flow_node_xml(n, placed, id_map, label_side, badges, '    ', lane,
                                   pix_types.get(n.id))
        lines.append('  </node>')

    # Фигуры вне дорожек: заголовок над картой и то, что не задевает ни одной
    # дорожки.
    for n in flow:
        if n.id not in host:
            lines += flow_node_xml(n, placed, id_map, label_side, badges, '  ',
                                   node_type=pix_types.get(n.id))

    # ── Связи ───────────────────────────────────────────────────────────────
    # Препятствия считаются один раз на карту и по УЖЕ размещённым координатам:
    # узлы внутри дорожки зажаты в её границы, и трассировать надо по тому, где
    # фигура окажется в студии, а не где она лежала в draw.io.
    obstacles = build_obstacles(flow, placed, extra=duration_badge_boxes(badges))
    # Накопитель проложенных связей: каждая следующая знает, где прошли
    # предыдущие, и не ложится поверх них.
    corridors = Corridors()
    lane_ids = {lane.id for lane in lanes}
    external_lanes = {lane.id: lane for lane in external}
    # Полоса в файле не ниже и не уже 80 px (см. выше), и точки крепления
    # студия считает по этой рамке: у полосы высотой 79 px нижняя точка иначе
    # разошлась бы с концом ломаной на пиксель, и студия перестроила бы линию.
    link_lanes = {
        lane.id: lane.model_copy(update={'geometry': Geometry(
            x=lane.geometry.x, y=lane.geometry.y,
            width=max(lane.geometry.width, 80), height=max(lane.geometry.height, 80))})
        for lane in lanes
    }
    pool_links = plan_pool_links(
        [e for e in process.edges if e.kind == 'messageFlow'], link_lanes, node_by_id, placed)

    # Опоры связей определяются отдельным проходом: развести причалы, сошедшиеся
    # в одной точке грани, можно только зная все связи карты сразу. Поодиночке
    # каждая ломаная выглядит правильной, а вместе они выходят из шага одной.
    drawn: List[Tuple[ProcessEdge, ProcessNode, ProcessNode, str, str]] = []
    for edge in process.edges:
        # Оформительские линии draw.io (разделители этапов) в карту PIX не идут.
        if edge.kind == 'annotationLine':
            continue
        if not edge.sourceId or not edge.targetId:
            continue
        if edge.sourceId not in id_map or edge.targetId not in id_map:
            continue
        # Петля из фигуры в саму себя: студия из-за одной такой линии
        # отказывается открыть всю карту целиком.
        if edge.sourceId == edge.targetId:
            continue

        # Связь с полосой-участником (клиент) остаётся на карте и ведётся прямо
        # в полосу, в свою точку крепления (``plan_pool_links``). Линия,
        # упирающаяся в дорожку с шагами, — оформление.
        link = pool_links.get(edge.id)
        if (edge.sourceId in lane_ids or edge.targetId in lane_ids) and link is None:
            continue
        if link is not None and link.track is not None:
            # До подхода к полосе линия идёт, как в draw.io: к точке, где она
            # встречала полосу там (``plan_pool_links``).
            lane_is_source = edge.sourceId in lane_ids
            peer = node_by_id[edge.targetId if lane_is_source else edge.sourceId]
            stub = point_stub(f'{edge.id}__lane', link.meet, link.face)
            src_node, tgt_node = (stub, peer) if lane_is_source else (peer, stub)
        else:
            src_node = node_by_id.get(edge.sourceId) or link_lanes.get(edge.sourceId)
            tgt_node = node_by_id.get(edge.targetId) or link_lanes.get(edge.targetId)
        if src_node is None or tgt_node is None:
            continue
        drawn.append((edge, src_node, tgt_node, edge.sourceId, edge.targetId))

    dock_anchors = spread_dock_anchors(
        [(edge, src_node, tgt_node) for edge, src_node, tgt_node, _s, _t in drawn], placed)

    for edge, src_node, tgt_node, source_key, target_key in drawn:
        line_style, marker_start, marker_end = _line_decoration(edge)
        label = (edge.name or edge.condition or '').strip()
        # Подпись связи в PIX — атрибут Text; label студия не читает. Строки
        # draw.io идут переносами (``&#10;`` — перевод строки в атрибуте
        # студия печатает, PIX банка 2026-09-23), пока подпись не правили на
        # платформе.
        if edge.labelLines and ' '.join(edge.labelLines.split()) == ' '.join(label.split()):
            label = edge.labelLines
        text_attr = (f' Text="{escape_xml(label).replace(chr(10), "&#10;")}"' if label else '')

        anchors = dock_anchors.get(edge.id)
        source_port = target_port = None
        link = pool_links.get(edge.id)
        if link is not None:
            port = (link.side, link.fraction)
            lane_is_source = source_key in lane_ids
            if lane_is_source:
                source_port = port
            else:
                target_port = port
            if link.track is None:
                # Фигура сбоку от полосы: конец ведёт общая трассировка.
                given = anchors or (edge.exitX, edge.exitY, edge.entryX, edge.entryY)
                anchors = ((*_port_anchor(port), given[2], given[3]) if lane_is_source
                           else (given[0], given[1], *_port_anchor(port)))
        route = polyline(edge, src_node, tgt_node, placed, obstacles, corridors, anchors)
        position = label_position(edge, src_node)
        drawn_route = route
        if link is not None and link.track is not None:
            lane = link_lanes[link.lane_id]
            if lane_is_source:
                route = _dock_to_pool(route[::-1], link, lane, placed)[::-1]
                src_node = lane
            else:
                route = _dock_to_pool(route, link, lane, placed)
                tgt_node = lane
        corridors.occupy(route)
        route, anchors = _pix_route(route, src_node, tgt_node, placed, source_port, target_port)
        label_point = None
        if label and edge.drawioLabelPoint:
            label_point = (edge.drawioLabelPoint[0], edge.drawioLabelPoint[1])
        elif label and edge.drawioLabel:
            label_point = drawio_label_point(drawn_route, edge.drawioLabel)
        if label_point is not None:
            # Подпись встаёт на линию там, где её видит аналитик, — с учётом
            # смещения, которым он уводит текст с шагов (``drawio_label_point``).
            position = _percent_nearest(label_point, route, position)
        elif link is not None and link.track is not None and label:
            # Подход к полосе у линии свой, и доля длины draw.io указала бы на
            # другое место: подпись ставится туда, где её поставил аналитик.
            position = _carried_label_position(drawn_route, route, position)

        lines.append(
            f'  <connector id="{escape_xml(_pix_id(edge.id))}" type="step"{text_attr}'
            f' lineStyle="{line_style}"'
            f' sourceNodeId="{escape_xml(id_map[source_key])}"'
            f' targetNodeId="{escape_xml(id_map[target_key])}"{anchors}>'
        )
        lines.append(f'    <MarkerStart>{marker_start}</MarkerStart>')
        lines.append('    <MarkerMiddle />')
        lines.append(f'    <MarkerEnd>{marker_end}</MarkerEnd>')
        for index, (px, py) in enumerate(route):
            lines.append(f'    <waypoint x="{_coord(px)}" y="{_coord(py)}" index="{index}" />')
        lines.append(
            f'    <labelPosition>{position}</labelPosition>')
        lines.append('    <color>var(--fg-gray-primary)</color>')
        lines.append(f'    <fontSize>{int(round(edge.labelFontSize or _DRAWIO_EDGE_FONT_SIZE))}</fontSize>')
        lines.append('    <fontBold>false</fontBold>')
        lines.append('    <fontItalic>false</fontItalic>')
        lines.append('    <fontUnderline>false</fontUnderline>')
        lines.append('    <fontStrikethrough>false</fontStrikethrough>')
        lines.append('  </connector>')

    lines.append('</Map>')
    return slug, '\n'.join(lines) + '\n'


def generate_configuration_xml() -> str:
    """Каталог свойств и нотаций студии.

    Отдаётся эталонный файл PIX без изменений: имена элементов нотаций
    (``dfd_process``, ``c4_person``, ``app_component`` и т.д.) заданы студией,
    и собственная реконструкция каталога рискует не пройти её валидацию.
    """
    return _CONFIGURATION_PATH.read_text(encoding='utf-8')


def generate_main_xml(slugs: Sequence[str]) -> str:
    """Манифест пакета: каталог нотаций и карта на каждую страницу файла."""
    overrides = ''.join(
        f'  <Override PartName="/pm/maps/{escape_xml(slug)}.xml" ContentType="application/xml" />\n'
        for slug in slugs
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Types xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}">\n'
        '  <Override PartName="/pm/configuration.xml" ContentType="application/xml" />\n'
        f'{overrides}'
        '</Types>\n'
    )


#: Промежуток между страницами файла на общей карте PIX, px.
_PAGE_GAP = 400


def _shift_process(page: BusinessProcess, dx: int, dy: int) -> BusinessProcess:
    """Копия страницы, сдвинутая целиком: фигуры, дорожки, изломы и свободные концы."""
    moved = page.model_copy(deep=True)

    def geometry(g: Optional[Geometry]) -> Optional[Geometry]:
        return None if g is None else Geometry(x=g.x + dx, y=g.y + dy, width=g.width, height=g.height)

    def point(p: Optional[ProcessEdgePoint]) -> Optional[ProcessEdgePoint]:
        return None if p is None else ProcessEdgePoint(x=p.x + dx, y=p.y + dy)

    for shape in [*moved.nodes, *moved.lanes]:
        shape.geometry = geometry(shape.geometry)
        shape.drawioGeometry = geometry(shape.drawioGeometry)
    for edge in moved.edges:
        edge.points = [point(p) for p in edge.points]
        if edge.drawioPoints is not None:
            edge.drawioPoints = [point(p) for p in edge.drawioPoints]
        edge.sourcePoint = point(edge.sourcePoint)
        edge.targetPoint = point(edge.targetPoint)
        if edge.drawioLabelPoint is not None:
            edge.drawioLabelPoint = [edge.drawioLabelPoint[0] + dx, edge.drawioLabelPoint[1] + dy]
    return moved


def _page_bounds(page: BusinessProcess) -> Optional[Tuple[int, int, int, int]]:
    """Рамка страницы по фигурам в том виде, в каком они уедут в PIX."""
    frames = [n.drawioGeometry or n.geometry for n in [*page.nodes, *page.lanes]]
    if not frames:
        return None
    return (min(g.x for g in frames), min(g.y for g in frames),
            max(g.x + g.width for g in frames), max(g.y + g.height for g in frames))


def merged_pages(process: BusinessProcess) -> BusinessProcess:
    """Все страницы файла на одной карте: основная сверху, остальные под ней.

    Пакет с несколькими картами PIX не открывает: на 18 картах «Kredit
    so‘ndirish…» импорт отказывал с «Sequence contains more than one matching
    element», а дочерние карты студия заводит только своей декомпозицией. Поэтому
    страницы подпроцессов ложатся на ту же карту, одна под другой, каждая со
    своей шапкой — ни одна не теряется. Идентификаторы разводятся тем же
    префиксом, что и в BPMN: шаг, скопированный аналитиком с одной страницы на
    другую, сохраняет в draw.io свой идентификатор.
    """
    pages = list(process.subProcesses or [])
    if not pages:
        return process
    merged = process.model_copy(deep=True)
    merged.subProcesses = []
    bounds = _page_bounds(process)
    if bounds is None:
        return merged
    left, _top, _right, bottom = bounds
    cursor = bottom + _PAGE_GAP
    for index, page in enumerate(pages, 1):
        copy = prefixed_page(page, index)
        page_bounds = _page_bounds(copy)
        if page_bounds is None:
            continue
        top = page_bounds[1]
        lanes_top = min((lane.geometry.y for lane in copy.lanes), default=top)
        # Своей шапки у страницы нет — ставим имя страницы, иначе на общей
        # карте не понять, где кончается одна часть процесса и начинается другая.
        titled = any(n.type == 'textAnnotation' and n.geometry.y + n.geometry.height <= lanes_top
                     for n in copy.nodes)
        if not titled:
            copy.nodes.append(ProcessNode(
                id=f'p{index}_page_title', name=page.name, type='textAnnotation',
                geometry=Geometry(x=page_bounds[0], y=top - _TITLE_POOL_HEIGHT - 40,
                                  width=max(page_bounds[2] - page_bounds[0], _TITLE_POOL_WIDTH),
                                  height=_TITLE_POOL_HEIGHT),
                style='fontSize=28;'))
            top -= _TITLE_POOL_HEIGHT + 40
        shifted = _shift_process(copy, left - page_bounds[0], cursor - top)
        merged.nodes.extend(shifted.nodes)
        merged.lanes.extend(shifted.lanes)
        merged.edges.extend(shifted.edges)
        cursor = _page_bounds(shifted)[3] + _PAGE_GAP
    return merged


def generate_pmm_zip(process: BusinessProcess) -> bytes:
    """Нативный пакет студии: каталог нотаций и одна карта со всеми страницами файла.

    Карта в пакете ровно одна, как в собственной выгрузке студии
    (``tests/fixtures/sap.pmm``): страницы-подпроцессы ложатся на неё же
    (``merged_pages``).
    """
    slug, map_xml = generate_map_xml(merged_pages(process))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('main.xml', generate_main_xml([slug]).encode('utf-8'))
        zf.writestr('pm/configuration.xml', generate_configuration_xml().encode('utf-8'))
        zf.writestr(f'pm/maps/{slug}.xml', map_xml.encode('utf-8'))
    return buf.getvalue()
