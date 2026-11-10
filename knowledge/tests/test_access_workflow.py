from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from test_registry import send,create,docx


def data(permission='reader',enabled=True):
    return {'user_id':4,'permission':permission,'enabled':enabled,'reason':'Для проверки документов'}


def test_grant_revoke_access_immediately_and_keep_immutable_audit(registry):
    department=registry[2]
    path=f'/departments/{department}/access'
    instruction=create(registry)
    version=send(registry,'POST',f'/instructions/{instruction}/versions',files={'file':('x.docx',docx())}).json()
    assert send(registry,'GET',path).status_code==403
    assert send(registry,'POST',path,json=data()).status_code==403
    assert send(registry,'POST',path,actor=3,admin=True,json=data()).json()['changed']==1
    assert send(registry,'POST',path,actor=3,admin=True,json=data()).json()['changed']==0
    assert send(registry,'GET',f'/instruction-versions/{version["id"]}/original',actor=4).status_code==200
    assert send(registry,'POST',f'/instructions/{instruction}/versions',actor=4,files={'file':('y.docx',docx('Новая'))}).status_code==403
    assert send(registry,'POST',path,actor=3,admin=True,json=data('owner')).status_code==200
    assert send(registry,'POST',f'/instructions/{instruction}/versions',actor=4,files={'file':('y.docx',docx('Новая'))}).status_code==201
    assert send(registry,'POST',path,actor=3,admin=True,json=data('all',False)).json()['changed']==2
    assert send(registry,'GET',f'/instructions/{instruction}/versions',actor=4).status_code==404
    assert send(registry,'GET',f'/instruction-versions/{version["id"]}/original',actor=4).status_code==404
    audit_path=f'/departments/{department}/audit'
    assert send(registry,'GET',audit_path,actor=4).status_code==403
    events=send(registry,'GET',audit_path,actor=3,admin=True).json()
    changes=[e for e in events if e['action'].startswith('access.')]
    assert len(changes)==4
    assert all(e['actor_id']==3 and e['details']['user_id']==4 and e['details']['reason']==data()['reason'] for e in changes)
    assert all('content' not in e for e in events)
    with pytest.raises(psycopg.Error):
        with psycopg.connect(registry[1].database_url) as conn:
            conn.execute("UPDATE audit_events SET details='{}'::jsonb WHERE id=%s",(changes[0]['id'],))


def test_concurrent_grants_are_idempotent_and_bad_department_rejected(registry):
    path=f'/departments/{registry[2]}/access'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:send(registry,'POST',path,actor=3,admin=True,json=data()).json()['changed'],[1,2]))
    assert sorted(results)==[0,1]
    assert send(registry,'POST',path,actor=3,admin=True,json=data('all')).status_code==422
    assert send(registry,'POST',path,actor=3,admin=True,json={**data(),'reason':'  '}).status_code==422
    assert send(registry,'POST','/departments/00000000-0000-0000-0000-000000000001/access',actor=3,admin=True,json=data()).status_code==404
