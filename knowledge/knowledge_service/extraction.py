"""Bounded local extraction, with anchors into immutable originals.

No semantic or regulatory completeness is inferred from readable text.
DOCX has no stable page numbers; PDF text order and tables need human review.
"""
import hashlib
import io
import json
import re
import zipfile
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from .files import validate_original
from .office_formats import DOC, XLSX
from .legacy_word import extract_doc
from .spreadsheet import extract_xlsx

PARSER_VERSION = 'source-v1'
MAX_PAGES = 300
MAX_BLOCKS = 5000
MAX_CHARACTERS = 2_000_000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class ExtractionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _xml(data):
    check = data.replace(b'\0', b'').upper()
    if b'<!DOCTYPE' in check or b'<!ENTITY' in check:
        raise ExtractionError('unsafe_xml', 'Документ содержит неподдерживаемую структуру XML')
    root = ET.fromstring(data)
    strict_namespace = '{http://purl.oclc.org/ooxml/wordprocessingml/main}'
    # Normalize names only in memory, preserving original bytes and locators.
    for node in root.iter():
        if node.tag.startswith(strict_namespace):
            node.tag = W + node.tag[len(strict_namespace):]
        node.attrib = {(W + key[len(strict_namespace):] if key.startswith(strict_namespace) else key): value
                       for key, value in node.attrib.items()}
    return root


def _clean(text):
    # Preserve rows/line breaks; discard invisible PDF/Office control bytes.
    return '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip() for line in
                     re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text).splitlines()).strip()


class _Result:
    def __init__(self, content):
        self.content_hash = hashlib.sha256(content).hexdigest()
        self.blocks, self.warnings = [], []
        self.characters = 0

    def add(self, text, locator, label, kind='paragraph', runs=None):
        # Function navigation can retain literal Word runs, including revisions.
        # The existing preparation ledger keeps its original parser semantics.
        text = _clean(text) if runs is None else text
        if not text:
            return False
        if len(self.blocks) >= MAX_BLOCKS or self.characters + len(text) > MAX_CHARACTERS:
            raise ExtractionError('limit_exceeded', 'Документ слишком большой для подготовки. Разделите его на отдельные документы')
        anchor = json.dumps(locator, sort_keys=True, ensure_ascii=False)
        block_id = hashlib.sha256(f'{PARSER_VERSION}:{self.content_hash}:{anchor}'.encode()).hexdigest()[:24]
        self.blocks.append({'id': block_id, 'order': len(self.blocks), 'kind': kind,
                            'text': text, 'locator': locator, 'label': label})
        if runs is not None:
            self.blocks[-1]['runs'] = runs
        self.characters += len(text)
        return True

    def warn(self, code, message, locator=None):
        # Keep diagnostic data bounded; many identical page warnings aggregate.
        if len(self.warnings) < 350:
            self.warnings.append({'code': code, 'message': message, **({'locator': locator} if locator else {})})

    def finish(self, status, total, with_text):
        return {'parser_version': PARSER_VERSION, 'status': status, 'blocks': self.blocks,
                'warnings': self.warnings, 'stats': {'units_total': total, 'units_with_text': with_text,
                'blocks_total': len(self.blocks), 'characters': self.characters}}


def _pdf(content, result):
    reader = PdfReader(io.BytesIO(content))
    try:
        if len(reader.pages) > MAX_PAGES:
            raise ExtractionError('limit_exceeded', 'PDF превышает лимит 300 страниц. Разделите его на отдельные документы')
        with_text, incomplete = 0, False
        result.warn('pdf_layout', 'Проверьте порядок текста, таблицы и схемы по оригиналу: PDF не сохраняет структуру пунктов.')
        for page_number, page in enumerate(reader.pages, 1):
            locator = {'format': 'pdf', 'page': page_number}
            try:
                text = _clean(page.extract_text() or '')
            except Exception:
                text = ''
                result.warn('unreadable_page', 'Не удалось прочитать текст страницы.', locator)
            if text:
                with_text += 1
                # PDF lines are physical source units, not invented clauses.
                for block_number, paragraph in enumerate(re.split(r'\n\s*\n', text), 1):
                    result.add(paragraph, {**locator, 'block': block_number}, f'Страница {page_number} · блок {block_number}')
            else:
                incomplete = True
                result.warn('no_text_layer', 'На странице нет читаемого текстового слоя. Проверьте оригинал; для скана потребуется OCR.', locator)
            resources = page.get('/Resources')
            resources = resources.get_object() if resources else {}
            xobjects = resources.get('/XObject', {})
            xobjects = xobjects.get_object() if hasattr(xobjects, 'get_object') else xobjects
            if any(obj.get_object().get('/Subtype') in ('/Image', '/Form') for obj in xobjects.values()):
                incomplete = True
                result.warn('pdf_graphics', 'Страница содержит изображение или графический блок. Его содержимое нужно проверить по оригиналу.', locator)
            if page.get('/Annots'):
                incomplete = True
                result.warn('pdf_annotations', 'Поля и аннотации PDF требуют проверки по оригиналу.', locator)
        return result.finish('needs_ocr' if not with_text else 'partial' if incomplete else 'ready', len(reader.pages), with_text)
    finally:
        reader.close()


def _paragraph_text(element):
    parts = []
    def walk(node):
        if node.tag in (W + 'del', W + 'moveFrom'):
            return
        if node.tag == W + 't':
            parts.append(node.text or '')
        elif node.tag == W + 'tab':
            parts.append('\t')
        elif node.tag in (W + 'br', W + 'cr'):
            parts.append('\n')
        for child in node:
            walk(child)
    walk(element)
    return _clean(''.join(parts))


def _paragraph_runs(element):
    """Literal source text with edits visible; never accept or reject revisions."""
    runs = []
    def walk(node, properties):
        properties = dict(properties)
        if node.tag in (W + 'del', W + 'moveFrom'):
            properties['change'] = 'deleted'
        elif node.tag in (W + 'ins', W + 'moveTo'):
            properties['change'] = 'inserted'
        if node.tag == W + 'r':
            props = node.find(W + 'rPr')
            if props is not None:
                for tag, key in [('b', 'bold'), ('i', 'italic'), ('strike', 'strike'), ('dstrike', 'strike')]:
                    value = props.find(W + tag)
                    if value is not None and value.get(W + 'val', '1') not in ('0', 'false', 'off'):
                        properties[key] = True
        value = None
        if node.tag in (W + 't', W + 'delText'):
            value = node.text or ''
        elif node.tag == W + 'tab':
            value = '\t'
        elif node.tag in (W + 'br', W + 'cr'):
            value = '\n'
        if value:
            if runs and {k: v for k, v in runs[-1].items() if k != 'text'} == properties:
                runs[-1]['text'] += value
            else:
                runs.append({'text': value, **properties})
        for child in node:
            if child.tag not in (W + 'pPr', W + 'rPr'):
                walk(child, properties)
    walk(element, {})
    return runs


def _roman(number):
    if not 0 < number < 4000:
        return str(number)
    out = ''
    for value, symbol in ((1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')):
        while number >= value:
            out += symbol
            number -= value
    return out


class _Numbering:
    def __init__(self, archive, result):
        self.definitions, self.counters = {}, {}
        self.result = result
        if 'word/numbering.xml' not in archive.namelist():
            return
        root = _xml(archive.read('word/numbering.xml'))
        abstracts = {n.get(W + 'abstractNumId'): n for n in root.findall(W + 'abstractNum')}
        for num in root.findall(W + 'num'):
            ref = num.find(W + 'abstractNumId')
            abstract = abstracts.get(ref.get(W + 'val')) if ref is not None else None
            levels = {}
            if abstract is not None:
                levels = {n.get(W + 'ilvl'): n for n in abstract.findall(W + 'lvl')}
            # Overrides and style-linked lists are flagged, never silently guessed.
            self.definitions[num.get(W + 'numId')] = (levels, bool(num.findall(W + 'lvlOverride')),
                abstract is not None and (abstract.find(W + 'numStyleLink') is not None or abstract.find(W + 'styleLink') is not None))

    def prefix(self, paragraph, locator):
        props = paragraph.find(W + 'pPr')
        numbering = props.find(W + 'numPr') if props is not None else None
        if numbering is None:
            return ''
        num, level = numbering.find(W + 'numId'), numbering.find(W + 'ilvl')
        if num is None or num.get(W + 'val') == '0':
            return ''
        num_id, ilvl = num.get(W + 'val'), level.get(W + 'val', '0') if level is not None else '0'
        definition, override, linked = self.definitions.get(num_id, ({}, False, False))
        spec = definition.get(ilvl)
        if (spec is None or override or linked or not ilvl.isdigit() or int(ilvl) > 8
                or (spec is not None and (spec.find(W + 'lvlRestart') is not None or spec.find(W + 'isLgl') is not None))):
            self.result.warn('unresolved_numbering', 'Нумерацию этого пункта нужно проверить по оригиналу.', locator)
            return ''
        current = int(ilvl)
        start = spec.find(W + 'start')
        initial = start.get(W + 'val', '1') if start is not None else '1'
        if not initial.isdigit():
            initial = '1'
        key = (num_id, current)
        self.counters[key] = self.counters.get(key, int(initial) - 1) + 1
        for deeper in range(current + 1, 9):
            self.counters.pop((num_id, deeper), None)
        template = spec.find(W + 'lvlText')
        pattern = template.get(W + 'val', '') if template is not None else ''
        def replace(match):
            index = int(match.group(1)) - 1
            nested = definition.get(str(index))
            fmt = nested.find(W + 'numFmt') if nested is not None else None
            fmt = fmt.get(W + 'val') if fmt is not None else 'decimal'
            value = self.counters.get((num_id, index))
            if value is None or fmt not in ('decimal', 'upperRoman', 'lowerRoman', 'upperLetter', 'lowerLetter'):
                raise ValueError()
            if fmt in ('upperRoman','lowerRoman'):
                rendered = _roman(value)
            elif fmt in ('upperLetter','lowerLetter'):
                if not 1 <= value <= 26:
                    raise ValueError()
                rendered = chr(64 + value)
            else:
                rendered = str(value)
            return rendered.lower() if fmt in ('lowerRoman','lowerLetter') else rendered
        try:
            return re.sub(r'%([1-9])', replace, pattern) + ' ' if pattern else ''
        except ValueError:
            self.result.warn('unresolved_numbering', 'Нумерацию этого пункта нужно проверить по оригиналу.', locator)
            return ''


def _docx(content, result, preserve_revisions=False):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = _xml(archive.read('word/document.xml'))
        styles = _xml(archive.read('word/styles.xml')) if 'word/styles.xml' in archive.namelist() else None
        body = root.find(W + 'body')
        if body is None:
            raise ExtractionError('unsupported_structure', 'Не найдена структура DOCX. Сохраните документ заново в формате DOCX')
        numbering = _Numbering(archive, result)
        omitted_parts = [n for n in archive.namelist() if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n)]
        if omitted_parts:
            result.warn('additional_parts', 'Колонтитулы, примечания и сноски требуют проверки по оригиналу; они не включены в основной текст.')
        for tag, code, message in (
            ('ins','tracked_changes','В документе есть исправления. Подтвердите редакцию по оригиналу.'),
            ('del','tracked_changes','В документе есть удалённый текст исправлений. Подтвердите редакцию по оригиналу.'),
            ('moveFrom','tracked_changes','В документе есть перемещённый текст исправлений.'),
            ('txbxContent','text_boxes','Текстовые блоки требуют проверки порядка по оригиналу.'),
            ('drawing','graphics','Изображения и диаграммы требуют проверки по оригиналу.'),
            ('pict','graphics','Изображения требуют проверки по оригиналу.'),
            ('fldChar','fields','Автоматические поля требуют проверки по оригиналу.'),
            ('altChunk','embedded_content','Встроенные материалы не включены в извлечённый текст.'),
        ):
            if root.find('.//' + W + tag) is not None:
                result.warn(code, message)
        total, with_text = 0, 0
        for body_index, element in enumerate(body, 1):
            locator = {'format': 'docx', 'part': 'word/document.xml', 'body_index': body_index}
            if element.tag == W + 'p':
                total += 1
                props = element.find(W + 'pPr')
                style = props.find(W + 'pStyle') if props is not None else None
                style_name = style.get(W + 'val', '') if style is not None else ''
                runs = _paragraph_runs(element) if preserve_revisions else None
                text = ''.join(run['text'] for run in runs) if runs is not None else _paragraph_text(element)
                if text:
                    prefix = numbering.prefix(element, locator)
                    text = prefix + text
                    if runs is not None and prefix:
                        runs.insert(0, {'text': prefix})
                kind = 'heading' if re.match(r'(?i)(heading|заголовок|title)', style_name) else 'paragraph'
                if result.add(text, {**locator, 'paragraph': body_index}, f'Абзац {body_index}', kind, runs):
                    with_text += 1
                if style_name and props is not None and props.find(W + 'numPr') is None:
                    # Style definitions can carry numbering; do not invent it.
                    if 'word/styles.xml' in archive.namelist():
                        style_def = next((s for s in styles.findall(W + 'style') if s.get(W + 'styleId') == style_name), None)
                        if style_def is not None and style_def.find('.//' + W + 'numPr') is not None:
                            result.warn('style_numbering', 'Нумерация из стиля требует проверки по оригиналу.', locator)
            elif element.tag == W + 'tbl':
                if element.find('.//' + W + 'tbl') is not None:
                    result.warn('nested_table', 'Вложенная таблица прочитана как текст; проверьте её структуру по оригиналу.', locator)
                for row_index, row in enumerate(element.findall(W + 'tr'), 1):
                    total += 1
                    texts, row_runs = [], []
                    cells = row.findall(W + 'tc')
                    for cell_index, cell in enumerate(cells, 1):
                        paragraphs = cell.findall('.//' + W + 'p')
                        cell_texts = []
                        if cell_index > 1:
                            row_runs.append({'text': ' | '})
                        for i, p in enumerate(paragraphs, 1):
                            prefix = numbering.prefix(p, {**locator, 'table': body_index, 'row': row_index,
                                'cell': cell_index, 'paragraph': i})
                            runs = _paragraph_runs(p) if preserve_revisions else None
                            cell_texts.append(prefix + (''.join(run['text'] for run in runs) if runs is not None else _paragraph_text(p)))
                            if i > 1:
                                row_runs.append({'text': '\n'})
                            if prefix:
                                row_runs.append({'text': prefix})
                            row_runs.extend(runs or [])
                        texts.append('\n'.join(cell_texts))
                    if any(text.strip() for text in texts) and result.add(' | '.join(texts), {**locator, 'table': body_index, 'row': row_index, 'cells': len(cells)},
                                  f'Таблица · блок {body_index} · строка {row_index}', 'table_row', row_runs if preserve_revisions else None):
                        with_text += 1
            elif element.tag != W + 'sectPr':
                total += 1
                result.warn('unsupported_body', 'Часть структуры DOCX требует проверки по оригиналу.', locator)
        if not result.blocks:
            result.warn('no_readable_text', 'Основной текст не найден. Проверьте оригинал; изображения потребуют OCR.')
        status = 'needs_ocr' if not result.blocks else 'partial' if result.warnings else 'ready'
        return result.finish(status, total, with_text)


def extract_document(content: bytes, media_type: str):
    result = _Result(content)
    try:
        # Файл с расширением .doc внутри бывает DOCX или PDF (переименован или пересохранён).
        if media_type == DOC and content.startswith(b'PK'):
            media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif media_type == DOC and content.startswith(b'%PDF-'):
            media_type = 'application/pdf'
        formats = {'application/pdf': 'source.pdf',
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'source.docx',
                   DOC: 'source.doc', XLSX: 'source.xlsx'}
        filename = formats.get(media_type)
        if filename is None:
            raise ExtractionError('unsupported_format', 'Для подготовки нужен PDF, DOCX, DOC или XLSX')
        validate_original(filename, content)
        if media_type == DOC:
            return extract_doc(content, result, ExtractionError)
        if media_type == XLSX:
            return extract_xlsx(content, result, ExtractionError)
        return _pdf(content, result) if media_type == 'application/pdf' else _docx(content, result)
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError('invalid_document', 'Не удалось прочитать документ. Проверьте оригинал и сохраните его заново') from None
