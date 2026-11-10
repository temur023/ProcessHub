"""Universal document boundaries against an isolated PostgreSQL schema."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest

from knowledge_service import document_store
from knowledge_service import rbac_store
from knowledge_service.preparation_store import claim_job, complete_job
from test_registry import docx, send


def upload(registry, *, actor=1, admin=False, files=None, **data):
    payload={'library_scope':'department','department_id':str(registry[2]),
             'document_type':'procedure','title':'Порядок контроля', 'upload_id':str(uuid4()),**data}
    if payload['library_scope']=='bank':
        payload.pop('department_id',None)
    return send(registry,'POST','/documents/upload',actor=actor,admin=admin,data=payload,
        files=files or [('files',('Порядок.docx',docx('Правило контроля'))),
                        ('files',('Приложение.docx',docx('Форма проверки')))])


def add(registry,document_id,*,text='Обновлённое правило',actor=1,admin=False,**data):
    return send(registry,'POST',f'/documents/{document_id}/editions',actor=actor,admin=admin,
                data={'upload_id':str(uuid4()),**data},files=[('files',('Порядок.docx',docx(text))),
                    ('files',('Приложение.docx',docx('Форма проверки')))])


def verify(registry,document_id,edition_id,*,actor=3,admin=True,**data):
    return send(registry,'POST',f'/documents/{document_id}/editions/{edition_id}/verify',
        actor=actor,admin=admin,json={'valid_from':date.today().isoformat(),
            'evidence':'Период подтверждён по акту №15, пункт 2',**data})


def counts(registry):
    with psycopg.connect(registry[1].database_url) as conn:
        return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
            ('instructions','document_editions','instruction_versions','document_edition_files',
             'preparation_jobs','upload_receipts'))


def test_bundle_preserves_independent_originals_provenance_and_unverified_dates(registry):
    result=upload(registry,bank_number='532',document_date='2026-10-01',
        owner_department_id=str(registry[4]),metadata=json.dumps({'source_name':'Переданный комплект'}),
        file_metadata=json.dumps([{'purpose':'main','source_metadata':{'source_reference':'Архив 532'}},
                                  {'purpose':'attachment','source_metadata':{'source_reference':'Приложение 1'}}]))
    assert result.status_code==201,result.text
    payload=result.json()
    document,edition=payload['document'],payload['edition']
    assert document['document_type']=='procedure' and document['role_id'] is None
    assert document['owner_department_id']==str(registry[4])
    assert document['current_edition'] is None
    assert edition['verification_status']=='unverified' and edition['valid_from'] is None
    assert [file['purpose'] for file in edition['files']]==['main','attachment']
    for file,text in zip(edition['files'],['Правило контроля','Форма проверки'],strict=True):
        original=send(registry,'GET',f"/documents/files/{file['id']}/original",actor=2)
        assert original.status_code==200 and original.content==docx(text)
        assert file['source_metadata']['source_reference']
        preparation=send(registry,'GET',f"/documents/files/{file['id']}/preparation",actor=2)
        assert preparation.json()['job'] is None
    assert counts(registry)==(1,1,2,2,0,1)


def test_retry_is_atomic_bound_to_entire_bundle_and_rechecked_after_revocation(registry):
    key=str(uuid4())
    first=upload(registry,upload_id=key)
    retry=upload(registry,upload_id=key)
    assert first.status_code==retry.status_code==201
    assert retry.json()['replayed'] and retry.json()['edition']['id']==first.json()['edition']['id']
    assert counts(registry)==(1,1,2,2,0,1)
    assert upload(registry,upload_id=key,edition_label='Изменённая редакция').status_code==409
    assert upload(registry,actor=2,upload_id=key).status_code==403
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=1')
    assert upload(registry,upload_id=key).status_code==404


def test_concurrent_replay_and_editions_commit_complete_bundles_once(registry):
    key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:upload(registry,upload_id=key),range(2)))
    assert [r.status_code for r in responses]==[201,201]
    assert sorted(r.json()['replayed'] for r in responses)==[False,True]
    document_id=responses[0].json()['document']['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        additions=list(pool.map(lambda text:add(registry,document_id,text=text),['Правило А','Правило Б']))
    assert all(r.status_code==201 for r in additions)
    assert sorted(r.json()['edition']['number'] for r in additions)==[2,3]
    assert counts(registry)==(1,3,6,6,0,3)
    files=[file for r in additions for file in r.json()['edition']['files']]
    assert len({file['id'] for file in files})==4
    assert files[1]['content_hash']==files[3]['content_hash']


def test_opposite_department_owners_and_applicability_do_not_deadlock(registry,monkeypatch):
    # Both writers own their library-scope lock before taking FK KEY SHARE
    # locks on the other department. FOR UPDATE on scope would deadlock here.
    barrier=Barrier(2)
    actual=document_store._lock_scope
    def synchronized_lock(*args,**kwargs):
        result=actual(*args,**kwargs)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(document_store,'_lock_scope',synchronized_lock)
    first,second=registry[2],registry[4]
    def action(pair):
        department,owner=pair
        return upload(registry,actor=3,admin=True,department_id=str(department),
            owner_department_id=str(owner),
            applicability=json.dumps([{'department_id':str(owner),'status':'confirmed','evidence':'Пункт 3'}]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(action,[(first,second),(second,first)]))
    assert [response.status_code for response in responses]==[201,201]
    assert counts(registry)==(2,2,4,4,0,2)


def test_cross_department_upload_does_not_deadlock_multidepartment_access_lock(registry,monkeypatch):
    manager_has_reference=Event()
    writer_has_scope=Event()
    manager_can_continue=Event()
    scope,reference=registry[2],registry[4]
    actual=document_store._lock_scope
    def synchronized_lock(*args,**kwargs):
        result=actual(*args,**kwargs)
        writer_has_scope.set()
        assert manager_can_continue.wait(timeout=10)
        return result
    monkeypatch.setattr(document_store,'_lock_scope',synchronized_lock)
    def coordination():
        with psycopg.connect(registry[1].database_url) as conn:
            rbac_store._lock_departments(conn,[reference])
            manager_has_reference.set()
            assert writer_has_scope.wait(timeout=10)
            manager_can_continue.set()
            rbac_store._lock_departments(conn,[reference,scope])
    with ThreadPoolExecutor(max_workers=2) as pool:
        manager=pool.submit(coordination)
        assert manager_has_reference.wait(timeout=10)
        writer=pool.submit(upload,registry,actor=3,admin=True,department_id=str(scope),
            owner_department_id=str(reference),
            applicability=json.dumps([{'department_id':str(reference),'status':'confirmed','evidence':'Пункт 3'}]))
        response=writer.result(timeout=15)
        manager.result(timeout=15)
    assert response.status_code==201,response.text


@pytest.mark.parametrize('operation',['create','create-replay','edition','edition-replay'])
def test_revocation_committed_while_bundle_waits_blocks_write_and_replay(registry,monkeypatch,operation):
    upload_id=str(uuid4())
    document=None
    if operation!='create':
        document=upload(registry,upload_id=upload_id).json()['document']
    if operation.startswith('edition'):
        upload_id=str(uuid4())
        def action():
            return add(registry,document['id'],upload_id=upload_id)
        if operation=='edition-replay':
            assert action().status_code==201
    else:
        def action():
            return upload(registry,upload_id=upload_id)
    baseline=counts(registry)
    checked=Event()
    receipt=document_store._receipt
    def receipt_checked(*args,**kwargs):
        result=receipt(*args,**kwargs)
        checked.set()
        return result
    monkeypatch.setattr(document_store,'_receipt',receipt_checked)
    with psycopg.connect(registry[1].database_url) as blocker,ThreadPoolExecutor(max_workers=1) as pool:
        # Use the production access coordinator rather than a stronger test
        # lock: NO KEY UPDATE must still fence a revocation and its retries.
        rbac_store._lock_departments(blocker,[registry[2]])
        future=pool.submit(action)
        try:
            assert checked.wait(timeout=10)
            blocker.execute('DELETE FROM access_grants WHERE user_id=1')
        finally:
            blocker.commit()
        response=future.result(timeout=15)
    assert response.status_code==404,response.text
    assert counts(registry)==baseline


def test_invalid_later_file_and_storage_failure_leave_no_partial_edition(registry,monkeypatch):
    result=upload(registry,files=[('files',('good.docx',docx())),('files',('bad.docx',b'broken'))])
    assert result.status_code==422 and counts(registry)==(0,0,0,0,0,0)
    def fail_audit(*args,**kwargs):
        raise RuntimeError('simulated audit failure')
    monkeypatch.setattr(document_store,'_audit',fail_audit)
    with pytest.raises(RuntimeError,match='simulated audit failure'):
        upload(registry)
    assert counts(registry)==(0,0,0,0,0,0)


@pytest.mark.parametrize('fields',[
    {'document_type':'unknown'}, {'role_id':str(uuid4())}, {'metadata':'[]'},
    {'metadata':json.dumps({'description':'x'*32768})},
    {'metadata':json.dumps({'description':'bad\x00text'})},
    {'file_metadata':'[{"purpose":"attachment"},{"purpose":"attachment"}]'},
    {'applicability':json.dumps([{'department_id':str(uuid4()),'status':'confirmed'}])},
])
def test_invalid_classification_and_metadata_never_create_empty_records(registry,fields):
    response=upload(registry,**fields)
    assert response.status_code==422,response.text
    assert counts(registry)==(0,0,0,0,0,0)


def test_bundle_count_bound_and_duplicate_file_validation(registry):
    files=[('files',(f'{number}.docx',docx(str(number)))) for number in range(11)]
    assert upload(registry,files=files).status_code==422
    same=[('files',('same.docx',docx())),('files',('same.docx',docx()))]
    assert upload(registry,files=same).status_code==422
    assert counts(registry)==(0,0,0,0,0,0)


def test_cumulative_file_bound_is_checked_before_any_write(registry,monkeypatch):
    from knowledge_service import document_routes
    monkeypatch.setattr(document_routes,'MAX_BUNDLE_BYTES',len(docx('Правило контроля'))+20)
    response=upload(registry)
    assert response.status_code==413,response.text
    assert counts(registry)==(0,0,0,0,0,0)


def test_bank_ownership_and_applicability_never_grant_read_download_or_preparation(registry):
    links=json.dumps([{'department_id':str(registry[2]),'status':'confirmed','evidence':'Пункт 3'}])
    assert upload(registry,library_scope='bank',applicability=links).status_code==404
    result=upload(registry,actor=3,admin=True,library_scope='bank',owner_department_id=str(registry[2]),
                  applicability=links)
    assert result.status_code==201,result.text
    document_id=result.json()['document']['id']
    file_id=result.json()['edition']['files'][0]['id']
    for path in [f'/documents/{document_id}',f'/documents/files/{file_id}/original',
                 f'/documents/files/{file_id}/preparation',f'/instruction-versions/{file_id}/original',
                 f'/instruction-versions/{file_id}/preparation']:
        assert send(registry,'GET',path,actor=1).status_code==404,path
        assert send(registry,'GET',path,actor=3,admin=True).status_code==200,path
    assert send(registry,'POST',f'/documents/files/{file_id}/preparation',actor=1).status_code==404
    assert send(registry,'GET','/documents?library_scope=bank',actor=1).status_code==404
    dept_path=f'/documents?library_scope=department&department_id={registry[2]}'
    assert send(registry,'GET',dept_path,actor=1).json()==[]
    assert send(registry,'GET',dept_path,actor=3,admin=True).json()[0]['id']==document_id
    assert verify(registry,document_id,result.json()['edition']['id']).status_code==200
    assert len(send(registry,'GET',dept_path+'&view=current',admin=True).json())==1
    changed=send(registry,'PATCH',f'/documents/{document_id}',admin=True,
                 json={'applicability':[{'department_id':str(registry[2]),'status':'unconfirmed'}]})
    assert changed.status_code==200,changed.text
    assert send(registry,'GET',dept_path+'&view=current',admin=True).json()==[]
    assert len(send(registry,'GET','/documents?library_scope=bank&view=current',admin=True).json())==1


def test_manual_dates_do_not_follow_upload_order_and_overlaps_are_rejected(registry):
    first=upload(registry).json()
    document_id=first['document']['id']
    edition_id=first['edition']['id']
    assert verify(registry,document_id,edition_id,actor=1,admin=False).status_code==403
    today=date.today()
    verified=verify(registry,document_id,edition_id,valid_from=(today-timedelta(days=30)).isoformat(),
                    valid_to=(today+timedelta(days=30)).isoformat())
    assert verified.status_code==200 and verified.json()['is_current']
    newer=add(registry,document_id).json()['edition']
    detail=send(registry,'GET',f'/documents/{document_id}',actor=2).json()
    assert detail['current_edition']['id']==edition_id and detail['editions'][0]['id']==newer['id']
    overlap=verify(registry,document_id,newer['id'],valid_from=(today+timedelta(days=30)).isoformat())
    assert overlap.status_code==409
    future=verify(registry,document_id,newer['id'],valid_from=(today+timedelta(days=31)).isoformat())
    assert future.status_code==200 and not future.json()['is_current']
    assert verify(registry,document_id,edition_id,evidence='Другое основание').status_code==409
    current=send(registry,'GET',f'/documents?library_scope=department&department_id={registry[2]}&view=current').json()
    assert len(current)==1 and current[0]['current_edition']['id']==edition_id


def test_concurrent_verification_serializes_period_overlap(registry):
    first=upload(registry).json()
    document_id=first['document']['id']
    second=add(registry,document_id).json()['edition']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda id:verify(registry,document_id,id),[first['edition']['id'],second['id']]))
    assert sorted(result.status_code for result in results)==[200,409]
    detail=send(registry,'GET',f'/documents/{document_id}').json()
    assert sum(edition['is_current'] for edition in detail['editions'])==1


def test_open_ended_period_can_be_closed_by_a_new_immutable_fact_before_next_edition(registry):
    first=upload(registry).json()
    document_id,edition_id=first['document']['id'],first['edition']['id']
    today=date.today()
    start=today-timedelta(days=20)
    assert verify(registry,document_id,edition_id,valid_from=start.isoformat()).status_code==200
    second=add(registry,document_id).json()['edition']
    assert verify(registry,document_id,second['id']).status_code==409
    path=f'/documents/{document_id}/editions/{edition_id}/end-validity'
    data={'valid_to':(today-timedelta(days=1)).isoformat(),'evidence':'Следующая редакция по акту №22'}
    assert send(registry,'POST',path,json=data).status_code==403
    assert send(registry,'POST',path,json={**data,'valid_to':(start-timedelta(days=1)).isoformat()},admin=True).status_code==422
    ended=send(registry,'POST',path,json=data,admin=True)
    assert ended.status_code==200,ended.text
    assert ended.json()['valid_to']==data['valid_to'] and ended.json()['originally_verified_to'] is None
    assert ended.json()['end_validity_evidence']==data['evidence']
    assert send(registry,'POST',path,json=data,admin=True).status_code==200
    assert send(registry,'POST',path,json={**data,'evidence':'Другой акт'},admin=True).status_code==409
    assert verify(registry,document_id,second['id']).status_code==200
    detail=send(registry,'GET',f'/documents/{document_id}').json()
    assert detail['current_edition']['id']==second['id']
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT valid_to FROM document_edition_verifications WHERE edition_id=%s',
                            (edition_id,)).fetchone()[0] is None
        assert conn.execute('SELECT count(*) FROM document_edition_endings').fetchone()[0]==1
    for statement in ('DELETE FROM document_edition_endings','UPDATE document_edition_endings SET evidence=\'changed\''):
        with pytest.raises(psycopg.Error):
            with psycopg.connect(registry[1].database_url) as conn:
                conn.execute(statement)


def test_metadata_edit_does_not_reclassify_files_or_expand_access(registry):
    first=upload(registry).json()
    document_id=first['document']['id']
    assert send(registry,'PATCH',f'/documents/{document_id}',actor=2,json={'title':'Изменено'}).status_code==403
    patch={'title':'Методика проверки','document_type':'methodology','owner_department_id':str(registry[4]),
        'metadata':{'source_name':'Согласование'},'applicability':[{'department_id':str(registry[4]),
             'status':'confirmed','evidence':'Подтверждение владельца'}]}
    edited=send(registry,'PATCH',f'/documents/{document_id}',json=patch)
    assert edited.status_code==200,edited.text
    assert edited.json()['editions'][0]['files']==first['edition']['files']
    assert edited.json()['department_id']==str(registry[2])
    assert send(registry,'PATCH',f'/documents/{document_id}',json={'library_scope':'bank'}).status_code==422
    assert send(registry,'PATCH',f'/documents/{document_id}',json={'document_type':None}).status_code==422


@pytest.mark.parametrize('bank',[False,True])
def test_trash_preserves_all_files_and_jobs_and_fences_late_worker(registry,bank):
    first=upload(registry,actor=3,admin=True,document_type='job_instruction',**({'library_scope':'bank'} if bank else {})).json()
    document_id=first['document']['id']
    db=registry[0].app.state.store
    claimed=claim_job(db)
    assert claimed
    baseline=counts(registry)
    assert send(registry,'POST',f'/documents/{document_id}/delete',json={'reason':'На разбор'},admin=True).status_code==200
    for file in first['edition']['files']:
        assert send(registry,'GET',f"/documents/files/{file['id']}/original",admin=True).status_code==404
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute("SELECT count(*) FROM preparation_jobs WHERE status='cancelled'").fetchone()[0]==2
    restored=send(registry,'POST',f'/documents/{document_id}/restore',json={'reason':'Возвращён'},admin=True)
    assert restored.status_code==200
    # A stale token must be rejected before any result validation/publishing.
    assert not complete_job(db,claimed['id'],claimed['lease_token'],{})
    assert counts(registry)==baseline
    for file in first['edition']['files']:
        assert send(registry,'GET',f"/documents/files/{file['id']}/original",admin=True).status_code==200


def test_legacy_uploads_automatically_create_single_file_unverified_editions(registry):
    response=send(registry,'POST','/instructions/upload',data={'department_id':str(registry[2]),
        'role_id':str(registry[3]),'title':'Инструкция специалиста','effective_from':'2020-01-01'},
        files={'file':('instruction.docx',docx())})
    assert response.status_code==201,response.text
    legacy=response.json()
    detail=send(registry,'GET',f"/documents/{legacy['instruction']['id']}").json()
    assert detail['document_type']=='job_instruction'
    assert detail['current_edition'] is None
    assert detail['editions'][0]['id']==legacy['version']['id']
    assert detail['editions'][0]['valid_from'] is None
    assert detail['editions'][0]['files'][0]['id']==legacy['version']['id']


def test_legacy_instruction_routes_remain_scoped_to_instruction_type(registry):
    generic=upload(registry,title='Контроль операций').json()
    legacy=send(registry,'POST','/instructions/upload',data={'department_id':str(registry[2]),
        'title':'Контроль операций'},files={'file':('instruction.docx',docx())})
    assert legacy.status_code==201,legacy.text
    listed=send(registry,'GET',f'/departments/{registry[2]}/instructions').json()
    assert [item['id'] for item in listed]==[legacy.json()['instruction']['id']]
    assert send(registry,'POST','/instructions',json={'department_id':str(registry[2]),
        'title':'Контроль операций'}).status_code==409
    # The old JSON create also permits a title used only by another type.
    assert upload(registry,title='Отдельная форма',document_type='form').status_code==201
    assert send(registry,'POST','/instructions',json={'department_id':str(registry[2]),
        'title':'Отдельная форма'}).status_code==201


def test_migration_preserves_legacy_uuids_bytes_hashes_jobs_receipts_and_audit(registry):
    # Build a separate pre-008 schema, seed representative old originals and
    # immutable derived records, then apply the new migration to that copy.
    schema='migration_'+uuid4().hex
    folder=Path(__file__).resolve().parents[1]/'knowledge_service'/'migrations'
    dept,role,document,file,source,job,receipt=[uuid4() for _ in range(7)]
    original=docx()
    with psycopg.connect(registry[1].database_url,autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        try:
            conn.execute(f'SET search_path TO {schema}')
            for path in sorted(folder.glob('*.sql')):
                if path.name>='008':
                    break
                conn.execute(path.read_text())
            with conn.transaction():
                conn.execute('INSERT INTO departments(id,name) VALUES(%s,\'Dept\')',(dept,))
                conn.execute('INSERT INTO roles(id,department_id,name) VALUES(%s,%s,\'Role\')',(role,dept))
                conn.execute('INSERT INTO instructions(id,department_id,role_id,title,created_by) VALUES(%s,%s,%s,\'Old\',1)',
                             (document,dept,role))
                conn.execute('''INSERT INTO instruction_versions(id,instruction_id,number,filename,media_type,content,
                    content_hash,byte_size,effective_from,uploaded_by) VALUES(%s,%s,1,'old.docx','application/docx',%s,'preserved-hash',%s,'2020-01-01',1)''',
                             (file,document,original,len(original)))
                conn.execute("INSERT INTO audit_events(actor_id,department_id,action,entity_id) VALUES(1,%s,'version.uploaded',%s)",(dept,file))
                conn.execute("INSERT INTO upload_receipts(actor_id,upload_id,request_hash,instruction_id,version_id) VALUES(1,%s,'request-hash',%s,%s)",(receipt,document,file))
                conn.execute("INSERT INTO preparation_jobs(id,version_id,parser_version) VALUES(%s,%s,'source-v1')",(job,file))
                conn.execute("""INSERT INTO prepared_sources(id,version_id,parser_version,content_hash,status,blocks,warnings,stats)
                    VALUES(%s,%s,'source-v1','preserved-hash','partial','[]','[]','{}')""",(source,file))
            before={table:conn.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() for table in
                    ('instruction_versions','audit_events','upload_receipts','preparation_jobs','prepared_sources')}
            conn.execute((folder/'008_universal_documents.sql').read_text())
            after={table:conn.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() for table in before}
            assert before==after
            assert conn.execute('SELECT id,document_id FROM document_editions').fetchone()==(file,document)
            assert conn.execute('SELECT edition_id,file_id FROM document_edition_files').fetchone()==(file,file)
            assert conn.execute('SELECT count(*) FROM document_edition_verifications').fetchone()[0]==0
            assert conn.execute('SELECT id,document_type,owner_department_id FROM instructions').fetchone()==(document,'job_instruction',dept)
            for table in ('instruction_versions','document_editions','document_edition_files','document_edition_verifications'):
                if table=='document_edition_verifications':
                    continue
                with pytest.raises(psycopg.Error):
                    conn.execute(f'DELETE FROM {table}')
        finally:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def test_new_position_is_created_with_the_instruction_and_reused(registry):
    position={'display_name':'Ведущий менеджер','unit_name':'Отдел платежей /  Группа SWIFT ','category':'specialists'}
    first=upload(registry,document_type='job_instruction',title='Инструкция ведущего менеджера',
                 new_position=json.dumps(position,ensure_ascii=False))
    assert first.status_code==201,first.text
    role_id=first.json()['document']['role_id']
    library=send(registry,'GET',f'/departments/{registry[2]}/role-library').json()
    role=next(item for item in library['roles'] if item['id']==role_id)
    assert (role['display_name'],role['unit_name'],role['category'])==('Ведущий менеджер','Отдел платежей / Группа SWIFT','specialists')
    second=upload(registry,document_type='job_instruction',title='Вторая инструкция',
                  new_position=json.dumps({**position,'display_name':'ведущий менеджер'},ensure_ascii=False))
    assert second.json()['document']['role_id']==role_id
    # A position belongs to an instruction of the department only.
    assert upload(registry,title='Порядок',new_position=json.dumps(position,ensure_ascii=False)).status_code==422
    assert upload(registry,document_type='job_instruction',title='Обе',role_id=str(registry[3]),
                  new_position=json.dumps(position,ensure_ascii=False)).status_code==422


def test_failed_upload_leaves_no_new_position_and_edit_can_assign_one(registry):
    position={'display_name':'Главный бухгалтер','unit_name':'Бухгалтерия'}
    before=send(registry,'GET',f'/departments/{registry[2]}/roles').json()
    failed=upload(registry,document_type='job_instruction',title='Битая',new_position=json.dumps(position,ensure_ascii=False),
                  files=[('files',('Битая.docx',b'not a docx'))])
    assert failed.status_code==422
    assert send(registry,'GET',f'/departments/{registry[2]}/roles').json()==before
    created=upload(registry,document_type='job_instruction',title='Без должности').json()['document']
    assert created['role_id'] is None
    edited=send(registry,'PATCH',f"/documents/{created['id']}",json={'new_position':position})
    assert edited.status_code==200,edited.text
    assert edited.json()['role_name']=='Главный бухгалтер — Бухгалтерия'
    assert send(registry,'PATCH',f"/documents/{created['id']}",actor=2,json={'new_position':position}).status_code==403


def test_position_can_be_renamed_and_moved_without_duplicates(registry):
    def create(display,unit,title):
        response=upload(registry,document_type='job_instruction',title=title,
                        new_position=json.dumps({'display_name':display,'unit_name':unit},ensure_ascii=False))
        assert response.status_code==201,response.text
        return response.json()['document']['role_id']
    moved=create('Менеджер','Группа платежей','Инструкция менеджера')
    other=create('Старший менеджер','Группа SWIFT','Инструкция старшего')
    path=f'/departments/{registry[2]}/role-library/{moved}'
    profile={'display_name':'Ведущий менеджер','unit_name':'Валютные операции/Группа SWIFT','category':'specialists',
             'historical_candidate':False,'classification_evidence':'Штатное расписание 2026'}
    assert send(registry,'PATCH',path,actor=2,json=profile).status_code==403
    changed=send(registry,'PATCH',path,json=profile)
    assert changed.status_code==200,changed.text
    assert (changed.json()['unit_name'],changed.json()['name'])==('Валютные операции / Группа SWIFT','Ведущий менеджер — Валютные операции / Группа SWIFT')
    # Filing under the new name finds the moved position instead of creating one.
    assert create('ведущий менеджер','Валютные операции / Группа SWIFT','Ещё одна')==moved
    clash=send(registry,'PATCH',path,json={**profile,'display_name':'Старший менеджер','unit_name':'Группа SWIFT'})
    assert clash.status_code==409
    assert send(registry,'GET',f'/departments/{registry[2]}/role-library/{other}').json()['role']['display_name']=='Старший менеджер'
