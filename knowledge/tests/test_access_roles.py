"""Named access roles, scope changes, and optional professional classification."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_service import registry_store
from knowledge_service.permissions import PRESETS
from test_registry import docx, send


def role_data(registry, *, name='Редактор инструкций', permission='owner', scopes=None):
    return {'name':name,'permission':permission,'department_ids':
            [str(value) for value in (scopes if scopes is not None else [registry[2],registry[4]])],
            'reason':'Настройка доступа к документам'}


def create_role(registry, **kwargs):
    response = send(registry,'POST','/access-roles',actor=3,admin=True,json=role_data(registry,**kwargs))
    assert response.status_code==201,response.text
    return response.json()


def member(registry, role_id, *, user=4, enabled=True, **kwargs):
    return send(registry,'POST','/access-role-memberships',actor=3,admin=True,
                json={'role_id':str(role_id),'user_id':user,'enabled':enabled,'reason':'Назначение роли сотруднику',**kwargs})


def upload_without_role(registry, *, actor=4, admin=False, department=None, title='Инструкция без классификации', **kwargs):
    return send(registry,'POST','/instructions/upload',actor=actor,admin=admin,
                files={'file':('instruction.docx',docx())},
                data={'department_id':str(department or registry[2]),'title':title,**kwargs})


def audit(registry):
    response = send(registry,'GET','/access-role-audit',actor=3,admin=True)
    assert response.status_code==200,response.text
    return response.json()


def test_global_role_then_scope_then_membership_and_optional_professional_role(registry):
    role=create_role(registry,scopes=[])
    assert role['department_ids']==[] and role['member_count']==0
    assert member(registry,role['id']).json()['changed'] is True
    assert send(registry,'GET','/departments',actor=4).json()==[]
    update=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
                json={**role_data(registry),'expected_updated_at':role['updated_at']})
    assert update.status_code==200,update.text
    assert update.json()['member_count']==1
    departments=send(registry,'GET','/departments',actor=4).json()
    assert {item['id'] for item in departments}=={str(registry[2]),str(registry[4])}
    assert all(item['permissions']==['owner'] for item in departments)
    # The second folder contains no professional roles. Its original still
    # registers without inventing a professional role or hiding the entry.
    uploaded=upload_without_role(registry,department=registry[4])
    assert uploaded.status_code==201,uploaded.text
    instruction=uploaded.json()['instruction']
    assert instruction['role_id'] is None and instruction['role_name'] is None
    listed=send(registry,'GET',f'/departments/{registry[4]}/instructions',actor=4).json()
    assert listed[0]['id']==instruction['id'] and listed[0]['version_count']==1
    assert send(registry,'GET',f"/instruction-versions/{uploaded.json()['version']['id']}/original",actor=4).content==docx()
    # Access-role IDs are not valid professional classifications.
    assert upload_without_role(registry,role_id=role['id'],title='Wrong classification').status_code==422
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM roles WHERE department_id=%s',(registry[4],)).fetchone()[0]==0
    memberships=send(registry,'GET','/access-role-memberships',actor=3,admin=True).json()
    assert memberships[0]['role_id']==role['id'] and memberships[0]['user_id']==4
    assert set(memberships[0]['department_ids'])=={str(registry[2]),str(registry[4])}


def test_optional_role_admin_upload_and_idempotent_retry_stay_in_list(registry):
    upload_id=str(uuid4())
    first=upload_without_role(registry,actor=3,admin=True,upload_id=upload_id)
    retry=upload_without_role(registry,actor=3,admin=True,upload_id=upload_id)
    assert first.status_code==retry.status_code==201
    assert retry.json()['replayed'] and retry.json()['instruction']['id']==first.json()['instruction']['id']
    assert retry.json()['instruction']['role_id'] is None
    assert retry.json()['instruction']['role_name'] is None
    assert upload_without_role(registry,actor=3,admin=True,title='ИНСТРУКЦИЯ БЕЗ КЛАССИФИКАЦИИ').status_code==409
    listed=send(registry,'GET',f'/departments/{registry[2]}/instructions',actor=3,admin=True).json()
    assert len(listed)==1 and listed[0]['role_id'] is None
    # Owner through a legacy direct grant can also register without a role.
    assert upload_without_role(registry,actor=1,title='Другая инструкция').status_code==201


def test_union_of_memberships_and_direct_grants_revokes_only_one_source(registry):
    owner=create_role(registry,scopes=[registry[2]])
    reviewer=create_role(registry,name='Проверяющий',permission='reviewer',scopes=[registry[2]])
    reader=create_role(registry,name='Чтение другой папки',permission='reader',scopes=[registry[4]])
    for role in (owner,reviewer,reader):
        assert member(registry,role['id']).status_code==200
    # Existing direct permission is kept after every named role mutation.
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute("INSERT INTO access_grants(user_id,department_id,permission) VALUES(4,%s,'reader')",(registry[2],))
    permissions={d['id']:d['permissions'] for d in send(registry,'GET','/departments',actor=4).json()}
    assert permissions[str(registry[2])]==['owner','reader','reviewer']
    assert permissions[str(registry[4])]==['reader']
    uploaded=upload_without_role(registry)
    assert uploaded.status_code==201
    assert member(registry,owner['id'],enabled=False).json()['changed'] is True
    assert upload_without_role(registry,title='Denied').status_code==403
    assert member(registry,reviewer['id'],enabled=False).status_code==200
    permissions={d['id']:d['permissions'] for d in send(registry,'GET','/departments',actor=4).json()}
    assert permissions[str(registry[2])]==['reader']
    assert send(registry,'GET',f"/instruction-versions/{uploaded.json()['version']['id']}/original",actor=4).status_code==200
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT permission FROM access_grants WHERE user_id=4').fetchall()==[('reader',)]


def test_scope_permission_update_preserves_original_and_closes_access(registry):
    role=create_role(registry)
    assert member(registry,role['id']).status_code==200
    uploaded=upload_without_role(registry)
    assert uploaded.status_code==201
    path=f"/instructions/{uploaded.json()['instruction']['id']}/versions"
    changed=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
                 json={**role_data(registry,permission='reader',scopes=[registry[4]]),
                       'capabilities':list(PRESETS['reader']),'expected_updated_at':role['updated_at']})
    assert changed.status_code==200
    assert send(registry,'GET',path,actor=4).status_code==404
    assert send(registry,'POST',path,actor=4,files={'file':('new.docx',docx('new'))}).status_code==404
    assert upload_without_role(registry,department=registry[4]).status_code==403
    # Removal of scope never deletes documents or their originals.
    assert send(registry,'GET',f"/instruction-versions/{uploaded.json()['version']['id']}/original",actor=3,admin=True).content==docx()
    repeated=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
                  json={**role_data(registry,permission='reader',scopes=[registry[4]]),
                        'capabilities':list(PRESETS['reader']),'expected_updated_at':changed.json()['updated_at']})
    assert repeated.json()['updated_at']==changed.json()['updated_at']


def test_access_roles_admin_only_validated_names_scopes_and_reason(registry):
    for path in ('/access-roles','/access-role-memberships','/access-role-audit'):
        assert send(registry,'GET',path,actor=1).status_code==403
    assert send(registry,'POST','/access-roles',json=role_data(registry)).status_code==403
    original=create_role(registry,name='  Role   QA  ')
    assert original['name']=='Role QA'
    duplicate=send(registry,'POST','/access-roles',actor=3,admin=True,
                   json=role_data(registry,name='Ｒｏｌｅ QA'))
    assert duplicate.status_code==409
    initial_events=len(audit(registry))
    invalids=[{'name':'  '},{'name':'bad\x00name'},{'permission':'admin'},{'reason':' \t '},
              {'reason':'bad\x00reason'},{'department_ids':[str(registry[2])]*2},
              {'department_ids':[str(uuid4())]}]
    for values in invalids:
        response=send(registry,'POST','/access-roles',actor=3,admin=True,json={**role_data(registry),**values})
        assert response.status_code in (422,404),response.text
    update=send(registry,'PATCH',f"/access-roles/{original['id']}",actor=3,admin=True,
                json={**role_data(registry,name='Changed',scopes=[registry[2],uuid4()]),'expected_updated_at':original['updated_at']})
    assert update.status_code==404
    assert len(audit(registry))==initial_events
    assert send(registry,'GET','/access-roles',actor=3,admin=True).json()==[original]
    for values in ({'user_id':True},{'user_id':0},{'enabled':1},{'reason':'  '},{'reason':'bad\x00reason'}):
        assert member(registry,original['id'],**values).status_code==422
    assert member(registry,str(uuid4())).status_code==404


def test_global_immutable_audit_and_repeat_member_change_are_idempotent(registry):
    role=create_role(registry,scopes=[])
    assert member(registry,role['id']).json()['changed'] is True
    assert member(registry,role['id']).json()['changed'] is False
    assert member(registry,role['id'],enabled=False).json()['changed'] is True
    assert member(registry,role['id'],enabled=False).json()['changed'] is False
    events=audit(registry)
    assert [item['action'] for item in events]==['access_role.member.revoked','access_role.member.assigned','access_role.created']
    assert all(item['actor_id']==3 and item['role_id']==role['id'] and item['details']['reason'] for item in events)
    assert [item['user_id'] for item in events]==[4,4,None]
    assert all(item['details']['department_ids']==[] for item in events)
    for statement in ('UPDATE access_role_audit_events SET details=\'{}\'', 'DELETE FROM access_role_audit_events'):
        with pytest.raises(psycopg.Error):
            with psycopg.connect(registry[1].database_url) as conn:
                conn.execute(statement)


def test_concurrent_role_names_memberships_and_opposite_scopes_are_serialized(registry):
    with ThreadPoolExecutor(max_workers=2) as pool:
        creations=list(pool.map(lambda _:send(registry,'POST','/access-roles',actor=3,admin=True,
                                              json=role_data(registry)),range(2)))
    assert sorted(response.status_code for response in creations)==[201,409]
    role=next(response.json() for response in creations if response.status_code==201)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assignments=list(pool.map(lambda _:member(registry,role['id']),range(2)))
    assert sorted(response.json()['changed'] for response in assignments)==[False,True]
    # Two admins edit the same snapshot. Exactly one complete change wins;
    # the other must reload instead of silently overriding it.
    updates=[{**role_data(registry,permission='reader',scopes=[registry[2],registry[4]]),
              'capabilities':list(PRESETS['reader']),'expected_updated_at':role['updated_at']},
             {**role_data(registry,permission='reviewer',scopes=[registry[4],registry[2]]),
              'capabilities':list(PRESETS['reviewer']),'expected_updated_at':role['updated_at']}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda payload:send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,json=payload),updates))
    assert sorted(response.status_code for response in responses)==[200,409]
    current=send(registry,'GET','/access-roles',actor=3,admin=True).json()[0]
    assert current['permission'] in ('reader','reviewer') and len(current['department_ids'])==2
    permissions=send(registry,'GET','/departments',actor=4).json()
    assert all(d['permissions']==[current['permission']] for d in permissions)


def test_stale_admin_scope_snapshot_cannot_remove_another_admin_added_folder(registry):
    role=create_role(registry,scopes=[registry[2]])
    first=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
               json={**role_data(registry),'expected_updated_at':role['updated_at']})
    assert first.status_code==200,first.text
    assert first.json()['updated_at']!=role['updated_at']
    before_events=len(audit(registry))
    stale=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=5,admin=True,
               json={**role_data(registry,name='Название второго администратора',scopes=[registry[2]]),
                     'expected_updated_at':role['updated_at']})
    assert stale.status_code==409,stale.text
    assert 'другим администратором' in stale.json()['detail']
    current=send(registry,'GET','/access-roles',actor=3,admin=True).json()[0]
    assert set(current['department_ids'])=={str(registry[2]),str(registry[4])}
    assert current['name']==role['name'] and len(audit(registry))==before_events
    for invalid in ({}, {'expected_updated_at':'2026-10-07T12:00:00'}):
        response=send(registry,'PATCH',f"/access-roles/{role['id']}",actor=3,admin=True,
                      json={**role_data(registry),**invalid})
        assert response.status_code==422


def test_multi_folder_scope_edits_lock_in_same_order_for_distinct_roles(registry):
    first=create_role(registry,name='Первая роль')
    second=create_role(registry,name='Вторая роль')
    payloads=[(first,role_data(registry,name=first['name'],permission='reader',scopes=[registry[2],registry[4]])),
              (second,role_data(registry,name=second['name'],permission='reviewer',scopes=[registry[4],registry[2]]))]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda item:send(registry,'PATCH',f"/access-roles/{item[0]['id']}",actor=3,admin=True,
                                                json={**item[1],'capabilities':list(PRESETS[item[1]['permission']]),
                                                      'expected_updated_at':item[0]['updated_at']}),payloads))
    assert all(response.status_code==200 for response in responses)
    assert [response.json()['permission'] for response in responses]==['reader','reviewer']


@pytest.mark.parametrize('change',['member','scope','permission'])
@pytest.mark.parametrize('replay',[False,True])
def test_completed_derived_revocation_while_upload_waits_prevents_write(registry,monkeypatch,change,replay):
    role=create_role(registry,scopes=[registry[2]])
    assert member(registry,role['id']).status_code==200
    upload_id=str(uuid4())
    if replay:
        assert upload_without_role(registry,upload_id=upload_id).status_code==201
    with psycopg.connect(registry[1].database_url) as conn:
        initial=tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                      for table in ('instructions','instruction_versions','upload_receipts'))
    checked=Event()
    actual=registry_store._receipt
    def receipt_hook(*args,**kwargs):
        result=actual(*args,**kwargs)
        checked.set()
        return result
    monkeypatch.setattr(registry_store,'_receipt',receipt_hook)
    with psycopg.connect(registry[1].database_url) as blocker,ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute('SELECT id FROM departments WHERE id=%s FOR UPDATE',(registry[2],))
        future=pool.submit(lambda:upload_without_role(registry,upload_id=upload_id))
        try:
            assert checked.wait(timeout=5)
            # Same mutations/department-lock order as the admin endpoints; the
            # lock lets us place the revoke exactly between the two checks.
            if change=='member':
                blocker.execute('DELETE FROM role_memberships WHERE role_id=%s AND user_id=4',(role['id'],))
            elif change=='scope':
                blocker.execute('DELETE FROM role_departments WHERE role_id=%s',(role['id'],))
            else:
                blocker.execute("UPDATE access_roles SET permission='reader',capabilities=%s WHERE id=%s",
                                (list(PRESETS['reader']),role['id']))
        finally:
            blocker.commit()
        response=future.result(timeout=10)
    assert response.status_code==(403 if change=='permission' else 404),response.text
    with psycopg.connect(registry[1].database_url) as conn:
        assert tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('instructions','instruction_versions','upload_receipts'))==initial
