import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import knowledge
from app.services.auth import current_user
from app.services.store import User

SECRET = 'test-context-secret-' + 'k' * 40
ROLE_PATH = '/api/v1/knowledge/departments/00000000-0000-0000-0000-000000000001/roles'


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', SECRET)
    monkeypatch.setenv('SQB_KNOWLEDGE_URL', 'http://knowledge.test')
    calls = []
    original = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        payload, signature = request.headers['authorization'][7:].split('.')
        assert hmac.compare_digest(signature, hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest())
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        assert claims['body'] == hashlib.sha256(request.content).hexdigest()
        assert claims['target'] == request.url.path + ('?' + request.url.query.decode() if request.url.query else '')
        assert claims['method'] == request.method
        assert claims['sub'] != 999
        assert 'sqb_session' not in request.headers.get('cookie', '')
        if request.url.path == '/instructions/upload':
            return httpx.Response(201, json={'id': 'created'})
        return httpx.Response(200, json=[], headers={'set-cookie': 'evil=1'})
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    return TestClient(app), calls


def test_proxy_does_not_trust_browser_identity(proxy):
    client, calls = proxy
    res = client.get('/api/v1/knowledge/departments', headers={'X-User-ID': '999', 'Authorization': 'Bearer browser-forged'})
    assert res.status_code == 200
    assert 'set-cookie' not in res.headers
    assert res.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert client.post('/api/v1/knowledge/instructions/upload', files={'file':('x.pdf',b'%PDF-')}, headers={'Origin': 'http://testserver'}).status_code == 201


def test_unauthorized_proxy_rejects_without_upstream_call(proxy):
    client, calls = proxy
    override = app.dependency_overrides.pop(current_user, None)
    try:
        assert client.get('/api/v1/knowledge/departments').status_code == 401
        assert calls == []
    finally:
        if override:
            app.dependency_overrides[current_user] = override


def test_contract_and_cross_origin_mutation(proxy):
    client, calls = proxy
    assert client.get('/api/v1/knowledge/health').status_code == 404
    assert client.get('/api/v1/knowledge/departments?user_id=999').status_code == 404
    assert client.post('/api/v1/knowledge/instructions/upload', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert calls == []


def test_business_position_and_structure_contract_preserve_trusted_context(proxy):
    client,calls = proxy
    identifier = '00000000-0000-0000-0000-000000000001'
    library = f'/api/v1/knowledge/departments/{identifier}/role-library'
    for path in (library,library+'/'+identifier,f'/api/v1/knowledge/documents/files/{identifier}/structure'):
        assert client.get(path).status_code == 200
        assert client.get(path+'?user_id=999').status_code == 404
    assert client.patch(library+'/'+identifier,json={},headers={'Origin':'http://testserver'}).status_code == 200
    assert client.post(library,json={},headers={'Origin':'http://testserver'}).status_code == 200
    assert client.delete(library+'/'+identifier,headers={'Origin':'http://testserver'}).status_code == 200
    assert client.post(library,json={},headers={'Origin':'https://evil.example'}).status_code == 403
    assert len(calls) == 6


def test_analysis_registry_contract_and_removed_manual_comparison(proxy):
    client, calls = proxy
    identifier = '00000000-0000-0000-0000-000000000001'
    prefix = '/api/v1/knowledge'
    registry = f'{prefix}/departments/{identifier}/analysis-registry'
    assert client.get(registry).status_code == 200
    assert calls[-1].url.path == f'/departments/{identifier}/analysis-registry'
    assert client.get(registry + '?user_id=999').status_code == 404
    count = len(calls)
    for method, path in (('GET', f'{prefix}/departments/{identifier}/comparison-workflows'),
                         ('GET', f'{prefix}/documents/{identifier}/comparison-workflows'),
                         ('POST', f'{prefix}/documents/{identifier}/comparison-workflows'),
                         ('PATCH', f'{prefix}/comparison-workflows/{identifier}')):
        assert client.request(method, path, headers={'Origin': 'http://testserver'}).status_code == 404
    assert len(calls) == count

def test_mutation_origin_keeps_local_port_boundary(proxy):
    client, calls = proxy
    same = {'Host': '127.0.0.1:8080', 'Origin': 'http://127.0.0.1:8080'}
    assert client.post(ROLE_PATH, json={}, headers=same).status_code == 200
    for origin in ('http://127.0.0.1:8081', 'http://localhost:8080', 'http://127.0.0.1:8080/path'):
        assert client.post(ROLE_PATH, json={}, headers={**same, 'Origin': origin}).status_code == 403
    assert len(calls) == 1


def test_disabled_module_and_upstream_auth_failure(proxy, monkeypatch):
    client, _ = proxy
    monkeypatch.delenv('SQB_KNOWLEDGE_SECRET')
    assert client.get('/api/v1/knowledge/departments').status_code == 503
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', SECRET)
    class RejectingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs): return httpx.Response(401, json={'detail': 'bad signature'})
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', lambda **kw: RejectingClient())
    result = client.get('/api/v1/knowledge/departments')
    assert result.status_code == 503
    assert 'bad signature' not in result.text


def test_admin_context_comes_from_session_not_browser(proxy):
    client, calls = proxy
    assert client.get('/api/v1/knowledge/access', headers={'X-Platform-Admin': 'true'}).status_code == 200
    def claims():
        raw = calls[-1].headers['authorization'][7:].split('.')[0]
        return json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    assert claims()['platform_admin'] is False
    previous = app.dependency_overrides[current_user]
    app.dependency_overrides[current_user] = lambda: User(id=3, email='admin@sqb.uz', name='Администратор', isAdmin=True)
    try:
        assert client.post(ROLE_PATH, json={'name': 'Специалист'}).status_code == 200
        assert claims()['platform_admin'] is True
        assert claims()['sub'] == 3
        assert client.post(ROLE_PATH, json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    finally:
        app.dependency_overrides[current_user] = previous


def test_browser_cannot_create_departments_or_send_source_copy(proxy):
    client, calls = proxy
    assert client.post('/api/v1/knowledge/departments', json={'name': 'Своя папка'}).status_code == 404
    assert client.post('/api/v1/knowledge/catalog-departments', json={'departments': []}).status_code == 404
    assert client.post('/api/v1/knowledge/instructions', json={'title':'Пустая запись'}).status_code == 404
    assert calls == []


def test_preparation_contract_signs_current_session_and_guards_mutations(proxy):
    client, calls = proxy
    path = '/api/v1/knowledge/instruction-versions/00000000-0000-0000-0000-000000000001/preparation'
    assert client.get(path).status_code == 200
    assert client.post(path, headers={'Origin': 'http://testserver'}).status_code == 200
    assert client.post(path + '/cancel', headers={'Origin': 'http://testserver'}).status_code == 200
    assert len(calls) == 3
    assert client.post(path + '/cancel', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get(path + '/cancel').status_code == 404
    assert client.get(path + '?user_id=999').status_code == 404
    assert client.post(path + '/publish', json={'source': 'forged'}).status_code == 404
    assert len(calls) == 3


INSTRUCTION_ID = '00000000-0000-0000-0000-000000000001'
DELETED_INSTRUCTIONS_PATH = f'/api/v1/knowledge/departments/{INSTRUCTION_ID}/deleted-instructions'


@pytest.mark.parametrize('action', ['delete', 'restore'])
def test_instruction_lifecycle_contract_forwards_only_exact_signed_action(proxy, action):
    client, calls = proxy
    path = f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/{action}'
    body = json.dumps({'reason': 'Проверка истории документа'}, ensure_ascii=False).encode()
    response = client.post(path, content=body, headers={
        'Origin': 'http://testserver', 'Content-Type': 'application/json',
        'X-User-ID': '999', 'X-Platform-Admin': 'true',
    })
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert calls[0].method == 'POST'
    assert calls[0].url.path == f'/instructions/{INSTRUCTION_ID}/{action}'
    assert calls[0].content == body
    raw = calls[0].headers['authorization'][7:].split('.')[0]
    claims = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    assert claims['platform_admin'] is False
    assert claims['sub'] != 999


def test_deleted_instructions_contract_preserves_upstream_authorization_result(proxy, monkeypatch):
    client, calls = proxy
    assert client.get(DELETED_INSTRUCTIONS_PATH).status_code == 200
    assert len(calls) == 1
    assert calls[0].method == 'GET'
    assert calls[0].url.path == f'/departments/{INSTRUCTION_ID}/deleted-instructions'

    class DenyingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, method, url, **kwargs):
            calls.append(httpx.Request(method, url, **kwargs))
            return httpx.Response(403, json={'detail': 'Недостаточно прав для просмотра удалённых документов'})

    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', lambda **kwargs: DenyingClient())
    response = client.get(DELETED_INSTRUCTIONS_PATH)
    assert response.status_code == 403
    assert response.json()['detail'] == 'Недостаточно прав для просмотра удалённых документов'
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 2


@pytest.mark.parametrize('path', [
    '/api/v1/knowledge/instructions/not-a-uuid/delete',
    '/api/v1/knowledge/instructions/00000000-0000-0000-0000-00000000000/delete',
    '/api/v1/knowledge/instructions/gggggggg-0000-0000-0000-000000000001/restore',
    f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete/restore',
    f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/restore/extra',
    f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete-all',
    f'/api/v1/knowledge/instruction-versions/{INSTRUCTION_ID}/delete',
    f'/api/v1/knowledge/departments/{INSTRUCTION_ID}/delete',
])
def test_instruction_lifecycle_contract_rejects_non_exact_path(proxy, path):
    client, calls = proxy
    assert client.post(path, json={'reason': 'Проверка'}).status_code == 404
    assert calls == []


@pytest.mark.parametrize('path', [
    '/api/v1/knowledge/departments/not-a-uuid/deleted-instructions',
    f'/api/v1/knowledge/departments/{INSTRUCTION_ID}/deleted-instructions/extra',
    f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/deleted-instructions',
    f'/api/v1/knowledge/departments/{INSTRUCTION_ID}/archived-instructions',
])
def test_deleted_instructions_contract_rejects_non_exact_path(proxy, path):
    client, calls = proxy
    assert client.get(path).status_code == 404
    assert calls == []


@pytest.mark.parametrize('method,path,status', [
    ('GET', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete', 404),
    ('GET', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/restore', 404),
    ('POST', DELETED_INSTRUCTIONS_PATH, 404),
    ('PATCH', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete', 404),
    ('PATCH', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/restore', 404),
    ('PATCH', DELETED_INSTRUCTIONS_PATH, 404),
    ('DELETE', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete', 405),
    ('PUT', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/restore', 405),
])
def test_instruction_lifecycle_contract_rejects_wrong_method(proxy, method, path, status):
    client, calls = proxy
    assert client.request(method, path).status_code == status
    assert calls == []


@pytest.mark.parametrize('method,path', [
    ('POST', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/delete'),
    ('POST', f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/restore'),
    ('GET', DELETED_INSTRUCTIONS_PATH),
])
@pytest.mark.parametrize('query', ['?user_id=999', '?department_id=forged', '?purge=true'])
def test_instruction_lifecycle_contract_rejects_query_overrides(proxy, method, path, query):
    client, calls = proxy
    assert client.request(method, path + query).status_code == 404
    assert calls == []


@pytest.mark.parametrize('action', ['delete', 'restore'])
def test_instruction_lifecycle_origin_guard_keeps_host_and_port_boundary(proxy, action):
    client, calls = proxy
    path = f'/api/v1/knowledge/instructions/{INSTRUCTION_ID}/{action}'
    headers = {'Host': '127.0.0.1:8080', 'Origin': 'http://127.0.0.1:8080'}
    assert client.post(path, json={'reason': 'Проверка'}, headers=headers).status_code == 200
    for origin in (
        'https://evil.example', 'http://127.0.0.1:8081', 'http://localhost:8080',
        'http://user@127.0.0.1:8080', 'http://127.0.0.1:8080/path',
        'http://127.0.0.1:8080?forged=1', 'http://127.0.0.1:8080#forged',
        'http://[broken',
    ):
        assert client.post(path, json={'reason': 'Проверка'}, headers={**headers, 'Origin': origin}).status_code == 403
    assert len(calls) == 1


def test_document_list_filters_are_in_the_signed_target(proxy):
    client, calls = proxy
    query = f'library_scope=department&department_id={INSTRUCTION_ID}&document_type=procedure&view=unverified'
    response = client.get('/api/v1/knowledge/documents?' + query)
    assert response.status_code == 200
    assert calls[0].url.query.decode() == query
    raw = calls[0].headers['authorization'][7:].split('.')[0]
    claims = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    assert claims['target'] == '/documents?' + query


@pytest.mark.parametrize('query', [
    'user_id=999', 'library_scope=department&library_scope=bank', 'department_id=forged',
    'view=purge', 'view=', 'document_type=unknown', 'library_scope=bank&platform_admin=true',
])
def test_document_filters_cannot_override_identity_or_contract(proxy, query):
    client, calls = proxy
    assert client.get('/api/v1/knowledge/documents?' + query).status_code == 404
    assert calls == []


def test_bank_library_requires_actual_administrator(proxy):
    client, calls = proxy
    assert client.get('/api/v1/knowledge/documents?library_scope=bank&view=all', headers={'X-Platform-Admin': 'true'}).status_code == 403
    assert calls == []
    previous = app.dependency_overrides[current_user]
    app.dependency_overrides[current_user] = lambda: User(id=3, email='admin@sqb.uz', name='Администратор', isAdmin=True)
    try:
        assert client.get('/api/v1/knowledge/documents?library_scope=bank&view=all').status_code == 200
        assert len(calls) == 1
    finally:
        app.dependency_overrides[current_user] = previous


@pytest.mark.parametrize('method,path', [
    ('GET', 'documents/types'), ('GET', f'documents/{INSTRUCTION_ID}'),
    ('PATCH', f'documents/{INSTRUCTION_ID}'), ('POST', 'documents/upload'),
    ('POST', f'documents/{INSTRUCTION_ID}/editions'),
    ('POST', f'documents/{INSTRUCTION_ID}/editions/{INSTRUCTION_ID}/verify'),
    ('POST', f'documents/{INSTRUCTION_ID}/editions/{INSTRUCTION_ID}/end-validity'),
    ('POST', f'documents/{INSTRUCTION_ID}/delete'), ('POST', f'documents/{INSTRUCTION_ID}/restore'),
    ('GET', f'documents/files/{INSTRUCTION_ID}/original'),
    ('GET', f'documents/files/{INSTRUCTION_ID}/preview'),
    ('GET', f'documents/files/{INSTRUCTION_ID}/preparation'),
    ('POST', f'documents/files/{INSTRUCTION_ID}/preparation'),
    ('POST', f'documents/files/{INSTRUCTION_ID}/preparation/cancel'),
    ('DELETE', f'documents/files/{INSTRUCTION_ID}/preparation'),
])
def test_universal_document_routes_forward_signed_session(proxy, method, path):
    client, calls = proxy
    assert client.request(method, '/api/v1/knowledge/' + path, headers={'Origin': 'http://testserver'}).status_code == 200
    assert len(calls) == 1
    assert calls[0].url.path == '/' + path
    if method != 'GET':
        assert client.request(method, '/api/v1/knowledge/' + path, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert len(calls) == 1


@pytest.mark.parametrize('path', [
    'documents/types', f'documents/{INSTRUCTION_ID}', f'documents/files/{INSTRUCTION_ID}/original',
    f'documents/files/{INSTRUCTION_ID}/preparation', f'documents/files/{INSTRUCTION_ID}/preview',
])
def test_document_detail_routes_reject_query_overrides(proxy, path):
    client, calls = proxy
    assert client.get('/api/v1/knowledge/' + path + '?department_id=' + INSTRUCTION_ID).status_code == 404
    assert calls == []


def test_multiple_file_upload_has_separate_bounded_body_limit(proxy):
    client, calls = proxy
    # The service validates each file. The proxy allows a multi-file envelope,
    # while keeping the old single-file bound and all non-upload routes bounded.
    body = b'x' * (11 * 1024 * 1024)
    assert client.post('/api/v1/knowledge/documents/upload', content=body).status_code == 200
    assert client.post(f'/api/v1/knowledge/documents/{INSTRUCTION_ID}/editions', content=body).status_code == 200
    assert client.post('/api/v1/knowledge/instructions/upload', content=body).status_code == 413
    assert client.patch(f'/api/v1/knowledge/documents/{INSTRUCTION_ID}', content=body).status_code == 413
    oversized = b'x' * (42 * 1024 * 1024)
    assert client.post('/api/v1/knowledge/documents/upload', content=oversized).status_code == 413
    assert len(calls) == 2


@pytest.mark.parametrize('method,path', [
    ('GET', 'nova/status'), ('GET', f'documents/{INSTRUCTION_ID}/analysis-runs'),
    ('POST', f'documents/{INSTRUCTION_ID}/analysis-runs'), ('GET', f'analysis-runs/{INSTRUCTION_ID}'),
    ('POST', f'analysis-runs/{INSTRUCTION_ID}/cancel'), ('POST', f'analysis-runs/{INSTRUCTION_ID}/reviews'),
    ('PATCH', f'departments/{INSTRUCTION_ID}/nova-binding'),
])
def test_analysis_routes_forward_signed_session(proxy, method, path):
    client, calls = proxy
    assert client.request(method, '/api/v1/knowledge/' + path, headers={'Origin': 'http://testserver'}).status_code == 200
    assert calls[-1].url.path == '/' + path
    assert client.request(method, '/api/v1/knowledge/' + path + '/extra').status_code in (404, 405)


def test_preview_accepts_only_the_view_choice(proxy):
    client, calls = proxy
    base = f'/api/v1/knowledge/documents/files/{INSTRUCTION_ID}/preview'
    for query in ('?view=clean', '?view=markup'):
        assert client.get(base + query).status_code == 200
        assert calls[-1].url.query.decode() == query[1:]
    for query in ('?view=raw', '?view=clean&user_id=2', '?user_id=2'):
        assert client.get(base + query).status_code == 404
