import base64
import copy
import hashlib
import json
import zlib
import urllib.parse
import xml.etree.ElementTree as ET
import re
import uuid
from datetime import datetime
from typing import Dict, List, NamedTuple, Sequence, Set, Tuple, Optional
from xml.sax.saxutils import quoteattr

from app.models.process import (
    BusinessProcess,
    ProcessNode,
    ProcessEdge,
    ProcessEdgePoint,
    ProcessPassport,
    ProcessValidation,
    PixRegistrySchema,
    PixRegistryRecord,
    ProcessField,
    Geometry,
    NodeType,
    StepCategory,
    EdgeKind,
    ARTIFACT_NODE_TYPES,
    FLOW_NODE_TYPES,
    TASK_NODE_TYPES,
)
from app.services.conformance_engine import analyze_process_conformance
from app.services.diagnostics import collect_import_diagnostics
from app.services.drawio_orthogonal import orthogonal_route, perimeter_kind
from app.services.edge_routing import drawio_elbow_route, orthogonal_waypoints
from app.services.it_systems import canonical as canonical_system
from app.services.layout import normalize_layout
from app.services.map_layout import split_external_lanes

#: Хвост тега: атрибуты вплоть до закрывающей скобки. Значение в кавычках
#: пропускается целиком, потому что внутри него ``>`` — обычный символ, а не
#: конец тега. Наивное ``[^>]*>`` на таком значении обрывается посреди
#: атрибута, и весь остаток разметки вываливается в подпись как текст: подписи
#: с классами Tailwind (``class="[&>*]:pointer-events-auto …"``) приезжали в
#: Процессную студию вместе с версткой вместо названия шага.
_TAG_TAIL = r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*>'

#: Буфер обмена draw.io, попавший в подпись.
#:
#: Копируя фигуры, редактор кладёт в буфер их модель — ``<mxGraphModel>`` со
#: всеми ячейками, — и при вставке в текстовое поле она приезжает туда как
#: есть, обычно в процентной кодировке. Настоящая подпись при этом остаётся
#: в начале строки, а дальше тянется полотно разметки: на карте студии оно
#: разворачивается колонкой мусора через полсхемы.
_CLIPBOARD_PAYLOAD_RE = re.compile(
    r'(?:%3C|<)\s*mxGraphModel\b.*', re.IGNORECASE | re.DOTALL)

#: Фигуры-цилиндры draw.io: все варианты базы данных, какие есть в библиотеке.
_CYLINDER_SHAPE_RE = re.compile(r'^(?:cylinder\d*|datastore|db)$', re.IGNORECASE)

#: Теги, которые в draw.io означают перенос строки, а не оформление внутри неё.
_BLOCK_TAG_RE = re.compile(
    rf'<\s*/?\s*(?:br|div|p|li|ul|ol|tr|td|h[1-6])\b{_TAG_TAIL}', re.IGNORECASE)

#: Любой тег — снимается после блочных, уже без замены на пробел.
_ANY_TAG_RE = re.compile(rf'<\s*/?\s*[a-zA-Z][^\s/>]*{_TAG_TAIL}')

#: Комментарии и служебные объявления: внутри них ``>`` тоже допустим.
_COMMENT_RE = re.compile(r'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>', re.DOTALL)


def clean_label(raw: Optional[str]) -> str:
    """Текст подписи фигуры без разметки draw.io.

    Блочные теги заменяются пробелом (это перенос строки), а строчные снимаются
    без пробела. Разница существенная: редактор режет подпись тегом ``<span>``
    посреди слова, как только к части текста применили оформление, и пробел на
    этом месте разрывал число — «1 440 min» превращалось в «1 44» и «0 min»,
    а время шага уезжало с 1440 минут на 0.

    Теги снимаются с учётом кавычек: в подпись, вставленную копированием с
    веб-страницы, приезжает разметка, где значение атрибута само содержит
    ``>``. Разбор по первому попавшемуся ``>`` обрывался внутри атрибута, и
    остаток верстки уходил в название шага — в Процессной студии вместо
    «Avizlovchi bank …» стояло полстраницы классов и data-атрибутов.
    """
    if not raw:
        return ''
    text = raw.replace('&nbsp;', ' ')
    # Буфер обмена редактора обрезаем ДО снятия тегов: внутри него разметки
    # больше, чем текста, и снимать её по одному тегу незачем.
    text = _CLIPBOARD_PAYLOAD_RE.sub('', text)
    text = _COMMENT_RE.sub('', text)
    text = _BLOCK_TAG_RE.sub(' ', text)
    text = _ANY_TAG_RE.sub('', text)
    text = (
        text.replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&amp;', '&')
            .replace('&quot;', '"')
            .replace('&#39;', "'")
    )
    return ' '.join(text.split()).strip()


def label_lines(raw: Optional[str]) -> str:
    """Подпись с переносами строк, как её разбил аналитик: ``<div>``/``<br>`` — перенос.

    Та же чистка, что в ``clean_label``, только блочный тег даёт не пробел, а
    новую строку. Пустые строки и лишние пробелы внутри строки убираются.
    """
    if not raw:
        return ''
    text = raw.replace('&nbsp;', ' ')
    text = _CLIPBOARD_PAYLOAD_RE.sub('', text)
    text = _COMMENT_RE.sub('', text)
    text = _BLOCK_TAG_RE.sub('\n', text)
    text = _ANY_TAG_RE.sub('', text)
    text = (
        text.replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&amp;', '&')
            .replace('&quot;', '"')
            .replace('&#39;', "'")
    )
    return '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())

def inflate_diagram(data: str) -> str:
    clean_data = re.sub(r'\s+', '', data.strip())
    if not clean_data:
        raise ValueError('Пустое содержимое диаграммы')

    if clean_data.startswith('<') or '<mxGraphModel' in clean_data:
        return clean_data

    binary = base64.b64decode(clean_data)
    try:
        decompressed = zlib.decompress(binary, -zlib.MAX_WBITS)
    except Exception:
        try:
            decompressed = zlib.decompress(binary)
        except Exception:
            return binary.decode('utf-8', errors='ignore')

    decoded_str = decompressed.decode('utf-8', errors='ignore')
    try:
        return urllib.parse.unquote(decoded_str)
    except Exception:
        return decoded_str

def _model_vertex_count(model_xml: str) -> int:
    """Сколько фигур на странице draw.io: пустые страницы пропускаем."""
    try:
        root = ET.fromstring(model_xml)
    except ET.ParseError:
        return 0
    return sum(
        1 for c in root.iter('mxCell')
        if c.get('vertex') == '1' and c.get('id') not in ('0', '1')
    )


#: Фигуры, из которых draw.io собирает таблицу: сама таблица и её строки.
#: Ячейки — их дети, и стиль у ячейки любой (обычно ``partialRectangle``).
_TABLE_SHAPES = ('table', 'tablerow')


def table_members(cells: Sequence[ET.Element]) -> Dict[str, str]:
    """Ячейки таблиц draw.io: id фигуры -> id таблицы, которой она принадлежит.

    Таблица — это не фигуры процесса, а оформление карты: на странице «Tavsif»
    в «Akkreditiv ochish» и «Ecommerce+» так набраны сроки и затраты по строкам
    AS IS, TO BE и IQTISOD. Разбор этого не знал, и хуже того — строка таблицы
    несёт в стиле ``swimlaneHead=0``, поэтому таблица сходила за пул, строки —
    за дорожки, а тридцать ячеек становились шагами с часами: «78 daqiqa»
    читалось временем соседнего шага. В выгрузку страница уезжала процессом из
    тридцати шагов.

    Признак — в модели draw.io, а не в имени страницы: фигура принадлежит
    таблице, если таблица или её строка стоит в цепочке её родителей.
    """
    by_id = {c.get('id'): c for c in cells if c.get('id')}
    owner: Dict[str, str] = {}
    for cell in cells:
        cid = cell.get('id')
        if not cid or cell.get('vertex') != '1':
            continue
        table: Optional[str] = None
        seen: Set[str] = set()
        node: Optional[ET.Element] = cell
        while node is not None and node.get('id') not in seen:
            seen.add(node.get('id') or '')
            if (_style_map(node.get('style') or '').get('shape') or '').lower() in _TABLE_SHAPES:
                table = node.get('id')
            parent = node.get('parent')
            node = by_id.get(parent) if parent else None
        if table is not None:
            owner[cid] = table
    return owner


def _process_vertex_count(model_xml: str) -> int:
    """Сколько на странице фигур, кроме ячеек таблиц: есть ли там схема вообще."""
    try:
        root = ET.fromstring(model_xml)
    except ET.ParseError:
        return 0
    unwrap_objects(root)
    cells = root.findall('.//mxCell')
    tables = table_members(cells)
    return sum(
        1 for c in cells
        if c.get('vertex') == '1' and c.get('id') not in ('0', '1') and c.get('id') not in tables
    )


def _diagram_models(root: ET.Element) -> List[str]:
    """XML каждой страницы <diagram> в порядке следования в файле.

    Список идёт вровень с :func:`page_names`: у нечитаемой страницы на её месте
    пустая строка. Пропуск сдвигал бы все следующие страницы, и модель одной
    доставалась бы имени другой.
    """
    models: List[str] = []
    for diag in root.findall('.//diagram'):
        m = diag.find('.//mxGraphModel')
        if m is not None:
            models.append(ET.tostring(m, encoding='unicode'))
            continue
        r = diag.find('.//root')
        if r is not None:
            models.append(f"<mxGraphModel>{ET.tostring(r, encoding='unicode')}</mxGraphModel>")
            continue
        inner = (diag.text or '').strip()
        if not inner:
            models.append('')
            continue
        if '<mxGraphModel' in inner:
            models.append(inner)
            continue
        try:
            models.append(inflate_diagram(inner))
        except Exception:
            models.append('')
    return models


#: Имя сводной страницы на картах банка: «jami» по-узбекски — «всего».
SUMMARY_PAGE_NAME = 'jami'


def page_names(root: ET.Element) -> List[str]:
    """Имена страниц файла в порядке следования."""
    return [
        d.get('name') or f'Страница {i + 1}'
        for i, d in enumerate(root.findall('.//diagram'))
    ]


def main_page_index(names: Sequence[str], models: Sequence[str]) -> int:
    """Номер страницы, которую берём за основную карту процесса.

    Сводная страница «JAMI», если она в файле есть. «Jami» по-узбекски —
    «всего», и аналитики банка называют так страницу, на которой процесс
    собран целиком: в «Xizmat avtotransportini boshqarish jarayoni» на ней 511
    фигур против 149 на первой, а остальные страницы — его части (покупка,
    страхование, ремонт, топливо, продажа). Брать первую значило бы выдавать
    за весь процесс одну его пятую.

    Во всех прочих случаях — первая непустая, та же, что открывает draw.io.
    Соблазнительное «самая крупная страница» отвергнуто замером: в
    «Korporativ Markaz…» оно перевело бы карту с «As Is» на «To Be», а в
    «Asosiy hisob raqam ochish yangi» — на промежуточный вариант, который
    крупнее исходного на одну фигуру.

    Страница, на которой нет ничего, кроме таблиц, схемой не считается: иначе
    файл, начатый с «Tavsif», открывался бы таблицей вместо карты.
    """
    for index, name in enumerate(names):
        if (name or '').strip().lower() != SUMMARY_PAGE_NAME:
            continue
        if index < len(models) and _process_vertex_count(models[index]) > 0:
            return index
    for index, model in enumerate(models):
        if _process_vertex_count(model) > 0:
            return index
    return 0


#: Пометка состояния процесса в имени страницы: «As Is», «AS-IS», «To Be», «To-Be».
_VARIANT_PAGE_RE = re.compile(
    r'(?<![0-9a-zа-яё])(?:as[\s_-]*is|to[\s_-]*be)(?![0-9a-zа-яё])', re.IGNORECASE)


def is_variant_page_name(name: str) -> bool:
    """Страница — вариант процесса (AS IS / TO BE), а не его часть.

    Карты банка делят страницы файла двумя способами. Части процесса — покупка,
    страхование, ремонт в «Xizmat avtotransportini», «Konvertatsiya» в
    «Akkreditiv ochish» — это куски одной схемы, и в выгрузку они идут вместе
    с основной картой. Варианты — «As Is» и «To Be» в «Korporativ Markaz»,
    «AS IS ўзгариш» и «To-Be» в «Asosiy hisob raqam ochish yangi» — это тот
    же процесс в другом состоянии: вложить «To Be» в выгрузку «As Is» значит
    выдать два процесса за один. Аналитики помечают состояние в имени
    страницы, и другого признака в файле нет.
    """
    return bool(_VARIANT_PAGE_RE.search(name or ''))


class PagePlan(NamedTuple):
    """Что делать с каждой страницей многостраничного файла."""
    main: str
    #: (номер страницы в файле, имя)
    parts: List[Tuple[int, str]]
    variants: List[Tuple[int, str]]
    #: Страницы, где кроме таблиц ничего нет: схемы процесса на них нет.
    tables: List[str]
    empty: List[str]


def plan_pages(content: str) -> Optional[PagePlan]:
    """Классификация страниц файла; ``None`` — если страница одна.

    Одна мера на всех, кто говорит о страницах: разбор решает по ней, куда
    положить страницу, а предпроверка и отчёт импорта — что о ней сказать.
    Пока каждый считал по-своему, подсказка обещала «на холст не выводятся»
    про страницы, открытые вкладками, и «идут в выгрузку» про пустые.
    """
    trimmed = (content or '').strip()
    if '<mxfile' not in trimmed and '<diagram' not in trimmed:
        return None
    try:
        root = ET.fromstring(trimmed)
    except ET.ParseError:
        return None
    if len(root.findall('.//diagram')) < 2:
        return None
    names = page_names(root)
    models = _diagram_models(root)
    main = main_page_index(names, models)
    parts: List[Tuple[int, str]] = []
    variants: List[Tuple[int, str]] = []
    tables: List[str] = []
    empty: List[str] = []
    for index, (name, model) in enumerate(zip(names, models)):
        if index == main:
            continue
        if _model_vertex_count(model) <= 0:
            empty.append(name)
        elif _process_vertex_count(model) <= 0:
            tables.append(name)
        elif is_variant_page_name(name):
            variants.append((index, name))
        else:
            parts.append((index, name))
    return PagePlan(names[main] if main < len(names) else '', parts, variants, tables, empty)


def _quoted(names: Sequence[str]) -> str:
    return ', '.join(f'«{n}»' for n in names)


def pages_hint(plan: PagePlan, failed: Sequence[Tuple[str, str]] = ()) -> str:
    """Что стало с каждой страницей — по одному предложению на вид страницы."""
    sentences: List[str] = []
    parts = [name for _, name in plan.parts if name not in dict(failed)]
    variants = [name for _, name in plan.variants if name not in dict(failed)]
    if parts:
        sentences.append(
            f'{_quoted(parts)} — часть процесса: открывается вкладкой над картой и идёт '
            'в выгрузку своим процессом.'
            if len(parts) == 1 else
            f'{_quoted(parts)} — части процесса: открываются вкладками над картой и идут '
            'в выгрузку своими процессами.')
    if variants:
        sentences.append(
            f'{_quoted(variants)} — вариант процесса: открывается вкладкой над картой, но '
            'в выгрузку этой карты не идёт.'
            if len(variants) == 1 else
            f'{_quoted(variants)} — варианты процесса: открываются вкладками над картой, но '
            'в выгрузку этой карты не идут.')
    if plan.tables:
        one = len(plan.tables) == 1
        sentences.append(
            f'{_quoted(plan.tables)} — {"таблица, а не схема" if one else "таблицы, а не схемы"} '
            f'процесса: на холст и в выгрузку {"не идёт" if one else "не идут"}.')
    if plan.empty:
        sentences.append(
            f'{_quoted(plan.empty)} — {"пустая страница" if len(plan.empty) == 1 else "пустые страницы"}.')
    for name, reason in failed:
        sentences.append(f'«{name}» разобрать не удалось ({reason}): на холст и в выгрузку она не попала.')
    return ' '.join(sentences)


def page_report(content: str) -> Tuple[str, List[str]]:
    """Имя основной страницы и имена остальных.

    Нужно, чтобы сотрудник видел, что файл многостраничный и какая страница
    взята за основную. Остальные больше не пропадают — они разбираются
    подпроцессами (``attach_sub_pages``), — но знать их состав всё равно надо:
    по списку видно, что в карту приехало.
    """
    trimmed = (content or '').strip()
    if '<mxfile' not in trimmed and '<diagram' not in trimmed:
        return '', []
    try:
        root = ET.fromstring(trimmed)
    except ET.ParseError:
        return '', []
    diagrams = root.findall('.//diagram')
    if len(diagrams) < 2:
        return (diagrams[0].get('name') or 'Страница 1') if diagrams else '', []

    names = page_names(root)
    used = main_page_index(names, _diagram_models(root))
    return names[used], [n for i, n in enumerate(names) if i != used]


def extract_graph_xml(content: str) -> Tuple[str, bool]:
    """XML одной карты процесса + признак «это BPMN 2.0, а не draw.io».

    Порядок проверок важен: файл ``.drawio`` — это ``<mxfile>`` со страницами,
    и внутри него тоже встречается подстрока ``<mxGraphModel``. Если сначала
    искать модель, многостраничный файл разбирается как его первая страница
    случайно, а сжатый — попадает в другую ветку и разбирается иначе.

    Страницы НЕ склеиваются в один холст: в картах банка это либо варианты
    одного процесса (AS-IS, AS-IS с изменениями, TO-BE), либо его части, и
    наложение давало одну нечитаемую схему с дублями шагов. Здесь возвращается
    ОСНОВНАЯ страница (``main_page_index``); остальные разбираются отдельными
    картами в ``attach_sub_pages`` и едут в выгрузку своими процессами.
    """
    trimmed = content.strip()

    if any(k in trimmed for k in ('<definitions', '<bpmn:definitions', '<bpmn2:definitions', '<bpmn:process')):
        return trimmed, True

    if '<mxfile' in trimmed or '<diagram' in trimmed:
        root = ET.fromstring(trimmed)
        if root.findall('.//diagram'):
            models = _diagram_models(root)
            if not any(models):
                raise ValueError('Не удалось извлечь ни одной диаграммы из mxfile')
            return models[main_page_index(page_names(root), models)], False

    if '<mxGraphModel' in trimmed:
        root = ET.fromstring(trimmed)
        if root.tag == 'mxGraphModel':
            return trimmed, False
        model = root.find('.//mxGraphModel')
        if model is not None:
            return ET.tostring(model, encoding='unicode'), False

    raise ValueError('Файл не распознан как диаграмма draw.io или BPMN 2.0 XML')

SYSTEM_TAGS = {
    'iabs', 'iabs / crm', 'iabs / eha', 'eha', 'edo', 'zoom', 'crobs', 'excell rmr',
    'dragle bi', 'nibbd', 'soliq', 'katm', 'didox', 'myorg.uz', 'ihamkor', 'orginfo',
    'registr.stat.uz', 'jira', 'e-baholash.uz', 'garov.uz', 'davreestr.uz', 'korporativ pochta',
    'tsoyat', 'intranet', 'emv service', 'sqb crm', 'internet saytlari', 'crobs, internet saytlari',
    'internet saytlari, tsoyat, crobs', 'internet saytlari va iabs', 'internet saytlari tsoyat',
    'iabs, regstr.uz', 'iabs, garov.uz, davreestr.uz', 'iabs, garov.uz, davrestr.uz', 'iabs, crobs'
}

ARTIFACT_TAGS = {
    'dalolatnoma', 'chek-list', "yig'ma jild", 'yig‘majild', 'yig‘ma jild', 'asoslantiruvchi xat',
    'fotosuratlar', 'fotosuratlar va hujjatlar', "hujjatlar ro'yxati", 'hujjatlar',
    'xulosa', 'loyiha hujjatlari', "ko'chirma", 'kuzatuv kengash bayonnomasi',
    'yirik bitimlar bayonnomasi', 'kredit/sug‘urta/kafillik', 'kredit/sug\'urta/kafillik',
    'qo‘shimcha kelishuv', "qo'shimcha kelishuv", 'shartnoma', 'baholash dalolatnomasi',
    'garov xulosasi', "yig'ilish bayonnomasi", 'yuriskonsult xulosasi', 'qaror loyihasi',
    'asoslantirilgan xat', 'moliyaviy hisobotlar', 'skaner', 'kredit/garov/kafillik shartnomasi',
    'kredit/kafillik/sug\'urta shartnomasi', 'hukumat qarori', 'tegishli qaror', "ma'lumotnoma",
    'mijoz murojaati, ta`sischilar qarori', 'ta`sischilar qarori'
}

CONDITION_TAGS = {
    "ha", "yo'q", "yo`q", "yo’q", "да", "нет", "yes", "no",
    "to'liq", "to'liq emas", "to`liq", "to`liq emas",
    "mos keldi", "mos kelmaydi", "mos kelmadi", "to'liq mos keladi",
    "manba aniqlandi", "qabul qilindi", "rad etildi", "rad javob berildi",
    "asoslantirilgan rad javob berildi", "mulkiy", "nomulkiy", "o'rganildi", "bajarildi",
    "nazorat uchun", "ijobiy", "salbiy", "kamchilik mavjudmi", "kamchiliklar mavjudmi",
    "kamchilik mavjudmi?", "kamchiliklar mavjudmi?", "to'g'ri rasmiylashtirilganmi?",
    "tog'ri rasmiylashtirilganmi?", "barcha ma'lumotlar to'g'ri kiritilganmi?",
    "barcha hujjatlar mavjudmi", "hujjatlar to'liqmi?", "hujjatlar to'plami to'liqmi?",
    "resurs mablag'lari mavjudmi?", "muzokara ijobiymi?", "muqobil resurs aniqlandimi ?",
    "vakolatli organ qarori ijobiymi?", "qo'mita qarori ijobiymi?", "qaror qabul qilish qo'mita vakolatidami?",
    "kredit maqsadli ishlatilganmi?", "mijoz talabi kredit mahsuloti shartlariga muvofiqmi?",
    "garov obyekti qiymati mustaqil baholovchining hisoboti bilan mosligini o'rganish"
}

def is_clipart_style(style: str) -> bool:
    """Картинка из библиотеки draw.io — украшение схемы, а не шаг процесса.

    На картах SQB встречаются иконки телефона, поезда, здания: аналитик ставит
    их рядом с шагом «для наглядности». Фигурой BPMN такая картинка не является
    никогда, но раньше безымянная иконка со случайной линией превращалась в
    полноценную «Операцию STEP-NN» и попадала в регламент.
    """
    s = (style or '').lower()
    return (
        s.startswith('image;')
        or ';image=' in s
        or 'shape=image' in s
        or 'shape=mxgraph.signs' in s
        # Библиотека Office — тот же клипарт: телефон, монитор, здание. Иконка
        # телефона с подписью-времени превращалась в «Операцию STEP-NN» и
        # ложилась поверх ряда шагов.
        #
        # Кроме документов: ``office.concepts.documents`` — это лист бумаги,
        # который аналитик ставит рядом с шагом и ведёт к нему пунктир. Это
        # артефакт процесса, а не украшение, и он разбирается ниже как документ.
        or ('shape=mxgraph.office.' in s and 'office.concepts.documents' not in s)
    )


def is_pictogram_style(style: str) -> bool:
    """Значок из готовой библиотеки draw.io: телефон, поезд, здание.

    От просто вставленной картинки отличается тем, что у него есть имя фигуры
    из набора (``shape=mxgraph.signs.…``, ``shape=mxgraph.office.…``). Такой
    значок шагом не бывает никогда, даже подписанный: подпись называет сам
    значок, а не операцию. В «1.54» рядом с тремя шагами стоит иконка телефона
    с подписью «telegram» — она становилась «Операцией STEP-NN», ложилась
    поверх шага и запирала его: подпись шага перестала помещаться в рамку,
    потому что расширяться было некуда.

    Просто картинка (``image;``) с подписью шагом остаться может: так на старых
    картах нарисованы «Avtomat sms xabarnoma yuborish» и «Skaner».
    """
    s = (style or '').lower()
    return (
        'shape=mxgraph.signs' in s
        or ('shape=mxgraph.office.' in s and 'office.concepts.documents' not in s)
    )


def is_text_overlay(style: str) -> bool:
    """Текстовая накладка draw.io: подпись поверх холста, а не фигура процесса.

    В draw.io стиль ``text;`` — это просто текст без рамки и заливки. На картах
    им подписывают безымянные шлюзы и документы, а в повёрнутом виде — саму
    дорожку. Обрамлённая врезка (``strokeColor`` задан) — исключение: это
    примечание к процессу, его отбирает :func:`is_text_note`.
    """
    s = (style or '').lower()
    return 'text;' in s and 'swimlane' not in s and 'edgelabel' not in s


#: Фигуры draw.io, которые импортёр разбирает по имени.
_KNOWN_SHAPE_RE = re.compile(
    r'mxgraph\.bpmn\.'
    r'|mxgraph\.flowchart\.annotation'
    r'|^(?:datastore|dataobject|note|rhombus|ellipse|process|pool|swimlane|image)$'
)


def _shape_is_understood(style: str, shape: str) -> bool:
    """Разбирает ли импортёр фигуру с таким ``shape=`` — спрашиваем у него же.

    Список знакомых имён здесь был вторым, отдельным от :func:`classify_vertex`,
    и они разошлись: цилиндр ``cylinder3``, база ``mxgraph.flowchart.database``,
    выноска ``callout``, двойной круг ``doubleellipse`` и лист
    ``office.concepts.documents`` давно разбираются правильно, а предпроверка
    всё равно докладывала «платформа не распознала фигуру… в выгрузке они
    станут обычными задачами». На десяти картах банка таких сообщений 46 из 50,
    и настоящее предупреждение тонуло среди них.

    Поэтому не ведём второй список, а спрашиваем сам классификатор: фигура
    понята, если он выводит из неё что-то, кроме «обычной задачи» — задача и
    есть то самое молчаливое «ближайшее похожее», о котором надо предупредить.
    """
    if _KNOWN_SHAPE_RE.search(shape):
        return True
    return classify_vertex(style, '', False, False, '') != 'userTask'


#: Виды шлюзов, у которых на карте банка есть свой смысл.
_KNOWN_GATEWAY_KINDS = frozenset({
    '', 'none', 'exclusive', 'xor', 'parallel', 'and',
    'inclusive', 'or', 'complex', 'multiple',
})
#: Значки событий, которые модель различает.
_KNOWN_EVENT_SYMBOLS = frozenset({
    '', 'none', 'general', 'timer', 'message', 'terminate', 'terminate2',
})


def unsupported_shape(style: str) -> Optional[str]:
    """Чего импортёр в фигуре не понял — короткой строкой для отчёта.

    Молча подставлять «ручную операцию» вместо незнакомой фигуры нельзя:
    аналитик рисует схему в draw.io и вправе считать, что видит на карте банка
    то же самое. Если фигура или её значок платформе неизвестны, она обязана
    сказать об этом, а не тихо заменить смысл ближайшим похожим.
    """
    lowered = (style or '').lower()
    if 'swimlane' in lowered or 'shape=pool' in lowered:
        return None
    smap = _style_map(style)
    shape = (smap.get('shape') or '').lower()
    if shape and not _shape_is_understood(style, shape):
        return f'фигуру «{shape}»'
    if 'gateway' in shape:
        kind = (smap.get('gwtype') or '').lower()
        if kind not in _KNOWN_GATEWAY_KINDS:
            return f'шлюз «{kind}»'
    if shape.endswith('.event'):
        symbol = (smap.get('symbol') or '').lower()
        if symbol not in _KNOWN_EVENT_SYMBOLS:
            return f'событие со значком «{symbol}»'
    return None


def is_decoration_style(style: str) -> bool:
    s = (style or '').lower()
    return (
        'timer' in s
        or 'clock' in s
        or 'mxgraph.bpmn.icon' in s
        or 'shape=mxgraph.bpmn.timer' in s
        or 'eventicon' in s
        or 'symbol=timer' in s
        or 'symbol=clock' in s
        or 'shape=datastore' in s
        or 'shape=mxgraph.bpmn.datastore' in s
        or 'kind=datastore' in s
        or 'shape=mxgraph.signs' in s  # транспортные иконки car/train в файле кредита
        or 'shape=mxgraph.bpmn.dataobject' in s
        or 'shape=note' in s
        or 'shape=mxgraph.bpmn.annotation' in s
    )


#: Число длительности: «5», «0.5», «1 440» (пробел — разделитель тысяч).
_DURATION_NUMBER = r'\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?'
#: Единицы времени, которыми на картах подписаны часы, и их вес в минутах.
#:
#: Часть карт набрана по-узбекски целиком, и время на них стоит не в минутах:
#: «8 soat» — восемь часов, «1 kun» — сутки, «5 kungacha» — до пяти суток.
#: Пока список ограничивался минутами, такой значок не разбирался вовсе, и
#: время с карты пропадало: у шага оставались часы без цифры.
_DURATION_UNITS = {
    'min': 1.0, 'daq': 1.0, 'мин': 1.0,
    'soat': 60.0,
    'kun': 1440.0,
}
_DURATION_UNIT_ALT = '|'.join(_DURATION_UNITS)
#: «5 min», «0.5 daq», «120 мин», «8 soat», «1 kun» — длительность в подписи.
_DURATION_RE = re.compile(
    rf'({_DURATION_NUMBER})\s*({_DURATION_UNIT_ALT})[a-zа-я]*',
    re.IGNORECASE,
)
#: Хвост подписи, начинающийся с длительности: в имя шага он не идёт.
_DURATION_TAIL_RE = re.compile(
    rf'\b(?:{_DURATION_NUMBER})\s*(?:{_DURATION_UNIT_ALT}|минут)\b.*$',
    re.IGNORECASE,
)
#: Подпись, помечающая время ОЖИДАНИЯ (WT), а не выполнения (ST).
_WAIT_RE = re.compile(r"kutish\s+vaqti|время\s+ожидания|wait", re.IGNORECASE)


def duration_minutes(text: Optional[str]) -> Optional[float]:
    """Минуты из подписи-бейджа длительности; None — если числа нет."""
    if not text:
        return None
    m = _DURATION_RE.search(text)
    if not m:
        return None
    digits = re.sub(r'[ \u00a0\u202f]', '', m.group(1)).replace(',', '.')
    try:
        return float(digits) * _DURATION_UNITS[m.group(2).lower()]
    except ValueError:
        return None


def is_wait_label(text: Optional[str]) -> bool:
    return bool(text) and bool(_WAIT_RE.search(text))


#: Подпись значка, состоящая из одного числа: «10», «0,5».
_BARE_NUMBER_RE = re.compile(r'\d+(?:[.,]\d+)?')


def duration_badge_minutes(style: str, label: str) -> Optional[float]:
    """Время со значка часов у шага; ``None`` — если фигура значком не является.

    По Методике (4-ILOVA) время операции проставляется отдельной мелкой
    фигурой-таймером рядом с шагом, а не в подписи самого шага. Импорт снимает
    такую фигуру с карты и переносит её число в ST/WT шага, поэтому шагом она
    не становится — и предпроверка не должна докладывать о ней как о шаге вне
    дорожки. Признак один на оба места, чтобы они не разошлись.

    Внутри циферблата аналитик нередко пишет одно число без единицы: «10» на
    часах — это десять минут, другого прочтения у значка нет. В подписи шага
    такое число значило бы номер или сумму, поэтому голое число принимаем
    только внутри самой фигуры-таймера.
    """
    style_l = (style or '').lower()
    if 'swimlane' in style_l:
        return None
    is_timer_shape = 'symbol=timer' in style_l or 'shape=mxgraph.bpmn.timer' in style_l
    minutes = duration_minutes(label)
    if minutes is None:
        bare = _BARE_NUMBER_RE.fullmatch((label or '').strip())
        if not (is_timer_shape and bare):
            return None
        minutes = float(bare.group(0).replace(',', '.'))
    if is_timer_shape:
        return minutes
    # Текстовая пометка «O'rtacha kutish vaqti 1440 min»: только текст, никогда
    # не фигура BPMN, иначе можно съесть настоящий шаг.
    residual = _DURATION_RE.sub('', label).strip(' .,:;-')
    if 'mxgraph.bpmn' in style_l or len(residual) > 32:
        return None
    return minutes


def is_non_task_label(val: str) -> bool:
    v = val.lower().strip()
    if not v:
        return True
    if v in CONDITION_TAGS or v in SYSTEM_TAGS or v in ARTIFACT_TAGS:
        return True
    if v.startswith('kutish vaqti') or v.startswith("o'rtacha kutish vaqti"):
        return True
    if any(k in v for k in ('(as is)', '(to be)', '(as-is)', '(to-be)')):
        return True
    return False

#: Короткая подпись рядом с фигурой — это бейдж, а не примечание к процессу.
TEXT_NOTE_MIN_LEN = 12


def is_text_note(style: str, label: str) -> bool:
    """Обрамлённая текстовая врезка draw.io — примечание к процессу.

    На картах SQB перечень требуемых документов оформлен как ``text``-фигура с
    рамкой (``strokeColor`` задан). Такие врезки раньше отбрасывались вместе с
    подписями-накладками, и содержательный текст пропадал из выгрузки. Заголовок
    схемы и подписи связей рамки не имеют (``strokeColor=none``) — они по-прежнему
    остаются оформлением.
    """
    s = (style or '').lower()
    if 'text;' not in s or 'swimlane' in s or 'edgelabel' in s:
        return False
    text = (label or '').strip()
    if len(text) < TEXT_NOTE_MIN_LEN or is_non_task_label(text):
        return False
    if duration_minutes(text) is not None:
        return False
    stroke = _style_map(s).get('strokecolor', 'none')
    return stroke not in ('none', '')


def _id_has_token(node_id: str, token: str) -> bool:
    i = (node_id or '').lower()
    token = token.lower()
    if (
        i == token
        or i.startswith(token + '_')
        or i.startswith(token + '-')
        or i.endswith('_' + token)
        or i.endswith('-' + token)
    ):
        return True
    return token in re.split(r'[-_]', i)


def _style_map(style: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (style or '').split(';'):
        if not part:
            continue
        if '=' in part:
            key, val = part.split('=', 1)
            out[key.strip().lower()] = val.strip()
        else:
            out[part.strip().lower()] = '1'
    return out


def _style_float(style_map: Dict[str, str], key: str) -> Optional[float]:
    raw = style_map.get(key)
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parent_origin(
    cell_id: Optional[str],
    cell_map: Dict[str, ET.Element],
    cache: Dict[str, Tuple[float, float]],
    _chain: Optional[Set[str]] = None,
) -> Tuple[float, float]:
    """Absolute top-left of a cell's parent chain.

    mxGraph semantics: children of a swimlane are positioned relative to the
    swimlane's FULL origin (including the title/startSize area) — verified by
    pool/lane geometry fitting exactly (lane y+h == pool h). Therefore we add
    only geo.x/geo.y of ancestors, no startSize shift.

    ``_chain`` — идентификаторы, уже пройденные на этом спуске. Файл с
    повторяющимися id (обычная история при склейке двух схем) даёт ячейку,
    которая оказывается сама себе предком, и обход уходил в бесконечную
    рекурсию: импорт падал с RecursionError, то есть пятисотой ошибкой без
    единого понятного слова для сотрудника. Цикл разрываем на первом же
    повторе; сам дефект ловит и объясняет предпроверка (``drawio_precheck``).
    """
    if not cell_id or cell_id in ('0', '1'):
        return 0.0, 0.0
    if cell_id in cache:
        return cache[cell_id]
    if _chain and cell_id in _chain:
        cache[cell_id] = (0.0, 0.0)
        return 0.0, 0.0
    cell = cell_map.get(cell_id)
    if cell is None:
        cache[cell_id] = (0.0, 0.0)
        return 0.0, 0.0
    px, py = _parent_origin(
        cell.get('parent'), cell_map, cache, (_chain or set()) | {cell_id}
    )
    geo = cell.find('mxGeometry')
    # Relative geometries (edge labels, edge frames) do not shift the coordinate space.
    if geo is not None and geo.get('relative') != '1':
        px += float(geo.get('x', '0') or 0)
        py += float(geo.get('y', '0') or 0)
    cache[cell_id] = (px, py)
    return px, py


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.rsplit('}', 1)[-1]
    if ':' in tag:
        return tag.split(':')[-1]
    return tag


def classify_vertex(style: str, label: str, has_incoming: bool, has_outgoing: bool, node_id: str) -> NodeType:
    s = style.lower()
    l = label.lower()
    i = node_id.lower()
    smap = _style_map(style)
    shape = smap.get('shape', '').lower()

    if 'swimlane' in s or 'pool;' in s or 'shape=pool' in s:
        return 'lane'

    # ── Артефакты (2-ILOVA: Artefaktlar) ────────────────────────────────────
    # Хранилище данных: IABS, EHA, EDO, Korporativ pochta.
    #
    # Цилиндр рисуют не только фигурой BPMN: в библиотеке draw.io их несколько
    # (``cylinder``, ``cylinder2``, ``cylinder3``, база из блок-схем), и
    # аналитики берут ту, что попалась под руку. Нераспознанный цилиндр
    # становился шагом — лишней строкой в регламенте и прямоугольником вместо
    # базы на карте студии.
    if (
        'shape=datastore' in s
        or 'mxgraph.bpmn.datastore' in s
        or 'kind=datastore' in s
        or _CYLINDER_SHAPE_RE.match(shape)
        or 'mxgraph.flowchart.database' in s
    ):
        return 'dataStore'
    # Объект данных: Dalolatnoma, Yig'ma jild, Hujjatlar ro'yxati.
    if ('mxgraph.bpmn.data2' in s or shape.endswith('bpmn.data') or 'shape=dataobject' in s
            or 'office.concepts.documents' in s):
        return 'dataObject'
    # Текстовое примечание: фигура-заметка или обрамлённая текстовая врезка.
    # Выноска (``callout``) — это записка к шагу, как её и рисует аналитик:
    # облачко с текстом над фигурой. Нераспознанная, она становилась
    # полноразмерной задачей и ложилась поверх соседнего ряда.
    if ('shape=note' in s or 'mxgraph.bpmn.annotation' in s
            or 'shape=mxgraph.flowchart.annotation' in s or 'shape=callout' in s):
        return 'textAnnotation'
    if is_text_note(style, label):
        return 'textAnnotation'

    if 'mxgraph.bpmn.gateway' in s or shape.endswith('gateway2') or 'gwtype' in smap:
        gw = (smap.get('gwtype') or smap.get('symbol') or '').lower()
        # Сложный шлюз (звёздочка) — не то же самое, что параллельный (плюс):
        # у параллельного срабатывают все ветки, у сложного условие задаётся
        # отдельно. Раньше они сливались, и на карте звёздочка превращалась
        # в плюс — аналитик видел не ту схему, что нарисовал.
        if gw in ('complex', 'multiple') or 'outline=star' in s:
            return 'complexGateway'
        if gw in ('parallel', 'and') or 'outline=plus' in s or 'parallel' in s:
            return 'parallelGateway'
        if gw in ('inclusive', 'or') or 'inclusive' in s or 'outline=circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    if 'mxgraph.bpmn.event' in s or shape.endswith('.event'):
        outline = (smap.get('outline') or '').lower()
        symbol = (smap.get('symbol') or '').lower()
        # Таймер внутри потока — «Kutish vaqti»: промежуточное событие-обработчик.
        # Одиночные таймеры-бейджи длительности сюда не доходят: их снимает
        # _collect_duration_badges() и переносит в ST/WT ближайшего шага.
        if symbol == 'timer' and has_incoming and has_outgoing:
            return 'intermediateTimerEvent'
        if symbol == 'message' and has_incoming and has_outgoing:
            return 'intermediateMessageEvent'
        if outline in ('end', 'terminate') or 'outline=end' in s or 'outline=double' in s:
            return 'endEvent'
        if outline == 'catching' and has_incoming and has_outgoing:
            return 'intermediateTimerEvent' if symbol == 'timer' else 'intermediateMessageEvent'
        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if 'mxgraph.bpmn.task' in s:
        marker = (smap.get('taskmarker') or smap.get('symbol') or '').lower()
        # `isLoopSub=1` — это и есть квадратик с плюсом, которым draw.io метит
        # свёрнутый подпроцесс: сам `taskMarker` при этом остаётся `abstract`,
        # потому что он выбирает ВИД активности, а не её маркер. Без этой
        # проверки рамка с плюсом приезжала обычным шагом, и в выгрузке вместо
        # `<bpmn:subProcess>` уходил `<bpmn:userTask>`.
        if (marker in ('sub', 'subprocess') or 'issubprocess=1' in s
                or smap.get('isloopsub') == '1' or 'mxgraph.bpmn.transaction' in s):
            return 'subProcess'
        if marker in ('service', 'script', 'send', 'receive', 'businessrule') or any(
            k in l for k in ('rpa', 'робот', 'авто-', 'avtomat', 'sms')
        ):
            return 'serviceTask'
        return 'userTask'

    is_gateway_shape = (
        'rhombus' in s
        or 'shape=rhombus' in s
        or 'gateway' in s
        or i.startswith('gw')
        or i.startswith('gateway')
        or '-gw-' in i
        or '_gw_' in i
    )
    if is_gateway_shape:
        if 'complex' in s or l.strip() in ('*', '✳', '✱'):
            return 'complexGateway'
        if 'outline=plus' in s or 'parallel' in s or l.strip() in ('+', 'and', 'и'):
            return 'parallelGateway'
        if 'inclusive' in s or 'outline=circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    is_event_shape = (
        'ellipse' in s
        or 'bpmn.shape' in s
        or 'shape=ellipse' in s
        or _id_has_token(node_id, 'start')
        or _id_has_token(node_id, 'end')
        or _id_has_token(node_id, 'reject')
    )
    if is_event_shape:
        if (
            any(k in l for k in ('rad etildi', 'rad javob', 'otkaz', 'отказ', 'bekor', 'отклон'))
            or _id_has_token(node_id, 'reject')
            or any(c in s for c in ('#ef4444', '#e11d48', '#be123c', '#dc2626', '#b91c1c'))
        ):
            return 'endEvent'

        is_end = (
            _id_has_token(node_id, 'end')
            or _id_has_token(node_id, 'finish')
            or any(k in l for k in ('заверш', 'конец', 'выдан', 'ochildi', 'tugashi', 'bajarildi', 'активирован'))
            or 'outline=double' in s
            or 'outline=end' in s
        )
        is_start = (
            _id_has_token(node_id, 'start')
            or _id_has_token(node_id, 'begin')
            or any(k in l for k in ('старт', 'поступлен', 'tashrif', 'boshlanish'))
        )

        # End markers win over a generic green fill (success end events are often green)
        if is_end:
            return 'endEvent'
        if is_start:
            return 'startEvent'

        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if any(k in s for k in ('robot', 'rpa', 'service', '#dcfce7', '#d5e8d4')) or any(k in l for k in ('rpa', 'робот', 'авто-', 'avtomat', 'sms')):
        return 'serviceTask'

    return 'userTask'

def classify_category(node_type: NodeType, name: str, style: str) -> StepCategory:
    lower = f"{name} {style}".lower()
    if node_type in ARTIFACT_NODE_TYPES:
        return 'api_service' if node_type == 'dataStore' else 'manual'
    if node_type in ('startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent'):
        return 'notification'
    if node_type == 'serviceTask' or any(k in lower for k in ('rpa', 'робот', 'авто-', 'avtomat', 'генерация', 'sms')):
        return 'rpa_bot'
    if any(k in lower for k in ('согласован', 'комитет', 'утвержд', 'подпис', 'голос', 'imzo', 'vizo', 'tasdiq', 'himoya')):
        return 'approval'
    if any(k in lower for k in ('проверк', 'валидац', 'скоринг', 'скор', 'андеррайт', 'риск', 'tekshirish', 'solishtirish', 'identifikatsiya', 'o\'rganish')):
        return 'validation'
    if any(k in lower for k in ('api', 'абс', 'сервис', 'цфт', 'didox', 'iabs', 'eha', 'edo', 'nibbd')):
        return 'api_service'
    return 'manual'

def detect_system(name: str, lane_name: str) -> str:
    """Система по словам в названии шага и дорожки — догадка; пусто, если слов нет.

    Раньше вместо пустого ответа шла «SQB CRM / Core»: в картах банка так
    оказались подписаны 2818 шагов из 8041, хотя на картах этой системы нет, —
    и в таблицу AS-IS она уходила как факт.
    """
    lower = f"{name} {lane_name}".lower()
    if 'rpa' in lower or 'робот' in lower or 'avtomat sms' in lower:
        return 'PIX RPA'
    if 'nibbd' in lower:
        return 'NIBBD / ЦБ РУз'
    if 'eha' in lower or 'еха' in lower:
        return 'EHA'
    if 'edo' in lower or 'эдо' in lower or 'didox' in lower or 'эцп' in lower:
        return 'EDO / Didox (ЭЦП)'
    if 'aml' in lower or 'komplayens' in lower:
        return 'AML/CFT Moduli'
    if 'iabs' in lower or 'абс' in lower or 'счет' in lower or 'проводк' in lower or 'цфт' in lower or 'клиенты и счета' in lower or 'комиссия' in lower:
        return 'iABS'
    if any(k in lower for k in ('гнк', 'налог', 'soliq')):
        return 'API Soliq (ГНК)'
    if 'катм' in lower or 'katm' in lower or 'бюро' in lower:
        return 'API KATM'
    if 'епигу' in lower or 'egrpo' in lower or 'егрпо' in lower:
        return 'ЕПИГУ / ЕГРПО'
    if 'dragle' in lower:
        return 'Dragle BI'
    if 'crobs' in lower:
        return 'CROBS'
    if 'zoom' in lower:
        return 'Zoom'
    if 'swift' in lower or 'свифт' in lower:
        return 'SWIFT'
    return ''


def guess_system(node: ProcessNode, lane_name: str) -> None:
    """Догадка о системе — только шагу, у которого система не взята с карты."""
    if node.system and not node.systemGuessed:
        return
    node.system = detect_system(node.name, lane_name) or None
    node.systemGuessed = node.system is not None

def sla_is_measured(raw_text: str, node_type: NodeType) -> bool:
    """Стояло ли время в самой подписи фигуры.

    Отличать измеренное от подставленного нужно ровно для карты: время,
    которого в draw.io не было, не должно появляться на ней ни значком часов,
    ни свойством шага.
    """
    if node_type in ARTIFACT_NODE_TYPES:
        return False
    if duration_minutes(raw_text) is not None:
        return True
    return bool(re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', raw_text or '', re.IGNORECASE))


def extract_sla_minutes(raw_text: str, category: StepCategory, node_type: NodeType) -> int:
    if node_type in ARTIFACT_NODE_TYPES:
        return 0
    if node_type in ('startEvent', 'endEvent'):
        # Часы в начале процесса («Kutish vaqti / 1 440 min») несут время в
        # подписи так же, как промежуточный таймер; без подписи — условные 5.
        minutes = duration_minutes(raw_text)
        return max(1, int(round(minutes))) if minutes else 5

    if node_type in ('intermediateTimerEvent', 'intermediateMessageEvent'):
        # Событие ожидания: длительность — это и есть его подпись.
        minutes = duration_minutes(raw_text)
        return max(1, int(round(minutes))) if minutes else 30

    minutes = duration_minutes(raw_text)
    if minutes is None:
        # «15 m» без единицы целиком: отдельный случай, в бейджах не встречается.
        short = re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', raw_text, re.IGNORECASE)
        minutes = float(short.group(1).replace(',', '.')) if short else None
    if minutes is not None:
        return max(1, int(round(minutes)))

    if category == 'rpa_bot':
        return 3
    if category == 'api_service':
        return 2
    if category == 'validation':
        return 45
    if category == 'approval':
        return 180
    return 60

def fallback_node_name(node_type: NodeType, code: Optional[str], raw_text: str = '') -> str:
    """Осмысленное имя фигуры без подписи — никогда не идентификатор ячейки.

    Раньше на карту и в выгрузку попадали заголовки вида «Операция
    G9DXMv3N_W9X6-3aXuzq-1»: у промежуточного таймера вся подпись — это
    длительность («10 min»), а её снимает нормализация имени шага. Возвращаем
    подпись события на место и подписываем остальные фигуры по их роли.

    Именно подпись аналитика, а не пересчёт: эталон вида карты — исходный файл
    draw.io. Там у таймера написано «10 min» или «1 kun», и придуманное
    «Ожидание 1440 мин» на карте банка не написано нигде — ни в тех единицах,
    ни на том языке.
    """
    if node_type == 'intermediateTimerEvent':
        return (raw_text or '').strip() or 'Ожидание'
    if node_type == 'intermediateMessageEvent':
        return 'Событие-сообщение'
    if node_type == 'startEvent':
        return 'Старт'
    if node_type == 'endEvent':
        return 'Завершение'
    if node_type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway'):
        return 'Условие'
    if node_type == 'dataStore':
        return 'Информационная система'
    if node_type == 'dataObject':
        return 'Документ'
    if node_type == 'textAnnotation':
        return 'Примечание'
    if node_type == 'subProcess':
        return f'Подпроцесс {code}' if code else 'Подпроцесс'
    return f'Операция {code}' if code else 'Операция'


#: Элемент BPMN 2.0 -> тип узла карты. Список закрытый: всё, что сюда не
#: попало (``laneSet``, ``participant``, ``documentation``), фигурой не является.
BPMN_TYPE_MAP: Dict[str, NodeType] = {
    'startevent': 'startEvent',
    'endevent': 'endEvent',
    'intermediatecatchevent': 'intermediateMessageEvent',  # уточняется по definition
    'intermediatethrowevent': 'intermediateMessageEvent',
    'boundaryevent': 'intermediateMessageEvent',
    'task': 'userTask',
    'usertask': 'userTask',
    'manualtask': 'userTask',
    'receivetask': 'userTask',
    'sendtask': 'serviceTask',
    'servicetask': 'serviceTask',
    'scripttask': 'serviceTask',
    'businessruletask': 'serviceTask',
    'subprocess': 'subProcess',
    'callactivity': 'subProcess',
    'exclusivegateway': 'exclusiveGateway',
    'eventbasedgateway': 'exclusiveGateway',
    'parallelgateway': 'parallelGateway',
    'inclusivegateway': 'inclusiveGateway',
    'complexgateway': 'complexGateway',
    'datastorereference': 'dataStore',
    'dataobjectreference': 'dataObject',
    'textannotation': 'textAnnotation',
}


#: Длительность в подписи значка — в тех единицах, в которых её пишет выгрузка.
#:
#: Выгрузка пишет время и пересчитанным («1 ч 30 мин»), и словами карты
#: draw.io («1 440 min», «5 daqiqa», «2 soat»), поэтому словарь тот же, что у
#: разбора самой карты.
_BADGE_UNIT_RE = re.compile(
    rf'(?:{_DURATION_NUMBER})\s*(?:мин|ч|дн|{_DURATION_UNIT_ALT})[a-zа-я]*', re.IGNORECASE,
)
#: Что может остаться от подписи значка длительности, кроме самого числа.
_BADGE_RESIDUE_RE = re.compile(
    r"[\s·,;:/–—-]+|ожидание|kutish\s*vaqti|wait", re.IGNORECASE,
)


#: Длительность, приписанная к подписи нашей же выгрузкой: «… 1 ч 30 мин».
_BADGE_TAIL_RE = re.compile(
    rf'\s*(?:{_BADGE_UNIT_RE.pattern})(?:\s+(?:{_BADGE_UNIT_RE.pattern}))*\s*$',
    re.IGNORECASE,
)


def is_duration_badge_name(name: str) -> bool:
    """Подпись «15 мин · ожидание 2 ч» — значок времени, а не имя события.

    Выгрузка в BPMN вешает время шага граничным таймером: иначе Процессная
    студия открывает карту без единой цифры. При обратном чтении такой таймер
    обязан исчезнуть — время уже восстановлено из документации шага, а иначе
    у каждого шага появлялось бы фантомное событие-двойник.
    """
    text = (name or '').strip()
    if not text:
        return False
    # Голое «10» на часах draw.io — тоже минуты, и выгрузка пишет его как есть.
    if _BARE_NUMBER_RE.fullmatch(text):
        return True
    if not _BADGE_UNIT_RE.search(text):
        return False
    # От диапазона «10-120 min» остаётся первое число.
    rest = _BADGE_RESIDUE_RE.sub('', _BADGE_UNIT_RE.sub(' ', text))
    return not re.sub(r'[\d.,]', '', rest).strip()


def _bpmn_documentation(el: ET.Element) -> Dict[str, str]:
    """Паспорт шага из ``<documentation>``, который пишет наш же экспортёр.

    Стандартный BPMN не хранит ни исполнителя, ни время операции, ни систему —
    выгрузка складывает их в документацию строкой ``Role: …; ST: 15 min; …``.
    Без обратного разбора карта, выгруженная и открытая заново, теряла и роли,
    и SLA, и весь расчёт экономии.
    """
    meta: Dict[str, str] = {}
    for child in el:
        if _local_tag(child.tag).lower() != 'documentation':
            continue
        for chunk in (child.text or '').split(';'):
            key, sep, value = chunk.partition(':')
            if sep and value.strip():
                meta[key.strip().lower()] = value.strip()
        break
    return meta


def _own_documentation(el: ET.Element) -> str:
    """Текст ``<documentation>`` элемента как есть — без разбора на пары.

    У дорожки там лежит её полное название: в атрибут ``name`` оно не влезает,
    потому что импортёры печатают его повёрнутым в узкой полосе заголовка.
    """
    for child in el:
        if _local_tag(child.tag).lower() == 'documentation':
            return (child.text or '').strip()
    return ''


def _apply_bpmn_documentation(node: ProcessNode, meta: Dict[str, str]) -> None:
    if not meta:
        return
    if meta.get('code'):
        node.code = meta['code']
    if meta.get('role'):
        node.role = meta['role']
    if meta.get('lane'):
        node.laneName = meta['lane']
    if meta.get('system'):
        node.system = canonical_system(meta['system'])
        node.systemGuessed = False
    for key, field in (('st', 'slaMinutes'), ('wt', 'waitMinutes')):
        minutes = duration_minutes(meta.get(key))
        if minutes is not None:
            setattr(node, field, int(round(minutes)))
    for key, field in (('in', 'inputArtifacts'), ('out', 'outputArtifacts')):
        if meta.get(key):
            setattr(node, field, [p.strip() for p in meta[key].split(',') if p.strip()])
    category = meta.get('category')
    if category in ('manual', 'rpa_bot', 'api_service', 'approval', 'validation', 'notification'):
        node.category = category
    potential = re.match(r'(\d+)', meta.get('rpa potential', ''))
    if potential:
        node.automationPotential = int(potential.group(1))
    node.costPerExecution = 800 if node.category == 'rpa_bot' else (node.slaMinutes or 0) * 1932


def process_id_for(filename: str, content: str = '') -> str:
    """Постоянный идентификатор карты — по имени её файла И его содержимому.

    Это ключ хранилища процессов. Пока он был случайным, каждый повторный
    импорт того же файла заводил ЕЩЁ ОДНУ запись: в списке «Процессы на
    сервере» набралось 210 карт с повторяющимися именами, и отличить свежую
    от снятой прежней сборкой платформы стало нечем. Поэтому разбор одного и
    того же файла обязан давать один и тот же id.

    Одного имени для этого мало. Ключ по имени молча затирал сохранённую карту
    любой другой, названной так же: «Kredit.drawio» и «kredit.DRAWIO» ложились
    в одну запись, а вставка XML всегда идёт под именем
    «Pasted_Process.drawio» — и каждая вставка стирала предыдущую (карта на
    90 узлов сменялась картой на 35). Идентификатор страницы draw.io ключом
    тоже не годится: аналитики копируют файлы, и один ``diagram id`` стоит у
    четырёх разных карт банка.

    Отсюда правило: тот же файл без изменений — та же запись; другая карта
    под тем же именем или карта после правки — новая запись рядом, ничего не
    затирается. Регистр имени по-прежнему не различается.
    """
    name = (filename or 'process').strip().lower()
    body = hashlib.sha1((content or '').strip().encode('utf-8')).hexdigest()
    return f"proc_{hashlib.sha1(f'{name}\n{body}'.encode('utf-8')).hexdigest()[:8]}"


def passport_code_for(process_id: str) -> str:
    """Код процесса по его идентификатору: тот же процесс — тот же код.

    Код стоит в именах всех выгружаемых файлов (``PRC-SQB-…_PIX_Map.bpmn``).
    Пока он был случайным, повторный импорт той же карты давал новый код —
    PRC-SQB-292839, затем PRC-SQB-5F0D80, — и файлы одной и той же карты
    назывались при каждой выгрузке по-новому.
    """
    return f"PRC-SQB-{hashlib.sha1(process_id.encode('utf-8')).hexdigest()[:6].upper()}"


def _diagram_document(root: ET.Element, index: int) -> ET.Element:
    """Копия документа, в которой осталась одна схема и её процессы.

    Файл BPMN может нести несколько определений процессов, у каждого свою
    ``BPMNDiagram`` — так мы и выгружаем многостраничный draw.io. Разбор идёт по
    всему документу сразу, поэтому без этой чистки страницы легли бы на один
    холст друг поверх друга: координаты у них свои, и схемы наложились бы.
    """
    doc = copy.deepcopy(root)
    diagrams = [el for el in doc if _local_tag(el.tag).lower() == 'bpmndiagram']
    for position, diagram in enumerate(diagrams):
        if position != index:
            doc.remove(diagram)
    if index >= len(diagrams):
        return doc

    keep = {el.get('bpmnElement') for el in diagrams[index].iter() if el.get('bpmnElement')}
    for el in doc:
        if _local_tag(el.tag).lower() != 'collaboration' or el.get('id') not in keep:
            continue
        for part in el.iter():
            if _local_tag(part.tag).lower() == 'participant' and part.get('processRef'):
                keep.add(part.get('processRef'))
    for el in list(doc):
        if _local_tag(el.tag).lower() not in ('process', 'collaboration'):
            continue
        if el.get('id') not in keep:
            doc.remove(el)
    return doc


def parse_bpmn_xml(xml_str: str, filename: str) -> BusinessProcess:
    """Parse OMG BPMN 2.0 XML (any namespace prefix) into a BusinessProcess.

    Первая схема файла — основная карта, остальные — её страницы-подпроцессы:
    ровно так их выгружает ``bpmn_exporter`` из многостраничного draw.io.
    Раньше при чтении оставалась одна первая схема, и прочитанный .bpmn
    «Akkreditiv ochish» приходил без своих страниц.

    Файл с одной схемой читается целиком, как и раньше: делить его не на что.
    """
    root = ET.fromstring(xml_str)
    process_id = process_id_for(filename, xml_str)
    diagrams = [el for el in root if _local_tag(el.tag).lower() == 'bpmndiagram']
    if len(diagrams) < 2:
        return _parse_bpmn_document(root, filename, process_id)
    main = _parse_bpmn_document(_diagram_document(root, 0), filename, process_id)

    page_of_process: Dict[str, str] = {}
    for index in range(1, len(diagrams)):
        doc = _diagram_document(root, index)
        page_id = f'{process_id}_p{index}'
        try:
            page = _parse_bpmn_document(doc, filename, page_id)
        except ValueError:
            # Схема без единого элемента процесса страницей не становится.
            continue
        page.name = page.passport.name
        main.subProcesses.append(page)
        for el in doc:
            if _local_tag(el.tag).lower() == 'process' and el.get('id'):
                page_of_process.setdefault(el.get('id'), page.id)

    # Шаг-вызов страницы (``callActivity``) снова ссылается на её процесс.
    called = {
        el.get('id'): el.get('calledElement')
        for el in root.iter()
        if _local_tag(el.tag).lower() == 'callactivity' and el.get('calledElement')
    }
    for node in main.nodes:
        target = page_of_process.get(called.get(node.id) or '')
        if target:
            node.calledSubProcessId = target
    return main


def _parse_bpmn_document(root: ET.Element, filename: str, process_id: str) -> BusinessProcess:
    """Одна схема BPMN (одна страница) -> модель процесса."""
    process_el = None
    for el in root.iter():
        if _local_tag(el.tag).lower() == 'process':
            process_el = el
            break

    process_name = (
        (process_el.get('name') if process_el is not None else None)
        or filename.rsplit('.', 1)[0]
    )
    process_id_attr = process_el.get('id') if process_el is not None else None

    bounds_map: Dict[str, Geometry] = {}
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'bpmnshape':
            continue
        bpmn_element = el.get('bpmnElement')
        if not bpmn_element:
            continue
        for child in el:
            if _local_tag(child.tag).lower() == 'bounds':
                bounds_map[bpmn_element] = Geometry(
                    x=int(float(child.get('x', '100'))),
                    y=int(float(child.get('y', '100'))),
                    width=int(float(child.get('width', '120'))),
                    height=int(float(child.get('height', '60'))),
                )
                break

    # ── Ломаные связей ──────────────────────────────────────────────────────
    #
    # BPMNDI хранит связь точками, и первая с последней — это причалы на гранях
    # фигур; их считает трассировщик. Ценны промежуточные: ими задан ход линии,
    # который нарисовал автор карты. Без них файл, открытый и выгруженный
    # заново, терял всю ручную прокладку — линии перекладывались с нуля.
    waypoints: Dict[str, List[ProcessEdgePoint]] = {}
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'bpmnedge':
            continue
        bpmn_element = el.get('bpmnElement')
        if not bpmn_element:
            continue
        bends: List[ProcessEdgePoint] = []
        for child in el:
            if _local_tag(child.tag).lower() != 'waypoint':
                continue
            try:
                bends.append(ProcessEdgePoint(
                    x=int(round(float(child.get('x', '0')))),
                    y=int(round(float(child.get('y', '0')))),
                ))
            except (TypeError, ValueError):
                bends = []
                break
        if len(bends) > 2:
            waypoints[bpmn_element] = bends[1:-1]

    nodes: List[ProcessNode] = []
    step_index = 1
    for el in root.iter():
        tag = _local_tag(el.tag).lower()
        node_type = BPMN_TYPE_MAP.get(tag)
        if not node_type:
            continue
        node_id = el.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        raw_name = el.get('name') or ''
        if tag == 'boundaryevent' and is_duration_badge_name(raw_name):
            continue
        if node_type == 'textAnnotation' and not raw_name:
            for child in el:
                if _local_tag(child.tag).lower() == 'text':
                    raw_name = (child.text or '').strip()
                    break
        if tag == 'intermediatecatchevent':
            has_timer = any(
                _local_tag(child.tag).lower() == 'timereventdefinition' for child in el
            )
            node_type = 'intermediateTimerEvent' if has_timer else 'intermediateMessageEvent'
        meta = _bpmn_documentation(el)
        category = classify_category(node_type, raw_name, '')
        is_task = node_type in TASK_NODE_TYPES
        if node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1
        else:
            code = None
        sla = extract_sla_minutes(raw_name, category, node_type)
        sla_measured = sla_is_measured(raw_name, node_type)
        geo = bounds_map.get(node_id) or Geometry(
            x=100 + (step_index * 150), y=100, width=140, height=70
        )
        # Выгрузка дописывает таймеру его длительность — так подпись выглядит в
        # draw.io. Читая собственный файл, снимаем её обратно: время уже взято
        # из документации, а иначе имя росло бы с каждым кругом.
        if node_type == 'intermediateTimerEvent':
            raw_name = _BADGE_TAIL_RE.sub('', raw_name).strip(' .,:;-–—·')
        name = raw_name or fallback_node_name(node_type, code, el.get('name') or '')
        node = ProcessNode(
            id=node_id,
            name=name,
            type=node_type,
            category=category,
            code=code,
            geometry=geo,
            style='',
            slaMinutes=sla,
            slaMeasured=sla_measured,
            costPerExecution=800 if category == 'rpa_bot' else sla * 1932,
            automationPotential=95 if category == 'rpa_bot' else 60,
        )
        guess_system(node, '')
        _apply_bpmn_documentation(node, meta)
        nodes.append(node)

    if not nodes:
        raise ValueError('В BPMN-файле не найдено ни одного элемента процесса')

    node_ids = {n.id for n in nodes}
    lane_refs: Dict[str, List[str]] = {}
    lanes: List[ProcessNode] = []
    lane_idx = 0
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'lane':
            continue
        lane_id = el.get('id') or f"lane_{lane_idx}"
        lane_name = el.get('name') or f"Подразделение {lane_idx + 1}"
        refs = []
        for child in el.iter():
            if _local_tag(child.tag).lower() == 'flownoderef' and (child.text or '').strip():
                refs.append(child.text.strip())
        lane_refs[lane_id] = refs
        bounds = bounds_map.get(lane_id)
        # Имя дорожки печатается повёрнутым в узкой полосе заголовка, и длинное
        # в неё не помещается: выгрузка обрезает его многоточием, а целиком
        # кладёт в документацию. Читаем оттуда — иначе карта, выгруженная и
        # открытая заново, теряет названия подразделений.
        lane_name = _own_documentation(el) or lane_name
        lanes.append(ProcessNode(
            id=lane_id,
            name=lane_name,
            type='lane',
            role=lane_name,
            geometry=bounds or Geometry(x=50, y=50 + lane_idx * 180, width=1400, height=180),
            style='swimlane;',
        ))
        lane_idx += 1

    # ── Внешние участники ───────────────────────────────────────────────────
    #
    # Полосу клиента или госоргана BPMN описывает не дорожкой, а отдельным
    # участником-«чёрным ящиком»: у него свой процесс, и тот пуст. Пока такие
    # участники не читались, с карты пропадала целая строка вместе со всеми
    # пунктирными связями к ней — на десяти выгрузках PIX из каталога банка это
    # 11 полос и 70 связей.
    populated: Set[str] = set()
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'process':
            continue
        if any(BPMN_TYPE_MAP.get(_local_tag(c.tag).lower()) for c in el):
            populated.add(el.get('id') or '')
    #: Участники, ставшие полосами: концы messageFlow держатся за участника, а
    #: модель карты ведёт такую связь в полосу — под тем же идентификатором.
    participant_ids: Set[str] = set()
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'participant':
            continue
        part_id = el.get('id') or ''
        if not part_id or (el.get('processRef') or '') in populated:
            continue
        lane_name = _own_documentation(el) or el.get('name') or f"Участник {lane_idx + 1}"
        participant_ids.add(part_id)
        lanes.append(ProcessNode(
            id=part_id,
            name=lane_name,
            type='lane',
            role=lane_name,
            geometry=bounds_map.get(part_id)
            or Geometry(x=50, y=50 + lane_idx * 180, width=1400, height=180),
            style='swimlane;',
        ))
        lane_idx += 1

    for lane in lanes:
        for ref in lane_refs.get(lane.id, []):
            node = next((n for n in nodes if n.id == ref), None)
            if node:
                node.laneId = lane.id
                node.laneName = lane.name
                node.role = node.role or lane.name
                guess_system(node, lane.name)

    # Дорожка в BPMN перечисляет только узлы потока: документ и хранилище
    # данных в ``flowNodeRef`` не запишешь, спецификация этого не позволяет.
    # Поэтому у артефактов после чтения дорожки нет, хотя на рисунке они лежат
    # внутри неё. Добираем по геометрии — тем же правилом, которым разбор
    # draw.io определяет исполнителя шага, и которым это делает клиентский
    # читатель (``parseBpmnMap`` в ``app/src/lib/bpmn-import.ts``).
    for node in nodes:
        if node.laneId:
            continue
        host = next((lane for lane in lanes if _lane_holds(lane, node)), None)
        if host is None:
            continue
        node.laneId = host.id
        node.laneName = host.name
        node.role = node.role or host.name
        guess_system(node, host.name)

    # Ассоциации и сообщения — такие же связи карты, как переходы: без них с
    # карты пропадают все системы и документы, привязанные к шагам.
    edge_kinds: Dict[str, EdgeKind] = {
        'sequenceflow': 'sequenceFlow',
        'association': 'association',
        'messageflow': 'messageFlow',
    }
    edges: List[ProcessEdge] = []

    # ── Связи с данными живут ВНУТРИ активности ────────────────────────────
    #
    # По спецификации хранилище и документ подключаются к шагу не ассоциацией,
    # а ``dataInputAssociation`` / ``dataOutputAssociation`` — вложенными в сам
    # шаг элементами, у которых концы записаны не атрибутами, а дочерними
    # тегами ``sourceRef`` / ``targetRef``. Обычный обход по атрибутам их не
    # видит, и при повторном чтении собственной выгрузки все системы и
    # документы отваливались от шагов.
    #
    # Направление читается из имени тега: вход — из данных в шаг, выход — из
    # шага в данные. У выходной связи ``sourceRef`` может отсутствовать вовсе:
    # источник выводится из места объявления.
    for holder in root.iter():
        holder_id = holder.get('id')
        if not holder_id:
            continue
        for child in holder:
            tag = _local_tag(child.tag).lower()
            if tag not in ('datainputassociation', 'dataoutputassociation'):
                continue
            refs = {
                _local_tag(g.tag).lower(): (g.text or '').strip()
                for g in child
                if _local_tag(g.tag).lower() in ('sourceref', 'targetref')
            }
            if tag == 'datainputassociation':
                data_id, step_id = refs.get('sourceref', ''), holder_id
            else:
                data_id, step_id = refs.get('targetref', ''), holder_id
            if data_id not in node_ids or step_id not in node_ids:
                continue
            source_id, target_id = (
                (data_id, step_id) if tag == 'datainputassociation' else (step_id, data_id)
            )
            edges.append(ProcessEdge(
                id=child.get('id') or f'edge_{uuid.uuid4().hex[:8]}',
                name='',
                kind='association',
                sourceId=source_id,
                targetId=target_id,
                dashed=True,
            ))

    # Конец обмена сообщениями держится за участника, а не за фигуру: в модели
    # карты такая связь ведёт в полосу этого участника — так же, как её рисует
    # аналитик в draw.io.
    end_ids = node_ids | participant_ids
    for el in root.iter():
        kind = edge_kinds.get(_local_tag(el.tag).lower())
        if not kind:
            continue
        source_id = el.get('sourceRef')
        target_id = el.get('targetRef')
        if not source_id or not target_id:
            continue
        if source_id not in end_ids or target_id not in end_ids:
            continue
        cond_text = ''
        for child in el:
            if _local_tag(child.tag).lower() == 'conditionexpression':
                cond_text = (child.text or '').strip()
                break
        edge_name = el.get('name') or cond_text or ''
        edges.append(ProcessEdge(
            id=el.get('id') or f"edge_{uuid.uuid4().hex[:8]}",
            name=edge_name,
            sourceId=source_id,
            targetId=target_id,
            kind=kind,
            condition=cond_text or edge_name or None,
            points=waypoints.get(el.get('id') or '', []),
        ))

    title = process_name
    # Паспорт считаем по тем же правилам, что и при импорте draw.io: только
    # шаги регламента и вместе с ожиданием. Иначе карта, выгруженная и открытая
    # заново, показывала SLA в разы больше — время событий и артефактов.
    total_hours = round(
        sum(
            (n.slaMinutes or 0) + (n.waitMinutes or 0)
            for n in nodes if n.type in TASK_NODE_TYPES
        ) / 60,
        1,
    ) or 8.0
    passport_code = (
        process_id_attr if process_id_attr and process_id_attr.startswith('PRC-')
        else passport_code_for(process_id)
    )
    passport = ProcessPassport(
        code=passport_code,
        name=title,
        version='1.0',
        status='draft',
        # Владельца и подразделение на рисунке не найти — их заполняют в
        # паспорте карты. Раньше здесь стояли заглушка и первая дорожка.
        owner='',
        department='',
        category='Банковские процессы',
        targetSlaHours=total_hours,
        description='',  # описание процесса заполняют в паспорте; откуда карта — видно в истории версий
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    first_task = next((n for n in nodes if n.type in ('userTask', 'serviceTask', 'task')), None)
    seed = first_task or (nodes[0] if nodes else None)
    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Реестр заявок по процессу {title}",
        fields=[
            ProcessField(id='f1', code='case_number', name='Номер заявки', type='string', required=True),
            ProcessField(id='f2', code='client_inn', name='ИНН Клиента', type='string', required=True),
            ProcessField(id='f3', code='client_title', name='Компания', type='string', required=True),
            ProcessField(id='f4', code='status', name='Статус', type='select', required=True, options=['В работе', 'Одобрено', 'Отклонено'])
        ],
        records=[
            PixRegistryRecord(
                id='rec-1',
                caseId='SQB-2026-BPM01',
                createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
                status='in_progress',
                currentStepId=seed.id if seed else 'step-1',
                currentStepName=seed.name if seed else 'Первичный шаг',
                assignedTo=(seed.role if seed and seed.role else 'Сотрудник банка'),
                elapsedMinutes=15,
                data={
                    'case_number': 'SQB-2026-BPM01',
                    'client_inn': '309819284',
                    'client_title': 'OOO "GLOBAL AGRO"',
                    'status': 'В работе'
                }
            )
        ]
    )

    _resolve_artifact_links(nodes, edges)
    validations = collect_import_diagnostics(nodes, lanes, edges)
    metrics = analyze_process_conformance(nodes, passport, len(registry.records))

    return BusinessProcess(
        id=process_id,
        name=title,
        fileName=filename,
        passport=passport,
        nodes=nodes,
        edges=edges,
        lanes=lanes,
        validation=validations,
        registry=registry,
        miningMetrics=metrics
    )


#: Максимальное расстояние от бейджа длительности до центра шага, px.
#: На реальных картах SQB бейдж лежит в 56-90 px от центра своего шага,
#: а до соседнего шага — не ближе ~190 px, поэтому порог однозначен.
BADGE_ATTACH_RADIUS = 130.0


def _lane_holds(lane: ProcessNode, node: ProcessNode) -> bool:
    """Центр фигуры лежит внутри рамки дорожки."""
    g, ng = lane.geometry, node.geometry
    cx = ng.x + ng.width / 2
    cy = ng.y + ng.height / 2
    return g.x <= cx <= g.x + g.width and g.y <= cy < g.y + g.height


def _name_and_prune_lanes(
    lanes: List[ProcessNode],
    flow_nodes: List[ProcessNode],
    lane_titles: Optional[Dict[str, str]] = None,
) -> None:
    """Убирает безымянные дорожки-баннеры и даёт имена остальным.

    На картах SQB встречаются swimlane-рамки оформления (шапка схемы) — без
    подписи и без шагов внутри. Настоящая безымянная дорожка содержит шаги,
    поэтому её сохраняем: сперва берём заголовок, набранный отдельным
    повёрнутым текстом внутри дорожки, и только потом — позиционное имя.

    Порядок важен: чистку делаем по собственной подписи дорожки. Заголовок
    схемы тоже лежит внутри пустой swimlane-рамки, и если сначала раздать
    заголовки, шапка схемы останется на карте как ещё одно подразделение.

    «Содержит шаги» считаем ПО РИСУНКУ, а не по родителю ячейки: дальше
    исполнителя шага разбор берёт именно оттуда, где фигура лежит. В «AVGUST
    1.29» аналитик сложил половину первой дорожки в отдельную рамку без имени
    и без высоты, приткнутую к низу пула. По родителю она выглядела полной и
    оставалась на карте — пустой полосой «Дорожка 5», которой в draw.io нет, —
    а её содержимое разъезжалось по настоящим дорожкам.
    """
    populated = {
        lane.id for lane in lanes
        if any(_lane_holds(lane, node) for node in flow_nodes)
    }
    doomed = {
        lane.id for lane in lanes
        if not (lane.name or '').strip() and lane.id not in populated
    }
    if doomed:
        lanes[:] = [lane for lane in lanes if lane.id not in doomed]
        for node in flow_nodes:
            if node.laneId in doomed:
                node.laneId = None
                node.laneName = None

    for index, lane in enumerate(sorted(lanes, key=lambda l: (l.geometry.y, l.geometry.x)), 1):
        if (lane.name or '').strip():
            continue
        lane.name = (lane_titles or {}).get(lane.id) or f'Дорожка {index}'
        lane.role = lane.name


#: Насколько близко свободный конец линии должен подойти к фигуре, px.
FREE_ENDPOINT_SNAP = 30.0

#: Насколько близко текстовая накладка должна лежать к безымянной фигуре, px.
#: На картах SQB подпись касается своего шлюза или документа (зазор 0-34 px),
#: а до следующей безымянной фигуры не ближе ~100 px, поэтому порог однозначен.
TEXT_LABEL_SNAP = 60.0


def _box_gap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Зазор между двумя прямоугольниками (0 — если пересекаются)."""
    dx = max(b[0] - (a[0] + a[2]), 0.0, a[0] - (b[0] + b[2]))
    dy = max(b[1] - (a[1] + a[3]), 0.0, a[1] - (b[1] + b[3]))
    return (dx * dx + dy * dy) ** 0.5


#: Подпись дорожки из одних символов («+», «—») смыслом не является.
_WORDY_RE = re.compile(r'[0-9A-Za-z\u0400-\u04FF]')


def is_junk_label(text: str) -> bool:
    """Подпись без единой буквы и цифры — оформление, а не имя."""
    return not _WORDY_RE.search(text or '')


def attach_text_overlays(
    overlays: List[Tuple[str, str, Tuple[float, float, float, float], str]],
    targets: List[Tuple[str, Tuple[float, float, float, float]]],
    lane_of: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
    """Раздаёт текстовые накладки безымянным фигурам и дорожкам.

    В draw.io подпись фигуры можно набрать отдельным текстовым блоком рядом —
    редактор рисует то же самое, но в модели у фигуры остаётся пустое ``value``.
    Без этой привязки шлюз попадал в регламент как «Условие», документ — как
    «Документ», а сам текст либо терялся, либо становился отдельным шагом.

    ``overlays`` — ``(id накладки, текст, рамка, id дорожки-родителя)``;
    ``targets`` — рамки фигур, у которых своей подписи нет;
    ``lane_of`` — дорожки без осмысленного имени: ``id -> id``.

    Возвращает ``(подписи фигур, названия дорожек, разобранные накладки)``.
    Третьим идут идентификаторы накладок, ставших чьей-то подписью: всё
    остальное подписью не стало, и разбор решает их судьбу сам.
    """
    pairs: List[Tuple[float, str, str]] = []
    for overlay_id, _text, box, _lane in overlays:
        for target_id, target_box in targets:
            gap = _box_gap(box, target_box)
            if gap <= TEXT_LABEL_SNAP:
                pairs.append((gap, overlay_id, target_id))
    pairs.sort(key=lambda t: (t[0], t[1], t[2]))

    text_by_overlay = {o[0]: o[1] for o in overlays}
    shape_labels: Dict[str, str] = {}
    used_overlays: Set[str] = set()
    for _gap, overlay_id, target_id in pairs:
        if overlay_id in used_overlays or target_id in shape_labels:
            continue
        shape_labels[target_id] = text_by_overlay[overlay_id]
        used_overlays.add(overlay_id)

    # Накладка, не нашедшая фигуры, но лежащая в безымянной дорожке, —
    # её заголовок: так подписаны вертикальные дорожки на картах SQB.
    lane_titles: Dict[str, str] = {}
    title_overlay: Dict[str, str] = {}
    for overlay_id, text, _box, lane_id in overlays:
        if overlay_id in used_overlays or not lane_id or lane_id not in lane_of:
            continue
        current = lane_titles.get(lane_id, '')
        if len(text) > len(current):
            lane_titles[lane_id] = text
            title_overlay[lane_id] = overlay_id
    return shape_labels, lane_titles, used_overlays | set(title_overlay.values())


#: Условия-антонимы: подпись одной ветки развилки задаёт подпись второй.
_CONDITION_OPPOSITES: Dict[str, str] = {
    'ha': "Yo'q", "yo'q": 'Ha', 'yo`q': 'Ha', 'yo’q': 'Ha',
    'да': 'Нет', 'нет': 'Да',
    'yes': 'No', 'no': 'Yes',
    "to'liq": "To'liq emas", "to'liq emas": "To'liq",
    'mos keldi': 'Mos kelmadi', 'mos kelmadi': 'Mos keldi',
    'ijobiy': 'Salbiy', 'salbiy': 'Ijobiy',
    'qabul qilindi': 'Rad etildi', 'rad etildi': 'Qabul qilindi',
}


def complete_binary_gateway_conditions(
    flow_nodes: List[ProcessNode],
    edges: List[ProcessEdge],
) -> List[Tuple[ProcessNode, ProcessEdge, str]]:
    """Достраивает подпись второй ветки развилки по подписи первой.

    У развилки «да/нет» аналитик часто подписывает только отрицательную ветку:
    на рисунке и так понятно, что вторая — «Ha». PIX BPM так не умеет: ветка без
    условия не автоматизируется, и шаг регламента упирается в неё.

    Достраиваем только там, где догадка однозначна: исключающий шлюз ровно с
    двумя исходящими ветками, подписана ровно одна, и её подпись входит в пару
    антонимов. Всё остальное остаётся ошибкой — угадывать смысл развилки на
    три ветки платформа не вправе.

    Возвращает список ``(шлюз, ветка, подставленное условие)`` — о каждой
    подстановке отчёт обязан сказать сотруднику.
    """
    outgoing: Dict[str, List[ProcessEdge]] = {}
    for edge in edges:
        if (edge.kind or 'sequenceFlow') == 'sequenceFlow' and edge.sourceId:
            outgoing.setdefault(edge.sourceId, []).append(edge)

    filled: List[Tuple[ProcessNode, ProcessEdge, str]] = []
    for node in flow_nodes:
        if node.type != 'exclusiveGateway':
            continue
        branches = outgoing.get(node.id, [])
        if len(branches) != 2:
            continue
        named = [e for e in branches if (e.name or e.condition or '').strip()]
        blank = [e for e in branches if not (e.name or e.condition or '').strip()]
        if len(named) != 1 or len(blank) != 1:
            continue
        known = (named[0].name or named[0].condition or '').strip().lower()
        opposite = _CONDITION_OPPOSITES.get(known)
        if not opposite:
            continue
        blank[0].name = opposite
        blank[0].condition = opposite
        filled.append((node, blank[0], opposite))
    return filled


def reclassify_events(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> None:
    """Приводит тип события к его реальной степени на карте.

    Тип фигуры определяется до того, как разобраны связи: у линии draw.io конец
    может висеть в пустоте, и её притягивает к фигуре уже отдельный проход. До
    этого прохода промежуточное событие выглядит как висячее и получает тип
    ``startEvent`` — на карте вместо одного старта рисуется пять, а в отчёте
    появляется ложное «Стартовых событий: 5».
    """
    incoming: Set[str] = set()
    outgoing: Set[str] = set()
    for e in edges:
        if (e.kind or 'sequenceFlow') != 'sequenceFlow':
            continue
        if e.targetId:
            incoming.add(e.targetId)
        if e.sourceId:
            outgoing.add(e.sourceId)

    for node in flow_nodes:
        if node.type not in ('startEvent', 'endEvent'):
            continue
        has_in = node.id in incoming
        has_out = node.id in outgoing
        if has_in and has_out:
            style_l = (node.style or '').lower()
            is_timer = 'symbol=timer' in style_l or is_wait_label(node.name)
            new_type = 'intermediateTimerEvent' if is_timer else 'intermediateMessageEvent'
        elif has_in and not has_out and node.type == 'startEvent':
            new_type = 'endEvent'
        elif has_out and not has_in and node.type == 'endEvent':
            new_type = 'startEvent'
        else:
            continue
        was_generated = node.name in (
            fallback_node_name(node.type, node.code),
            fallback_node_name(node.type, None),
        )
        node.type = new_type
        node.category = classify_category(new_type, node.name, node.style or '')
        node.code = 'START' if new_type == 'startEvent' else 'END' if new_type == 'endEvent' else None
        if was_generated:
            # Подпись «120 min» ушла в длительность ещё у «конечного события»;
            # таймеру возвращаем её, как и таймеру, распознанному сразу, —
            # иначе оставалось «Ожидание», и на карте часы стояли без времени.
            node.name = fallback_node_name(new_type, node.code, node.slaText or '')


def _distance_to_box(px: float, py: float, node: ProcessNode) -> float:
    g = node.geometry
    dx = max(g.x - px, 0.0, px - (g.x + g.width))
    dy = max(g.y - py, 0.0, py - (g.y + g.height))
    return (dx * dx + dy * dy) ** 0.5


def _distance_to_polyline(px: float, py: float, points: List[Tuple[float, float]]) -> float:
    """Насколько точка отстоит от ломаной — по ближайшему её отрезку.

    Именно так подпись держится за свою связь: «YO'Q» стоит вплотную к линии,
    уходящей от шлюза вверх, а не к середине всего её пути. Пока расстояние
    считалось до центра тяжести ломаной, длинная связь «шлюз → шаг 9» с
    отводом на 440 px вверх оказывалась «далеко», и подпись доставалась
    короткой соседней связи, входящей в шлюз.
    """
    if not points:
        return float('inf')
    best = float('inf')
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = dx * dx + dy * dy
        t = 0.0 if not length else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
        best = min(best, ((x1 + dx * t - px) ** 2 + (y1 + dy * t - py) ** 2) ** 0.5)
    return best if len(points) > 1 else (
        (points[0][0] - px) ** 2 + (points[0][1] - py) ** 2) ** 0.5


def _resolve_free_endpoint(
    point: Optional[Tuple[float, float]],
    candidates: List[ProcessNode],
    exclude_id: Optional[str] = None,
) -> Optional[str]:
    """Фигура под свободным концом линии draw.io.

    В draw.io конец связи может быть не привязан к фигуре, а задан точкой
    (``mxPoint as="sourcePoint"``). Редактор всё равно рисует линию, а мы
    раньше выбрасывали её целиком — на карте пропадали и потоки, и пунктирные
    ассоциации к хранилищам данных.
    """
    if point is None or not candidates:
        return None
    best_id: Optional[str] = None
    best_key: Optional[Tuple[int, float, float]] = None
    for node in candidates:
        # Второй конец той же линии уже занял эту фигуру: связь фигуры с самой
        # собой не бывает ни в BPMN, ни в PIX — студия отказывается открывать
        # карту целиком («Connector source and target node cannot be the same»).
        if exclude_id is not None and node.id == exclude_id:
            continue
        dist = _distance_to_box(point[0], point[1], node)
        if dist > FREE_ENDPOINT_SNAP:
            continue
        # Дорожка — контейнер, а не конец связи, и по расстоянию она выигрывает
        # всегда: её рамка накрывает и свободный конец, и шаг рядом с ним, так
        # что до дорожки ноль, а до шага — десятки пикселей. Пунктир от
        # цилиндра системы к шагу становился при этом линией «в дорожку»,
        # оформлением, и в выгрузку не шёл: связь «шаг работает в CROBS»
        # пропадала с карты студии молча. На 22 картах банка таких линий 125, и
        # у 120 из них шаг лежит в том же радиусе притяжения, что и дорожка.
        #
        # Поэтому дорожку берём только тогда, когда рядом нет ни одной фигуры
        # процесса, — это случай пунктира к полосе внешнего участника, где
        # шагов внутри и не бывает. При равном расстоянии выигрывает меньшая
        # фигура: точка внутри шага лежит и внутри его дорожки.
        key = (
            1 if node.type == 'lane' else 0,
            dist,
            float(node.geometry.width) * float(node.geometry.height),
        )
        if best_key is None or key < best_key:
            best_key, best_id = key, node.id
    return best_id


#: Насколько линии draw.io могут разойтись и всё ещё считаться сомкнутыми, px.
_BUS_TOLERANCE = 2.0

_Point = Tuple[float, float]


def _closest_on_segment(p: _Point, a: _Point, b: _Point) -> Tuple[_Point, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if not length else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    q = (a[0] + dx * t, a[1] + dy * t)
    return q, ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5


def _bus_junction(bus: List[_Point], carrier: List[_Point]) -> Optional[Tuple[int, int, _Point]]:
    """Где линия-шина смыкается с переходом: (отрезок шины, отрезок перехода, точка).

    Смыкание — это общий отрезок на одной прямой либо конец шины на линии
    перехода. Общий отрезок проверяется первым: шина, дошедшая до грани шага
    по линии перехода, вливается в неё там, где на неё легла, а не у самой
    грани — иначе новый переход сначала шёл бы назад, к шагу, и лишь потом к
    цели. Пересечение под прямым углом смыканием не считается: линии на
    картах банка пересекаются сплошь и рядом.
    """
    for i, (a1, a2) in enumerate(zip(bus, bus[1:])):
        for j, (b1, b2) in enumerate(zip(carrier, carrier[1:])):
            for axis in (0, 1):
                along = 1 - axis
                if (abs(a1[axis] - a2[axis]) > _BUS_TOLERANCE or abs(b1[axis] - b2[axis]) > _BUS_TOLERANCE
                        or abs(a1[axis] - b1[axis]) > _BUS_TOLERANCE):
                    continue
                lo_a, hi_a = sorted((a1[along], a2[along]))
                lo_b, hi_b = sorted((b1[along], b2[along]))
                if lo_a > hi_b + _BUS_TOLERANCE or lo_b > hi_a + _BUS_TOLERANCE:
                    continue
                # Стык — край общего участка, ближний к концу перехода.
                meet = min(hi_a, hi_b) if b2[along] >= b1[along] else max(lo_a, lo_b)
                point = [0.0, 0.0]
                point[axis], point[along] = b1[axis], min(max(meet, lo_b), hi_b)
                return i, j, (point[0], point[1])
    for end, index in ((bus[0], 0), (bus[-1], len(bus) - 2)):
        for j, (a, b) in enumerate(zip(carrier, carrier[1:])):
            q, dist = _closest_on_segment(end, a, b)
            if dist <= _BUS_TOLERANCE:
                return index, j, q
    return None


def rejoin_merge_buses(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> int:
    """Шина draw.io: шаги, сведённые одной линией в чужой переход.

    Параллельное согласование аналитики рисуют так: от каждого «Kelishish»
    линия без стрелки уходит в общую вертикаль, а та вливается в ромб
    «Kelishildimi?». Редактор хранит это как связь «шаг → соседний шаг» без
    наконечника (``endArrow=none``) и один переход из шины в ромб. Глазу видно
    слияние, а в модели выходило «4 → 5»: в BPMN — поток между параллельными
    шагами, в PIX — стрелка в соседний шаг, и до ромба доходила одна ветка из
    трёх. На картах банка таких линий полторы сотни.

    Связь без стрелки между двумя шагами, чья линия смыкается с переходом
    X → T, заменяется переходами в T от обоих её концов; ломаная нового
    перехода повторяет шину и хвост перехода. Шина, сомкнутая с другой шиной,
    разбирается следующим проходом — уже по её новым переходам. Линия,
    сомкнутая с переходами в разные цели, остаётся как была: угадывать тут
    нечего. Возвращает число разобранных шин.

    Та же скобка бывает и на входе. Согласующих ставят столбиком и соединяют
    линиями без стрелок с обеих сторон: стрелка входит в один шаг группы,
    выходит из одного, а скобки слева раздают вход, справа сводят выход («1.3»:
    217 → 218, скобки 218–219–220, 218 → «Kelishildimi?»). Скобка — линия,
    прицепленная к обоим шагам с одной стороны; если с этой же стороны в шаг A
    входит стрелка X → A, скобка раздаёт её: вместо неё переход X → B. Линия
    с правой грани одного шага на левую грань другого («Tovarlar»: kelishish →
    Boshqaruv…) скобкой не бывает, даже если в ту же грань входит стрелка. Раньше такая скобка оставалась переходом между
    согласующими («219 → 218»), а последний шаг группы — недостижимым.
    Раздаём только переход из шага или события: ветка шлюза, разведённая на
    два шага, стала бы двумя одинаковыми ветками.
    """
    node_by_id = {n.id: n for n in flow_nodes}
    tasks = set(TASK_NODE_TYPES)
    gateways = {'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway'}

    def arrowless(edge: ProcessEdge) -> bool:
        return 'endarrow=none' in (edge.style or '').lower()

    def box(edge: ProcessEdge) -> Tuple[float, float, float, float]:
        xs, ys = [], []
        for nid in (edge.sourceId, edge.targetId):
            g = node_by_id[nid].geometry
            xs += [g.x, g.x + g.width]
            ys += [g.y, g.y + g.height]
        xs += [p.x for p in edge.points]
        ys += [p.y for p in edge.points]
        return min(xs), min(ys), max(xs), max(ys)

    routes: Dict[str, List[_Point]] = {}

    def side(node_id: str, point: _Point) -> str:
        """Грань фигуры, на которой лежит точка линии."""
        g = node_by_id[node_id].geometry
        gaps = {'left': abs(point[0] - g.x), 'right': abs(point[0] - (g.x + g.width)),
                'top': abs(point[1] - g.y), 'bottom': abs(point[1] - (g.y + g.height))}
        return min(gaps, key=gaps.get)

    def route(edge: ProcessEdge) -> List[_Point]:
        # Шина — то, что аналитик видит в draw.io, поэтому «локоть» и
        # ортогональная линия строятся алгоритмами самого редактора, как и в
        # выгрузке. Общий поиск пути уводил начало перехода «kelishish →
        # imzolash» (выход слева, Tovarlar) на правую грань шага, там он
        # «касался» линии к соседу, и сосед получал несуществующий переход.
        if edge.id not in routes:
            src, tgt = node_by_id[edge.sourceId], node_by_id[edge.targetId]
            style = (edge.edgeStyle or '').lower()
            path: List[_Point] = []
            if style == 'elbowedgestyle':
                path = drawio_elbow_route(edge, src, tgt)
            elif style == 'orthogonaledgestyle' and src.id != tgt.id:
                frame = lambda n: (float(n.geometry.x), float(n.geometry.y),
                                   float(n.geometry.width), float(n.geometry.height))
                path = orthogonal_route(edge.style, frame(src), frame(tgt),
                                        perimeter_kind(src.style), perimeter_kind(tgt.style),
                                        [(float(p.x), float(p.y)) for p in edge.points])
            if len(path) < 2:
                path = orthogonal_waypoints(edge, src, tgt)
            routes[edge.id] = [(float(x), float(y)) for x, y in path]
        return routes[edge.id]

    flows = [e for e in edges if e.kind == 'sequenceFlow'
             and e.sourceId in node_by_id and e.targetId in node_by_id]
    buses = [e for e in flows if arrowless(e)
             and node_by_id[e.sourceId].type in tasks and node_by_id[e.targetId].type in tasks]
    resolved = 0
    progress = True
    while buses and progress:
        progress = False
        for bus in list(buses):
            bus_route = route(bus)
            if len(bus_route) < 2:
                continue
            bx0, by0, bx1, by1 = box(bus)
            ends = (bus.sourceId, bus.targetId)

            # Раздача: шина — скобка (обе её грани одной стороны), и в её шаг
            # входит стрелка с той же стороны.
            forks = []
            bracket = side(bus.sourceId, bus_route[0]) == side(bus.targetId, bus_route[-1])
            for flow in flows if bracket else []:
                if (flow is bus or arrowless(flow) or flow.targetId not in ends or flow.sourceId in ends
                        or node_by_id[flow.sourceId].type in gateways):
                    continue
                flow_route = route(flow)
                if len(flow_route) < 2:
                    continue
                at_bus = bus_route[0] if flow.targetId == bus.sourceId else bus_route[-1]
                if side(flow.targetId, flow_route[-1]) == side(flow.targetId, at_bus):
                    forks.append(flow)
            if forks:
                for carrier in forks:
                    end = carrier.targetId
                    other = bus.targetId if end == bus.sourceId else bus.sourceId
                    if any(e.sourceId == carrier.sourceId and e.targetId == other for e in flows):
                        continue
                    own = bus_route if end == bus.sourceId else bus_route[::-1]
                    path = [*route(carrier)[:-1], *own]
                    bends: List[_Point] = []
                    for point in path[1:-1]:
                        if not bends or abs(point[0] - bends[-1][0]) + abs(point[1] - bends[-1][1]) > 0.5:
                            bends.append(point)
                    entry_x, entry_y = (bus.entryX, bus.entryY) if other == bus.targetId else (bus.exitX, bus.exitY)
                    forked = ProcessEdge(
                        id=f'{bus.id}__in__{carrier.sourceId}',
                        kind='sequenceFlow',
                        sourceId=carrier.sourceId,
                        targetId=other,
                        points=[ProcessEdgePoint(x=int(round(x)), y=int(round(y))) for x, y in bends],
                        exitX=carrier.exitX,
                        exitY=carrier.exitY,
                        entryX=entry_x,
                        entryY=entry_y,
                        style=carrier.style,
                        edgeStyle=carrier.edgeStyle,
                        strokeColor=carrier.strokeColor,
                        strokeWidth=carrier.strokeWidth,
                    )
                    # Следующая скобка группы раздаёт уже этот переход — по той
                    # ломаной, что видна на карте, а не по пересчитанной.
                    routes[forked.id] = path
                    edges.append(forked)
                    flows.append(forked)
                edges.remove(bus)
                flows.remove(bus)
                buses.remove(bus)
                resolved += 1
                progress = True
                continue

            carriers = []
            for flow in flows:
                if flow is bus or arrowless(flow) or flow.targetId in ends:
                    continue
                fx0, fy0, fx1, fy1 = box(flow)
                if (fx0 > bx1 + _BUS_TOLERANCE or bx0 > fx1 + _BUS_TOLERANCE
                        or fy0 > by1 + _BUS_TOLERANCE or by0 > fy1 + _BUS_TOLERANCE):
                    continue
                flow_route = route(flow)
                meet = _bus_junction(bus_route, flow_route) if len(flow_route) >= 2 else None
                # У самого конца перехода шина уже не вливается в него, а
                # просто подходит к той же цели.
                if meet is None or _closest_on_segment(meet[2], flow_route[-1], flow_route[-1])[1] <= _BUS_TOLERANCE:
                    continue
                carriers.append((flow, meet))
            targets = {flow.targetId for flow, _meet in carriers}
            if len(targets) > 1:
                targets &= {t for t in targets if node_by_id[t].type in gateways}
            if len(targets) != 1:
                continue
            target = targets.pop()
            carrier, (bus_index, flow_index, joint) = next(
                (flow, meet) for flow, meet in carriers if flow.targetId == target)
            flow_route = route(carrier)
            tail = [joint, *flow_route[flow_index + 1:]]
            for end in ends:
                if any(e.sourceId == end and e.targetId == target for e in flows):
                    continue
                own = bus_route if end == bus.sourceId else bus_route[::-1]
                index = bus_index if end == bus.sourceId else len(bus_route) - 2 - bus_index
                path = [*own[:index + 1], *tail]
                bends: List[_Point] = []
                for point in path[1:-1]:
                    if not bends or abs(point[0] - bends[-1][0]) + abs(point[1] - bends[-1][1]) > 0.5:
                        bends.append(point)
                exit_x, exit_y = (bus.exitX, bus.exitY) if end == bus.sourceId else (bus.entryX, bus.entryY)
                joined = ProcessEdge(
                    id=f'{bus.id}__{end}',
                    kind='sequenceFlow',
                    sourceId=end,
                    targetId=target,
                    points=[ProcessEdgePoint(x=int(round(x)), y=int(round(y))) for x, y in bends],
                    exitX=exit_x,
                    exitY=exit_y,
                    entryX=carrier.entryX,
                    entryY=carrier.entryY,
                    style=carrier.style,
                    edgeStyle=carrier.edgeStyle,
                    strokeColor=carrier.strokeColor,
                    strokeWidth=carrier.strokeWidth,
                )
                edges.append(joined)
                flows.append(joined)
            edges.remove(bus)
            flows.remove(bus)
            buses.remove(bus)
            resolved += 1
            progress = True
    return resolved


def _apply_duration_badges(
    flow_nodes: List[ProcessNode],
    badges: List[Tuple[float, float, float, bool, str]],
) -> Set[str]:
    """Переносит ST/WT из фигур-таймеров в ближайший шаг процесса (4-ILOVA).

    Возвращает шаги, которым время проставил реальный бейдж с карты: остальным
    оно досталось от эвристики по категории, и об этом надо сказать аналитику.
    """
    tasks = [n for n in flow_nodes if n.type in TASK_NODE_TYPES]
    if not tasks or not badges:
        return set()
    st_seen: Set[str] = set()
    wt_seen: Set[str] = set()
    for cx, cy, minutes, is_wait, text in badges:
        best: Optional[ProcessNode] = None
        best_dist = BADGE_ATTACH_RADIUS
        for t in tasks:
            tx = t.geometry.x + t.geometry.width / 2
            ty = t.geometry.y + t.geometry.height / 2
            dist = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
            if dist < best_dist:
                best_dist, best = dist, t
        if best is None:
            continue
        value = max(1, int(round(minutes)))
        # Время пришло с карты — отдельной фигурой-часами рядом с шагом. Именно
        # так его и рисует аналитик: в подписи шага цифры нет. Снятым считается
        # только время выполнения (ST): значок ожидания говорит о WT, а ST шага
        # при нём остаётся подставленным по категории, и выдавать его за
        # снятое с карты нельзя — холст нарисовал бы часы с условной цифрой.
        if not is_wait:
            best.slaMeasured = True
        # Подпись часов запоминаем словами карты. Двое часов одного рода у шага
        # складываются в одно время, но на карте это две подписи — «10 min» и
        # «480 min», и «8 ч 10 мин» там не написано: пишем обе через «+».
        if is_wait:
            best.waitMinutes = (best.waitMinutes or 0) + value
            best.waitText = (f'{best.waitText} + {text}' if best.waitText else None) if best.id in wt_seen else text
            wt_seen.add(best.id)
        elif best.id in st_seen:
            best.slaMinutes = (best.slaMinutes or 0) + value
            best.slaText = f'{best.slaText} + {text}' if best.slaText else None
        else:
            best.slaMinutes = value
            best.slaText = text
            st_seen.add(best.id)
        if best.category != 'rpa_bot':
            best.costPerExecution = (best.slaMinutes or 0) * 1932
    return st_seen


def _resolve_artifact_links(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> None:
    """Системы и документы шага — из реальных ассоциаций карты, а не из эвристик."""
    by_id = {n.id: n for n in flow_nodes}
    systems: Dict[str, List[str]] = {}
    inputs: Dict[str, List[str]] = {}
    outputs: Dict[str, List[str]] = {}

    def _add(bucket: Dict[str, List[str]], key: str, value: str) -> None:
        if not value:
            return
        items = bucket.setdefault(key, [])
        if value not in items:
            items.append(value)

    for e in edges:
        if e.kind != 'association':
            continue
        src = by_id.get(e.sourceId or '')
        tgt = by_id.get(e.targetId or '')
        if not src or not tgt:
            continue
        for artifact, step, incoming_dir in ((src, tgt, True), (tgt, src, False)):
            if step.type not in TASK_NODE_TYPES:
                continue
            if artifact.type == 'dataStore':
                _add(systems, step.id, artifact.name)
            elif artifact.type == 'dataObject':
                _add(inputs if incoming_dir else outputs, step.id, artifact.name)

    for node in flow_nodes:
        linked = systems.get(node.id)
        if linked:
            node.system = canonical_system(', '.join(linked))
            node.systemGuessed = False
        if inputs.get(node.id):
            node.inputArtifacts = inputs[node.id]
        if outputs.get(node.id):
            node.outputArtifacts = outputs[node.id]


#: Свойства фигуры draw.io, которыми платформа переносит свои поля (``drawio_exporter``).
PLATFORM_DATA_PREFIX = 'sqb_'

#: Семейства типов: свойство ``sqb_type`` уточняет тип только внутри семейства,
#: которое следует из рисунка. Перерисованная в draw.io фигура (шаг стал
#: шлюзом) получает тип по рисунку, а не по прежнему свойству.
_TYPE_FAMILIES = (
    frozenset(('task', 'userTask', 'serviceTask', 'subProcess')),
    frozenset(('startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent')),
    frozenset(('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')),
    frozenset(('dataStore', 'dataObject', 'textAnnotation')),
)


def platform_type(drawn: NodeType, wanted: Optional[str]) -> NodeType:
    """Тип по свойству ``sqb_type`` — только там, где его не опровергает рисунок.

    Внутри семейства (пользовательская задача или сервис, старт или
    завершение) уточняет карточка платформы. Шаг может стать примечанием: так
    импорт делает рамку-заголовок над пулом, хотя нарисована она шагом. Фигура,
    перерисованная в draw.io в другое семейство, получает тип по рисунку.
    """
    if not wanted or wanted == drawn:
        return drawn
    family = next((f for f in _TYPE_FAMILIES if drawn in f), frozenset())
    if wanted in family or (drawn in TASK_NODE_TYPES and wanted == 'textAnnotation'):
        return wanted  # type: ignore[return-value]
    return drawn


def _is_bare_duration(text: str) -> bool:
    """Подпись — одно время («5 min», «1 kun») и ничего больше."""
    return bool(text) and duration_minutes(text) is not None and not _DURATION_RE.sub('', text).strip(' .,:;-–—·')


def platform_data(root: ET.Element) -> Dict[str, Dict[str, str]]:
    """Свойства ``sqb_*`` ячеек: {id ячейки: {поле: значение}}."""
    out: Dict[str, Dict[str, str]] = {}
    for cell in root.iter('mxCell'):
        found = {key[len(PLATFORM_DATA_PREFIX):]: value for key, value in cell.attrib.items()
                 if key.startswith(PLATFORM_DATA_PREFIX)}
        if found and cell.get('id'):
            out[cell.get('id')] = found
    return out


def _data_value(field: str, raw: str):
    """Значение свойства в типе поля модели; пустое — ``None`` (список — пустой)."""
    if field in ('inputArtifacts', 'outputArtifacts', 'formFields'):
        return json.loads(raw) if raw else []
    if not raw:
        return None
    if field in ('automationPotential', 'costPerExecution', 'slaMinutes', 'hourlyRate', 'probability'):
        return int(float(raw))
    return raw


def apply_platform_data(flow_nodes: List[ProcessNode], lanes: List[ProcessNode], edges: List[ProcessEdge],
                        data: Dict[str, Dict[str, str]]) -> None:
    """Поля платформы из свойств фигур — поверх того, что разбор вывел из рисунка.

    Свойство пишет только выгрузка самой платформы, и только там, где рисунок
    поле не восстанавливает (роль, отличная от дорожки, категория, заданная в
    карточке, цена запуска робота). Время с карты (значок часов) свойство не
    перебивает: ``sqb_slaMinutes`` — время шага без значка.
    """
    for node in flow_nodes:
        found = data.get(node.id)
        if not found:
            continue
        for field, raw in found.items():
            try:
                value = _data_value(field, raw)
            except (ValueError, TypeError):
                continue
            if field == 'type':
                continue  # тип применён при разборе фигуры (``platform_type``)
            if field == 'name':
                # Имя, которое разбор подписи не восстанавливает: с точкой на
                # конце или со временем внутри (их он срезает), или у события,
                # подписанного одним временем. Только пока подпись та же: переписанная в draw.io
                # подпись важнее.
                bare = _DURATION_TAIL_RE.sub('', value or '').strip(' .,:;-–—·')
                if value and (bare == node.name or _is_bare_duration(node.name)):
                    node.name = value
            elif field == 'slaMinutes':
                if not node.slaMeasured and value is not None:
                    node.slaMinutes = value
            elif field == 'formFields':
                node.formFields = [ProcessField.model_validate(f) for f in value]
            elif field == 'system':
                # Систему задали в карточке платформы: это уже не догадка.
                node.system = canonical_system(value)
                node.systemGuessed = False
            elif field in ProcessNode.model_fields and field not in ('id', 'geometry', 'style'):
                setattr(node, field, value)
    for lane in lanes:
        found = data.get(lane.id, {})
        if 'hourlyRate' in found:
            try:
                lane.hourlyRate = _data_value('hourlyRate', found['hourlyRate'])
            except ValueError:
                pass
    for edge in edges:
        found = data.get(edge.id, {})
        try:
            if 'probability' in found:
                edge.probability = _data_value('probability', found['probability'])
            if 'condition' in found:
                edge.condition = _data_value('condition', found['condition'])
            for field in ('exitX', 'exitY', 'entryX', 'entryY'):
                if field in found:
                    setattr(edge, field, float(found[field]) if found[field] else None)
        except ValueError:
            pass


def unwrap_objects(root: ET.Element) -> int:
    """Переносит ``id`` и подпись с обёртки ``<object>`` на её ``mxCell``.

    Фигуру, которой аналитик задал собственные свойства (в draw.io — «Изменить
    данные»), редактор пишет не ``<mxCell id=… value=…>``, а
    ``<object id=… label=…><mxCell style=… /></object>``: у внутренней ячейки
    нет ни идентификатора, ни подписи. Разбор идёт по ``mxCell``, поэтому такая
    фигура пропадала молча — вместе со всеми связями, которые к ней шли.

    В «Xizmat avtotransportini boshqarish jarayoni» так записаны ВСЕ фигуры
    подпроцесса (рамка с плюсом), и на странице «JAMI» из-за этого исчезали три
    шага и обрывалось девять связей.

    Правим дерево на месте, а не в каждом месте разбора: дальше по коду фигура
    в обёртке ничем не отличается от обычной. Возвращаем число развёрнутых
    обёрток — по нему видно, что правило вообще сработало.
    """
    unwrapped = 0
    for parent in root.iter():
        for holder in list(parent):
            if holder.tag not in ('object', 'UserObject'):
                continue
            inner = holder.find('mxCell')
            if inner is None:
                continue
            if holder.get('id') and not inner.get('id'):
                inner.set('id', holder.get('id'))
            label = holder.get('label')
            if label is not None and inner.get('value') is None:
                inner.set('value', label)
            # Данные платформы (``sqb_*``, см. ``drawio_exporter``) и ссылка на
            # страницу живут на обёртке — переносим их на ячейку вместе с id.
            for key, value in holder.attrib.items():
                if (key.startswith(PLATFORM_DATA_PREFIX) or key == 'link') and inner.get(key) is None:
                    inner.set(key, value)
            # Ячейка встаёт на место обёртки: порядок фигур в файле задаёт
            # порядок отрисовки, и терять его нельзя.
            index = list(parent).index(holder)
            parent.remove(holder)
            parent.insert(index, inner)
            unwrapped += 1
    return unwrapped


def _page_key(name: str) -> str:
    """Имя страницы для сравнения с подписью фигуры: без регистра и лишних пробелов."""
    return re.sub(r'\s+', ' ', (name or '').strip()).lower()


#: Ссылка draw.io на страницу того же файла.
_PAGE_LINK_PREFIX = 'data:page/id,'

#: Номер шага в начале подписи: «5. Kreditning…», «6.1. Huquqiy…».
_STEP_NUMBER_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\.?(?:\s|$)')


def link_sub_pages(process: BusinessProcess, root: ET.Element,
                   models: Sequence[str], main_index: int) -> None:
    """Какую страницу раскрывает каждая фигура с плюсом (``calledSubProcessId``).

    Первым делом — ссылка самого draw.io (``link="data:page/id,…"``): по ней
    аналитик и переходит на страницу. Совпадение подписи с именем страницы
    находило не всё: в «Kredit shartnomasining…» страницы названы номерами
    шагов («2», «6.1»), подписи — «2. Mijozni identifikatsiyalash…», и из 18
    подпроцессов главной страницы не находился ни один. Без ссылки или с
    битой ссылкой (у шага «5.» там она ведёт на удалённую страницу) страница
    ищется по имени, а затем по номеру шага в начале подписи.
    """
    diagram_ids = [d.get('id') or '' for d in root.findall('.//diagram')]
    page_by_index = {
        int(page.id.rsplit('_p', 1)[1]): page
        for page in process.subProcesses
        if page.id.rsplit('_p', 1)[-1].isdigit()
    }
    page_by_diagram = {diagram_ids[i]: page for i, page in page_by_index.items()
                       if i < len(diagram_ids) and diagram_ids[i]}
    by_name = {_page_key(page.name): page for page in process.subProcesses}

    def links_of(index: int) -> Dict[str, str]:
        if not (0 <= index < len(models)) or not models[index]:
            return {}
        try:
            model = ET.fromstring(models[index])
        except ET.ParseError:
            return {}
        return {
            el.get('id'): el.get('link')[len(_PAGE_LINK_PREFIX):]
            for el in model.iter()
            if el.get('id') and (el.get('link') or '').startswith(_PAGE_LINK_PREFIX)
        }

    for index, owner in [(main_index, process), *page_by_index.items()]:
        links = links_of(index)
        for node in owner.nodes:
            if node.type != 'subProcess':
                continue
            target = page_by_diagram.get(links.get(node.id, ''))
            if target is None:
                target = by_name.get(_page_key(node.name))
            if target is None:
                number = _STEP_NUMBER_RE.match(node.name or '')
                target = by_name.get(number.group(1)) if number else None
            node.calledSubProcessId = target.id if target is not None and target is not owner else None


def attach_sub_pages(process: BusinessProcess, content: str, filename: str, keep_blank_shapes: bool = False) -> None:
    """Разбирает остальные страницы файла: части — в подпроцессы, варианты — в варианты.

    Каждая страница — самостоятельная схема со своими дорожками и своим
    началом; объединять их в один холст нельзя. Поэтому разбираем каждую тем же
    разбором, что и основную. Части процесса ложатся в ``subProcesses``, и
    выгрузка отдаёт их отдельными ``<bpmn:process>`` со своими схемами.
    Варианты (AS IS / TO BE) ложатся в ``variants``: на холсте они открываются
    вкладками, а в выгрузку этой карты не идут. Страница из одних таблиц
    схемой не является и не разбирается вовсе.

    О каждой странице карта сообщает замечанием — и сообщает то, что с ней
    действительно стало, включая страницу, которую разобрать не удалось: раньше
    такая молча пропадала.

    Рекурсии здесь не бывает: странице отдаётся ``<mxfile>`` ровно с одной
    диаграммой, и на следующем витке эта же функция сразу выходит.
    """
    plan = plan_pages(content)
    if plan is None:
        return
    root = ET.fromstring((content or '').strip())
    names = page_names(root)
    models = _diagram_models(root)

    failed: List[Tuple[str, str]] = []
    for bucket, pages in ((process.subProcesses, plan.parts), (process.variants, plan.variants)):
        for index, name in pages:
            single = f'<mxfile><diagram name={quoteattr(name)}>{models[index]}</diagram></mxfile>'
            try:
                page = parse_drawio_xml(single, f'{filename} — {name}', keep_blank_shapes)
            except Exception as exc:  # noqa: BLE001 — одна кривая страница не отменяет импорт
                failed.append((name, str(exc) or exc.__class__.__name__))
                continue
            # Страница живёт внутри своей карты: её id и код выводятся из id
            # карты и номера страницы, иначе две одинаковые страницы файла
            # получили бы один id, а вкладки на холсте — одну страницу на двоих.
            page.id = f'{process.id}_p{index}'
            page.passport.code = passport_code_for(page.id)
            # Имя страницы важнее заголовка, набранного на ней: по нему сотрудник
            # находит подпроцесс, и по нему же на него ссылается фигура с плюсом.
            page.name = name
            page.passport.name = name
            bucket.append(page)

    link_sub_pages(process, root, models, main_page_index(names, models))
    process.mainPageName = plan.main or None

    process.validation.insert(0, ProcessValidation(
        level='warning' if failed else 'info',
        code='pages_skipped',
        message=f'В файле {len(names)} стр., основная карта — «{plan.main}».',
        hint=pages_hint(plan, failed),
    ))


def parse_drawio_xml(content: str, filename: str, keep_blank_shapes: bool = False) -> BusinessProcess:
    xml_str, is_bpmn = extract_graph_xml(content)
    if is_bpmn:
        return parse_bpmn_xml(xml_str, filename)

    root = ET.fromstring(xml_str)
    unwrap_objects(root)
    platform = platform_data(root)
    cells = root.findall('.//mxCell')
    cell_map: Dict[str, ET.Element] = {c.get('id', ''): c for c in cells if c.get('id')}

    # ── Таблицы draw.io ─────────────────────────────────────────────────────
    # Таблица и её ячейки фигурами процесса не являются (см. ``table_members``).
    # Снимаем их до всех остальных правил: иначе строка таблицы сходит за
    # дорожку, ячейка «78 daqiqa» — за значок времени, а остальные — за шаги.
    # В ``cell_map`` они остаются: от них отсчитываются координаты детей.
    table_of = table_members(cells)
    tables: Dict[str, Tuple[str, int]] = {}
    for c in cells:
        table_id = table_of.get(c.get('id') or '')
        if table_id is None:
            continue
        title, count = tables.get(table_id, ('', 0))
        is_cell = (_style_map(c.get('style') or '').get('shape') or '').lower() not in _TABLE_SHAPES
        tables[table_id] = (title or clean_label(c.get('value')), count + (1 if is_cell else 0))
    cells = [c for c in cells if c.get('id') not in table_of]

    raw_edges = [c for c in cells if c.get('edge') == '1']
    edge_id_set = {e.get('id', '') for e in raw_edges if e.get('id')}

    incoming: Set[str] = {e.get('target', '') for e in raw_edges if e.get('target')}
    outgoing: Set[str] = {e.get('source', '') for e in raw_edges if e.get('source')}

    label_map: Dict[str, str] = {}
    #: Подпись связи отдельной ячейкой: её строки и кегль — для карты PIX.
    label_lines_map: Dict[str, str] = {}
    label_font_map: Dict[str, float] = {}
    edge_label_geo: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    edge_label_raw: Dict[str, List[float]] = {}
    ignore_cell_ids: Set[str] = set()
    origin_cache: Dict[str, Tuple[float, float]] = {}
    orphan_condition_labels: List[Tuple[str, float, float]] = []  # text, x, y
    #: Текстовые накладки: (id, текст, рамка в абсолютных координатах, id родителя).
    text_overlays: List[Tuple[str, str, Tuple[float, float, float, float], str]] = []
    # Бейджи длительности: (центр X, центр Y, минуты, это ожидание WT?, подпись)
    duration_badges: List[Tuple[float, float, float, bool, str]] = []

    # ── Бейджи ST/WT ────────────────────────────────────────────────────────
    # По Методике (4-ILOVA) время операции проставляется отдельной мелкой
    # фигурой-таймером рядом с шагом, а не в самой подписи шага. Такие фигуры
    # не соединены рёбрами: снимаем их с карты и переносим в ST/WT шага.
    for c in cells:
        if c.get('vertex') != '1':
            continue
        c_id = c.get('id', '')
        if not c_id or c_id in incoming or c_id in outgoing:
            continue
        text = clean_label(c.get('value'))
        minutes = duration_badge_minutes(c.get('style') or '', text)
        if minutes is None:
            continue
        geo_b = c.find('mxGeometry')
        if geo_b is None:
            continue
        try:
            bx, by = _parent_origin(c.get('parent'), cell_map, origin_cache)
            cx = float(geo_b.get('x', '0') or 0) + bx + float(geo_b.get('width', '20') or 20) / 2
            cy = float(geo_b.get('y', '0') or 0) + by + float(geo_b.get('height', '20') or 20) / 2
        except ValueError:
            continue
        duration_badges.append((cx, cy, minutes, is_wait_label(text), text))
        ignore_cell_ids.add(c_id)

    def _raw_label_geo(geo_el: Optional[ET.Element]) -> Optional[List[float]]:
        """Геометрия подписи draw.io без пересчёта: x, y и смещение."""
        if geo_el is None:
            return None
        off = next((p for p in geo_el.findall('mxPoint') if (p.get('as') or '') == 'offset'), None)
        try:
            return [float(geo_el.get('x') or 0), float(geo_el.get('y') or 0),
                    float(off.get('x') or 0) if off is not None else 0.0,
                    float(off.get('y') or 0) if off is not None else 0.0]
        except ValueError:
            return None

    def _remember_label_geo(edge_id: str, geo_el: Optional[ET.Element]) -> None:
        if geo_el is None or edge_id in edge_label_geo:
            return
        raw = _raw_label_geo(geo_el)
        if raw is not None:
            edge_label_raw[edge_id] = raw
        lx = geo_el.get('x')
        ly = geo_el.get('y')
        off = None
        for p in geo_el.findall('mxPoint'):
            if (p.get('as') or '') == 'offset':
                off = p
                break
        x_val = float(lx) if lx not in (None, '') else None
        y_val = float(ly) if ly not in (None, '') else None
        if off is not None:
            ox = float(off.get('x', '0') or 0)
            oy = float(off.get('y', '0') or 0)
            # offset.y всегда прибавляется к перпендикулярному смещению
            if y_val is None:
                y_val = oy
            else:
                y_val += oy
            # offset.x добавляем к доле вдоль ребра (маленький шаг, т.к. x - доля -1..1)
            if x_val is not None and ox != 0:
                # 100px ~ 0.1 доли, эвристика
                x_val += ox * 0.005
            elif x_val is None and ox != 0:
                x_val = ox * 0.005
        if x_val is not None or y_val is not None:
            edge_label_geo[edge_id] = (x_val, y_val)

    for c in cells:
        c_id = c.get('id', '')
        parent_id = c.get('parent', '')
        style = (c.get('style') or '').lower()
        raw_val = c.get('value', '')
        cleaned = clean_label(raw_val)
        geo = c.find('mxGeometry')
        is_relative = geo.get('relative') == '1' if geo is not None else False
        is_connectable0 = c.get('connectable') == '0'

        # 1. Child of an edge (relative label)
        if parent_id in edge_id_set:
            ignore_cell_ids.add(c_id)
            if cleaned:
                label_map[parent_id] = cleaned
                label_lines_map[parent_id] = label_lines(raw_val)
                size = _style_float(_style_map(c.get('style') or ''), 'fontsize')
                if size:
                    label_font_map[parent_id] = size
            _remember_label_geo(parent_id, geo)
            continue

        # 2. Explicit edgeLabel or relative=1
        if 'edgelabel' in style or is_connectable0 or is_relative:
            ignore_cell_ids.add(c_id)
            if cleaned and parent_id:
                label_map[parent_id] = cleaned
                label_lines_map[parent_id] = label_lines(raw_val)
                size = _style_float(_style_map(c.get('style') or ''), 'fontsize')
                if size:
                    label_font_map[parent_id] = size
            # Подпись, оттащенную с линии, draw.io перекладывает на слой: стиль
            # остаётся ``edgeLabel``, а ``parent`` указывает уже не на связь, и
            # координаты становятся абсолютными. Класть такую в ``label_map``
            # бессмысленно — ребра с таким id нет, и «XA» с «YO'Q» у шлюза
            # «Kelishildimi va imzolandimi?» пропадали с карты вовсе. Отдаём её
            # тому же разбору по геометрии, что и подписи-врезки ниже.
            if cleaned and parent_id not in edge_id_set and not is_relative and geo is not None:
                try:
                    ox2, oy2 = _parent_origin(parent_id, cell_map, origin_cache)
                    orphan_condition_labels.append((
                        cleaned,
                        float(geo.get('x', '0') or 0) + ox2 + float(geo.get('width', '0') or 0) / 2,
                        float(geo.get('y', '0') or 0) + oy2 + float(geo.get('height', '0') or 0) / 2,
                    ))
                except ValueError:
                    pass
            continue

        # 2.5. Обрамлённая текстовая врезка — примечание, а не оформление.
        if is_text_note(style, cleaned):
            continue

        # 3. Text label overlay
        if c_id.endswith('_label') or is_text_overlay(style):
            ignore_cell_ids.add(c_id)
            base_id = re.sub(r'_label$', '', c_id)
            if base_id != c_id and cleaned:
                label_map[base_id] = cleaned
            elif cleaned:
                # Подпись отдельным блоком: чью фигуру она называет, решаем
                # по геометрии — соседство на холсте здесь и есть связь.
                g3 = c.find('mxGeometry')
                if g3 is not None:
                    try:
                        ox3, oy3 = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        text_overlays.append((
                            c_id,
                            cleaned,
                            (
                                float(g3.get('x', '0') or 0) + ox3,
                                float(g3.get('y', '0') or 0) + oy3,
                                float(g3.get('width', '0') or 0),
                                float(g3.get('height', '0') or 0),
                            ),
                            parent_id,
                        ))
                    except ValueError:
                        pass
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    # absolute centre
                    ox, oy = _parent_origin(c.get('parent'), {k: v for k, v in cell_map.items()}, origin_cache)  # approximate, will be recomputed later but ok
                    # fallback to direct parent origin
                    try:
                        lx = float(g2.get('x', '0') or 0) + ox + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + oy + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
            continue

        # 4. Diagram title banner
        if 'text;' in style and is_non_task_label(cleaned):
            ignore_cell_ids.add(c_id)
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    try:
                        # need parent origin
                        px, py = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        lx = float(g2.get('x', '0') or 0) + px + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + py + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
            continue

        # 5. Non-task system tags, artifacts, conditions without connections
        # Дорожки исключены: у безымянной дорожки подпись пустая, но это не мусор.
        if 'swimlane' in style or 'shape=pool' in style:
            continue
        # Цилиндр и лист с подписью «IABS» — фигуры, а не метки: легенда это или
        # нет, решает их место на карте ниже, в разборе артефактов.
        is_data_shape = 'datastore' in style or 'dataobject' in style
        # Во встроенном редакторе пустая фигура — шаг, который ещё не подписали,
        # а не оформление чужого файла: иначе нарисованное пропадало при сохранении.
        if (is_non_task_label(cleaned) and c_id not in incoming and c_id not in outgoing
                and not is_data_shape and not (keep_blank_shapes and not cleaned)):
            ignore_cell_ids.add(c_id)
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    try:
                        px, py = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        lx = float(g2.get('x', '0') or 0) + px + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + py + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
            continue

    swimlane_cells = [
        c for c in cells
        if c.get('vertex') == '1' and any(k in (c.get('style') or '').lower() for k in ('swimlane', 'shape=pool'))
    ]

    def _nested_lane_fits(outer: ET.Element, inner: ET.Element) -> bool:
        """Нарисована ли дочерняя дорожка внутри рамки родителя.

        У настоящего пула дорожки замощают его без остатка (``lane y+h == pool h``),
        поэтому вложенная дорожка — верный признак пула. Но на картах банка
        дорожка бывает записана потомком соседней, а нарисована вне её рамки:
        в «Xizmat avtotransportini» «MXD guruh rahbari» лежит целиком ниже
        «MXD mas'ul xodimi», в «Tovarlar … logistikasi» «Xizmat ko'tsatuvchi
        tashkilot» — на тысячу пикселей выше. Аналитик перетащил дорожку мышью,
        ссылка на родителя осталась. Считать родителя пулом из-за такого потомка
        нельзя — дорожка пропадала из разбора, её шаги оставались без
        ответственного, и раскладка сбивала их в кучу на чужой строке.
        """
        og, ig = outer.find('mxGeometry'), inner.find('mxGeometry')
        if og is None or ig is None:
            return True
        try:
            ow = float(og.get('width', '0') or 0)
            oh = float(og.get('height', '0') or 0)
            ix = float(ig.get('x', '0') or 0)
            iy = float(ig.get('y', '0') or 0)
            iw = float(ig.get('width', '0') or 0)
            ih = float(ig.get('height', '0') or 0)
        except ValueError:
            return True
        return (
            ix >= -1.0 and iy >= -1.0
            and ix + iw <= ow + 1.0 and iy + ih <= oh + 1.0
        )

    pool_ids: Set[str] = set()
    for sw in swimlane_cells:
        sw_id = sw.get('id', '')
        has_children = any(
            other.get('parent') == sw_id and _nested_lane_fits(sw, other)
            for other in swimlane_cells
        )
        if has_children or 'stacklayout' in (sw.get('style') or '').lower():
            pool_ids.add(sw_id)

    swimlane_ids = {c.get('id', '') for c in swimlane_cells if c.get('id')}
    container_ids = set(pool_ids) | swimlane_ids | {'0', '1'}

    # ── Подписи, набранные отдельным текстовым блоком ────────────────────────
    # Их адресат определяется соседством на холсте, поэтому раздаём подписи
    # после того, как собраны все фигуры и известно, у каких из них имени нет.
    unlabeled_boxes: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for c in cells:
        c_id = c.get('id', '')
        if c.get('vertex') != '1' or not c_id or c_id in ignore_cell_ids:
            continue
        if c_id in swimlane_ids or c_id in pool_ids or c_id in label_map:
            continue
        if clean_label(c.get('value')):
            continue
        g4 = c.find('mxGeometry')
        if g4 is None:
            continue
        try:
            ox4, oy4 = _parent_origin(c.get('parent'), cell_map, origin_cache)
            unlabeled_boxes.append((c_id, (
                float(g4.get('x', '0') or 0) + ox4,
                float(g4.get('y', '0') or 0) + oy4,
                float(g4.get('width', '0') or 0),
                float(g4.get('height', '0') or 0),
            )))
        except ValueError:
            continue

    nameless_lane_ids = {
        c.get('id', ''): c.get('id', '')
        for c in swimlane_cells
        if c.get('id') and is_junk_label(clean_label(c.get('value')))
    }
    overlay_labels, lane_titles, used_overlays = attach_text_overlays(
        text_overlays, unlabeled_boxes, nameless_lane_ids,
    )
    label_map.update(overlay_labels)

    def _is_artifact_shape(s: str) -> bool:
        return any(k in s for k in ('datastore', 'dataobject', 'shape=note', 'shape=mxgraph.signs', 'shape=mxgraph.bpmn.annotation'))

    # ── Заголовок схемы ─────────────────────────────────────────────────────
    # Название процесса аналитик пишет широкой рамкой над пулом: в «AVGUST-1.29»
    # это прямоугольник 2880×160 с текстом «Bank tarmog‘i bo‘yicha ma’lumotlarni
    # shakllantirish … jarayoni». Фигурой ``text`` она не является, поэтому
    # разбор принимал её за шаг: в регламенте появлялась операция с названием
    # всего процесса и часом времени, а на карте студии — задача во весь экран.
    #
    # Отличать такую рамку по размеру или кеглю нельзя: у десяти карт ширина
    # 2880…37080 px, кегль 8, 12 и 72 pt. Признак лежит в самой модели —
    # шаг процесса либо стоит в дорожке (у него есть ответственный), либо
    # включён в поток (у него есть связи). Фигура вне пула и без единой связи
    # не шаг ни по одному из двух признаков. На 22 картах банка таких фигур 13,
    # и все 13 — заголовки схем.
    #
    # С карты заголовок не пропадает: в draw.io он нарисован, значит нарисован
    # и у нас, — но становится примечанием, а не операцией регламента.
    lane_boxes: List[Tuple[float, float, float, float]] = []
    for sw in swimlane_cells:
        sw_geo = sw.find('mxGeometry')
        if sw_geo is None:
            continue
        try:
            lox, loy = _parent_origin(sw.get('parent'), cell_map, origin_cache)
            lane_boxes.append((
                float(sw_geo.get('x', '0') or 0) + lox,
                float(sw_geo.get('y', '0') or 0) + loy,
                float(sw_geo.get('width', '0') or 0),
                float(sw_geo.get('height', '0') or 0),
            ))
        except ValueError:
            continue

    def _outside_every_lane(cell: ET.Element) -> bool:
        """Лежит ли центр фигуры за пределами всех дорожек карты.

        Центр, а не рамка: ту же меру применяет разбор, когда определяет
        дорожку шага (``_lane_under``), и расходиться они не должны.
        """
        geo_el = cell.find('mxGeometry')
        if geo_el is None:
            return False
        try:
            cox, coy = _parent_origin(cell.get('parent'), cell_map, origin_cache)
            ccx = float(geo_el.get('x', '0') or 0) + cox + float(geo_el.get('width', '0') or 0) / 2
            ccy = float(geo_el.get('y', '0') or 0) + coy + float(geo_el.get('height', '0') or 0) / 2
        except ValueError:
            return False
        return not any(
            lx <= ccx <= lx + lw and ly <= ccy < ly + lh
            for lx, ly, lw, lh in lane_boxes
        )

    caption_ids: Set[str] = set()
    if lane_boxes:
        caption_ids = {
            c.get('id', '') for c in cells
            if c.get('vertex') == '1' and c.get('id')
            and c.get('id') not in swimlane_ids
            and c.get('id') not in incoming and c.get('id') not in outgoing
            and _outside_every_lane(c)
        }

    # ── Текст, набранный внутри дорожки ─────────────────────────────────────
    # Накладка, не ставшая ничьей подписью, до сих пор просто пропадала. Чаще
    # всего это правильно: заголовок схемы набран тем же ``text`` и на карте
    # процесса ему делать нечего. Но ровно тот же приём аналитик использует,
    # чтобы написать, чем занята полоса участника: в «1.54» внутри полосы
    # «Sport maydoni» лежит абзац на 337 символов о том, что делает спортивный
    # комплекс, и в выгрузке полоса оказывалась пустой.
    #
    # Различает их то же, что и заголовок карты от шага, — положение
    # относительно пула. Текст ВНУТРИ дорожки написан про эту дорожку и
    # остаётся на карте примечанием; текст вне дорожек — оформление холста.
    # На 22 картах банка таких потерянных абзацев 8: 6 внутри дорожек (перечень
    # требуемых документов, содержание работ участника) и 2 снаружи — оба
    # заголовки схем.
    lane_notes: Set[str] = set()
    if lane_boxes:
        lane_notes = {
            overlay_id for overlay_id, _text, _box, _lane in text_overlays
            if overlay_id not in used_overlays
            and overlay_id in cell_map
            and not _outside_every_lane(cell_map[overlay_id])
        }
        ignore_cell_ids -= lane_notes

    #: Иконки-украшения, снятые с карты: о них сотруднику тоже надо сказать.
    skipped_clipart: List[str] = []
    raw_vertices = []
    for c in cells:
        if c.get('vertex') != '1':
            continue
        c_id = c.get('id', '')
        if not c_id or c_id in ignore_cell_ids or c_id in pool_ids:
            continue
        style = (c.get('style') or '').lower()
        # Артефакт без связей (datastore IABS/EHA, note) — легенда, но только вне
        # дорожек. Цилиндр, стоящий в дорожке, нарисован про её работу, как и
        # текст внутри дорожки (см. ``lane_notes``): на «Кредит_шартнома…КБ_7»
        # восемь таких цилиндров молча пропадали из обеих выгрузок.
        if (_is_artifact_shape(style) and c_id not in incoming and c_id not in outgoing
                and (not lane_boxes or _outside_every_lane(c))):
            continue
        parent_id = c.get('parent') or ''
        parent_el = cell_map.get(parent_id)
        if parent_el is not None and parent_el.get('vertex') == '1' and parent_id not in container_ids:
            continue
        geo = c.find('mxGeometry')
        w = float(geo.get('width', '0')) if geo is not None else 0.0
        h = float(geo.get('height', '0')) if geo is not None else 0.0
        unlabeled = not clean_label(c.get('value')) and c_id not in label_map
        tiny = 0 < w <= 32 and 0 < h <= 32
        # Безымянная картинка из библиотеки — украшение, даже если к ней
        # подведена линия: шагом регламента иконка телефона не бывает.
        if is_clipart_style(style) and (unlabeled or is_pictogram_style(style)):
            skipped_clipart.append(_style_map(style).get('shape') or 'image')
            continue
        if is_decoration_style(style) and (unlabeled or tiny) and c_id not in incoming and c_id not in outgoing:
            continue
        if unlabeled and c_id not in incoming and c_id not in outgoing and tiny:
            continue
        raw_vertices.append(c)

    nodes: List[ProcessNode] = []
    step_index = 1
    #: Шаги, чьё время взято с карты, а не из эвристики по категории.
    timed_step_ids: Set[str] = set()
    #: Фигуры, смысл которых импортёр не понял: описание -> подставленные узлы.
    unsupported: Dict[str, List[ProcessNode]] = {}
    #: Рамки-заголовки, снятые с потока: шагами регламента они не стали.
    captions: List[str] = []

    for cell in raw_vertices:
        node_id = cell.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        style = cell.get('style') or ''
        raw_val = cell.get('value')
        raw_cleaned = clean_label(raw_val)
        if not raw_cleaned and 'swimlane' not in (style or '').lower():
            raw_cleaned = label_map.get(node_id, '')

        parent_id = cell.get('parent')

        geo = cell.find('mxGeometry')
        # В draw.io отсутствующий x/y означает 0 (не 100!)
        local_x = float(geo.get('x', '0')) if geo is not None else 0.0
        local_y = float(geo.get('y', '0')) if geo is not None else 0.0
        width = float(geo.get('width', '120')) if geo is not None else 120.0
        height = float(geo.get('height', '60')) if geo is not None else 60.0

        ox, oy = _parent_origin(parent_id, cell_map, origin_cache)
        x = local_x + ox
        y = local_y + oy

        node_type = classify_vertex(style, raw_cleaned, node_id in incoming, node_id in outgoing, node_id)
        # Тип из карточки платформы — до размеров и категории: от типа зависят
        # и минимальная рамка, и вид связей с фигурой.
        wanted_type = platform.get(node_id, {}).get('type')
        node_type = platform_type(node_type, wanted_type)
        if wanted_type:
            pass  # тип задан карточкой платформы — правила по положению не нужны
        elif node_id in lane_notes:
            node_type = 'textAnnotation'
        elif node_type in TASK_NODE_TYPES and node_id in caption_ids:
            node_type = 'textAnnotation'
            captions.append(node_id)
        category = classify_category(node_type, raw_cleaned, style)
        is_task = node_type in ('task', 'userTask', 'serviceTask')

        code = None
        code_match = re.search(r'\b(STEP[-_ ]?\d+|START|END|GW[-_ ]?\w+)\b', f"{raw_val or ''} {raw_cleaned}", re.I)
        # Номер шага бывает составным («84.1.», «37.1.1») и не всегда кончается
        # точкой. Берём его целиком: иначе шаг 84.1 получает код своего родителя
        # STEP-84 и в регламенте эти строки не отличить. Одиночное число без
        # разделителя номером не считаем — «2026 yil» не шаг №2026.
        num_prefix = re.match(r'^(\d+(?:\.\d+)*)([.)]?)(?=\s)', raw_cleaned)
        if num_prefix and not (num_prefix.group(2) or '.' in num_prefix.group(1)):
            num_prefix = None

        if code_match:
            code = code_match.group(1).upper().replace('_', '-')
        elif num_prefix and is_task:
            head, _, rest = num_prefix.group(1).partition('.')
            code = f"STEP-{int(head):02d}" + (f'.{rest}' if rest else '')
        elif node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1

        raw_text = f"{raw_val or ''} {raw_cleaned}"
        # Время ищем только в очищенной подписи: в сырой разметке draw.io лежат
        # цвета вида «#000», и «0 min» из атрибута стиля забирал шагу его SLA.
        sla_min = extract_sla_minutes(raw_cleaned, category, node_type)
        sla_measured = sla_is_measured(raw_cleaned, node_type)
        if duration_minutes(raw_cleaned) is not None:
            timed_step_ids.add(node_id)

        # Номер шага остаётся в названии: на карте draw.io он написан перед
        # текстом, по нему аналитик находит шаг в регламенте и в самой Методике.
        # Снимаем только служебные пометки — тег вида «[PIX RPA]», машинный код
        # «STEP-01:» и приписанное к подписи время.
        clean_name = raw_cleaned
        clean_name = re.sub(r'^\[.*?\]\s*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'^STEP[-_ ]?\d+[:\s-]*', '', clean_name, flags=re.I)
        # Знак, отделявший время от текста, вместе с временем и уходит: подпись
        # «Kutish vaqti : 30 min» набрана в две строки, и без этого в имени
        # оставалось висящее двоеточие.
        tail = _DURATION_TAIL_RE.search(clean_name)
        sla_text = tail.group(0).strip() if tail and sla_measured else None
        clean_name = _DURATION_TAIL_RE.sub('', clean_name).strip(' .,:;-–—·')

        if node_type == 'lane' and is_junk_label(clean_name):
            clean_name = ''  # «+», «—»: имя присвоим позиционно после разбора
        elif not clean_name:
            clean_name = fallback_node_name(node_type, code, raw_cleaned)

        # Guard against zero/negative and invisible boxes (8px не читаемо)
        if node_type == 'lane':
            width = max(int(round(width)), 40)
            height = max(int(round(height)), 40)
        elif node_type.endswith('Event'):
            # Круг/эллипс: минимум 32px для читаемости. Промежуточные события
            # тоже: под минимум шага (80 px) таймер 40×40 растягивался в рамку
            # 80×40, квадрат из неё потом строился по её центру — и таймер
            # уезжал на 20 px с оси шагов, а прямые линии к нему ломались.
            width = max(int(round(width or 44)), 32)
            height = max(int(round(height or 44)), 32)
        elif 'Gateway' in node_type:
            width = max(int(round(width or 48)), 32)
            height = max(int(round(height or 48)), 32)
        elif node_type in ARTIFACT_NODE_TYPES:
            # Артефакт подписан СНАРУЖИ — и в bpmn.io, и в студии, — поэтому
            # места под текст внутри ему не нужно. Минимум 80×40, рассчитанный
            # на шаг, раздувал цилиндр базы вдвое против нарисованного (в
            # картах банка он 30×25 … 50×36) и делал его заметнее самого шага.
            width = max(int(round(width or 40)), 24)
            height = max(int(round(height or 30)), 20)
        else:
            # task / serviceTask: минимум 80x40 для текста
            width = max(int(round(width or 120)), 80)
            height = max(int(round(height or 60)), 40)

        fot_cost = (sla_min * 1932) if category != 'rpa_bot' else 800

        node = ProcessNode(
            id=node_id,
            name=clean_name,
            type=node_type,
            category=category,
            code=code,
            geometry=Geometry(x=int(round(x)), y=int(round(y)), width=int(width), height=int(height)),
            style=style,
            laneId=parent_id,
            slaMinutes=sla_min,
            slaMeasured=sla_measured,
            slaText=sla_text,
            costPerExecution=fot_cost,
            automationPotential=95 if category == 'rpa_bot' else (65 if category == 'manual' else 40)
        )
        unknown = unsupported_shape(style)
        if unknown:
            unsupported.setdefault(unknown, []).append(node)
        nodes.append(node)

    # ── Идентификаторы фигур уникальны ──────────────────────────────────────
    # Инвариант модели, а не удобство экспортёра: и BPMN (xsd:ID), и .pmm
    # требуют уникальности, а карта, склеенная из двух файлов, приносит по два
    # `id="node1"`. Раньше такие фигуры доезжали до выгрузки как есть, и студия
    # отвергала весь пакет. Первое вхождение сохраняет идентификатор — на него
    # уже ссылаются связи, — последующие получают суффикс.
    seen_ids: Set[str] = set()
    for n in nodes:
        if n.id not in seen_ids:
            seen_ids.add(n.id)
            continue
        suffix = 2
        while f'{n.id}__dup{suffix}' in seen_ids:
            suffix += 1
        n.id = f'{n.id}__dup{suffix}'
        seen_ids.add(n.id)

    lanes = [n for n in nodes if n.type == 'lane']
    lane_ids = {l.id for l in lanes}
    flow_nodes = [n for n in nodes if n.type != 'lane']

    for n in flow_nodes:
        if n.laneId and n.laneId not in lane_ids:
            n.laneId = None

    _name_and_prune_lanes(lanes, flow_nodes, lane_titles)

    lane_by_id = {l.id: l for l in lanes}

    def _lane_under(node: ProcessNode) -> Optional[ProcessNode]:
        """Дорожка, внутри которой лежит центр фигуры."""
        return next((lane for lane in lanes if _lane_holds(lane, node)), None)

    for n in flow_nodes:
        # Родитель ячейки в draw.io — не то же самое, что дорожка на рисунке:
        # фигуру можно утащить из дорожки, и редактор сохранит прежнего родителя.
        # Исполнителя шага определяем по тому, где фигура лежит на самом деле,
        # иначе роль в регламенте берётся от чужого подразделения.
        declared = lane_by_id.get(n.laneId or '')
        actual = _lane_under(n)
        if actual is not None and actual.id != (declared.id if declared else None):
            n.laneId = actual.id
            n.role = None
        elif declared is not None and actual is None:
            n.laneId = None
            n.laneName = None
            n.role = None

        p_lane = lane_by_id.get(n.laneId or '')
        if p_lane:
            n.laneName = p_lane.name
            n.role = n.role or p_lane.name
        guess_system(n, n.laneName or '')

    valid_node_ids = {n.id for n in flow_nodes}
    valid_lane_ids = {l.id for l in lanes}
    lane_of_node: Dict[str, Optional[str]] = {n.id: n.laneId for n in flow_nodes}
    type_by_id: Dict[str, str] = {n.id: n.type for n in flow_nodes}
    # Дорожка без единого шага — это внешний участник (клиент, госорган):
    # аналитик отводит ему полосу и тянет к ней пунктир от шагов банка.
    #
    # Решает та же функция, что укладка и выгрузка: пустая полоса ВНУТРИ
    # вертикального размаха заполненных участником не становится — вынести её
    # из пула значило бы пул разорвать. Своё правило здесь стоять не может:
    # пока разбор считал внешней любую пустую дорожку, он помечал пунктир к
    # ней ``messageFlow``, а выгрузка участника под него не находила — и связь
    # молча пропадала из .bpmn, оставаясь на холсте. Теперь такой пунктир
    # честно становится ``annotationLine``, и о нём предупреждает проверка
    # импорта («Линий, не опирающихся на фигуры»).
    external_lane_ids = {
        lane.id for lane in split_external_lanes(lanes, flow_nodes)[1]
    }
    def _edge_kind(src_id: str, tgt_id: str, dashed: bool) -> EdgeKind:
        if src_id in valid_lane_ids or tgt_id in valid_lane_ids:
            lane_id, other_id = (
                (src_id, tgt_id) if src_id in valid_lane_ids else (tgt_id, src_id)
            )
            # Пунктир «шаг банка ↔ полоса клиента» — это обмен сообщениями с
            # внешним участником, а не оформление: в выгрузке он обязан
            # остаться, иначе с карты пропадают точки контакта с клиентом.
            #
            # Второй конец — любая фигура потока, шлюз в том числе. Раньше
            # здесь стоял список InteractionNode из спецификации BPMN, где
            # шлюзов нет, и пунктир от полосы участника к ромбу становился
            # оформлением: на 22 картах банка так терялись 84 связи (66 к
            # «ИЛИ», 18 к «И»). Ограничение это не модели, а одной из двух
            # выгрузок, и место ему в ``bpmn_exporter``, где оно и стоит:
            # карте банка, холсту и .pmm шлюз концом связи быть не мешает.
            if lane_id in external_lane_ids and type_by_id.get(other_id, '') in FLOW_NODE_TYPES:
                return 'messageFlow'
            # Пунктир от фигуры к ЧУЖОЙ дорожке — тот же контакт, даже если в
            # полосе стоят события. На «Кредит_шартнома…КБ_7» в полосе «Mijoz»
            # есть старт и завершение, и 26 стрелок от шагов к её границе
            # пропадали из обеих выгрузок как оформление. Линия, оборвавшаяся
            # внутри своей же дорожки, контактом не бывает: на 13 картах таких
            # 16, и все висят в 42–175 px от любой границы.
            other_lane = lane_of_node.get(other_id)
            if (type_by_id.get(other_id, '') in FLOW_NODE_TYPES
                    and other_lane is not None and other_lane != lane_id):
                return 'messageFlow'
            return 'annotationLine'
        st, tt = type_by_id.get(src_id, ''), type_by_id.get(tgt_id, '')
        # Связь с артефактом — всегда ассоциация (по BPMN sequenceFlow к
        # хранилищу/документу недопустим).
        if st in ARTIFACT_NODE_TYPES or tt in ARTIFACT_NODE_TYPES:
            return 'association'
        if dashed and 'intermediateMessageEvent' in (st, tt):
            return 'messageFlow'
        return 'sequenceFlow'

    edges: List[ProcessEdge] = []
    snap_targets = flow_nodes + lanes
    #: Связи, у которых конец висел в пустоте и был притянут к фигуре.
    snapped_edge_ids: List[str] = []

    for cell in raw_edges:
        s_id = cell.get('source')
        t_id = cell.get('target')

        edge_geo = cell.find('mxGeometry')
        edge_ox, edge_oy = _parent_origin(cell.get('parent'), cell_map, origin_cache)
        free_ends: Dict[str, Tuple[float, float]] = {}
        if edge_geo is not None:
            for mx in edge_geo.findall('mxPoint'):
                role = mx.get('as') or ''
                if role not in ('sourcePoint', 'targetPoint'):
                    continue
                try:
                    free_ends[role] = (
                        float(mx.get('x', '0') or 0) + edge_ox,
                        float(mx.get('y', '0') or 0) + edge_oy,
                    )
                except ValueError:
                    continue

        # Конец без привязки — притягиваем к ближайшей фигуре под ним, но не к
        # той, на которой уже стоит второй конец: у линии, оба конца которой
        # висят в пустоте рядом, иначе получалась петля из фигуры в себя.
        # Конец, который выгрузка платформы пометила свободным, не притягиваем:
        # в карте он висел в пустоте, и притянутый он менял бы вид связи.
        if not s_id and cell.get(PLATFORM_DATA_PREFIX + 'freeSource') != '1':
            s_id = _resolve_free_endpoint(free_ends.get('sourcePoint'), snap_targets, t_id)
            if s_id:
                snapped_edge_ids.append(cell.get('id') or '')
        if not t_id and cell.get(PLATFORM_DATA_PREFIX + 'freeTarget') != '1':
            t_id = _resolve_free_endpoint(free_ends.get('targetPoint'), snap_targets, s_id)
            if t_id and (cell.get('id') or '') not in snapped_edge_ids:
                snapped_edge_ids.append(cell.get('id') or '')

        def _known(node_id: Optional[str]) -> bool:
            return bool(node_id) and (node_id in valid_node_ids or node_id in valid_lane_ids)

        s_known, t_known = _known(s_id), _known(t_id)
        if not s_known:
            s_id = None
        if not t_known:
            t_id = None
        # Линия без единой опоры на фигуру — не связь и не оформление, а мусор.
        if not s_known and not t_known:
            continue
        # Оформительская линия: один конец висит в пустоте. Рисуем, но в
        # выгрузку BPMN/PIX не отдаём — такой конструкции там нет.
        is_annotation_line = not (s_known and t_known)
        if is_annotation_line and not free_ends:
            continue

        edge_id = cell.get('id') or f"edge_{uuid.uuid4().hex[:8]}"
        raw_val = cell.get('value')
        edge_name = clean_label(raw_val) or label_map.get(edge_id, '')
        raw_style = cell.get('style') or ''
        smap = _style_map(raw_style)
        if clean_label(raw_val):
            edge_lines, edge_font = label_lines(raw_val), _style_float(smap, 'fontsize')
        else:
            edge_lines = label_lines_map.get(edge_id, '')
            edge_font = label_font_map.get(edge_id) or _style_float(smap, 'fontsize')

        eox, eoy = _parent_origin(cell.get('parent'), cell_map, origin_cache)
        pts: List[ProcessEdgePoint] = []
        for arr in cell.findall('.//Array'):
            if (arr.get('as') or '') != 'points':
                continue
            for p in arr.findall('mxPoint'):
                pts.append(ProcessEdgePoint(
                    x=int(round(float(p.get('x', '0') or 0) + eox)),
                    y=int(round(float(p.get('y', '0') or 0) + eoy)),
                ))

        lx, ly = edge_label_geo.get(edge_id, (None, None))
        geo = cell.find('mxGeometry')
        if lx is None and geo is not None and geo.get('x') not in (None, ''):
            try:
                lx = float(geo.get('x'))
            except ValueError:
                pass
        if ly is None and geo is not None and geo.get('y') not in (None, ''):
            try:
                ly = float(geo.get('y'))
            except ValueError:
                pass

        lower_style = raw_style.lower()
        is_dashed = 'dashed=1' in lower_style
        dash_pat = smap.get('dashpattern')
        edge_style = smap.get('edgestyle')
        stroke_col = smap.get('strokecolor')
        sw = _style_float(smap, 'strokewidth')

        kind = 'annotationLine' if is_annotation_line else _edge_kind(s_id, t_id, is_dashed)
        # Пунктир без единого наконечника, конец которого висел в воздухе и
        # был притянут к фигуре, — обрывок, а не переход: на «1.3» точечный
        # хвостик длиной 20 px от шага 214, притянутый к соседнему шагу, давал
        # переход «214 → 213» против хода процесса. Пунктир, который аналитик
        # прицепил к обеим фигурам, остаётся переходом: так на картах банка
        # нарисованы и настоящие — «… → Завершение» в «Reyting agentliklari».
        if (kind == 'sequenceFlow' and is_dashed and 'endarrow=none' in lower_style
                and ('startarrow=' not in lower_style or 'startarrow=none' in lower_style)
                and (cell.get('id') or '') in snapped_edge_ids):
            kind = 'annotationLine'
        # Вид связи из карточки платформы (``sqb_kind``) — выгрузка пишет его
        # там, где рисунок вид не восстанавливает: обрывок, признанный
        # оформлением, в выгруженном файле прицеплен к обеим фигурам.
        wanted_kind = platform.get(cell.get('id') or '', {}).get('kind')
        if wanted_kind in ('sequenceFlow', 'messageFlow', 'association', 'annotationLine'):
            kind = wanted_kind

        edges.append(ProcessEdge(
            id=edge_id,
            name=edge_name,
            kind=kind,
            sourceId=s_id,
            targetId=t_id,
            condition=edge_name or None,
            points=pts,
            sourcePoint=(
                ProcessEdgePoint(x=int(round(free_ends['sourcePoint'][0])), y=int(round(free_ends['sourcePoint'][1])))
                if 'sourcePoint' in free_ends else None
            ),
            targetPoint=(
                ProcessEdgePoint(x=int(round(free_ends['targetPoint'][0])), y=int(round(free_ends['targetPoint'][1])))
                if 'targetPoint' in free_ends else None
            ),
            exitX=_style_float(smap, 'exitx'),
            exitY=_style_float(smap, 'exity'),
            entryX=_style_float(smap, 'entryx'),
            entryY=_style_float(smap, 'entryy'),
            labelX=lx,
            labelY=ly,
            labelLines=edge_lines if '\n' in edge_lines else None,
            labelFontSize=edge_font if edge_name else None,
            # Подпись на отдельной ячейке-метке — её геометрия, иначе своя.
            drawioLabel=(edge_label_raw.get(edge_id) or _raw_label_geo(geo)) if edge_name else None,
            style=raw_style,
            dashed=is_dashed if is_dashed else None,
            dashPattern=dash_pat,
            edgeStyle=edge_style,
            strokeColor=stroke_col,
            strokeWidth=sw,
        ))

    # Привязываем висячие метки Yo'q/Ha/To'liq к ближайшему безымянному ребру (как в draw.io отдельные text)
    if orphan_condition_labels:
        node_by_id = {n.id: n for n in flow_nodes + lanes}
        # Короткие метки раскладываем первыми. Ветку помечают словом («Ha»,
        # «Yo'q»), а вопрос шлюза — целой фразой, и обе строки числятся
        # условиями. Пока порядок был документным, длинный вопрос успевал
        # занять ребро раньше своей же ветки, и вместо «Yo'q» на связи
        # оказывалось «Qaror qabul qilish Qo'mita vakolatidami?».
        for text, lx, ly in sorted(orphan_condition_labels, key=lambda item: len(item[0])):
            best = None
            best_dist = float('inf')
            for e in edges:
                if e.name:
                    continue
                s = node_by_id.get(e.sourceId or '')
                t = node_by_id.get(e.targetId or '')
                if not s or not t:
                    continue
                is_gw = s.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')
                route = (
                    [(s.geometry.x + s.geometry.width / 2, s.geometry.y + s.geometry.height / 2)]
                    + [(p.x, p.y) for p in e.points]
                    + [(t.geometry.x + t.geometry.width / 2, t.geometry.y + t.geometry.height / 2)]
                )
                d = _distance_to_polyline(lx, ly, route)
                penalty = 0 if is_gw else 35
                if d + penalty < best_dist and d < 140:
                    best_dist = d + penalty
                    best = e
            if best:
                best.name = text
                best.condition = text
                # Надпись стоит на карте сама по себе — её место и есть место
                # подписи; иначе подпись уходила в середину линии, на шаги.
                best.drawioLabelPoint = [lx, ly]

    # Шины слияния разбираются до уточнения типов: новые переходы меняют
    # степени фигур.
    rejoin_merge_buses(flow_nodes, edges)
    # Связи разобраны и притянуты к фигурам — только теперь у события известна
    # его настоящая степень, а значит и тип.
    reclassify_events(flow_nodes, edges)
    completed_branches = complete_binary_gateway_conditions(flow_nodes, edges)
    badge_step_ids = _apply_duration_badges(flow_nodes, duration_badges)
    _resolve_artifact_links(flow_nodes, edges)
    apply_platform_data(flow_nodes, lanes, edges, platform)
    # Геометрию правим до сбора замечаний: часть из них про размеры фигур.
    # Рамки и изломы draw.io запоминаем там, где правка их изменила: карта PIX
    # строится по ним (``drawioGeometry``), холст — по поправленным.
    drawn = {n.id: n.geometry.model_copy() for n in flow_nodes}
    drawn_points = {e.id: [p.model_copy() for p in e.points] for e in edges}
    layout_report = normalize_layout(flow_nodes, lanes, edges)
    for n in flow_nodes:
        if n.geometry != drawn[n.id]:
            n.drawioGeometry = drawn[n.id]
    for e in edges:
        if e.points != drawn_points.get(e.id, e.points):
            e.drawioPoints = drawn_points[e.id]
    timed_step_ids |= badge_step_ids

    title = filename.replace('.drawio', '').replace('.xml', '')
    task_nodes = [n for n in flow_nodes if n.type in TASK_NODE_TYPES]
    total_hours = round(
        sum((n.slaMinutes or 0) + (n.waitMinutes or 0) for n in task_nodes) / 60, 1
    ) or 8.0

    process_id = process_id_for(filename, content)
    passport = ProcessPassport(
        code=passport_code_for(process_id),
        name=title,
        version='1.0',
        status='draft',
        # Владельца и подразделение на рисунке не найти — их заполняют в
        # паспорте карты. Раньше здесь стояли заглушка и первая дорожка.
        owner='',
        department='',
        category='Банковские процессы (Методика SQB)',
        targetSlaHours=total_hours,
        description='',  # описание процесса заполняют в паспорте; откуда карта — видно в истории версий
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Операционный реестр по процессу {title} (PIX BPM)",
        fields=[
            ProcessField(id='f1', code='case_number', name='Номер заявки', type='string', required=True),
            ProcessField(id='f2', code='client_inn', name='ИНН Клиента', type='string', required=True),
            ProcessField(id='f3', code='client_title', name='Наименование компании', type='string', required=True),
            ProcessField(id='f4', code='status', name='Статус', type='select', required=True, options=['В работе', 'Одобрено', 'Отклонено'])
        ],
        records=[
            PixRegistryRecord(
                id='rec-1',
                caseId='SQB-2026-IMP01',
                createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
                status='in_progress',
                currentStepId=flow_nodes[1].id if len(flow_nodes) > 1 else (flow_nodes[0].id if flow_nodes else 'step-1'),
                currentStepName=flow_nodes[1].name if len(flow_nodes) > 1 else 'Первичный шаг',
                assignedTo=(flow_nodes[1].role or 'Сотрудник банка') if len(flow_nodes) > 1 else 'Сотрудник банка',
                elapsedMinutes=25,
                data={
                    'case_number': 'SQB-2026-IMP01',
                    'client_inn': '309819284',
                    'client_title': 'OOO "ORIENT TRADE"',
                    'status': 'В работе'
                }
            )
        ]
    )

    # Идентификаторы связей живут в том же пространстве, что и фигуры: в .pmm
    # `id` связи и `id` фигуры лежат в одной карте, а в BPMN оба попадают в
    # xsd:ID. Совпадение здесь так же ломает импорт, как два одинаковых шага.
    taken_ids: Set[str] = {n.id for n in nodes}
    for e in edges:
        if e.id not in taken_ids:
            taken_ids.add(e.id)
            continue
        suffix = 2
        while f'{e.id}__dup{suffix}' in taken_ids:
            suffix += 1
        e.id = f'{e.id}__dup{suffix}'
        taken_ids.add(e.id)

    validations = collect_import_diagnostics(
        flow_nodes, lanes, edges,
        snapped_edges=snapped_edge_ids,
        layout_report=layout_report,
        timed_step_ids=timed_step_ids,
        unsupported_shapes=unsupported,
        skipped_clipart=skipped_clipart,
        completed_branches=completed_branches,
        captions=[n for n in flow_nodes if n.id in set(captions)],
        tables=list(tables.values()),
    )

    metrics = analyze_process_conformance(flow_nodes, passport, len(registry.records))

    process = BusinessProcess(
        id=process_id,
        name=title,
        fileName=filename,
        passport=passport,
        nodes=flow_nodes,
        edges=edges,
        lanes=lanes,
        validation=validations,
        registry=registry,
        miningMetrics=metrics
    )
    attach_sub_pages(process, content, filename, keep_blank_shapes)
    return process
