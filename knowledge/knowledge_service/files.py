import hashlib
import io
import stat
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.etree import ElementTree

from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .office_formats import DOC, XLSX, validate_doc, validate_xlsx


def validate_effective_dates(effective_from, effective_to):
    if effective_to and (not effective_from or effective_to < effective_from):
        raise HTTPException(422, 'Дата окончания не может предшествовать началу действия')


async def read_original(file, max_file_bytes):
    try:
        content = await file.read(max_file_bytes + 1)
    finally:
        await file.close()
    if not content:
        raise HTTPException(422, 'Файл пуст. Выберите непустой PDF, DOCX, DOC или XLSX')
    if len(content) > max_file_bytes:
        raise HTTPException(413, f'Размер файла не должен превышать {max_file_bytes // (1024 * 1024)} МБ')
    filename, media_type = validate_original(file.filename, content)
    return {'filename': filename, 'media_type': media_type, 'content': content,
            'content_hash': hashlib.sha256(content).hexdigest()}


def _validate_xml(content, root_name):
    # No entity expansion or DTDs while inspecting an untrusted Office file.
    declaration_check = content.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in declaration_check or b'<!ENTITY' in declaration_check:
        raise ValueError()
    root = ElementTree.fromstring(content)
    if root.tag.rsplit('}', 1)[-1] != root_name:
        raise ValueError()
    if root_name == 'document':
        namespaces = ('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}',
                      '{http://purl.oclc.org/ooxml/wordprocessingml/main}')
        namespace = next((value for value in namespaces if root.tag == value + 'document'), None)
        if namespace is None or root.find(namespace + 'body') is None:
            raise ValueError()


def validate_original(filename, content):
    # Имена — только метаданные, не пути на диске и не HTML.
    filename = (filename or '').replace('\\', '/').split('/')[-1]
    if not filename or len(filename) > 200 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, 'Некорректное имя файла')
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in ('.doc', '.xlsx'):
        try:
            (validate_doc if suffix == '.doc' else validate_xlsx)(content)
            return filename, DOC if suffix == '.doc' else XLSX
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, KeyError, NotImplementedError,
                ElementTree.ParseError, zlib.error, RecursionError, OverflowError, AssertionError):
            raise HTTPException(422, 'DOC или XLSX повреждён, защищён паролем, содержит макросы либо не соответствует расширению. Сохраните документ без защиты и повторите загрузку') from None
    if suffix == '.pdf':
        reader = None
        try:
            if not content.startswith(b'%PDF-') or b'%%EOF' not in content[-2048:]:
                raise ValueError()
            # Inspect the actual catalog/page tree without rewriting the file,
            # rendering pages, extracting text or claiming OCR readiness.
            reader = PdfReader(io.BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise HTTPException(422, 'PDF защищён паролем. Загрузите копию PDF без парольной защиты')
            root = reader.root_object
            if root.get('/Type') != '/Catalog' or root['/Pages'].get('/Type') != '/Pages' or len(reader.pages) < 1:
                raise ValueError()
            return filename, 'application/pdf'
        except (PyPdfError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, AssertionError):
            raise HTTPException(422, 'PDF повреждён или не содержит страниц. Сохраните документ заново и повторите загрузку') from None
        finally:
            if reader is not None:
                reader.close()
    if suffix == '.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(i.file_size for i in entries) > 50 * 1024 * 1024:
                    raise ValueError()
                if any(i.flag_bits & 1 or i.file_size > 20 * 1024 * 1024 for i in entries):
                    raise ValueError()
                if any(i.filename.startswith(('/', '\\')) or '..' in PurePosixPath(i.filename.replace('\\', '/')).parts
                       or stat.S_ISLNK(i.external_attr >> 16) for i in entries):
                    raise ValueError()
                names = {i.filename for i in entries}
                if len(names) != len(entries):
                    raise ValueError()
                if not {'[Content_Types].xml', 'word/document.xml'} <= names or any('vbaproject' in n.lower() for n in names):
                    raise ValueError()
                # Читаем XML целиком в ограниченных пределах, проверяем CRC.
                _validate_xml(archive.read('[Content_Types].xml'), 'Types')
                _validate_xml(archive.read('word/document.xml'), 'document')
            return filename, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        except (ValueError, zipfile.BadZipFile, RuntimeError, KeyError, NotImplementedError, ElementTree.ParseError, zlib.error):
            pass
    raise HTTPException(422, 'Нужен файл PDF, DOCX, DOC или XLSX без макросов. Формат файла не соответствует расширению либо повреждён')
