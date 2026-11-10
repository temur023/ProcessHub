"""Replacing an edition and delegating confirmation of its dates."""
from datetime import date, timedelta

import psycopg

from knowledge_service.permissions import PRESETS
from test_capabilities_lifecycle import granular_role
from test_documents import add, upload, verify
from test_registry import send


def test_new_edition_closes_previous_open_period_in_one_confirmation(registry):
    first = upload(registry).json()
    document_id, first_id = first['document']['id'], first['edition']['id']
    today = date.today()
    start = today - timedelta(days=30)
    assert verify(registry, document_id, first_id, valid_from=start.isoformat()).status_code == 200
    second = add(registry, document_id).json()['edition']
    data = {'valid_from': today.isoformat(), 'evidence': 'Приказ №48: новая редакция'}
    # Without the explicit choice the overlap is still refused, nothing changes.
    assert verify(registry, document_id, second['id'], **data).status_code == 409
    result = verify(registry, document_id, second['id'], close_previous=True, **data)
    assert result.status_code == 200, result.text
    detail = send(registry, 'GET', f'/documents/{document_id}').json()
    by_id = {edition['id']: edition for edition in detail['editions']}
    assert detail['current_edition']['id'] == second['id']
    assert by_id[first_id]['valid_to'] == (today - timedelta(days=1)).isoformat()
    assert by_id[first_id]['originally_verified_to'] is None
    assert by_id[first_id]['end_validity_evidence'] == data['evidence']
    with psycopg.connect(registry[1].database_url) as conn:
        details = conn.execute("SELECT details FROM audit_events WHERE action='document.edition.validity.ended' "
                               'AND entity_id=%s', (first_id,)).fetchone()[0]
        assert details['superseded_by'] == second['id']


def test_close_previous_never_rewrites_an_already_ended_period(registry):
    first = upload(registry).json()
    document_id, first_id = first['document']['id'], first['edition']['id']
    today = date.today()
    assert verify(registry, document_id, first_id, valid_from=(today - timedelta(days=30)).isoformat(),
                  valid_to=(today + timedelta(days=30)).isoformat()).status_code == 200
    ended = send(registry, 'POST', f'/documents/{document_id}/editions/{first_id}/end-validity', admin=True,
                 json={'valid_to': (today + timedelta(days=10)).isoformat(), 'evidence': 'Досрочное окончание'})
    assert ended.status_code == 200, ended.text
    second = add(registry, document_id).json()['edition']
    # The ending is immutable: a start inside the remaining period stays an overlap.
    refused = verify(registry, document_id, second['id'], close_previous=True, valid_from=today.isoformat())
    assert refused.status_code == 409
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM document_edition_endings').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM document_edition_verifications').fetchone()[0] == 1


def test_confirmation_can_be_delegated_only_by_explicit_capability(registry):
    first = upload(registry).json()
    document_id, edition_id = first['document']['id'], first['edition']['id']
    assert all('editions.verify' not in preset for preset in PRESETS.values())
    granular_role(registry, ['documents.read', 'documents.download', 'instructions.create', 'versions.create'])
    assert verify(registry, document_id, edition_id, actor=4, admin=False).status_code == 403
    granular_role(registry, ['documents.read', 'editions.verify'], name='Проверяющий версии')
    document = send(registry, 'GET', f'/documents/{document_id}', actor=4).json()
    assert 'editions.verify' in document['capabilities']
    confirmed = verify(registry, document_id, edition_id, actor=4, admin=False)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['verified_by'] == 4
    path = f'/documents/{document_id}/editions/{edition_id}/end-validity'
    assert send(registry, 'POST', path, actor=4, json={'valid_to': date.today().isoformat(),
                                                       'evidence': 'Окончание по приказу'}).status_code == 200
