"""Action authorization and reversible deletion, using isolated PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from conftest import URL
from knowledge_service.auth import Principal
from knowledge_service.extraction import extract_document
from knowledge_service.permissions import CAPABILITIES, PRESETS
from knowledge_service.preparation_store import claim_job, complete_job, heartbeat
from knowledge_service.store import Store, migrate
from knowledge_service import registry_store
from test_access_roles import create_role, member, role_data
from test_preparation import db, path
from test_registry import docx, send
from test_upload_workflow import upload


def granular_role(registry, capabilities, *, user=4, name='Явные права', permission='owner'):
    result = send(registry,'POST','/access-roles',actor=3,admin=True,
                  json={**role_data(registry,name=name,permission=permission,scopes=[registry[2]]),
                        'capabilities':capabilities})
    assert result.status_code == 201,result.text
    role = result.json()
    assert member(registry,role['id'],user=user).status_code == 200
    return role


def lifecycle(registry, instruction, action, *, actor=3, admin=True, reason='Исправление состава документов'):
    return send(registry,'POST',f'/instructions/{instruction}/{action}',actor=actor,admin=admin,
                json={'reason':reason})


def trash(registry, *, actor=3, admin=True):
    return send(registry,'GET',f'/departments/{registry[2]}/deleted-instructions',actor=actor,admin=admin)


def test_explicit_read_only_owner_does_not_inherit_upload_or_download(registry):
    role = granular_role(registry,['documents.read'])
    assert role['capabilities'] == ['documents.read']
    departments = send(registry,'GET','/departments',actor=4).json()
    assert departments[0]['permissions'] == ['owner']
    assert departments[0]['capabilities'] == ['documents.read']
    memberships = send(registry,'GET','/access-role-memberships',actor=3,admin=True).json()
    assert memberships[0]['capabilities'] == ['documents.read']
    instruction = upload(registry).json()
    version = instruction['version']['id']
    assert send(registry,'GET',f'/departments/{registry[2]}/instructions',actor=4).status_code == 200
    assert send(registry,'GET',path(version),actor=4).status_code == 200
    assert send(registry,'GET',f'/instruction-versions/{version}/original',actor=4).status_code == 403
    assert upload(registry,actor=4,title='Denied').status_code == 403
    assert send(registry,'POST',f"/instructions/{instruction['instruction']['id']}/versions",actor=4,
                files={'file':('v2.docx',docx('Новая редакция'))}).status_code == 403
    assert send(registry,'POST',path(version),actor=4).status_code == 403
    assert lifecycle(registry,instruction['instruction']['id'],'delete',actor=4,admin=False).status_code == 403
    assert trash(registry,actor=4,admin=False).status_code == 403


def test_create_and_versions_are_independent_and_download_is_optional(registry):
    creator = granular_role(registry,['documents.read','instructions.create'],name='Создатель')
    first = upload(registry,actor=4,title='Создано отдельным правом')
    assert first.status_code == 201,first.text
    instruction, version = first.json()['instruction']['id'],first.json()['version']['id']
    assert send(registry,'GET',path(version),actor=4).json()['job']['status'] == 'queued'
    assert send(registry,'POST',f'/instructions/{instruction}/versions',actor=4,
                files={'file':('new.docx',docx('Попытка новой редакции'))}).status_code == 403
    assert send(registry,'POST',path(version),actor=4).status_code == 403
    assert send(registry,'GET',f'/instruction-versions/{version}/original',actor=4).status_code == 403
    editor = granular_role(registry,['documents.read','versions.create'],user=5,name='Редакции')
    assert upload(registry,actor=5,title='Нельзя создать инструкцию').status_code == 403
    new = send(registry,'POST',f'/instructions/{instruction}/versions',actor=5,
               files={'file':('new.docx',docx('Разрешённая новая редакция'))})
    assert new.status_code == 201,new.text
    assert new.json()['number'] == 2
    assert send(registry,'GET',f"/instruction-versions/{new.json()['id']}/original",actor=5).status_code == 403
    assert creator['capabilities'] != editor['capabilities']


def test_capabilities_union_keeps_direct_grants_and_revokes_only_selected_role(registry):
    role = granular_role(registry,['documents.read','instructions.delete'],user=1)
    other = granular_role(registry,['documents.read','instructions.restore'],user=1,name='Восстановление')
    union = send(registry,'GET','/departments').json()[0]['capabilities']
    assert union == list(PRESETS['owner']) + ['instructions.delete','instructions.restore']
    assert 'comparisons.manage' not in union
    assert member(registry,role['id'],user=1,enabled=False).status_code == 200
    reduced = send(registry,'GET','/departments').json()[0]['capabilities']
    assert 'instructions.delete' not in reduced and 'instructions.restore' in reduced
    assert set(PRESETS['owner']).issubset(reduced)
    assert member(registry,other['id'],user=1,enabled=False).status_code == 200
    assert send(registry,'GET','/departments').json()[0]['capabilities'] == list(PRESETS['owner'])
    assert send(registry,'GET','/departments',actor=3,admin=True).json()[0]['capabilities'] == list(CAPABILITIES)


@pytest.mark.parametrize('capabilities', [[],['unknown'],['documents.download'],
    ['documents.read','documents.read'],['documents.read','versions.create','alien']])
def test_invalid_capabilities_never_create_a_role(registry,capabilities):
    response = send(registry,'POST','/access-roles',actor=3,admin=True,
                    json={**role_data(registry),'capabilities':capabilities})
    assert response.status_code == 422,response.text
    assert send(registry,'GET','/access-roles',actor=3,admin=True).json() == []


def test_capabilities_change_has_cas_and_immutable_audit(registry):
    role = granular_role(registry,['documents.read','versions.create'])
    payload = {**role_data(registry,scopes=[registry[2]]),
               'name':role['name'],'expected_updated_at':role['updated_at'],
               'capabilities':['documents.read','documents.download']}
    changed = send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,json=payload)
    assert changed.status_code == 200,changed.text
    assert changed.json()['capabilities'] == ['documents.read','documents.download']
    assert send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
                json={**payload,'capabilities':['documents.read','instructions.delete']}).status_code == 409
    audit = send(registry,'GET','/access-role-audit',actor=3,admin=True).json()
    event = next(item for item in audit if item['action'] == 'access_role.updated')
    assert event['details']['previous_capabilities'] == ['documents.read','versions.create']
    assert event['details']['capabilities'] == ['documents.read','documents.download']
    assert send(registry,'GET','/departments',actor=4).json()[0]['capabilities'] == ['documents.read','documents.download']


@pytest.mark.parametrize('include_null',[False,True])
def test_legacy_patch_never_expands_explicit_capabilities(registry,include_null):
    role = granular_role(registry,['documents.read'])
    payload = {**role_data(registry,name='Переименовано',permission='owner',scopes=[registry[2]]),
               'expected_updated_at':role['updated_at']}
    if include_null:
        payload['capabilities'] = None
    result = send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,json=payload)
    assert result.status_code == 200,result.text
    assert result.json()['capabilities'] == ['documents.read']
    assert upload(registry,actor=4,title='Не расширять права').status_code == 403


def test_presets_never_grant_delete_and_reasons_are_mandatory(registry):
    instruction = upload(registry).json()['instruction']['id']
    for permission in ('owner','reader','reviewer'):
        role = create_role(registry,name=permission,permission=permission,scopes=[registry[2]])
        assert role['capabilities'] == list(PRESETS[permission])
        assert 'instructions.delete' not in role['capabilities']
    assert lifecycle(registry,instruction,'delete',actor=1,admin=False).status_code == 403
    for action in ('delete','restore'):
        for data in ({},{'reason':'   '},{'reason':'x'*301},{'reason':'ok','force':True}):
            assert send(registry,'POST',f'/instructions/{instruction}/{action}',actor=3,admin=True,json=data).status_code == 422


def test_delete_restore_preserves_history_and_denies_all_active_endpoints(registry):
    upload_id = str(uuid4())
    uploaded = upload(registry,upload_id=upload_id).json()
    instruction, version = uploaded['instruction']['id'],uploaded['version']['id']
    deleted = lifecycle(registry,instruction,'delete',reason='  Устранение   дубликата ')
    assert deleted.status_code == 200,deleted.text
    state = deleted.json()
    assert state['id'] == instruction and state['deleted_by'] == 3 and state['deleted_at']
    assert send(registry,'GET',f'/departments/{registry[2]}/instructions').json() == []
    item = trash(registry).json()[0]
    assert item['title'] == uploaded['instruction']['title'] and item['version_count'] == 1
    assert item['role_name'] == 'Главный специалист' and item['deleted_by'] == 3
    for actor,admin in ((1,False),(2,False),(3,True)):
        for target in (f'/instructions/{instruction}/versions',f'/instruction-versions/{version}/original',path(version)):
            assert send(registry,'GET',target,actor=actor,admin=admin).status_code == 404
    for target in (path(version),path(version)+'/cancel'):
        assert send(registry,'POST',target,actor=3,admin=True).status_code == 404
    assert send(registry,'POST',f'/instructions/{instruction}/versions',actor=3,admin=True,
                files={'file':('v2.docx',docx('После удаления'))}).status_code == 404
    assert upload(registry,upload_id=upload_id).status_code == 404
    duplicate = upload(registry)
    assert duplicate.status_code == 409 and 'корзине' in duplicate.json()['detail']
    assert lifecycle(registry,instruction,'delete').json() == state
    restored = lifecycle(registry,instruction,'restore')
    assert restored.json() == {'id':instruction,'deleted_at':None,'deleted_by':None}
    assert trash(registry).json() == []
    assert send(registry,'GET',f'/instruction-versions/{version}/original').content == docx()
    assert len(send(registry,'GET',f'/instructions/{instruction}/versions').json()) == 1
    assert upload(registry,upload_id=upload_id).json()['replayed']
    assert lifecycle(registry,instruction,'restore').json() == restored.json()
    with psycopg.connect(registry[1].database_url) as conn:
        events = conn.execute("SELECT action,details FROM audit_events WHERE action IN ('instruction.deleted','instruction.restored') ORDER BY id").fetchall()
        assert [event[0] for event in events] == ['instruction.deleted','instruction.restored']
        assert events[0][1]['reason'] == 'Устранение дубликата'
        assert conn.execute('SELECT count(*) FROM instruction_versions').fetchone()[0] == 1


def test_delete_and_restore_rights_independent_and_trash_scope_not_global(registry):
    granular_role(registry,['documents.read','instructions.delete'],user=4,name='Удаление')
    granular_role(registry,['documents.read','instructions.restore'],user=5,name='Восстановление')
    instruction = upload(registry).json()['instruction']['id']
    assert lifecycle(registry,instruction,'delete',actor=4,admin=False).status_code == 200
    assert trash(registry,actor=4,admin=False).json()[0]['id'] == instruction
    assert trash(registry,actor=5,admin=False).json()[0]['id'] == instruction
    assert send(registry,'GET',f'/departments/{registry[4]}/deleted-instructions',actor=4).status_code == 404
    assert lifecycle(registry,instruction,'restore',actor=4,admin=False).status_code == 403
    assert lifecycle(registry,instruction,'restore',actor=5,admin=False).status_code == 200
    assert lifecycle(registry,instruction,'delete',actor=5,admin=False).status_code == 403


def test_archive_cancels_running_and_queued_jobs_and_fences_after_restore(registry):
    uploaded = upload(registry).json()
    instruction = uploaded['instruction']['id']
    first = claim_job(db(registry))
    assert complete_job(db(registry),first['id'],first['lease_token'],
                        extract_document(bytes(first['content']),first['media_type']))
    second = send(registry,'POST',f'/instructions/{instruction}/versions',files={'file':('v2.docx',docx('Вторая редакция'))}).json()
    running = claim_job(db(registry))
    result = extract_document(bytes(running['content']),running['media_type'])
    third = send(registry,'POST',f'/instructions/{instruction}/versions',files={'file':('v3.docx',docx('Третья редакция'))}).json()
    assert lifecycle(registry,instruction,'delete').status_code == 200
    assert not heartbeat(db(registry),running['id'],running['lease_token'])
    assert not complete_job(db(registry),running['id'],running['lease_token'],result)
    assert claim_job(db(registry)) is None
    assert lifecycle(registry,instruction,'restore').status_code == 200
    assert not complete_job(db(registry),running['id'],running['lease_token'],result)
    assert send(registry,'GET',path(second['id'])).json()['job']['status'] == 'cancelled'
    assert send(registry,'GET',path(third['id'])).json()['job']['status'] == 'cancelled'
    assert send(registry,'GET',path(uploaded['version']['id'])).json()['source'] is not None
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM prepared_sources').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM instruction_versions').fetchone()[0] == 3
    assert send(registry,'POST',path(second['id'])).status_code == 202
    new = claim_job(db(registry))
    assert new['lease_token'] != running['lease_token']
    assert complete_job(db(registry),new['id'],new['lease_token'],result)


@pytest.mark.parametrize('operation',['delete','restore'])
def test_capability_revoked_before_department_lock_prevents_lifecycle_write(registry,monkeypatch,operation):
    role = granular_role(registry,['documents.read','instructions.delete','instructions.restore'])
    instruction = upload(registry).json()['instruction']['id']
    if operation == 'restore':
        assert lifecycle(registry,instruction,'delete').status_code == 200
    paused, proceed = Event(), Event()
    original = db(registry).authorize
    seen = False

    def authorization_hook(conn,actor,department_id,write=False,capability=None):
        nonlocal seen
        original(conn,actor,department_id,write,capability=capability)
        if actor.id == 4 and capability == f'instructions.{operation}' and not seen:
            seen = True
            paused.set()
            assert proceed.wait(5)

    monkeypatch.setattr(db(registry),'authorize',authorization_hook)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(lifecycle,registry,instruction,operation,actor=4,admin=False)
        assert paused.wait(5)
        revoked = send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
            json={**role_data(registry,name=role['name'],scopes=[registry[2]]),
                  'capabilities':['documents.read'],'expected_updated_at':role['updated_at']})
        assert revoked.status_code == 200,revoked.text
        proceed.set()
        assert pending.result().status_code == 403
    assert bool(trash(registry).json()) is (operation == 'restore')


def test_restore_detects_legacy_collision_without_changing_originals(registry):
    uploaded = upload(registry).json()
    instruction = uploaded['instruction']['id']
    assert lifecycle(registry,instruction,'delete').status_code == 200
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('INSERT INTO instructions(id,department_id,role_id,title,created_by) VALUES (%s,%s,%s,%s,1)',
                     (uuid4(),registry[2],registry[3],uploaded['instruction']['title']))
    assert lifecycle(registry,instruction,'restore').status_code == 409
    assert trash(registry).json()[0]['id'] == instruction


@pytest.mark.parametrize('operation',['first_replay','version_new','version_replay'])
def test_archive_committed_while_upload_waits_prevents_write_and_receipt_replay(registry,monkeypatch,operation):
    first_key = str(uuid4())
    uploaded = upload(registry,upload_id=first_key).json()
    instruction = uploaded['instruction']['id']
    version_key = str(uuid4())

    def version_upload():
        return send(registry,'POST',f'/instructions/{instruction}/versions',
                    data={'upload_id':version_key},files={'file':('v2.docx',docx('Вторая редакция'))})

    if operation == 'version_replay':
        assert version_upload().status_code == 201
    with psycopg.connect(registry[1].database_url) as conn:
        versions_before = conn.execute('SELECT count(*) FROM instruction_versions').fetchone()[0]
    paused, proceed = Event(), Event()
    original_receipt = registry_store._receipt

    def receipt_hook(*args,**kwargs):
        receipt = original_receipt(*args,**kwargs)
        paused.set()
        assert proceed.wait(5)
        return receipt

    monkeypatch.setattr(registry_store,'_receipt',receipt_hook)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(lambda:upload(registry,upload_id=first_key) if operation == 'first_replay' else version_upload())
        assert paused.wait(5)
        assert lifecycle(registry,instruction,'delete').status_code == 200
        proceed.set()
        denied = pending.result(timeout=5)
        assert denied.status_code == 404,denied.text
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM instruction_versions').fetchone()[0] == versions_before
        assert conn.execute("SELECT count(*) FROM preparation_jobs WHERE status IN ('queued','running')").fetchone()[0] == 0


def test_migration_existing_owner_gets_first_five_and_no_destructive_rights():
    schema = 'test_' + uuid4().hex
    with psycopg.connect(URL,autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    url = URL + ('&' if '?' in URL else '?') + f'options=-csearch_path%3D{schema}'
    role,dept = uuid4(),uuid4()
    try:
        with psycopg.connect(url) as conn:
            conn.execute('CREATE TABLE schema_migrations(name text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now())')
            for migration in sorted((Path(__file__).parents[1]/'knowledge_service'/'migrations').glob('*.sql')):
                if migration.name >= '007_':
                    break
                conn.execute(migration.read_text())
                conn.execute('INSERT INTO schema_migrations(name) VALUES (%s)',(migration.name,))
            conn.execute("INSERT INTO departments(id,name) VALUES (%s,'Legacy department')",(dept,))
            conn.execute("INSERT INTO access_roles(id,name,name_key,permission) VALUES (%s,'Legacy owner','legacy owner','owner')",(role,))
            conn.execute('INSERT INTO role_departments(role_id,department_id) VALUES (%s,%s)',(role,dept))
            conn.execute('INSERT INTO role_memberships(role_id,user_id) VALUES (%s,4)',(role,))
        migrate(url)
        store = Store(url)
        try:
            assert store.departments(Principal(4))[0]['capabilities'] == list(PRESETS['owner'])
            with store.pool.connection() as conn:
                with pytest.raises(HTTPException) as denied:
                    store.authorize(conn,Principal(4),dept,capability='instructions.delete')
                assert denied.value.status_code == 403
                with pytest.raises(HTTPException) as denied_comparison:
                    store.authorize(conn,Principal(4),dept,capability='comparisons.manage')
                assert denied_comparison.value.status_code == 403
        finally:
            store.close()
    finally:
        with psycopg.connect(URL,autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
