"""Synthetic Office sources only; no uploaded bank document fixtures."""
import hashlib
import io
import struct
import zipfile
from xml.sax.saxutils import escape

from fastapi import HTTPException
import pytest

from knowledge_service.extraction import ExtractionError, extract_document
from knowledge_service.files import validate_original
from knowledge_service.office_formats import DOC, XLSX, S


def workbook(rows, *, extra=None, sheet_name='Тестовый лист', dimension='A1:C2',
             workbook_extra='', sheet_attributes='', worksheet_extra='', content_type=None, namespace=None):
    namespace = namespace or S[1:-1]
    out = io.BytesIO()
    content_type = content_type or 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'
    parts = {
        '[Content_Types].xml': f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="{content_type}"/></Types>',
        'xl/workbook.xml': f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1" {sheet_attributes}/></sheets>{workbook_extra}</workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{namespace}"><dimension ref="{dimension}"/><sheetData>{rows}</sheetData>{worksheet_extra}</worksheet>',
    }
    parts.update(extra or {})
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()


def inline(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def legacy_doc(*, flags=0, macro=False, word_magic=0xA5EC):
    """A minimal synthetic CFB with real Word FIB/table streams, never text in .doc."""
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex('D0CF11E0A1B11AE1')
    struct.pack_into('<HHHH', header, 24, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into('<H', header, 32, 6)
    stream_names = ['WordDocument', '1Table' if flags & 0x200 else '0Table'] + (['VBA'] if macro else [])
    fat_index = 1 + len(stream_names) * 8
    struct.pack_into('<IIIIIIIII', header, 40, 0, 1, 0, 0, 4096, end, 0, end, 0)
    struct.pack_into('<109I', header, 76, fat_index, *([free] * 108))
    directory = bytearray(512)
    def entry(index, name, kind, start=end, size=0, child=free, left=free, right=free):
        offset = index * 128
        encoded = (name + '\0').encode('utf-16le')
        directory[offset:offset + len(encoded)] = encoded
        struct.pack_into('<HBBIII', directory, offset + 64, len(encoded), kind, 1, left, right, child)
        struct.pack_into('<IQ', directory, offset + 116, start, size)
    entry(0, 'Root Entry', 5, child=1)
    streams = []
    for index, name in enumerate(stream_names, 1):
        entry(index, name, 2, start=1 + (index - 1) * 8, size=4096,
              left=2 if index == 1 else free, right=3 if index == 1 and macro else free)
        data = bytearray(4096)
        if name == 'WordDocument':
            struct.pack_into('<HH', data, 0, word_magic, 0xC1)
            struct.pack_into('<H', data, 10, flags)
        streams.append(data)
    fat = [end]
    for index in range(len(streams)):
        start = 1 + index * 8
        fat.extend([*range(start + 1, start + 8), end])
    fat.append(fat_sector)
    fat.extend([free] * (128 - len(fat)))
    return bytes(header + directory + b''.join(streams) + struct.pack('<128I', *fat))


def test_original_acceptance_checks_actual_word_container_and_macro_encryption():
    data = legacy_doc()
    original_hash = hashlib.sha256(data).hexdigest()
    assert validate_original('../synthetic.doc', data) == ('synthetic.doc', DOC)
    assert hashlib.sha256(data).hexdigest() == original_hash
    for bad in (b'text disguised as Word', legacy_doc(word_magic=0), legacy_doc(flags=0x100),
                legacy_doc(flags=0x8000), legacy_doc(macro=True)):
        with pytest.raises(HTTPException) as error:
            validate_original('unsafe.doc', bad)
        assert error.value.status_code == 422


def test_doc_derivative_anchors_are_stable_and_never_claim_original_pages(monkeypatch):
    import knowledge_service.legacy_word as module
    monkeypatch.setattr(module, 'convert_doc', lambda content, error: '1. Ўзбекча вазифа\nРусский текст\n\n2. Проверка\n')
    data = legacy_doc()
    result = extract_document(data, DOC)
    assert result['status'] == 'partial'
    assert [block['text'] for block in result['blocks']] == ['1. Ўзбекча вазифа\nРусский текст', '2. Проверка']
    assert result['blocks'][0]['locator']['line_start'] == 1
    assert result['blocks'][0]['locator']['line_end'] == 2
    assert result['blocks'][0]['locator']['derivative'] == 'antiword-text-v1'
    assert all('page' not in block['locator'] for block in result['blocks'])
    assert result == extract_document(data, DOC)


def test_doc_converter_is_explicit_when_unavailable(monkeypatch, tmp_path):
    import knowledge_service.legacy_word as module
    monkeypatch.setattr(module, 'ANTIWORD', tmp_path / 'not-installed')
    with pytest.raises(ExtractionError) as error:
        extract_document(legacy_doc(), DOC)
    assert error.value.code == 'converter_unavailable'


def test_converter_uses_fixed_command_clean_environment_timeout_and_bounded_output(monkeypatch, tmp_path):
    import knowledge_service.legacy_word as module
    converter = tmp_path / 'antiword'
    converter.write_text('synthetic executable marker')
    monkeypatch.setattr(module, 'ANTIWORD', converter)
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'never-in-child')
    seen = []
    def run(arguments, **options):
        seen.append((arguments, options))
        options['stdout'].write('Текст Ўзбекистон'.encode())
        return type('Completed', (), {'returncode': 0})()
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.convert_doc(legacy_doc(), ExtractionError) == 'Текст Ўзбекистон'
    arguments, options = seen[0]
    assert arguments[:5] == [str(converter), '-m', 'UTF-8.txt', '-w', '0']
    assert options['shell'] is False and options['timeout'] == 20
    assert 'AWS_SECRET_ACCESS_KEY' not in options['env']
    assert 'HOME' not in options['env']
    assert options['preexec_fn'] is module._converter_limits
    monkeypatch.setattr(module, 'MAX_CONVERTED_BYTES', 2)
    with pytest.raises(ExtractionError) as error:
        module.convert_doc(legacy_doc(), ExtractionError)
    assert error.value.code == 'limit_exceeded'


def test_xlsx_preserves_sheet_row_cell_formulas_caches_and_raw_date_values():
    rows = '<row r="1">' + inline('A1', 'Условие — ўзбекча') + '<c r="B1"><f>SUM(B2:C2)</f><v>7</v></c><c r="C1" s="0"><v>46200</v></c></row><row r="2"><c r="B2"><v>3</v></c><c r="C2"><v>4</v></c></row>'
    data = workbook(rows, extra={'xl/styles.xml': f'<styleSheet xmlns="{S[1:-1]}"><cellXfs><xf numFmtId="14"/></cellXfs></styleSheet>'})
    before = hashlib.sha256(data).hexdigest()
    assert validate_original('Source.XLSX', data) == ('Source.XLSX', XLSX)
    result = extract_document(data, XLSX)
    assert result['status'] == 'partial'
    assert result['stats']['units_total'] == 2 and result['stats']['units_with_text'] == 2
    assert result['blocks'][0]['kind'] == 'table_row'
    locator = result['blocks'][0]['locator']
    assert locator['sheet'] == 'Тестовый лист' and locator['row'] == 1
    assert locator['part'] == 'xl/worksheets/sheet1.xml'
    assert 'page' not in locator
    assert locator['cells'][1]['formula'] == {'text': 'SUM(B2:C2)', 'type': 'normal'}
    assert locator['cells'][1]['cached_value'] == '7'
    assert locator['cells'][2]['value'] == '46200' and locator['cells'][2]['number_format_id'] == '14'
    assert '46200' in result['blocks'][0]['text'] and '2026' not in result['blocks'][0]['text']
    assert result == extract_document(data, XLSX)
    assert hashlib.sha256(data).hexdigest() == before


def test_shared_strings_rich_text_and_strict_spreadsheetml():
    shared = f'<sst xmlns="{S[1:-1]}"><si><r><t>Русский</t></r><r><t> текст</t></r></si></sst>'
    data = workbook('<row r="1"><c r="A1" t="s"><v>0</v></c></row>',
                    extra={'xl/sharedStrings.xml': shared}, namespace='http://purl.oclc.org/ooxml/spreadsheetml/main')
    assert extract_document(data, XLSX)['blocks'][0]['text'] == 'A1: Русский текст'


def test_external_references_and_formulas_are_opaque_and_missing_cache_explicit():
    data = workbook('<row r="1"><c r="A1"><f>[1]Sheet1!A1</f></c><c r="B1"><f t="shared" si="0"/><v>4</v></c></row>',
        extra={'xl/externalLinks/_rels/externalLink1.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="external" TargetMode="External" Type="externalLinkPath" Target="https://invalid.example/secret.xlsx"/></Relationships>'},
        workbook_extra='<externalReferences/>')
    result = extract_document(data, XLSX)
    assert {'external_references', 'external_relationships', 'formula_cached_values', 'shared_or_array_formula'} <= {warning['code'] for warning in result['warnings']}
    cells = result['blocks'][0]['locator']['cells']
    assert cells[0]['cached_value'] is None and cells[0]['formula']['text'] == '[1]Sheet1!A1'
    assert cells[1]['formula']['type'] == 'shared' and cells[1]['cached_value'] == '4'
    assert 'сохранённое значение отсутствует' in result['blocks'][0]['text']
    assert all('secret.xlsx' not in warning['message'] for warning in result['warnings'])


def test_hidden_and_merged_source_cells_remain_traceable():
    data = workbook('<row r="4" hidden="1">' + inline('A4', 'Hidden source') + '</row>',
        dimension='A4:B4', sheet_attributes='state="hidden"',
        worksheet_extra='<mergeCells><mergeCell ref="A4:B4"/></mergeCells>')
    result = extract_document(data, XLSX)
    assert result['blocks'][0]['locator']['row'] == 4
    assert result['blocks'][0]['locator']['cells'][0]['ref'] == 'A4'
    assert {'hidden_sheet', 'hidden_row', 'merged_cells'} <= {warning['code'] for warning in result['warnings']}


@pytest.mark.parametrize('extra,content_type', [
    ({'xl/vbaProject.bin': b'not executable'}, None),
    ({'xl/activeX/activeX1.xml': '<activeX/>'}, None),
    ({'../escape.xml': '<ignored/>'}, None),
    ({'xl/comments1.xml': '<!DOCTYPE note [<!ENTITY x "private">]><note>&x;</note>'}, None),
    ({'xl/large.xml': '<ignored>' + '0' * (2 * 1024 * 1024) + '</ignored>'}, None),
    ({}, 'application/vnd.ms-excel.sheet.macroEnabled.main+xml'),
])
def test_xlsx_rejects_macros_unsafe_xml_archive_paths_and_bombs(extra, content_type):
    data = workbook('<row r="1">' + inline('A1', 'Synthetic') + '</row>', extra=extra, content_type=content_type)
    with pytest.raises(HTTPException) as error:
        validate_original('Unsafe.xlsx', data)
    assert error.value.status_code == 422


def test_huge_declared_dimensions_do_not_allocate_empty_rows_or_cells():
    data = workbook('<row r="1048576">' + inline('XFD1048576', 'Sparse actual cell') + '</row>', dimension='A1:XFD1048576')
    result = extract_document(data, XLSX)
    assert result['stats']['units_total'] == 1 and len(result['blocks']) == 1
    assert result['blocks'][0]['locator']['cells'][0]['ref'] == 'XFD1048576'


@pytest.mark.parametrize('rows,dimension', [
    ('<row r="1">' + inline('XFE1', 'outside') + '</row>', 'A1:XFE1'),
    ('<row r="1">' + inline('A2', 'wrong row') + '</row>', 'A1:A2'),
    ('<row r="1">' + inline('A1', 'first') + inline('A1', 'duplicate') + '</row>', 'A1'),
    ('<row r="1"><c r="A1" t="s"><v>50000000</v></c></row>', 'A1'),
    ('<row r="1"><c r="A1"><f t="array" ref="A1:XFE1048576">1</f></c></row>', 'A1'),
])
def test_bad_cell_coordinates_and_formula_ranges_fail_without_partial_publication(rows, dimension):
    with pytest.raises(ExtractionError):
        extract_document(workbook(rows, dimension=dimension), XLSX)


def test_cell_limit_stops_extraction_without_publishing_truncated_text(monkeypatch):
    import knowledge_service.spreadsheet as module
    monkeypatch.setattr(module, 'MAX_CELLS', 1)
    with pytest.raises(ExtractionError) as error:
        extract_document(workbook('<row r="1">' + inline('A1', 'one') + inline('B1', 'two') + '</row>'), XLSX)
    assert error.value.code == 'limit_exceeded'


def test_a_zip_or_spreadsheet_renamed_as_another_format_is_rejected():
    data = workbook('<row r="1">' + inline('A1', 'Synthetic') + '</row>')
    for filename, source in (('Spreadsheet.doc', data), ('Spreadsheet.docx', data), ('Spreadsheet.xlsx', b'PK\x03\x04not a workbook')):
        with pytest.raises(HTTPException):
            validate_original(filename, source)


def test_xml_depth_and_node_limits_apply_to_omitted_workbook_parts(monkeypatch):
    import knowledge_service.office_formats as module
    data = workbook('<row r="1">' + inline('A1', 'Synthetic') + '</row>',
                    extra={'xl/comments1.xml': '<a>' * 65 + '</a>' * 65})
    with pytest.raises(HTTPException):
        validate_original('Unsafe.xlsx', data)
    monkeypatch.setattr(module, 'MAX_XML_NODES', 5)
    with pytest.raises(HTTPException):
        validate_original('TooManyNodes.xlsx', workbook('<row r="1">' + inline('A1', 'Synthetic') + '</row>'))


def test_converter_timeout_and_invalid_encoding_are_specific_safe_errors(monkeypatch, tmp_path):
    import knowledge_service.legacy_word as module
    converter = tmp_path / 'antiword'
    converter.write_text('synthetic executable marker')
    monkeypatch.setattr(module, 'ANTIWORD', converter)
    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(['private text'], 20)
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    with pytest.raises(ExtractionError) as error:
        module.convert_doc(legacy_doc(), ExtractionError)
    assert error.value.code == 'conversion_timeout' and 'private' not in error.value.message
    def invalid_encoding(*args, **kwargs):
        kwargs['stdout'].write(b'\xff')
        return type('Completed', (), {'returncode': 0})()
    monkeypatch.setattr(module.subprocess, 'run', invalid_encoding)
    with pytest.raises(ExtractionError) as error:
        module.convert_doc(legacy_doc(), ExtractionError)
    assert error.value.code == 'conversion_encoding'
