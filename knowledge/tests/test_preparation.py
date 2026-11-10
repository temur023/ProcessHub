from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from pathlib import Path

import psycopg
import pytest

from knowledge_service.extraction import extract_document
from knowledge_service.preparation_store import claim_job, heartbeat, complete_job, fail_job
from knowledge_service.worker import run_job
from test_registry import send
from conftest import URL
from test_extraction import word, paragraph, DOCX


def upload(registry, title='Подготовка', body=None, upload_id=None):
    return send(registry,'POST','/instructions/upload',data={'department_id':str(registry[2]),'title':title,
        **({'upload_id':str(upload_id)} if upload_id else {})},files={'file':('инструкция.docx',word(body or paragraph('1. Проверять документы')))})


def path(version):
    return f'/instruction-versions/{version}/preparation'


def db(registry):
    return registry[0].app.state.store


def expire(registry, job):
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute("UPDATE preparation_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(job['id'],))


def due(registry):
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute("UPDATE preparation_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE status='queued'")


def test_upload_atomically_queues_and_replay_does_not_duplicate(registry):
    key = uuid4()
    first = upload(registry,upload_id=key)
    assert first.status_code == 201, first.text
    version = first.json()['version']['id']
    state = send(registry,'GET',path(version),actor=2).json()
    assert state['job']['status'] == 'queued' and state['source'] is None
    assert upload(registry,upload_id=key).json()['replayed']
    assert send(registry,'POST',path(version)).json()['job']['id'] == state['job']['id']
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM preparation_jobs').fetchone()[0] == 1
    denied = send(registry,'POST',path(version),actor=2)
    assert denied.status_code == 403
    assert send(registry,'POST',path(version)+'/cancel',actor=2).status_code == 403


def test_worker_publishes_exact_source_bound_to_version_and_is_immutable(registry):
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    assert run_job(db(registry),job)
    state = send(registry,'GET',path(version),actor=2).json()
    assert state['job']['status'] == 'completed' and state['source']['status'] == 'ready'
    assert state['source']['content_hash'] == job['content_hash']
    assert state['source']['blocks'][0]['text'] == '1. Проверять документы'
    assert send(registry,'POST',path(version)).json()['source']['id'] == state['source']['id']
    assert claim_job(db(registry)) is None
    with psycopg.connect(registry[1].database_url) as conn:
        with pytest.raises(psycopg.Error,match='Immutable'):
            conn.execute('UPDATE prepared_sources SET status=\'partial\'')


def test_revoked_access_cannot_read_source_or_start_job(registry):
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    complete_job(db(registry),job['id'],job['lease_token'],extract_document(bytes(job['content']),DOCX))
    assert send(registry,'GET',path(version),actor=2).status_code == 200
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id IN (1,2)')
    assert send(registry,'GET',path(version),actor=2).status_code == 404
    assert send(registry,'POST',path(version)).status_code == 404
    assert send(registry,'POST',path(version)+'/cancel').status_code == 404
    assert send(registry,'GET',path(version),actor=3,admin=True).status_code == 200


def test_cancelled_late_result_and_old_token_after_resume_are_discarded(registry):
    version = upload(registry).json()['version']['id']
    old = claim_job(db(registry))
    result = extract_document(bytes(old['content']),DOCX)
    assert send(registry,'POST',path(version)+'/cancel').json()['job']['status'] == 'cancelled'
    assert not heartbeat(db(registry),old['id'],old['lease_token'])
    assert not complete_job(db(registry),old['id'],old['lease_token'],result)
    assert send(registry,'GET',path(version)).json()['source'] is None
    assert send(registry,'POST',path(version)).json()['job']['status'] == 'queued'
    new = claim_job(db(registry))
    assert new['lease_token'] != old['lease_token']
    assert not complete_job(db(registry),old['id'],old['lease_token'],result)
    assert complete_job(db(registry),new['id'],new['lease_token'],result)


def test_expired_lease_recovers_after_worker_restart(registry):
    version = upload(registry).json()['version']['id']
    old = claim_job(db(registry))
    expire(registry,old)
    assert claim_job(db(registry)) is None
    due(registry)
    fresh = claim_job(db(registry))
    assert fresh['attempts'] == 2 and fresh['lease_token'] != old['lease_token']
    assert not complete_job(db(registry),old['id'],old['lease_token'],extract_document(bytes(old['content']),DOCX))
    assert run_job(db(registry),fresh)
    assert send(registry,'GET',path(version)).json()['job']['status'] == 'completed'


def test_two_workers_claim_a_job_only_once(registry):
    upload(registry)
    with ThreadPoolExecutor(2) as executor:
        jobs = list(executor.map(lambda _:claim_job(db(registry)),range(2)))
    assert sum(job is not None for job in jobs) == 1


def test_bounded_retry_and_explicit_restart(registry):
    version = upload(registry).json()['version']['id']
    for attempt in range(1,4):
        job = claim_job(db(registry))
        assert job['attempts'] == attempt
        fail_job(db(registry),job['id'],job['lease_token'],'processing_timeout','Время обработки истекло',True)
        due(registry)
    state = send(registry,'GET',path(version)).json()
    assert state['job']['status'] == 'failed' and state['source'] is None
    assert claim_job(db(registry)) is None
    assert send(registry,'POST',path(version)).json()['job']['attempts'] == 0


def test_partial_source_is_completed_but_not_silently_ready(registry):
    version = upload(registry,body='<w:p><w:ins><w:r><w:t>Изменено</w:t></w:r></w:ins></w:p>').json()['version']['id']
    assert run_job(db(registry),claim_job(db(registry)))
    state = send(registry,'GET',path(version)).json()
    assert state['job']['status'] == 'completed' and state['source']['status'] == 'partial'
    assert state['source']['warnings']


def test_old_version_sources_do_not_mix_with_new_revision(registry):
    first = upload(registry).json()
    second = send(registry,'POST',f"/instructions/{first['instruction']['id']}/versions",files={'file':('2.docx',word(paragraph('Другая функция')))}).json()
    assert run_job(db(registry),claim_job(db(registry)))
    assert run_job(db(registry),claim_job(db(registry)))
    a,b = [send(registry,'GET',path(version)).json()['source'] for version in (first['version']['id'],second['id'])]
    assert a['blocks'][0]['text'] != b['blocks'][0]['text']
    assert a['id'] != b['id'] and a['content_hash'] != b['content_hash']


def test_child_timeout_preserves_retry_without_publishing_source(registry):
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    assert run_job(db(registry),job,timeout=0)
    state = send(registry,'GET',path(version)).json()
    assert state['job']['status'] == 'queued' and state['source'] is None
    assert state['job']['error_code'] == 'processing_timeout'


def test_invalid_parser_result_does_not_commit_a_source(registry):
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    result = extract_document(bytes(job['content']),DOCX)
    result['blocks'][0]['text'] = ''
    with pytest.raises(ValueError):
        complete_job(db(registry),job['id'],job['lease_token'],result)
    assert send(registry,'GET',path(version)).json()['source'] is None


def test_migration_queues_existing_original_without_changing_history():
    import hashlib
    schema = 'prep_migration_' + uuid4().hex
    migrations = Path(__file__).resolve().parents[1] / 'knowledge_service' / 'migrations'
    with psycopg.connect(URL,autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    url = URL + ('&' if '?' in URL else '?') + f'options=-csearch_path%3D{schema}'
    try:
        with psycopg.connect(url) as conn:
            for script in sorted(migrations.glob('*.sql')):
                if script.name.startswith('006'):
                    break
                conn.execute(script.read_text())
            department,instruction,version = uuid4(),uuid4(),uuid4()
            original=word(paragraph('Старая редакция'))
            conn.execute('INSERT INTO departments(id,name) VALUES (%s,%s)',(department,'Существующая папка'))
            conn.execute('INSERT INTO instructions(id,department_id,title,created_by) VALUES (%s,%s,%s,1)',(instruction,department,'Существующая инструкция'))
            conn.execute('INSERT INTO instruction_versions(id,instruction_id,number,filename,media_type,content,content_hash,byte_size,uploaded_by) VALUES (%s,%s,1,%s,%s,%s,%s,%s,1)',
                (version,instruction,'original.docx',DOCX,original,hashlib.sha256(original).hexdigest(),len(original)))
            conn.execute((migrations/'006_document_preparation.sql').read_text())
            assert bytes(conn.execute('SELECT content FROM instruction_versions WHERE id=%s',(version,)).fetchone()[0]) == original
            assert conn.execute('SELECT version_id,status FROM preparation_jobs').fetchone() == (version,'queued')
            assert conn.execute('SELECT count(*) FROM audit_events').fetchone()[0] == 0
    finally:
        with psycopg.connect(URL,autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def test_files_without_preparation_are_queued_once(registry):
    version = upload(registry).json()['version']['id']
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM preparation_jobs')
    from knowledge_service.preparation_store import enqueue_missing
    assert enqueue_missing(db(registry)) == 1
    assert enqueue_missing(db(registry)) == 0
    assert send(registry,'GET',path(version)).json()['job']['status'] == 'queued'


def test_failed_preparation_is_retried_on_worker_start(registry):
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    assert fail_job(db(registry),job['id'],job['lease_token'],'conversion_failed','Сбой')
    from knowledge_service.preparation_store import retry_failed
    assert retry_failed(db(registry)) == 1
    state = send(registry,'GET',path(version)).json()['job']
    assert state['status'] == 'queued' and state['attempts'] == 0 and state['error_code'] is None


def test_unreadable_doc_is_prepared_from_libreoffice_docx_copy(registry, monkeypatch):
    import knowledge_service.worker as worker
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    converted = bytes(job['content'])
    monkeypatch.setattr(worker,'_doc_as_docx',lambda content: converted)
    assert run_job(db(registry),{**job,'content':b'not a word document','media_type':'application/msword'})
    state = send(registry,'GET',path(version)).json()
    assert state['source']['status'] == 'ready'
    assert state['source']['blocks'][0]['text'] == '1. Проверять документы'
    assert 'doc_converted' in [warning['code'] for warning in state['source']['warnings']]


def test_doc_without_converter_copy_fails_with_reason(registry, monkeypatch):
    import knowledge_service.worker as worker
    version = upload(registry).json()['version']['id']
    job = claim_job(db(registry))
    monkeypatch.setattr(worker,'_doc_as_docx',lambda content: None)
    assert run_job(db(registry),{**job,'content':b'not a word document','media_type':'application/msword'})
    state = send(registry,'GET',path(version)).json()
    assert state['job']['status'] == 'failed' and state['job']['error_code'] == 'invalid_document'


def test_doc_that_is_really_docx_is_read_as_docx():
    data = word(paragraph('Функция из переименованного файла'))
    result = extract_document(data,'application/msword')
    assert result['status'] == 'ready' and result['blocks'][0]['text'] == 'Функция из переименованного файла'
