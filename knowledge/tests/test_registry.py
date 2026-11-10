import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from xml.sax.saxutils import escape
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest


def token(secret, user, method, path, body, exp=None, admin=False):
    now = int(time.time())
    claims = dict(iss='process-hub', aud='knowledge', sub=user, iat=now, exp=exp or now + 60,
                  method=method, target=path, body=hashlib.sha256(body).hexdigest(), platform_admin=admin)
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return raw + '.' + hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def send(registry, method, path, actor=1, admin=False, **kwargs):
    client, settings, *_ = registry
    prepared = httpx.Request(method, 'http://testserver' + path, **kwargs)
    body = prepared.read()
    headers = dict(prepared.headers)
    headers['authorization'] = 'Bearer ' + token(settings.hub_secret, actor, method, path, body, admin=admin)
    return client.request(method, path, headers=headers, content=body)


def docx(text='Обязанность: проверять документы'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        # ZIP's current-time header must not make equal test documents differ
        # across a two-second boundary in duplicate/concurrency assertions.
        z.writestr(zipfile.ZipInfo('[Content_Types].xml', date_time=(2026,10,1,0,0,0)), '<Types/>')
        z.writestr(zipfile.ZipInfo('word/document.xml', date_time=(2026,10,1,0,0,0)), '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def create(registry):
    result = send(registry, 'POST', '/instructions', json={'department_id': str(registry[2]), 'role_id': str(registry[3]), 'title': '  Инструкция   специалиста '})
    assert result.status_code == 201, result.text
    assert result.json()['title'] == 'Инструкция специалиста'
    return result.json()['id']


def test_registry_original_history_and_revocation(registry):
    instruction = create(registry)
    original = docx()
    path = f'/instructions/{instruction}/versions'
    first = send(registry, 'POST', path, files={'file': ('../инструкция.docx', original)}, data={'effective_from': '2026-10-01'})
    assert first.status_code == 201, first.text
    assert first.json()['filename'] == 'инструкция.docx'
    assert first.json()['number'] == 1
    assert 'content' not in first.json()
    assert send(registry, 'POST', path, files={'file': ('copy.docx', original)}).status_code == 409
    assert send(registry, 'POST', path, files={'file': ('new.docx', docx('Новая обязанность ИИ'))}).json()['number'] == 2
    versions = send(registry, 'GET', path, actor=2).json()
    assert [v['number'] for v in versions] == [2, 1]
    original_path = f"/instruction-versions/{first.json()['id']}/original"
    download = send(registry, 'GET', original_path, actor=2)
    assert download.content == original
    assert download.headers['cache-control'] == 'no-store'
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry, 'GET', path, actor=2).status_code == 404
    assert send(registry, 'GET', original_path, actor=2).status_code == 404
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM audit_events').fetchone()[0] == 4


def test_access_and_cross_department_role(registry):
    instruction = create(registry)
    assert send(registry, 'GET', '/departments', actor=3).json() == []
    assert send(registry, 'GET', f'/departments/{registry[4]}/instructions').status_code == 404
    assert send(registry, 'POST', '/instructions', actor=2, json={'department_id': str(registry[2]), 'role_id': str(registry[3]), 'title': 'Denied'}).status_code == 403
    assert send(registry, 'POST', '/instructions', json={'department_id': str(registry[2]), 'role_id': str(registry[4]), 'title': 'Wrong role'}).status_code == 422
    assert send(registry, 'POST', f'/instructions/{instruction}/versions', actor=2, files={'file': ('x.docx', docx())}).status_code == 403


def test_forgery_expiry_and_bound_request(registry):
    client, settings, *_ = registry
    path = '/departments'
    assert client.get(path).status_code == 401
    valid = token(settings.hub_secret, 1, 'GET', path, b'')
    assert client.get(path, headers={'Authorization': 'Bearer ' + valid[:-1] + ('a' if valid[-1] != 'a' else 'b')}).status_code == 401
    expired = token(settings.hub_secret, 1, 'GET', path, b'', exp=int(time.time()) - 1)
    assert client.get(path, headers={'Authorization': 'Bearer ' + expired}).status_code == 401
    assert client.get(f'/departments/{registry[2]}/roles', headers={'Authorization': 'Bearer ' + valid}).status_code == 401
    assert client.post('/instructions', content=b'{}', headers={'Authorization': 'Bearer ' + valid}).status_code == 401
    assert client.get('/health').status_code == 200


def test_file_dates_and_format(registry):
    instruction = create(registry)
    path = f'/instructions/{instruction}/versions'
    for name, content in [('fake.pdf', b'not pdf'), ('fake.docx', b'not docx'), ('x.exe', b'MZ')]:
        assert send(registry, 'POST', path, files={'file': (name, content)}).status_code == 422
    assert send(registry, 'POST', path, files={'file': ('x.docx', docx())}, data={'effective_from': '2026-11-01', 'effective_to': '2026-10-01'}).status_code == 422
    assert send(registry, 'POST', path, files={'file': ('x.docx', docx())}, data={'effective_to': '2026-10-01'}).status_code == 422
    assert send(registry, 'POST', path, files={'file': ('big.pdf', b'%PDF-' + b'x' * (10 * 1024 * 1024))}).status_code == 413
    # Подмена оригинала запрещена даже прямой SQL-ошибкой приложения.
    version = send(registry, 'POST', path, files={'file': ('x.docx', docx())}).json()
    with pytest.raises(psycopg.Error):
        with psycopg.connect(registry[1].database_url) as conn:
            conn.execute('UPDATE instruction_versions SET filename=%s WHERE id=%s', ('forged.docx', version['id']))


def test_concurrent_identical_upload_is_single_version(registry):
    instruction = create(registry)
    def upload(_):
        return send(registry, 'POST', f'/instructions/{instruction}/versions', files={'file': ('x.docx', docx())}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(upload, [0, 1])) == [201, 409]
    assert len(send(registry, 'GET', f'/instructions/{instruction}/versions').json()) == 1


def test_platform_admin_setup_global_access_and_revocation(registry):
    # user 3 не имеет локальных назначений; права приходят только от подписанного Hub.
    assert send(registry, 'GET', '/access', actor=3).json()['is_platform_admin'] is False
    assert send(registry, 'POST', '/departments', actor=3, json={'name': 'Новый'}).status_code == 405
    assert send(registry, 'POST', '/departments', actor=3, admin=True, json={'name': 'Новый'}).status_code == 405
    assert send(registry, 'POST', f'/departments/{registry[2]}/roles', json={'name': 'Ещё роль'}).status_code == 403
    assert send(registry, 'GET', '/access', actor=3, admin=True).json()['is_platform_admin'] is True
    assert len(send(registry, 'GET', '/departments', actor=3, admin=True).json()) == 2
    payload = {'space_id': 'cat_bank', 'departments': [{'catalog_item_id': 'cat_personnel', 'name': 'Департамент персонала', 'number': '10', 'position': 9}]}
    assert send(registry, 'POST', '/catalog-departments', actor=3, json=payload).status_code == 403
    imported = send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload)
    assert imported.status_code == 200, imported.text
    assert imported.json()['created'] == 1
    department = next(item for item in send(registry, 'GET', '/departments', actor=3, admin=True).json() if item['catalog_item_id'] == 'cat_personnel')
    dept_id = department['id']
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).json()['unchanged'] == 1
    role = send(registry, 'POST', f'/departments/{dept_id}/roles', actor=3, admin=True, json={'name': 'Специалист'})
    assert role.status_code == 201, role.text
    assert send(registry, 'POST', f'/departments/{dept_id}/roles', actor=3, admin=True, json={'name': 'СПЕЦИАЛИСТ'}).status_code == 409
    instruction = send(registry, 'POST', '/instructions', actor=3, admin=True, json={
        'department_id': dept_id, 'role_id': role.json()['id'], 'title': 'Инструкция специалиста'}).json()['id']
    version = send(registry, 'POST', f'/instructions/{instruction}/versions', actor=3, admin=True,
                   files={'file': ('инструкция.docx', docx())})
    assert version.status_code == 201, version.text
    original_path = f"/instruction-versions/{version.json()['id']}/original"
    assert send(registry, 'GET', original_path, actor=3, admin=True).content == docx()
    assert len(send(registry, 'GET', '/departments', actor=3, admin=True).json()) == 3
    assert send(registry, 'GET', f'/instructions/{instruction}/versions', actor=3).status_code == 404
    assert send(registry, 'GET', original_path, actor=3).status_code == 404
    assert send(registry, 'GET', f'/departments/{registry[4]}/instructions', actor=3, admin=True).status_code == 200
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT DISTINCT actor_id FROM audit_events').fetchall() == [(3,)]


def test_admin_claim_is_boolean_and_cannot_be_injected_in_body(registry):
    client, settings, *_ = registry
    for forged in (1, 'true', None):
        credential = token(settings.hub_secret, 3, 'GET', '/access', b'', admin=forged)
        assert client.get('/access', headers={'Authorization': 'Bearer ' + credential}).status_code == 401
    payload = {'space_id': 'cat_bank', 'departments': [{'catalog_item_id': 'cat_role', 'name': 'Название', 'number': '1', 'position': 0}], 'platform_admin': True}
    assert send(registry, 'POST', '/catalog-departments', actor=3, json=payload).status_code == 403
    payload['departments'][0]['name'] = '   '
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).status_code == 422


def test_catalog_copy_preserves_numbers_documents_and_grants(registry):
    payload = {'space_id': 'cat_bank', 'departments': [
        {'catalog_item_id': 'cat_10', 'name': 'Одинаковое название', 'number': '10', 'position': 9},
        {'catalog_item_id': 'cat_2', 'name': 'Одинаковое название', 'number': '2', 'position': 1}]}
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).json()['created'] == 2
    items = [item for item in send(registry, 'GET', '/departments', actor=3, admin=True).json() if item['catalog_space_id']]
    assert [item['catalog_number'] for item in items] == ['2', '10']
    department = items[0]
    role = send(registry, 'POST', f"/departments/{department['id']}/roles", actor=3, admin=True, json={'name': 'Специалист'}).json()
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute("INSERT INTO access_grants(user_id,department_id,permission) VALUES (2,%s,'reader')", (department['id'],))
    instruction = send(registry, 'POST', '/instructions', actor=3, admin=True, json={
        'department_id': department['id'], 'role_id': role['id'], 'title': 'Должностная инструкция'}).json()
    version = send(registry, 'POST', f"/instructions/{instruction['id']}/versions", actor=3, admin=True, files={'file': ('original.docx', docx())}).json()
    payload['departments'][1]['name'] = 'Переименованный департамент'
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).json()['updated'] == 1
    current = next(item for item in send(registry, 'GET', '/departments', actor=2).json() if item['id'] == department['id'])
    assert current['name'] == 'Переименованный департамент'
    assert send(registry, 'GET', f"/instruction-versions/{version['id']}/original", actor=2).content == docx()
    # Частичный повтор не удаляет старую папку или историю другой роли.
    payload['departments'] = payload['departments'][:1]
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).json()['unchanged'] == 1
    assert send(registry, 'GET', f"/instructions/{instruction['id']}/versions", actor=2).status_code == 200
    payload['departments'].append(payload['departments'][0])
    assert send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload).status_code == 422


def test_concurrent_catalog_copy_creates_one_department(registry):
    payload = {'space_id': 'cat_bank', 'departments': [
        {'catalog_item_id': 'cat_once', 'name': 'Департамент', 'number': '1', 'position': 0}]}
    def copy(_):
        response = send(registry, 'POST', '/catalog-departments', actor=3, admin=True, json=payload)
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(copy, [0, 1]))
    assert sum(item['created'] for item in results) == 1
    assert sum(item['unchanged'] for item in results) == 1
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute("SELECT count(*) FROM departments WHERE catalog_item_id='cat_once'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='department.catalog.imported'").fetchone()[0] == 1
