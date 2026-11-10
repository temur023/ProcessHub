"""Business boundaries of the complete upload, using an isolated PostgreSQL schema."""
import io
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from knowledge_service.files import validate_original
from knowledge_service import registry_store
from test_registry import docx, send


def upload(registry, *, actor=1, admin=False, filename='Должностная инструкция.docx',
           content=None, title='  Инструкция   специалиста ', **data):
    return send(registry, 'POST', '/instructions/upload', actor=actor, admin=admin,
                files={'file': (filename, docx() if content is None else content)},
                data={'department_id': str(registry[2]), 'role_id': str(registry[3]), 'title': title, **data})


def counts(registry):
    with psycopg.connect(registry[1].database_url) as conn:
        return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('instructions', 'instruction_versions', 'audit_events', 'upload_receipts'))


def pdf(*, pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if encrypted:
        writer.encrypt('test-document-password')
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def compressed_pdf(*, compressed_root=True, stream_type='/ObjStm'):
    # A real PDF1.5 cross-reference stream stores either the Catalog or Pages
    # object in object stream1. Changing that stream's Type reproduces the
    # PdfReader assertion instead of replacing or mocking its parser.
    objects = {
        2: b'<< /Type /Catalog /Pages 3 0 R >>',
        3: b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>',
        4: b'<< /Type /Page /Parent 3 0 R /MediaBox [0 0 595 842] >>',
    }
    compressed_number = 2 if compressed_root else 3
    index = f'{compressed_number} 0 '.encode()
    compressed = zlib.compress(index + objects.pop(compressed_number))
    document = bytearray(b'%PDF-1.5\n%\xe2\xe3\xcf\xd3\n')
    offsets = {1: len(document)}
    document.extend(f'1 0 obj\n<< /Type {stream_type} /N 1 /First {len(index)} '
                    f'/Length {len(compressed)} /Filter /FlateDecode >>\nstream\n'.encode())
    document.extend(compressed + b'\nendstream\nendobj\n')
    for number, value in sorted(objects.items()):
        offsets[number] = len(document)
        document.extend(f'{number} 0 obj\n'.encode() + value + b'\nendobj\n')
    offsets[5] = len(document)
    entries = []
    for number in range(6):
        kind, offset, generation = (0, 0, 65535) if number == 0 else (
            (2, 1, 0) if number == compressed_number else (1, offsets[number], 0))
        entries.append(bytes([kind]) + offset.to_bytes(4, 'big') + generation.to_bytes(2, 'big'))
    cross_reference = b''.join(entries)
    document.extend(f'5 0 obj\n<< /Type /XRef /Size 6 /Root 2 0 R /W [1 4 2] '
                    f'/Length {len(cross_reference)} >>\nstream\n'.encode())
    document.extend(cross_reference + b'\nendstream\nendobj\n')
    document.extend(f'startxref\n{offsets[5]}\n%%EOF\n'.encode())
    return bytes(document)


def test_atomic_upload_creates_first_version_with_exact_original(registry):
    original = docx()
    response = upload(registry, content=original, effective_from='2026-10-01', effective_to='2027-09-30')
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload['instruction']['title'] == 'Инструкция специалиста'
    assert payload['instruction']['role_name'] == 'Главный специалист'
    assert payload['instruction']['version_count'] == 1
    assert payload['version']['number'] == 1
    assert payload['version']['effective_from'] == '2026-10-01'
    assert 'content' not in payload['version']
    assert counts(registry) == (1, 1, 2, 0)
    download = send(registry, 'GET', f"/instruction-versions/{payload['version']['id']}/original", actor=2)
    assert download.content == original


def test_pdf_structure_is_validated_and_original_bytes_are_preserved(registry):
    original = pdf(pages=2)
    response = upload(registry, filename='Должностная инструкция.PDF', content=original)
    assert response.status_code == 201, response.text
    version = response.json()['version']
    assert version['media_type'] == 'application/pdf'
    assert version['byte_size'] == len(original)
    assert send(registry, 'GET', f"/instruction-versions/{version['id']}/original", actor=2).content == original


@pytest.mark.parametrize('original', [b'%PDF-1.7\npretend document\n%%EOF', pdf(pages=0), pdf()[:-40] + b'%%EOF'])
def test_broken_or_pageless_pdf_does_not_create_an_instruction(registry, original):
    response = upload(registry, filename='invalid.pdf', content=original)
    assert response.status_code == 422, response.text
    assert counts(registry) == (0, 0, 0, 0)


def test_password_protected_pdf_has_clear_error_and_no_empty_record(registry):
    response = upload(registry, filename='protected.pdf', content=pdf(encrypted=True))
    assert response.status_code == 422, response.text
    assert 'паролем' in response.json()['detail']
    assert counts(registry) == (0, 0, 0, 0)


@pytest.mark.parametrize('compressed_root', [True, False])
def test_invalid_pdf_object_stream_returns_422_without_empty_record(registry, compressed_root):
    valid = compressed_pdf(compressed_root=compressed_root)
    assert validate_original('compressed.pdf', valid)[1] == 'application/pdf'
    broken = compressed_pdf(compressed_root=compressed_root, stream_type='/InvalidStreamType')
    response = upload(registry, filename='broken-object-stream.pdf', content=broken)
    assert response.status_code == 422, response.text
    assert 'PDF повреждён' in response.json()['detail']
    assert counts(registry) == (0, 0, 0, 0)


@pytest.mark.parametrize('filename,content,data,status', [
    ('fake.docx', b'not a document', {}, 422),
    ('fake.pdf', b'not a pdf', {}, 422),
    ('empty.docx', b'', {}, 422),
    ('wrong.exe', b'MZ', {}, 422),
    ('large.pdf', b'%PDF-' + b'x' * (10 * 1024 * 1024), {}, 413),
    ('valid.docx', docx(), {'effective_from': '2026-11-01', 'effective_to': '2026-10-01'}, 422),
    ('valid.docx', docx(), {'effective_to': '2026-10-01'}, 422),
    ('valid.docx', docx(), {'effective_from': 'incorrect'}, 422),
    ('valid.docx', docx(), {'upload_id': 'incorrect'}, 422),
])
def test_invalid_upload_leaves_no_empty_instruction(registry, filename, content, data, status):
    response = upload(registry, filename=filename, content=content, **data)
    assert response.status_code == status, response.text
    assert counts(registry) == (0, 0, 0, 0)


def test_transaction_rolls_back_entry_and_original_if_audit_fails(registry, monkeypatch):
    def fail_audit(*args, **kwargs):
        raise RuntimeError('simulated audit storage failure')
    monkeypatch.setattr(registry[0].app.state.store, 'audit', fail_audit)
    with pytest.raises(RuntimeError, match='simulated audit storage failure'):
        upload(registry, upload_id=str(uuid4()))
    assert counts(registry) == (0, 0, 0, 0)


def test_upload_checks_write_access_and_role_department(registry):
    assert upload(registry, actor=2).status_code == 403
    assert upload(registry, actor=3).status_code == 404
    assert upload(registry, role_id=str(registry[4])).status_code == 422
    assert upload(registry, title=' \t ').status_code == 422
    assert counts(registry) == (0, 0, 0, 0)
    assert upload(registry, actor=3, admin=True).status_code == 201


def test_duplicate_instruction_requires_existing_history(registry):
    assert upload(registry).status_code == 201
    duplicate = upload(registry, title='ИНСТРУКЦИЯ СПЕЦИАЛИСТА', content=docx('Новый текст'))
    assert duplicate.status_code == 409, duplicate.text
    assert 'новую версию' in duplicate.json()['detail']
    assert counts(registry) == (1, 1, 2, 0)


def test_concurrent_instruction_creation_is_single_entry(registry):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(registry), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert counts(registry) == (1, 1, 2, 0)


def test_lost_response_retry_returns_the_same_original_without_new_audit(registry):
    upload_id = str(uuid4())
    first = upload(registry, upload_id=upload_id)
    retry = upload(registry, upload_id=upload_id)
    assert first.status_code == retry.status_code == 201
    assert not first.json()['replayed']
    assert retry.json()['replayed']
    assert first.json()['instruction']['id'] == retry.json()['instruction']['id']
    assert first.json()['version'] == retry.json()['version']
    assert counts(registry) == (1, 1, 2, 1)
    assert upload(registry, upload_id=upload_id, content=docx('Изменённое содержание')).status_code == 409
    assert upload(registry, upload_id=upload_id, effective_from='2026-11-01').status_code == 409
    # Receipt keys do not grant access and cannot be reused by a reader.
    assert upload(registry, actor=2, upload_id=upload_id).status_code == 403
    assert counts(registry) == (1, 1, 2, 1)
    for statement in ('UPDATE upload_receipts SET request_hash=\'changed\'', 'DELETE FROM upload_receipts'):
        with pytest.raises(psycopg.Error):
            with psycopg.connect(registry[1].database_url) as conn:
                conn.execute(statement)


def test_concurrent_retry_commits_one_original(registry):
    upload_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(registry, upload_id=upload_id), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json()['replayed'] for response in responses) == [False, True]
    assert len({response.json()['version']['id'] for response in responses}) == 1
    assert counts(registry) == (1, 1, 2, 1)


def test_version_retry_and_concurrent_distinct_version_numbers(registry):
    first = upload(registry).json()
    path = f"/instructions/{first['instruction']['id']}/versions"
    upload_id = str(uuid4())
    def send_version(content, **data):
        return send(registry, 'POST', path, files={'file': ('Новая инструкция.docx', content)}, data=data)
    second = send_version(docx('Новая функция'), upload_id=upload_id)
    retry = send_version(docx('Новая функция'), upload_id=upload_id)
    assert second.status_code == retry.status_code == 201
    assert retry.json()['replayed'] and retry.json()['id'] == second.json()['id']
    assert retry.json()['number'] == 2
    assert send_version(docx('Новая функция')).status_code == 409
    assert send_version(docx('Другой текст'), upload_id=upload_id).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda text: send_version(docx(text)), ['Функция А', 'Функция Б']))
    assert all(response.status_code == 201 for response in versions)
    assert sorted(response.json()['number'] for response in versions) == [3, 4]
    assert counts(registry) == (1, 4, 5, 1)
    # Later versions cannot overwrite any earlier original.
    assert send(registry, 'GET', f"/instruction-versions/{first['version']['id']}/original").content == docx()
    with pytest.raises(psycopg.Error):
        with psycopg.connect(registry[1].database_url) as conn:
            conn.execute('DELETE FROM instruction_versions WHERE id=%s', (first['version']['id'],))


def test_receipt_does_not_survive_writer_permission_revocation(registry):
    upload_id = str(uuid4())
    original = upload(registry, upload_id=upload_id)
    assert original.status_code == 201
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=1')
    assert upload(registry, upload_id=upload_id).status_code == 404


@pytest.mark.parametrize('operation', ['create', 'create-replay', 'version', 'version-replay'])
def test_revocation_completed_while_upload_waits_prevents_write_or_replay(registry, monkeypatch, operation):
    upload_id = str(uuid4())
    instruction = None
    if operation != 'create':
        instruction = upload(registry, upload_id=upload_id).json()['instruction']
    if operation.startswith('version'):
        upload_id = str(uuid4())
        path = f"/instructions/{instruction['id']}/versions"
        def action():
            return send(registry, 'POST', path, files={'file': ('version.docx', docx('Новая функция'))},
                        data={'upload_id': upload_id})
        if operation == 'version-replay':
            assert action().status_code == 201
    else:
        def action():
            return upload(registry, upload_id=upload_id)
    baseline = counts(registry)
    checked_before_lock = Event()
    actual_receipt = registry_store._receipt
    def receipt_hook(*args, **kwargs):
        result = actual_receipt(*args, **kwargs)
        checked_before_lock.set()
        return result
    monkeypatch.setattr(registry_store, '_receipt', receipt_hook)
    with psycopg.connect(registry[1].database_url) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute('SELECT id FROM departments WHERE id=%s FOR UPDATE', (registry[2],))
        future = pool.submit(action)
        try:
            # The upload has passed the initial access check while the access
            # transaction still owns the department lock.
            assert checked_before_lock.wait(timeout=5)
            blocker.execute('DELETE FROM access_grants WHERE user_id=1')
        finally:
            blocker.commit()
        response = future.result(timeout=10)
    assert response.status_code == 404, response.text
    assert counts(registry) == baseline


@pytest.mark.parametrize('document_xml', [b'<document>', b'<wrong/>',
    b'<!DOCTYPE document [<!ENTITY x "expansion">]><document>&x;</document>',
    '<!DOCTYPE document [<!ENTITY x "expansion">]><document>&x;</document>'.encode('utf-16')])
def test_docx_corruption_or_xml_entities_are_rejected(document_xml):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', document_xml)
    with pytest.raises(HTTPException) as error:
        validate_original('instruction.docx', out.getvalue())
    assert error.value.status_code == 422


def test_docx_zip_traversal_or_macros_are_rejected():
    for filename in ('../escape', 'word/vbaProject.bin'):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', '<document/>')
            archive.writestr(filename, 'unexpected payload')
        with pytest.raises(HTTPException):
            validate_original('instruction.docx', out.getvalue())


def test_docx_corrupt_compression_returns_format_error(registry):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<document/>')
        entry = archive.getinfo('word/document.xml')
    compressed_start = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    content = bytearray(out.getvalue())
    content[compressed_start:compressed_start + 4] = b'\xff' * 4
    response = upload(registry, content=bytes(content))
    assert response.status_code == 422, response.text
    assert counts(registry) == (0, 0, 0, 0)
