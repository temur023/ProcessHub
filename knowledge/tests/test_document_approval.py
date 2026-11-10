"""Bank adoption dates stay version-bound, evidenced, and immutable."""
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_documents import add, upload, verify
from test_registry import send


def test_approval_date_is_not_inferred_and_old_verification_call_remains_compatible(registry):
    payload = upload(registry, document_date='2020-01-02').json()
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert payload['edition']['approved_on'] is None
    result = verify(registry, document_id, edition_id)
    assert result.status_code == 200, result.text
    assert result.json()['approved_on'] is None
    detail = send(registry, 'GET', f'/documents/{document_id}').json()
    assert detail['document_date'] == '2020-01-02'
    assert detail['current_edition']['approved_on'] is None
    assert detail['editions'][0]['approved_on'] is None


def test_approval_is_exact_edition_fact_and_replay_cannot_change_it(registry):
    payload = upload(registry).json()
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    original_files = payload['edition']['files']
    today = date.today()
    data = {'approved_on': (today - timedelta(days=12)).isoformat(),
            'valid_from': (today - timedelta(days=10)).isoformat(),
            'valid_to': (today - timedelta(days=1)).isoformat(),
            'evidence': 'Приказ №22: принятие и период действия по оригиналу'}
    assert verify(registry, document_id, edition_id, actor=1, admin=False, **data).status_code == 403
    first = verify(registry, document_id, edition_id, **data)
    assert first.status_code == 200, first.text
    assert first.json()['approved_on'] == data['approved_on']
    assert verify(registry, document_id, edition_id, **data).status_code == 200
    assert verify(registry, document_id, edition_id,
                  **{**data, 'approved_on': today.isoformat()}).status_code == 409
    # Omitting a previously known approval date is a different confirmation,
    # rather than permission to erase it or silently change the stored fact.
    assert verify(registry, document_id, edition_id,
                  **{key: value for key, value in data.items() if key != 'approved_on'}).status_code == 409
    newer = add(registry, document_id).json()['edition']
    assert verify(registry, document_id, newer['id']).status_code == 200
    detail = send(registry, 'GET', f'/documents/{document_id}').json()
    by_id = {edition['id']: edition for edition in detail['editions']}
    assert by_id[edition_id]['approved_on'] == data['approved_on']
    assert by_id[newer['id']]['approved_on'] is None
    assert detail['current_edition']['id'] == newer['id']
    assert by_id[edition_id]['files'] == original_files
    with psycopg.connect(registry[1].database_url) as conn:
        audit = conn.execute("SELECT details FROM audit_events WHERE action='document.edition.verified' "
                             'AND entity_id=%s', (edition_id,)).fetchall()
        assert len(audit) == 1 and audit[0][0]['approved_on'] == data['approved_on']
        with pytest.raises(psycopg.Error):
            conn.execute('UPDATE document_edition_verifications SET approved_on=NULL WHERE edition_id=%s',
                         (edition_id,))


@pytest.mark.parametrize('approval', ['not-a-date', '2026-02-30', '2026-13-01'])
def test_invalid_approval_date_rejects_without_confirmation(registry, approval):
    payload = upload(registry).json()
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert verify(registry, document_id, edition_id, approved_on=approval).status_code == 422
    detail = send(registry, 'GET', f'/documents/{document_id}').json()
    assert detail['editions'][0]['verification_status'] == 'unverified'
    assert detail['editions'][0]['approved_on'] is None
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='document.edition.verified'").fetchone()[0] == 0


@pytest.mark.parametrize('offset', [-3, 3])
def test_documented_retrospective_or_scheduled_effect_is_not_rejected(registry, offset):
    payload = upload(registry).json()
    today = date.today()
    result = verify(registry, payload['document']['id'], payload['edition']['id'],
                    approved_on=(today + timedelta(days=offset)).isoformat(),
                    valid_from=today.isoformat())
    assert result.status_code == 200, result.text
    assert result.json()['approved_on'] == (today + timedelta(days=offset)).isoformat()
    assert result.json()['valid_from'] == today.isoformat()


def test_unknown_approval_date_cannot_be_backfilled_by_mutating_confirmed_edition(registry):
    payload = upload(registry).json()
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert verify(registry, document_id, edition_id).status_code == 200
    assert verify(registry, document_id, edition_id, approved_on=date.today().isoformat()).status_code == 409
    assert send(registry, 'GET', f'/documents/{document_id}').json()['editions'][0]['approved_on'] is None


def test_approval_migration_preserves_existing_confirmation_and_original(registry):
    schema = 'approval_migration_' + uuid4().hex
    folder = Path(__file__).resolve().parents[1] / 'knowledge_service' / 'migrations'
    department_id, document_id, file_id = [uuid4() for _ in range(3)]
    with psycopg.connect(registry[1].database_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        try:
            conn.execute(f'SET search_path TO {schema}')
            for path in sorted(folder.glob('*.sql')):
                if path.name >= '011':
                    break
                conn.execute(path.read_text())
            with conn.transaction():
                conn.execute("INSERT INTO departments(id,name) VALUES(%s,'Dept')", (department_id,))
                conn.execute("INSERT INTO instructions(id,department_id,title,document_date,created_by) "
                             "VALUES(%s,%s,'Instruction','2020-01-02',1)", (document_id, department_id))
                conn.execute("INSERT INTO instruction_versions(id,instruction_id,number,filename,media_type,content,"
                             "content_hash,byte_size,uploaded_by) VALUES(%s,%s,1,'original.docx','application/docx',"
                             "%s,'preserved-hash',3,1)", (file_id, document_id, b'abc'))
                conn.execute("INSERT INTO document_edition_verifications(edition_id,valid_from,evidence,verified_by) "
                             "VALUES(%s,'2020-01-03','Original order',3)", (file_id,))
            before = {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in
                      ('instructions', 'instruction_versions', 'document_editions', 'document_edition_files')}
            confirmed = conn.execute('SELECT * FROM document_edition_verifications').fetchone()
            conn.execute((folder / '011_edition_approval_dates.sql').read_text())
            assert before == {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in before}
            migrated = conn.execute('SELECT * FROM document_edition_verifications').fetchone()
            assert migrated[:-1] == confirmed and migrated[-1] is None
            with pytest.raises(psycopg.Error):
                conn.execute("UPDATE document_edition_verifications SET approved_on='2020-01-02'")
        finally:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
