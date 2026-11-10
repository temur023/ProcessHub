"""Предпроверка исходной карты draw.io — до того, как собирать выгрузку.

Зачем отдельный модуль, если рядом уже лежит ``export_validation``. Тот
проверяет РЕЗУЛЬТАТ: собранный ``.bpmn`` и ``.pmm``. Проверка результата ловит
поломки экспортёра, но бессильна против кривого исходника: конвертер честно
нормализует то, что ему дали, и на выходе получается формально валидный файл,
который в студии открывается без половины связей или с шагами не в тех
дорожках. Сотрудник видит «импорт прошёл», а карта не та.

Два уровня, и граница между ними проведена по одному признаку — что именно
сделает PIX:

``error``
    Процессная студия откажется открыть файл. Ровно те дефекты, на которых она
    выдаёт свои короткие сообщения: повторяющийся идентификатор, связь из фигуры
    в саму себя («Connector source and target node cannot be the same»), фигура
    без размера, недопустимый символ в подписи, нечитаемый XML. Выгрузка при
    таком дефекте не собирается: битый файл никому не нужен.

``warning``
    Файл откроется, но карта будет не той, что рисовал автор. Отдельно помечены
    замечания с потерей содержимого (``loses_data``): брошенная стрелка,
    пропущенная страница, шаг вне дорожки. По умолчанию они не останавливают
    выгрузку — PIX такой файл принимает, — но в строгом режиме
    (``ensure_exportable(strict=True)``) становятся блокирующими: подразделению,
    которое сдаёт регламент, потеря связи дороже лишнего круга правок.

Ничего не «чинится» молча: задача модуля — назвать проблему словами, по
которым её видно в draw.io, и указать фигуру, к которой она относится.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.services.drawio_parser import (
    _parent_origin,
    classify_vertex,
    duration_badge_minutes,
    extract_graph_xml,
    is_clipart_style,
    page_report,
    unsupported_shape,
    unwrap_objects,
)

#: Символы, которые XML 1.0 не принимает ни в подписи, ни в атрибуте.
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

#: Разметка draw.io внутри подписи — её надо снять, прежде чем показывать текст.
_TAGS = re.compile(r'<[^>]+>')

#: Служебные ячейки mxGraph: корень и слой. Фигурами не являются.
_ROOT_CELLS = {'0', '1'}

#: Сколько фигур перечисляем поимённо, прежде чем перейти на «и ещё N».
_SAMPLE_LIMIT = 3


@dataclass(frozen=True)
class SourceProblem:
    """Одно замечание к исходной карте."""

    level: str  # 'error' | 'warning'
    code: str
    #: Описание проблемы так, как её видно в draw.io.
    message: str
    #: id ячейки в файле — по нему фигура ищется через «Правка → Найти».
    where: Optional[str] = None
    #: Что сделать, чтобы починить.
    hint: Optional[str] = None
    #: Часть карты не доедет до PIX. В строгом режиме такое тоже блокирует.
    loses_data: bool = False


@dataclass
class PrecheckResult:
    """Итог предпроверки исходной карты."""

    filename: str
    problems: List[SourceProblem] = field(default_factory=list)
    #: Имя страницы, которая пойдёт в выгрузку.
    page: str = ''
    #: Имена остальных страниц — они в выгрузку не попадут.
    skipped_pages: List[str] = field(default_factory=list)
    shapes: int = 0
    steps: int = 0
    lanes: int = 0
    edges: int = 0

    @property
    def errors(self) -> List[SourceProblem]:
        """Дефекты, на которых студия откажется открыть файл."""
        return [p for p in self.problems if p.level == 'error']

    @property
    def warnings(self) -> List[SourceProblem]:
        return [p for p in self.problems if p.level == 'warning']

    @property
    def data_loss(self) -> List[SourceProblem]:
        """Замечания, из-за которых часть карты не доедет до PIX."""
        return [p for p in self.problems if p.loses_data]

    @property
    def ok(self) -> bool:
        """Соберётся ли файл, который студия примет."""
        return not self.errors

    def blocking(self, strict: bool = False) -> List[SourceProblem]:
        """Что останавливает выгрузку при выбранной строгости."""
        if not strict:
            return self.errors
        return self.errors + [
            p for p in self.problems if p.loses_data and p.level != 'error'
        ]

    def message(self, strict: bool = False) -> str:
        """Текст уведомления сотруднику — тот, что уходит в интерфейс и в лог."""
        problems = self.blocking(strict)
        if not problems:
            return f'Карта «{self.filename}» пригодна для выгрузки в PIX.'
        head = (
            f'Внимание: при импорте в PIX возникнет ошибка, '
            f'так как в draw.io файле «{self.filename}»'
        )
        if len(problems) == 1:
            single = problems[0]
            tail = f' — {single.hint}' if single.hint else ''
            return f'{head} {single.message}{tail}'
        counted = _plural(
            len(problems), 'блокирующая проблема', 'блокирующие проблемы', 'блокирующих проблем'
        )
        lines = [f'{head} найдено {counted}:']
        for index, problem in enumerate(problems, 1):
            line = f'  {index}. {problem.message[0].upper()}{problem.message[1:]}'
            if problem.hint:
                line += f' — {problem.hint}'
            lines.append(line)
        return '\n'.join(lines)


class DrawioPrecheckError(Exception):
    """Исходник заведомо не даст верную карту в PIX. Выгрузка не собирается."""

    def __init__(self, result: PrecheckResult, strict: bool = False):
        super().__init__(result.message(strict))
        self.result = result
        self.strict = strict


# ─────────────────────────────── разбор ──────────────────────────────────────

def _plain(value: Optional[str]) -> str:
    """Подпись фигуры без разметки draw.io и лишних пробелов."""
    text = _TAGS.sub(' ', value or '')
    text = (
        text.replace('&nbsp;', ' ')
        .replace('&amp;', '&')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&quot;', '"')
        .replace('&#39;', "'")
    )
    return re.sub(r'\s+', ' ', text).strip()


def _title(cell: Optional[ET.Element]) -> str:
    """Как назвать фигуру в сообщении: подписью, а если её нет — идентификатором."""
    if cell is None:
        return 'неизвестной фигуры'
    label = _plain(cell.get('value'))
    if label:
        return f'«{label[:60]}»' + ('…' if len(label) > 60 else '')
    return f'без подписи (id {cell.get("id")})'


def _is_lane(style: str) -> bool:
    lowered = (style or '').lower()
    return 'swimlane' in lowered or 'shape=pool' in lowered


def _is_decor(style: str) -> bool:
    """Оформление, а не шаг: текстовая заметка, картинка, легенда.

    Такие фигуры не участвуют в потоке, и требовать от них связей нельзя.
    Картинку из библиотеки (телефон, поезд, здание) импорт тоже отбрасывает как
    украшение — и говорит об этом отдельным замечанием ``clipart_skipped``, — а
    значит и здесь она не шаг.
    """
    lowered = (style or '').lower()
    return (
        lowered.startswith('text')
        or 'text;' in lowered
        or 'image;' in lowered
        or 'shape=image' in lowered
        or is_clipart_style(style)
    )


def _is_artifact(style: str) -> bool:
    """Хранилище, документ, примечание: содержимое карты, но не шаг регламента.

    Спрашиваем сам классификатор разбора, а не ведём второй список: пока список
    был свой, предпроверка требовала дорожку и связи от цилиндра «EHA» и от
    листа документа. На 22 картах банка из 75 фигур, о которых она сообщала
    «шаг не соединён ни с чем», настоящими шагами были восемь.
    """
    return classify_vertex(style, '', False, False, '') in (
        'dataStore', 'dataObject', 'textAnnotation',
    )


def _never_steps(
    vertices: List[ET.Element],
    edges: List[ET.Element],
    lanes: List[ET.Element],
) -> Set[ET.Element]:
    """Фигуры, которые шагом не станут ни при каком исходе разбора.

    Три случая, и все три импорт разбирает не как шаг:

    * подпись стрелки — она уезжает в имя связи;
    * значок часов у шага — он уезжает в ST/WT этого шага;
    * фигура, вложенная в другую фигуру (а не в дорожку), — так аналитик
      кладёт значок или иконку поверх шага, и импорт берёт только сам шаг.

    Спрашивать с таких ячеек дорожку или связь бессмысленно: это не потеря
    содержимого, а обычное устройство карты.
    """
    edge_ids = {e.get('id') for e in edges}
    lane_ids = {l.get('id') for l in lanes}
    vertex_ids = {v.get('id') for v in vertices}
    linked = {ref for e in edges for ref in (e.get('source'), e.get('target')) if ref}

    aside: Set[ET.Element] = set()
    for cell in vertices:
        style = cell.get('style') or ''
        parent = cell.get('parent')
        if parent in edge_ids or 'edgelabel' in style.lower():
            aside.add(cell)
            continue
        if parent in vertex_ids and parent not in lane_ids:
            aside.add(cell)
            continue
        if cell.get('id') in linked:
            continue
        if duration_badge_minutes(style, _plain(cell.get('value'))) is not None:
            aside.add(cell)
    return aside


def _is_decorative_line(style: str) -> bool:
    """Линия без наконечника — выноска или разделитель, а не поток управления.

    Аналитики подчёркивают ими группы шагов и подписывают области. Требовать от
    такой линии привязки к фигурам нельзя: она никуда и не должна вести, а в
    выгрузке для PIX подобной конструкции всё равно нет.
    """
    lowered = (style or '').lower()
    has_start = 'startarrow=' in lowered and 'startarrow=none' not in lowered
    return 'endarrow=none' in lowered and not has_start


def _enumerate(items: List[str]) -> str:
    """«A, B и ещё 7» — перечисление, которое не разрастается на пол-экрана."""
    if len(items) <= _SAMPLE_LIMIT:
        return ', '.join(items)
    head = ', '.join(items[:_SAMPLE_LIMIT])
    return f'{head} и ещё {len(items) - _SAMPLE_LIMIT}'


def _plural(count: int, one: str, few: str, many: str) -> str:
    """«1 фигура», «2 фигуры», «5 фигур».

    Сообщение читает сотрудник, а не разработчик: «1 фигур(ы)» в уведомлении от
    банковской платформы выглядит как недоделка и подрывает доверие к остальному
    тексту.
    """
    mod100, mod10 = count % 100, count % 10
    if 11 <= mod100 <= 14:
        return f'{count} {many}'
    if mod10 == 1:
        return f'{count} {one}'
    if 2 <= mod10 <= 4:
        return f'{count} {few}'
    return f'{count} {many}'


def _delivered_edge_ids(content: str, filename: str) -> Optional[Set[str]]:
    """Идентификаторы связей, которые доедут до BPMN/PIX, глазами самого парсера.

    Предпроверке нужно отличить «конец не прицеплен, но платформа дотянула его до
    соседней фигуры» от «дотягивать не к чему, связь пропала». Повторять здесь
    правило притяжения значило бы завести вторую копию логики, которая разойдётся
    с первой при первой же правке. Поэтому спрашиваем у парсера: связь, дошедшая
    до модели обычным потоком (а не декоративной линией ``annotationLine``), —
    это та, что окажется в выгрузке.

    Возвращает ``None``, если модель построить не удалось: тогда о судьбе связей
    судить не по чему, и вызывающий код остаётся на разметке исходника.
    """
    try:
        from app.services.drawio_parser import parse_drawio_xml

        process = parse_drawio_xml(content, filename)
    except Exception:  # noqa: BLE001 — предпроверка не имеет права падать
        return None
    return {
        # Дубли идентификаторов парсер разводит суффиксом; исходное имя связи
        # нужно вернуть, чтобы сопоставить её с ячейкой в файле.
        edge.id.split('__dup')[0]
        for edge in process.edges
        if edge.kind != 'annotationLine' and edge.sourceId and edge.targetId
    }


def precheck_drawio(content: str, filename: str = 'схема.drawio') -> PrecheckResult:
    """Проверяет исходную карту draw.io и возвращает список замечаний.

    Исключений не бросает: решение о том, останавливать ли выгрузку, принимает
    вызывающий код — API это делает через :func:`ensure_exportable`, а отчётный
    скрипт хочет видеть замечания по всем файлам, а не падать на первом.
    """
    result = PrecheckResult(filename=filename)

    def add(level, code, message, where=None, hint=None, loses_data=False):
        result.problems.append(SourceProblem(level, code, message, where, hint, loses_data))

    if not (content or '').strip():
        add('error', 'empty_file', 'файл пустой.',
            hint='Сохраните схему в draw.io ещё раз')
        return result

    # ── Файл вообще открывается? ────────────────────────────────────────────
    try:
        graph_xml, is_bpmn = extract_graph_xml(content)
    except ET.ParseError as exc:
        add('error', 'not_xml', f'файл повреждён и не читается как XML ({exc}).',
            hint='Откройте схему в draw.io и сохраните заново')
        return result
    except ValueError as exc:
        add('error', 'not_drawio', f'{exc}.'.replace('..', '.'),
            hint='Ожидается .drawio или .bpmn')
        return result

    if is_bpmn:
        # Готовый BPMN проверяет export_validation: там правила формата, а не
        # правила рисования от руки.
        return result

    # О страницах файла замечание пишет разбор (``attach_sub_pages``): только он
    # знает, какая страница стала частью процесса, какая — вариантом, какая
    # оказалась таблицей и какую разобрать не удалось. Здесь стояло второе,
    # своё замечание — «остальные страницы едут в выгрузку», — и оно было
    # неправдой про варианты, таблицы и пустые страницы.
    result.page, result.skipped_pages = page_report(content)

    try:
        model = ET.fromstring(graph_xml)
    except ET.ParseError as exc:
        add('error', 'not_xml', f'страница «{result.page}» не читается как XML ({exc}).',
            hint='Откройте схему в draw.io и сохраните заново')
        return result

    # Фигура с собственными свойствами записана обёрткой `<object>`: её `id` и
    # подпись лежат НА обёртке, а не на `mxCell`. Без разворота предпроверка не
    # видит такую фигуру и объявляет удалёнными все связи, которые к ней идут.
    unwrap_objects(model)

    objects = {o.get('id'): o for o in model.iter('object')}
    by_id: Dict[str, ET.Element] = {}
    order: List[ET.Element] = []
    duplicates: List[Tuple[str, ET.Element]] = []
    for cell in model.iter('mxCell'):
        cid = cell.get('id')
        if cid is None:
            continue
        if cid in by_id:
            duplicates.append((cid, cell))
            continue
        by_id[cid] = cell
        order.append(cell)

    vertices = [c for c in order
                if c.get('vertex') == '1' and c.get('id') not in _ROOT_CELLS]
    edges = [c for c in order if c.get('edge') == '1']
    lanes = [c for c in vertices if _is_lane(c.get('style') or '')]
    decor = [c for c in vertices if _is_decor(c.get('style') or '')]
    # Шагом считаем только то, что шагом станет. Значок часов и подпись стрелки
    # — это фигуры mxGraph, но импорт переносит их в свойства шага и в имя
    # связи. Считая их шагами, предпроверка сообщала о потере содержимого там,
    # где ничего не терялось: на десяти картах банка «вне дорожек» и «не
    # соединены ни с чем» набиралось 619 и 655 фигур, а настоящих среди них
    # было 26 и 29. Настоящую потерю в таком отчёте не разглядеть.
    aside = _never_steps(vertices, edges, lanes)
    artifacts = [c for c in vertices if _is_artifact(c.get('style') or '')]
    steps = [c for c in vertices
             if c not in lanes and c not in decor and c not in aside and c not in artifacts]

    result.shapes = len(vertices)
    result.steps = len(steps)
    result.lanes = len(lanes)
    result.edges = len(edges)

    # ── Схема вообще есть? ──────────────────────────────────────────────────
    if not vertices:
        add('error', 'no_shapes',
            f'на странице «{result.page or "1"}» нет ни одной фигуры.',
            hint='Нарисуйте схему процесса или выберите нужную страницу')
        return result
    if not steps:
        add('error', 'no_steps',
            'на схеме нет ни одного шага — только дорожки и оформление.',
            hint='Добавьте задачи процесса внутрь дорожек')
        return result

    # ── Повторяющиеся идентификаторы ────────────────────────────────────────
    # Блокирующее: и xsd:ID в BPMN, и id фигуры в карте .pmm обязаны быть
    # уникальны, иначе студия отвергает пакет целиком.
    if duplicates:
        names = _enumerate([_title(c) for _, c in duplicates])
        add('error', 'duplicate_ids',
            f'{_plural(len(duplicates), "фигура несёт", "фигуры несут", "фигур несут")} '
            f'тот же идентификатор, что и другие: {names}.',
            where=duplicates[0][0],
            hint='Так бывает после склейки двух схем: пересоздайте дубли')

    # ── Ячейка сама себе предок ─────────────────────────────────────────────
    for cell in order:
        seen: Set[Optional[str]] = set()
        walk: Optional[ET.Element] = cell
        while walk is not None:
            wid = walk.get('id')
            if wid in seen:
                add('error', 'parent_cycle',
                    f'фигура {_title(cell)} вложена сама в себя.',
                    where=cell.get('id'),
                    hint='Вырежьте фигуру и вставьте заново')
                break
            seen.add(wid)
            walk = by_id.get(walk.get('parent') or '')

    # ── Связи ───────────────────────────────────────────────────────────────
    # Свободный конец сам по себе ещё не беда: платформа притягивает его к
    # фигуре под ним (30 px, FREE_ENDPOINT_SNAP в парсере), и связь доезжает до
    # PIX целой. Беда — когда притягивать не к чему: такая линия в выгрузку не
    # попадает, и в студии появляется схема с разорванным маршрутом. Поэтому
    # решает не разметка исходника, а судьба связи в собранной модели.
    linkable = {c.get('id') for c in vertices} | {c.get('id') for c in edges}
    delivered = _delivered_edge_ids(content, filename)

    snapped: List[Tuple[str, str]] = []
    lost: List[Tuple[str, str]] = []
    decorative = 0
    for edge in edges:
        eid = edge.get('id')
        src, tgt = edge.get('source'), edge.get('target')
        holder = objects.get(eid)
        label = _plain(edge.get('value')) or _plain(
            holder.get('label') if holder is not None else ''
        )
        title = f'«{label}»' if label else f'(id {eid})'

        # Связь фигуры с самой собой — то самое «Connector source and target
        # node cannot be the same», на котором студия отказывает.
        if src and src == tgt:
            add('error', 'edge_self_loop',
                f'стрелка {title} начинается и заканчивается на одной фигуре '
                f'{_title(by_id.get(src))}.',
                where=eid,
                hint='PIX такую связь не принимает: проведите её к другому шагу')
            continue

        for role, ref in (('источник', src), ('приёмник', tgt)):
            if ref and ref not in linkable:
                add('error', 'edge_broken_ref',
                    f'стрелка {title} ссылается на удалённую фигуру ({role}: {ref}).',
                    where=eid,
                    hint='Удалите стрелку и проведите её заново')

        if src and tgt:
            continue
        if delivered is not None and eid in delivered:
            snapped.append((eid or '', title))
            continue
        if _is_decorative_line(edge.get('style') or ''):
            decorative += 1
            continue
        lost.append((eid or '', title))

    for eid, title in lost:
        cell = by_id.get(eid)
        src = cell.get('source') if cell is not None else None
        tgt = cell.get('target') if cell is not None else None
        if not src and not tgt:
            add('warning', 'edge_detached',
                f'стрелка {title} не подключена ни к одной фигуре — оба её конца '
                'висят в воздухе, и в выгрузку для PIX она не попадёт.',
                where=eid,
                hint='Притяните концы стрелки к фигурам до появления зелёного кружка',
                loses_data=True)
        else:
            loose = 'начало не подключено' if not src else 'конец не подключён'
            neighbour = by_id.get((tgt if not src else src) or '')
            near = (f' (второй конец держится за {_title(neighbour)})'
                    if neighbour is not None else '')
            add('warning', 'edge_dangling',
                f'у стрелки {title} {loose} к фигуре{near}, и рядом нет фигуры, '
                'к которой её можно притянуть — связь в PIX потеряется.',
                where=eid,
                hint='Перетащите свободный конец на нужный шаг',
                loses_data=True)

    if snapped:
        add('warning', 'edge_snapped',
            f'{_plural(len(snapped), "стрелка нарисована", "стрелки нарисованы", "стрелок нарисованы")} '
            'рядом с фигурой, но не прицеплены к ней; платформа притянула '
            f'{"её" if len(snapped) == 1 else "их"} к ближайшей: '
            f'{_enumerate([t for _, t in snapped])}.',
            where=snapped[0][0],
            hint='Проверьте, что связь ведёт к нужному шагу')

    if decorative:
        add('warning', 'decorative_line',
            f'{_plural(decorative, "линия без наконечника", "линии без наконечника", "линий без наконечника")} '
            'принята за оформление и в выгрузку не пойдёт.',
            hint='Если это связь процесса, включите ей стрелку и прицепите концы')

    # ── Геометрия ───────────────────────────────────────────────────────────
    # Подпись на связи («Да», «Нет») — тоже vertex, но размера у неё нет и не
    # должно быть: она позиционируется вдоль линии (relative="1"). Считать её
    # фигурой нулевого размера значило бы блокировать любую схему с ветвлением.
    #
    # Подпись, отцепленную от своей связи, узнаём по стилю. Аналитик оттаскивает
    # её мышью, чтобы не лежала на линии, и draw.io перекладывает ячейку на слой:
    # ни ``relative``, ни родителя-связи у неё больше нет — только ``edgeLabel``
    # в стиле и координаты. Размер редактор по-прежнему считает по тексту и
    # рисует её без единого замечания (проверено на app.diagrams.net). Пока
    # такие подписи шли в ошибки, карта «AVGUST-1.29» не открывалась из-за трёх
    # «фигур нулевого размера», которых на схеме нет: это «XA» и «YO'Q».
    edge_ids = {c.get('id') for c in edges}
    zero_sized = []
    for cell in vertices:
        if 'edgelabel' in (cell.get('style') or '').lower():
            continue
        geo = cell.find('mxGeometry')
        if geo is not None and (geo.get('relative') == '1' or cell.get('parent') in edge_ids):
            continue
        if geo is None:
            zero_sized.append(cell)
            continue
        try:
            width = float(geo.get('width') or 0)
            height = float(geo.get('height') or 0)
        except ValueError:
            zero_sized.append(cell)
            continue
        if width <= 0 or height <= 0:
            zero_sized.append(cell)
    if zero_sized:
        add('error', 'zero_size',
            f'{_plural(len(zero_sized), "фигура имеет", "фигуры имеют", "фигур имеют")} '
            f'нулевой размер: {_enumerate([_title(c) for c in zero_sized])}.',
            where=zero_sized[0].get('id'),
            hint='Задайте фигуре ширину и высоту — PIX рисует её по этим числам')

    # ── Управляющие символы ─────────────────────────────────────────────────
    tainted = [c for c in order + list(objects.values())
               if _CONTROL_CHARS.search(c.get('value') or c.get('label') or '')]
    if tainted:
        add('error', 'control_chars',
            f'{_plural(len(tainted), "подпись содержит", "подписи содержат", "подписей содержат")} '
            'управляющие символы, недопустимые в XML.',
            where=tainted[0].get('id'),
            hint='Перенаберите подпись вручную — текст вставлен из PDF или Word')

    # ── Дорожки: без них некому назначить ответственного ────────────────────
    if not lanes:
        add('warning', 'no_lanes',
            'нет ни одного пула или дорожки (swimlane), '
            f'хотя шагов на схеме {len(steps)} — в PIX у них не будет '
            'ни подразделения, ни роли.',
            hint='Оберните шаги в дорожки',
            loses_data=True)

    # ── Остальное: карта загрузится, но будет не такой ──────────────────────
    unknown: Dict[str, List[ET.Element]] = {}
    for cell in steps:
        kind = unsupported_shape(cell.get('style') or '')
        if kind:
            unknown.setdefault(kind, []).append(cell)
    for kind, cells_of_kind in unknown.items():
        add('warning', 'unsupported_shape',
            f'платформа не распознала {kind} — на схеме '
            f'{_plural(len(cells_of_kind), "такая фигура", "такие фигуры", "таких фигур")}; '
            'в выгрузке они станут обычными задачами.',
            where=cells_of_kind[0].get('id'),
            hint='Замените на фигуру BPMN, если смысл был другой')

    origin_cache: Dict[str, Tuple[float, float]] = {}

    def _box(cell: ET.Element) -> Optional[Tuple[float, float, float, float]]:
        geo = cell.find('mxGeometry')
        if geo is None:
            return None
        try:
            ox, oy = _parent_origin(cell.get('parent'), by_id, origin_cache)
            return (
                float(geo.get('x', '0') or 0) + ox,
                float(geo.get('y', '0') or 0) + oy,
                float(geo.get('width', '0') or 0),
                float(geo.get('height', '0') or 0),
            )
        except ValueError:
            return None

    lane_boxes = [b for b in (_box(l) for l in lanes) if b is not None]

    def _inside_a_lane(cell: ET.Element) -> bool:
        box = _box(cell)
        if box is None:
            return True
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        return any(
            lx <= cx <= lx + lw and ly <= cy < ly + lh
            for lx, ly, lw, lh in lane_boxes
        )

    linked: Set[str] = set()
    for edge in edges:
        for ref in (edge.get('source'), edge.get('target')):
            if ref:
                linked.add(ref)
    # Фигура вне пула, ни с чем не соединённая, — заголовок карты, а не шаг:
    # разбор делает её примечанием (см. ``caption_ids`` в ``drawio_parser``).
    # Спрашивать с неё связи незачем — их там и не рисуют.
    isolated = [
        c for c in steps
        if c.get('id') not in linked and (not lane_boxes or _inside_a_lane(c))
    ]
    if isolated:
        add('warning', 'isolated_step',
            f'{_plural(len(isolated), "шаг не соединён", "шага не соединены", "шагов не соединены")} '
            f'ни с чем: {_enumerate([_title(c) for c in isolated])}.',
            where=isolated[0].get('id'),
            hint='В PIX такой шаг попадёт на карту, но не в маршрут процесса')

    if lanes:
        # Дорожку задаёт геометрия, а не родитель ячейки. Аналитик рисует шаг
        # поверх дорожки, и редактор оставляет его ребёнком холста: в «1.54»
        # так лежат все 57 фигур, о которых предпроверка сообщала «вне
        # дорожек», хотя на рисунке они внутри полос. По 22 картам банка счёт
        # выходил 3507 против 54 настоящих, и настоящие в нём терялись.
        # Ту же меру — центр фигуры внутри рамки полосы — применяет разбор
        # (``_lane_under`` в ``drawio_parser``).
        outside = [c for c in steps if not _inside_a_lane(c)]
        if outside:
            add('warning', 'step_outside_lane',
                f'{_plural(len(outside), "шаг лежит", "шага лежат", "шагов лежат")} '
                f'вне дорожек: {_enumerate([_title(c) for c in outside])}.',
                where=outside[0].get('id'),
                hint='У шага вне дорожки нет ответственного подразделения',
                loses_data=True)

    return result


def ensure_exportable(
    content: str,
    filename: str = 'схема.drawio',
    strict: bool = False,
) -> PrecheckResult:
    """Предпроверка перед выгрузкой: при блокирующем дефекте бросает исключение.

    Точка, через которую проходит любая сборка ``.bpmn``/``.pmm`` из исходника.
    Собирать файл, который студия не откроет, бессмысленно — он только отнимет у
    сотрудника ещё один заход.

    ``strict=True`` дополнительно останавливает выгрузку там, где карта
    загрузится, но часть содержимого до PIX не доедет (брошенные связи,
    пропущенные страницы, шаги вне дорожек).
    """
    result = precheck_drawio(content, filename)
    if result.blocking(strict):
        raise DrawioPrecheckError(result, strict)
    return result
