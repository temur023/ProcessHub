"""Bounded SpreadsheetML extraction; formulas are source text, never executed."""
import re

from .office_formats import S, checked_zip, office_xml, workbook_parts

MAX_CELLS = 50_000
MAX_ROWS = 20_000
MAX_SHARED_STRINGS = 100_000
MAX_FORMULA_CHARACTERS = 8192
CELL_REFERENCE = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})\Z')


def _reference(value, error_type):
    match = CELL_REFERENCE.fullmatch(value)
    if not match:
        raise error_type('invalid_spreadsheet', 'В XLSX найден некорректный адрес ячейки. Проверьте оригинал')
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - ord('A') + 1
    row = int(match[2])
    if column > 16384 or row > 1048576:
        raise error_type('invalid_spreadsheet', 'Адрес ячейки XLSX выходит за допустимые границы')
    return column, row


def _range(value, error_type):
    # Inspect addresses without expanding a range or worksheet dimension.
    parts = value.split(':')
    if not 1 <= len(parts) <= 2:
        raise error_type('invalid_spreadsheet', 'В XLSX найден некорректный диапазон')
    start = _reference(parts[0], error_type)
    end = _reference(parts[-1], error_type)
    if start[0] > end[0] or start[1] > end[1]:
        raise error_type('invalid_spreadsheet', 'В XLSX найден некорректный диапазон')


def _rich_text(element):
    if element is None:
        return ''
    # Phonetic guides are not part of the visible text.
    return ''.join(node.text or '' for node in element.findall(S + 't')) + ''.join(
        node.text or '' for run in element.findall(S + 'r') for node in run.findall(S + 't'))


def _styles(archive):
    if 'xl/styles.xml' not in archive.namelist():
        return [], {}
    root = office_xml(archive.read('xl/styles.xml'))
    if root.tag != S + 'styleSheet':
        raise ValueError('invalid_styles')
    formats = root.find(S + 'numFmts')
    custom = {node.get('numFmtId'): node.get('formatCode', '')
              for node in (formats if formats is not None else ())}
    styles = root.find(S + 'cellXfs')
    return [node.get('numFmtId', '0') for node in styles] if styles is not None else [], custom


def extract_xlsx(content, result, error_type):
    with checked_zip(content) as archive:
        workbook, sheets = workbook_parts(archive)
        names = archive.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = office_xml(archive.read('xl/sharedStrings.xml'))
            if root.tag != S + 'sst':
                raise ValueError('invalid_shared_strings')
            for item in root.findall(S + 'si'):
                if len(shared) >= MAX_SHARED_STRINGS:
                    raise error_type('limit_exceeded', 'XLSX превышает лимит строковых значений')
                shared.append(_rich_text(item))
        styles, custom_formats = _styles(archive)
        workbook_props = workbook.find(S + 'workbookPr')
        date_system = '1904' if workbook_props is not None and workbook_props.get('date1904') in ('1', 'true') else '1900'
        result.warn('xlsx_display', 'Текст XLSX сохраняет адреса и исходные значения ячеек. Числа, даты, проценты и периоды не пересчитываются и не выводятся из формата; оформление проверьте по оригиналу.')
        if any(name.startswith('xl/externalLinks/') for name in names) or workbook.find(S + 'externalReferences') is not None:
            result.warn('external_references', 'Книга содержит ссылки на внешние источники. Они не открывались и не обновлялись; сохранённые значения могут быть устаревшими.')
        if any(name.startswith(('xl/drawings/', 'xl/charts/', 'xl/embeddings/', 'xl/pivotTables/', 'xl/comments')) for name in names):
            result.warn('spreadsheet_additional_parts', 'Диаграммы, изображения, встроенные материалы, сводные таблицы и комментарии требуют проверки по оригиналу; подготовлены только ячейки листов.')
        # External hyperlinks/connections remain opaque, never fetched. Their
        # targets are deliberately not copied into warnings or logs.
        if 'xl/connections.xml' in names:
            result.warn('external_references', 'В книге есть подключения к данным. Подключения не выполнялись и данные не обновлялись.')
        for name in names:
            if name.endswith('.rels'):
                relations = office_xml(archive.read(name))
                if any(rel.get('TargetMode') == 'External' for rel in relations):
                    result.warn('external_relationships', 'Внешние ссылки книги не открывались. Проверьте связанные сведения по оригиналу.')
                    break
        total, with_text, cells_total = 0, 0, 0
        warned_formulas, warned_shared = False, False
        for sheet in sheets:
            locator = {'format': 'xlsx', 'sheet': sheet['name'], 'sheet_id': sheet['id'], 'part': sheet['part']}
            if sheet['state'] != 'visible':
                result.warn('hidden_sheet', 'Скрытый лист включён в подготовленный текст. Подтвердите его применимость по оригиналу.', locator)
            if sheet['kind'] != 'worksheet':
                result.warn('chartsheet', 'Лист диаграммы не содержит читаемых ячеек и требует проверки по оригиналу.', locator)
                continue
            root = office_xml(archive.read(sheet['part']))
            dimension = root.find(S + 'dimension')
            if dimension is not None and dimension.get('ref'):
                _range(dimension.get('ref'), error_type)
            merged = root.find(S + 'mergeCells')
            if merged is not None and len(merged):
                for merge in merged:
                    _range(merge.get('ref', ''), error_type)
                result.warn('merged_cells', 'В листе есть объединённые ячейки. Сохранены их исходные адреса; структуру таблицы проверьте по оригиналу.', locator)
            cols = root.find(S + 'cols')
            if cols is not None and any(col.get('hidden') in ('1', 'true') for col in cols):
                result.warn('hidden_columns', 'Скрытые столбцы включены в подготовленный текст.', locator)
            if any(root.find(S + tag) is not None for tag in ('conditionalFormatting', 'dataValidations', 'extLst')):
                result.warn('spreadsheet_layout', 'Условия оформления, проверки данных и расширения листа не интерпретировались; проверьте их по оригиналу.', locator)
            sheet_data = root.find(S + 'sheetData')
            if sheet_data is None:
                raise error_type('invalid_spreadsheet', 'В XLSX не найдена структура ячеек листа')
            previous_row = 0
            for row in sheet_data:
                total += 1
                if total > MAX_ROWS:
                    raise error_type('limit_exceeded', 'XLSX превышает лимит 20 000 строк. Разделите книгу')
                row_value = row.get('r', '')
                if row.tag != S + 'row' or not row_value.isdecimal() or not previous_row < int(row_value) <= 1048576:
                    raise error_type('invalid_spreadsheet', 'В XLSX найден некорректный порядок строк')
                row_number = previous_row = int(row_value)
                row_locator = {**locator, 'row': row_number, 'date_system': date_system}
                if row.get('hidden') in ('1', 'true'):
                    result.warn('hidden_row', 'Скрытая строка включена в подготовленный текст.', row_locator)
                rendered, metadata, previous_column = [], [], 0
                for cell in row:
                    cells_total += 1
                    if cells_total > MAX_CELLS:
                        raise error_type('limit_exceeded', 'XLSX превышает лимит 50 000 ячеек. Разделите книгу')
                    if cell.tag != S + 'c':
                        raise error_type('invalid_spreadsheet', 'В XLSX найдена неподдерживаемая структура строки')
                    ref = cell.get('r', '')
                    column, cell_row = _reference(ref, error_type)
                    if cell_row != row_number or column <= previous_column:
                        raise error_type('invalid_spreadsheet', 'В XLSX найдены повторные или неверно расположенные ячейки')
                    previous_column = column
                    kind = cell.get('t', 'n')
                    value_element, formula = cell.find(S + 'v'), cell.find(S + 'f')
                    raw_value = value_element.text if value_element is not None else None
                    value = raw_value or ''
                    if kind == 's':
                        if raw_value is None or not raw_value.isdecimal() or int(raw_value) >= len(shared):
                            raise error_type('invalid_spreadsheet', 'В XLSX повреждена ссылка на строковое значение')
                        value = shared[int(raw_value)]
                    elif kind == 'inlineStr':
                        value = _rich_text(cell.find(S + 'is'))
                    elif kind not in ('n', 'b', 'e', 'str', 'd'):
                        raise error_type('invalid_spreadsheet', 'В XLSX найден неподдерживаемый тип ячейки')
                    details = {'ref': ref, 'type': kind, 'value': value}
                    if cell.get('s') is not None:
                        style_id = cell.get('s')
                        if not style_id.isdecimal() or int(style_id) >= len(styles):
                            raise error_type('invalid_spreadsheet', 'В XLSX повреждена ссылка на формат ячейки')
                        num_fmt_id = styles[int(style_id)]
                        details.update({'style_id': int(style_id), 'number_format_id': num_fmt_id})
                        if num_fmt_id in custom_formats:
                            details['number_format'] = custom_formats[num_fmt_id]
                    if formula is not None:
                        formula_text = formula.text or ''
                        if len(formula_text) > MAX_FORMULA_CHARACTERS:
                            raise error_type('limit_exceeded', 'Формула XLSX превышает допустимый размер')
                        if formula.get('ref'):
                            _range(formula.get('ref'), error_type)
                        formula_kind = formula.get('t', 'normal')
                        details['formula'] = {'text': formula_text, 'type': formula_kind,
                            **{key: formula.get(key) for key in ('si', 'ref') if formula.get(key) is not None}}
                        details['cached_value'] = value if value_element is not None else None
                        if not warned_formulas:
                            result.warn('formula_cached_values', 'Формулы сохранены как текст и не выполнялись. Значения рядом — кеш из оригинала; он может отсутствовать или быть устаревшим.')
                            warned_formulas = True
                        if formula_kind != 'normal' and not warned_shared:
                            result.warn('shared_or_array_formula', 'Общие формулы, формулы массивов и таблицы данных не разворачивались. Сохранены исходные атрибуты и кеш; проверьте формулы по оригиналу.')
                            warned_shared = True
                        display = '=' + formula_text if formula_text else f'формула {formula_kind}'
                        display += f' [сохранённое значение: {value}]' if value_element is not None else ' [сохранённое значение отсутствует]'
                    else:
                        display = value
                    if display:
                        rendered.append(f'{ref}: {display}')
                        metadata.append(details)
                if result.add(' | '.join(rendered), {**row_locator, 'cells': metadata},
                              f'Лист «{sheet["name"]}» · строка {row_number}', 'table_row'):
                    with_text += 1
        if not result.blocks:
            result.warn('no_readable_cells', 'В XLSX не найден читаемый текст ячеек. Проверьте пустые листы, изображения и диаграммы по оригиналу; OCR не выполнялся.')
        # Even a text-only workbook omits display semantics. No calculation,
        # date/period inference or coverage of charts is claimed by `ready`.
        return result.finish('partial', total, with_text)
