"""Read-only Office format inspection. No Office engine or formula evaluator."""
import io
import posixpath
import re
import stat
import struct
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

import olefile

DOC = 'application/msword'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
STRICT_S = '{http://purl.oclc.org/ooxml/spreadsheetml/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
STRICT_R = '{http://purl.oclc.org/ooxml/officeDocument/relationships}'
PACKAGE_R = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_SHEETS = 100
MAX_XML_NODES = 200_000
MAX_XML_DEPTH = 64


def office_xml(data):
    # Checking after removal of NUL also covers UTF-16 declarations. ElementTree
    # never resolves external entities, and DTD/internal entities are rejected.
    check = data.replace(b'\0', b'').upper()
    if b'<!DOCTYPE' in check or b'<!ENTITY' in check:
        raise ValueError('unsafe_xml')
    parser = ET.iterparse(io.BytesIO(data), events=('start', 'end'))
    depth, nodes = 0, 0
    for event, _ in parser:
        if event == 'start':
            depth += 1
            nodes += 1
            if depth > MAX_XML_DEPTH or nodes > MAX_XML_NODES:
                raise ValueError('xml_structure_limit')
        else:
            depth -= 1
    root = parser.root
    for node in root.iter():
        if node.tag.startswith(STRICT_S):
            node.tag = S + node.tag[len(STRICT_S):]
        node.attrib = {(R + key[len(STRICT_R):] if key.startswith(STRICT_R) else key): value
                       for key, value in node.attrib.items()}
    return root


def checked_zip(content):
    archive = zipfile.ZipFile(io.BytesIO(content))
    try:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES or sum(i.file_size for i in entries) > MAX_ZIP_BYTES:
            raise ValueError('archive_limit')
        names = {i.filename for i in entries}
        if len(names) != len(entries):
            raise ValueError('duplicate_parts')
        for entry in entries:
            name = entry.filename
            if (entry.flag_bits & 1 or entry.file_size > MAX_PART_BYTES
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or name.startswith(('/', '\\')) or '\\' in name
                    or '..' in PurePosixPath(name).parts or stat.S_ISLNK(entry.external_attr >> 16)
                    or (entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 1000)):
                raise ValueError('unsafe_archive')
            if any(token in name.casefold() for token in ('vbaproject', 'macrosheet', 'activex')):
                raise ValueError('active_content')
            # Read all members to check CRC in the bounded package. Nothing is
            # extracted onto disk. Check even XML parts omitted from extraction.
            data = archive.read(entry)
            if name.endswith(('.xml', '.rels')):
                office_xml(data)
        return archive
    except BaseException:
        archive.close()
        raise


def _internal_target(source_part, target, names):
    decoded = unquote(target)
    uri = urlsplit(decoded)
    if uri.scheme or uri.netloc or uri.query or uri.fragment or '\\' in decoded:
        raise ValueError('invalid_target')
    path = posixpath.normpath(decoded.lstrip('/') if decoded.startswith('/') else
                             posixpath.join(posixpath.dirname(source_part), decoded))
    if path.startswith('../') or path not in names:
        raise ValueError('missing_part')
    return path


def workbook_parts(archive):
    names = set(archive.namelist())
    required = {'[Content_Types].xml', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'}
    if not required <= names:
        raise ValueError('not_workbook')
    types = office_xml(archive.read('[Content_Types].xml'))
    if types.tag.rsplit('}', 1)[-1] != 'Types':
        raise ValueError('invalid_content_types')
    workbook_type = None
    for node in types:
        content_type = node.get('ContentType', '')
        if any(token in content_type.casefold() for token in ('macroenabled', 'vbaproject', 'macrosheet', 'activex')):
            raise ValueError('active_content')
        if node.get('PartName') == '/xl/workbook.xml':
            workbook_type = content_type
    if workbook_type != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml':
        raise ValueError('not_xlsx')
    root = office_xml(archive.read('xl/workbook.xml'))
    relationships = office_xml(archive.read('xl/_rels/workbook.xml.rels'))
    if root.tag != S + 'workbook' or relationships.tag != PACKAGE_R + 'Relationships':
        raise ValueError('invalid_workbook')
    relations = {}
    for rel in relationships:
        ident = rel.get('Id')
        if not ident or ident in relations or rel.tag != PACKAGE_R + 'Relationship':
            raise ValueError('invalid_relationship')
        relations[ident] = rel
    sheets = root.find(S + 'sheets')
    if sheets is None or not 1 <= len(sheets) <= MAX_SHEETS:
        raise ValueError('sheet_limit')
    out, seen_names, seen_ids, seen_parts = [], set(), set(), set()
    for sheet in sheets:
        name, ident = sheet.get('name', ''), sheet.get('sheetId', '')
        rel = relations.get(sheet.get(R + 'id'))
        if (sheet.tag != S + 'sheet' or not name or len(name) > 31
                or re.search(r'[\[\]:*?/\\\x00-\x1f]', name) or name.casefold() in seen_names
                or not ident.isdecimal() or int(ident) < 1 or ident in seen_ids or rel is None
                or rel.get('TargetMode', 'Internal') != 'Internal'):
            raise ValueError('invalid_sheet')
        kind = rel.get('Type', '').rsplit('/', 1)[-1]
        if kind not in ('worksheet', 'chartsheet'):
            raise ValueError('unsupported_sheet')
        part = _internal_target('xl/workbook.xml', rel.get('Target', ''), names)
        if part in seen_parts:
            raise ValueError('duplicate_sheet')
        sheet_root = office_xml(archive.read(part))
        if sheet_root.tag != S + kind:
            raise ValueError('invalid_sheet')
        seen_names.add(name.casefold())
        seen_ids.add(ident)
        seen_parts.add(part)
        out.append({'name': name, 'id': ident, 'part': part, 'kind': kind,
                    'state': sheet.get('state', 'visible')})
    return root, out


def validate_xlsx(content):
    with checked_zip(content) as archive:
        workbook_parts(archive)


def validate_doc(content):
    # A renamed OLE workbook or presentation is not a Word original. Opening is
    # read-only; FIB and streams are inspected without running the converter.
    if not content.startswith(olefile.MAGIC):
        raise ValueError('not_doc')
    with olefile.OleFileIO(io.BytesIO(content), raise_defects=olefile.DEFECT_INCORRECT) as archive:
        entries = archive.listdir(streams=True, storages=True)
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ValueError('doc_limit')
        if any(part.casefold() in ('vba', 'macros', '_vba_project', 'project', 'projectwm')
               for entry in entries for part in entry):
            raise ValueError('active_content')
        if not archive.exists('WordDocument') or archive.get_size('WordDocument') < 32:
            raise ValueError('not_word')
        fib = archive.openstream('WordDocument').read(32)
        magic, version = struct.unpack_from('<HH', fib)
        flags = struct.unpack_from('<H', fib, 10)[0]
        if magic != 0xA5EC or version not in (0xC1, 0xD9, 0x101, 0x10C, 0x112):
            raise ValueError('unsupported_doc')
        if flags & (0x0100 | 0x8000):
            raise ValueError('encrypted_doc')
        table = '1Table' if flags & 0x0200 else '0Table'
        if not archive.exists(table) or archive.get_size(table) < 1:
            raise ValueError('missing_table')
