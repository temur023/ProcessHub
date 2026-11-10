import base64
import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import knowledge
from app.services.auth import current_user
from app.services.store import User, get_store

BASE='/api/v1/knowledge'
PATH=BASE+'/departments/00000000-0000-0000-0000-000000000001/access'


@pytest.fixture
def access_proxy(monkeypatch):
    target=get_store().create_user('access-'+uuid4().hex+'@sqb.uz','Сотрудник для назначения','private-password-hash')
    calls=[]
    original=httpx.AsyncClient
    def respond(request):
        calls.append(request)
        if request.method=='GET':
            return httpx.Response(200,json=[{'user_id':target.id,'permissions':['reader'],'granted_at':'2026-10-07T12:00:00Z'}])
        return httpx.Response(200,json={'changed':1})
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET','test-secret-'+'z'*50)
    monkeypatch.setenv('SQB_KNOWLEDGE_URL','http://knowledge.test')
    monkeypatch.setattr(knowledge.httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(respond)))
    previous=app.dependency_overrides[current_user]
    try:
        yield TestClient(app),target,calls
    finally:
        app.dependency_overrides[current_user]=previous


def admin():
    app.dependency_overrides[current_user]=lambda:User(id=987,email='admin@sqb.uz',name='Администратор',isAdmin=True)


def test_ordinary_users_cannot_enumerate_accounts_or_assign_access(access_proxy):
    client,target,calls=access_proxy
    assert client.post(BASE+'/admin/users/lookup',json={'email':target.email}).status_code==403
    assert client.get(PATH).status_code==403
    assert client.get(PATH.replace('/access','/audit')).status_code==403
    assert client.post(PATH,json={'user_id':target.id,'permission':'owner','enabled':True,'reason':'Основание'}).status_code==403
    assert calls==[]


def test_exact_lookup_returns_only_public_profile(access_proxy):
    client,target,calls=access_proxy
    admin()
    result=client.post(BASE+'/admin/users/lookup',json={'email':' '+target.email.upper()+' '})
    assert result.status_code==200,result.text
    assert result.json()==target.model_dump()
    assert 'private-password-hash' not in result.text
    assert result.headers['cache-control']=='no-store'
    assert client.post(BASE+'/admin/users/lookup',json={'email':'*@sqb.uz'}).status_code==404
    assert client.post(BASE+'/admin/users/lookup',json={'email':target.email,'user_id':999}).status_code==422
    assert calls==[]


def test_verified_account_required_for_grants_and_revokes(access_proxy):
    client,target,calls=access_proxy
    admin()
    data={'user_id':target.id,'permission':'owner','enabled':True,'reason':'Загрузка инструкций'}
    assert client.post(PATH,json={**data,'user_id':9999999}).status_code==404
    assert client.post(PATH,json={**data,'user_id':True}).status_code==422
    assert client.post(PATH,json={**data,'enabled':'true'}).status_code==422
    assert client.post(PATH,json={**data,'platform_admin':True}).status_code==422
    assert client.post(PATH,json=data,headers={'Origin':'https://evil.example'}).status_code==403
    assert calls==[]
    assert client.post(PATH,json=data).status_code==200
    assert len(calls)==1
    assert client.get(PATH).json()[0]['user']==target.model_dump()
    assert 'private-password-hash' not in client.get(PATH).text


def test_local_assignments_do_not_change_global_admin(access_proxy):
    client,target,calls=access_proxy
    get_store().set_platform_admin(target.email,True,'Тестовый администратор')
    admin()
    assert client.post(PATH,json={'user_id':target.id,'permission':'all','enabled':False,'reason':'Отзыв'}).status_code==409
    assert calls==[]


ROLE_ID = '00000000-0000-0000-0000-000000000002'
ROLE_PATH = BASE + '/access-roles/' + ROLE_ID
MEMBERSHIPS_PATH = BASE + '/access-role-memberships'
ROLE_AUDIT_PATH = BASE + '/access-role-audit'
ROLE_SECRET = 'role-proxy-test-secret-' + 'r' * 40


@pytest.fixture
def role_proxy(monkeypatch):
    """Actual isolated Hub accounts, with only the private-service HTTP mocked."""
    store = get_store()
    target = store.create_user('role-target-' + uuid4().hex + '@sqb.uz', 'Читатель документов', 'private-role-password-hash')
    actor = store.create_user('role-admin-' + uuid4().hex + '@sqb.uz', 'Администратор ролей', 'private-admin-password-hash')
    store.set_platform_admin(actor.email, True, 'Тестовый администратор')
    actor = store.find_user(actor.email)[0]
    calls = []
    replies = {}
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        payload, signature = request.headers['authorization'][7:].split('.')
        assert hmac.compare_digest(signature, hmac.new(ROLE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest())
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        assert claims['sub'] == actor.id
        assert claims['platform_admin'] is True
        assert claims['method'] == request.method
        assert claims['target'] == request.url.path
        assert claims['body'] == hashlib.sha256(request.content).hexdigest()
        assert 'cookie' not in request.headers
        status, body = replies.get((request.method, request.url.path), (200, [] if request.method == 'GET' else {'changed': True}))
        return httpx.Response(status, json=body, headers={'set-cookie': 'upstream-session=discard'})

    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', ROLE_SECRET)
    monkeypatch.setenv('SQB_KNOWLEDGE_URL', 'http://knowledge.test')
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', lambda **options: original_client(**options, transport=httpx.MockTransport(respond)))
    previous = app.dependency_overrides[current_user]
    app.dependency_overrides[current_user] = lambda: actor
    try:
        yield TestClient(app), target, actor, calls, replies
    finally:
        app.dependency_overrides[current_user] = previous


@pytest.mark.parametrize('method,path', [
    ('GET', BASE + '/access-roles'),
    ('POST', BASE + '/access-roles'),
    ('PATCH', ROLE_PATH),
    ('GET', MEMBERSHIPS_PATH),
    ('POST', MEMBERSHIPS_PATH),
    ('GET', ROLE_AUDIT_PATH),
])
def test_access_role_endpoints_are_admin_only_before_upstream_call(role_proxy, method, path):
    client, target, _, calls, _ = role_proxy
    app.dependency_overrides[current_user] = lambda: target
    response = client.request(method, path, headers={'X-Platform-Admin': 'true', 'X-User-ID': '999'}, json={})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize('path', [
    BASE + '/access-roles',
    BASE + '/access-roles/not-a-uuid',
    ROLE_PATH + '/members',
    ROLE_PATH + '?user_id=999',
    MEMBERSHIPS_PATH,
    MEMBERSHIPS_PATH + '/' + ROLE_ID,
    ROLE_AUDIT_PATH,
    PATH.replace('/access', '/roles'),
])
def test_patch_contract_cannot_escape_exact_access_role_uuid_path(role_proxy, path):
    client, _, _, calls, _ = role_proxy
    assert client.patch(path, json={}).status_code == 404
    assert calls == []


def test_role_patch_preserves_payload_and_concurrency_conflict(role_proxy):
    client, _, _, calls, replies = role_proxy
    body = {'name': 'Владельцы кредитных инструкций', 'permission': 'owner',
            'department_ids': ['00000000-0000-0000-0000-000000000001'],
            'reason': 'Назначение владельцев', 'expected_updated_at': '2026-10-07T12:00:00Z'}
    replies[('PATCH', '/access-roles/' + ROLE_ID)] = (409, {'detail': 'Роль уже изменена другим администратором'})
    response = client.patch(ROLE_PATH, json=body, headers={'Origin': 'http://testserver'})
    assert response.status_code == 409
    assert response.json()['detail'] == 'Роль уже изменена другим администратором'
    assert len(calls) == 1
    assert calls[0].method == 'PATCH'
    assert json.loads(calls[0].content) == body
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('origin', [
    'https://evil.example', 'http://127.0.0.1:8081', 'http://localhost:8080',
    'http://127.0.0.1:8080/path', 'http://127.0.0.1:8080?other=1',
    'http://127.0.0.1:8080#fragment', 'http://user@127.0.0.1:8080',
    'ftp://127.0.0.1:8080', 'null',
])
def test_patch_origin_rejects_cross_origin_and_non_origin_urls(role_proxy, origin):
    client, _, _, calls, _ = role_proxy
    assert client.patch(ROLE_PATH, json={}, headers={'Host': '127.0.0.1:8080', 'Origin': origin}).status_code == 403
    assert calls == []


def test_patch_accepts_exact_local_host_and_port(role_proxy):
    client, _, _, calls, _ = role_proxy
    assert client.patch(ROLE_PATH, json={}, headers={'Host': '127.0.0.1:8080', 'Origin': 'http://127.0.0.1:8080'}).status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize('override', [
    {'user_id': True}, {'user_id': 0}, {'user_id': -1}, {'user_id': '1'},
    {'user_id': 1.5}, {'enabled': 'true'}, {'role_id': 'not-a-uuid'},
    {'email': 'forged@sqb.uz'}, {'platform_admin': True},
])
def test_role_membership_rejects_ambiguous_or_forged_account_inputs(role_proxy, override):
    client, target, _, calls, _ = role_proxy
    body = {'role_id': ROLE_ID, 'user_id': target.id, 'enabled': True, 'reason': 'Работа с документами'}
    assert client.post(MEMBERSHIPS_PATH, json={**body, **override}).status_code == 422
    assert calls == []


@pytest.mark.parametrize('enabled', [True, False])
def test_role_membership_requires_existing_exact_hub_account_for_grant_and_revoke(role_proxy, enabled):
    client, _, _, calls, _ = role_proxy
    assert client.post(MEMBERSHIPS_PATH, json={'role_id': ROLE_ID, 'user_id': 9999999,
        'enabled': enabled, 'reason': 'Назначение сотруднику'}).status_code == 404
    assert calls == []


@pytest.mark.parametrize('enabled', [True, False])
def test_role_membership_cannot_change_global_admin_permissions(role_proxy, enabled):
    client, _, actor, calls, _ = role_proxy
    assert client.post(MEMBERSHIPS_PATH, json={'role_id': ROLE_ID, 'user_id': actor.id,
        'enabled': enabled, 'reason': 'Локальное назначение'}).status_code == 409
    assert calls == []
    assert get_store().find_user(actor.email)[0].isAdmin is True


@pytest.mark.parametrize('enabled', [True, False])
def test_role_membership_for_verified_non_admin_is_signed_and_forwarded(role_proxy, enabled):
    client, target, _, calls, _ = role_proxy
    body = {'role_id': ROLE_ID, 'user_id': target.id, 'enabled': enabled, 'reason': 'Назначение по запросу владельца'}
    response = client.post(MEMBERSHIPS_PATH, json=body, headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    assert len(calls) == 1
    assert json.loads(calls[0].content) == body
    assert get_store().find_user(target.email)[0].isAdmin is False


def test_membership_list_enriches_existing_accounts_and_keeps_unknown_accounts_explicit(role_proxy):
    client, target, _, _, replies = role_proxy
    replies[('GET', '/access-role-memberships')] = (200, [
        {'role_id': ROLE_ID, 'user_id': target.id, 'role_name': 'Читатели', 'permission': 'reader',
         'user': {'name': 'Чужое имя', 'password_hash': 'upstream-secret'}},
        {'role_id': ROLE_ID, 'user_id': 9999999, 'role_name': 'Читатели', 'permission': 'reader'},
    ])
    response = client.get(MEMBERSHIPS_PATH)
    assert response.status_code == 200
    rows = response.json()
    assert rows[0]['user'] == target.model_dump()
    assert rows[1]['user'] is None
    assert 'password_hash' not in response.text
    assert 'private-role-password-hash' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_access_role_list_without_person_fields_preserves_role_metadata(role_proxy):
    client, _, _, _, replies = role_proxy
    body = [{'id': ROLE_ID, 'name': 'Владельцы документов', 'permission': 'owner',
             'department_ids': ['00000000-0000-0000-0000-000000000001'], 'member_count': 3}]
    replies[('GET', '/access-roles')] = (200, body)
    response = client.get(BASE + '/access-roles')
    assert response.status_code == 200
    assert response.json() == body


def test_role_audit_with_null_subject_enriches_actor_without_invalid_account_lookup(role_proxy, monkeypatch):
    client, target, actor, _, replies = role_proxy
    store = get_store()
    original_public_users = store.public_users
    looked_up = []

    def public_users(ids):
        looked_up.extend(ids)
        assert all(type(value) is int and value > 0 for value in ids)
        return original_public_users(ids)

    monkeypatch.setattr(store, 'public_users', public_users)
    replies[('GET', '/access-role-audit')] = (200, [
        {'id': 2, 'role_id': ROLE_ID, 'user_id': None, 'actor_id': actor.id,
         'action': 'access_role.created', 'details': {'user_id': None, 'reason': 'Создание роли'}},
        {'id': 1, 'role_id': ROLE_ID, 'user_id': target.id, 'actor_id': actor.id,
         'action': 'access_role.member.assigned', 'details': {'reason': 'Назначение роли'}},
    ])
    response = client.get(ROLE_AUDIT_PATH)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows[0]['user_id'] is None
    assert rows[0]['user'] is None
    assert rows[0]['actor'] == actor.model_dump()
    assert rows[1]['user'] == target.model_dump()
    assert rows[1]['actor'] == actor.model_dump()
    assert set(looked_up) == {actor.id, target.id}
    assert 'password_hash' not in response.text


def test_department_audit_handles_null_role_subject_and_enriches_member_subject(role_proxy):
    client, target, actor, _, replies = role_proxy
    audit_path = PATH.replace('/access', '/audit')
    upstream_path = audit_path.removeprefix(BASE)
    replies[('GET', upstream_path)] = (200, [
        {'id': 2, 'actor_id': actor.id, 'action': 'access_role.scope.changed', 'details': {'user_id': None}},
        {'id': 1, 'actor_id': actor.id, 'action': 'access_role.member.assigned', 'details': {'user_id': target.id}},
    ])
    response = client.get(audit_path)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows[0]['actor'] == actor.model_dump()
    assert rows[0].get('subject') is None
    assert rows[1]['actor'] == actor.model_dump()
    assert rows[1]['subject'] == target.model_dump()
