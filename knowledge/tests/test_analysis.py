"""Automatic instruction ↔ regulation analysis against the Nova stub."""
import asyncio
import dataclasses
import io
import zipfile
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
import psycopg
import pytest

from dev import nova_stub
from knowledge_service.analysis_engine import (AnalysisFailed, NovaCaller, analyze_instruction, normalize,
                                               quote_verified)
from knowledge_service.analysis_store import claim_run, run_inputs
from knowledge_service.analysis_worker import process
from knowledge_service.extraction import extract_document
from knowledge_service.nova import NovaClient, NovaError, SearchResult
from knowledge_service.document_structure import build_functions
from knowledge_service.preparation_store import claim_job, complete_job
from test_capabilities_lifecycle import granular_role
from test_documents import verify
from test_registry import send

FUNCTIONS = ['Проверяет платёжные документы клиентов на соответствие требованиям;',
             'Проводит платежи в национальной валюте в пределах установленных лимитов;',
             'Организует корпоративные мероприятия и спортивные соревнования сотрудников;']


def instruction_docx(functions=FUNCTIONS):
    paragraphs = ['ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ ведущего менеджера', '1. Общие положения',
                  'Ведущий менеджер подчиняется начальнику отдела.', '2. Функции и задачи',
                  *[f'{index}) {text}' for index, text in enumerate(functions, 1)],
                  '3. Ответственность', 'Несёт ответственность за достоверность операций.']
    body = ''.join(f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>' for text in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                         f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def nova_settings(registry):
    settings = dataclasses.replace(registry[1], nova_url='http://nova.test', nova_key=nova_stub.KEY,
                                   nova_collection=nova_stub.COLLECTION)
    registry[0].app.state.settings = settings
    return settings


def stub_client(settings):
    return NovaClient(settings, transport=httpx.ASGITransport(app=nova_stub.app))


def prepared_instruction(registry, functions=FUNCTIONS):
    db = registry[0].app.state.store
    uploaded = send(registry, 'POST', '/documents/upload', actor=3, admin=True,
        data={'library_scope': 'department', 'department_id': str(registry[2]), 'document_type': 'job_instruction',
              'title': 'Инструкция ведущего менеджера', 'upload_id': str(uuid4())},
        files=[('files', ('Инструкция.docx', instruction_docx(functions)))])
    assert uploaded.status_code == 201, uploaded.text
    job = claim_job(db)
    with psycopg.connect(registry[1].database_url) as conn:
        content, media_type = conn.execute('SELECT content,media_type FROM instruction_versions WHERE id=%s',
                                           (job['version_id'],)).fetchone()
    assert complete_job(db, job['id'], job['lease_token'], extract_document(bytes(content), media_type))
    return uploaded.json()


def start(registry, document_id, edition_id, actor=3, admin=True):
    return send(registry, 'POST', f'/documents/{document_id}/analysis-runs', actor=actor, admin=admin,
                json={'edition_id': edition_id})


async def no_sleep(_seconds):
    return None


def test_full_run_produces_verified_report_and_expert_review(registry):
    settings = nova_settings(registry)
    payload = prepared_instruction(registry)
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert start(registry, document_id, edition_id).status_code == 409   # not confirmed yet
    assert verify(registry, document_id, edition_id).status_code == 200
    assert start(registry, document_id, edition_id, actor=1, admin=False).status_code == 403
    started = start(registry, document_id, edition_id)
    assert started.status_code == 202, started.text
    run_id = started.json()['id']
    assert started.json()['status'] == 'queued'
    assert start(registry, document_id, edition_id).json()['id'] == run_id   # one active run

    db = registry[0].app.state.store
    run = claim_run(db, 900)
    assert process(db, settings, run, stub_client(settings), no_sleep)
    report = send(registry, 'GET', f'/analysis-runs/{run_id}', admin=True, actor=3).json()
    assert report['status'] == 'completed', report
    result = report['result']
    pick = lambda words: next(item for item in result['functions'] if words in item['text'])
    assert len(result['functions']) == 3
    checks = pick('Проверяет платёжные')
    assert checks['status'] == 'covered'
    assert checks['quotes'] and all(quote['verified'] for quote in checks['quotes'])
    assert pick('Организует корпорат')['status'] == 'not_found'
    assert result['regulation']['complete'] is False
    assert result['counts']['covered'] >= 1 and result['summary']['text']
    assert result['requests']['model'] >= 2 and all(item['request_id'].startswith('ph-')
                                                    for item in result['requests']['trace'])
    assert set(report['progress']) >= {'instruction', 'retrieval', 'matching', 'summary'}

    listed = send(registry, 'GET', f'/documents/{document_id}/analysis-runs', actor=1).json()
    assert listed['items'][0]['counts']['covered'] == result['counts']['covered']
    review = lambda **data: send(registry, 'POST', f'/analysis-runs/{run_id}/reviews', actor=3, admin=True, json=data)
    assert review(finding_id='F3', decision='rejected').status_code == 422      # reason required
    assert review(finding_id='F99', decision='confirmed').status_code == 404
    assert review(finding_id='F3', decision='clarify', comment='Уточнить у владельца положения').status_code == 200
    assert review(finding_id='F3', decision='confirmed').status_code == 200
    reviews = send(registry, 'GET', f'/analysis-runs/{run_id}', admin=True, actor=3).json()['reviews']
    assert reviews['F3']['decision'] == 'confirmed'
    with psycopg.connect(registry[1].database_url) as conn:
        assert conn.execute('SELECT count(*) FROM analysis_finding_reviews').fetchone()[0] == 2
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE analysis_runs SET result='{}'::jsonb WHERE id=%s", (run_id,))


def test_run_inputs_carry_what_the_pdf_structure_needs(registry):
    # PDF instructions are structured from the prepared text, which needs the
    # source hash, parser version and warnings (KeyError: 'content_hash' before).
    nova_settings(registry)
    payload = prepared_instruction(registry)
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert verify(registry, document_id, edition_id).status_code == 200
    assert start(registry, document_id, edition_id).status_code == 202
    db = registry[0].app.state.store
    source = run_inputs(db, claim_run(db, 900))[3]
    assert build_functions(source)['content_hash'] == source['content_hash']


def test_delegated_analyst_and_cancellation(registry):
    nova_settings(registry)
    payload = prepared_instruction(registry)
    document_id, edition_id = payload['document']['id'], payload['edition']['id']
    assert verify(registry, document_id, edition_id).status_code == 200
    granular_role(registry, ['documents.read', 'comparisons.manage'], user=4, name='Аналитик сверки')
    started = start(registry, document_id, edition_id, actor=4, admin=False)
    assert started.status_code == 202, started.text
    cancelled = send(registry, 'POST', f"/analysis-runs/{started.json()['id']}/cancel", actor=4)
    assert cancelled.json()['status'] == 'cancelled'
    assert claim_run(registry[0].app.state.store, 900) is None


def test_nova_not_configured_is_explained(registry):
    payload = prepared_instruction(registry)
    assert verify(registry, payload['document']['id'], payload['edition']['id']).status_code == 200
    response = start(registry, payload['document']['id'], payload['edition']['id'])
    assert response.status_code == 503 and 'Nova не подключена' in response.json()['detail']
    assert send(registry, 'GET', '/nova/status', actor=1).json() == {'configured': False, 'collection': None}


def test_unreachable_nova_fails_the_run_with_a_clear_reason(registry):
    settings = nova_settings(registry)
    payload = prepared_instruction(registry)
    assert verify(registry, payload['document']['id'], payload['edition']['id']).status_code == 200
    run_id = start(registry, payload['document']['id'], payload['edition']['id']).json()['id']
    def refuse(request):
        raise httpx.ConnectError('blocked by firewall')
    db = registry[0].app.state.store
    process(db, settings, claim_run(db, 900), NovaClient(settings, transport=httpx.MockTransport(refuse)), no_sleep)
    run = send(registry, 'GET', f'/analysis-runs/{run_id}', admin=True, actor=3).json()
    assert run['status'] == 'failed' and run['error_code'] == 'nova_unreachable'
    assert 'сетевого доступа' in run['error_message']


def test_binding_keeps_only_the_department_regulation(registry):
    async def check():
        settings = dataclasses.replace(registry[1], nova_url='http://nova.test', nova_key=nova_stub.KEY,
                                       nova_collection=nova_stub.COLLECTION)
        client = stub_client(settings)
        try:
            context = {'functions': [{'text': 'Оформление документов о приёме сотрудников на работу. '
                                              'Проверка платёжных документов клиентов на соответствие требованиям.'}],
                       'department_name': 'Операционный департамент', 'position_name': None}
            free = await analyze_instruction(context, NovaCaller(client, 'ph-test', no_sleep), lambda *_: None)
            bound = await analyze_instruction({**context, 'binding_document': nova_stub.DOCUMENT},
                                              NovaCaller(client, 'ph-test', no_sleep), lambda *_: None)
        finally:
            await client.close()
        assert {item['name'] for item in free['regulation']['documents']} >= {nova_stub.DOCUMENT, nova_stub.OTHER}
        assert any('нескольких документов' in warning for warning in free['warnings'])
        assert all(clause['document'] == nova_stub.DOCUMENT for clause in bound['clauses'].values())
    asyncio.run(check())


class FakeNova:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def search_candidates(self, query, request_id=None):
        self.calls.append(('search', request_id))
        return SearchResult(collection='c', chunks=[{'index': 1, 'score': 0.9, 'text': 'Departament to‘lovlarni amalga oshiradi va hisobot tayyorlaydi', 'metadata': {'document': 'Nizom', 'clause': '9-band'}}]), 'nova-id'

    async def analyze(self, system_prompt, text, temperature=None, request_id=None):
        self.calls.append(('model', request_id))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, 'nova-id'


def test_retry_rules_follow_gateway_documentation():
    pauses = []
    async def sleep(seconds):
        pauses.append(seconds)
    async def check():
        nova = FakeNova([NovaError('http_429', 'busy', retry_after='7'), NovaError('http_502', 'down'), '{"ok":1}'])
        caller = NovaCaller(nova, 'ph-x', sleep)
        assert await caller.model('s', 't') == '{"ok":1}'
        assert pauses == [7, 15]
        with pytest.raises(NovaError):
            await NovaCaller(FakeNova([NovaError('http_401', 'key')]), 'ph-x', sleep).model('s', 't')
        failing = FakeNova([NovaError('http_502', 'down')] * 3)
        with pytest.raises(NovaError):
            await NovaCaller(failing, 'ph-x', sleep).model('s', 't')
        assert len(failing.calls) == 3
    asyncio.run(check())


def test_invalid_model_answers_become_unresolved_not_invented():
    async def check():
        nova = FakeNova(['не JSON', '{"results":[{"function_id":"F9","status":"covered"}]}',
                         '{"results":[]}', '{"results":[]}', 'nope', 'nope'])
        result = await analyze_instruction({'functions': [{'text': 'Проводит платежи клиентов банка'}],
                                            'department_name': 'Д', 'position_name': None},
                                           NovaCaller(nova, 'ph-x', no_sleep), lambda *_: None)
        assert result['functions'][0]['status'] == 'unresolved'
        assert result['summary']['generated_by'] == 'rules'
    asyncio.run(check())


def test_quotes_must_be_verbatim():
    clause = 'Departament to‘lovlarni belgilangan limitlar doirasida amalga oshiradi.'
    assert quote_verified("to'lovlarni belgilangan limitlar doirasida", clause)
    assert not quote_verified('to‘lovlarni cheksiz amalga oshiradi', clause)
    assert not quote_verified('limitlar', clause)
    assert normalize('A­B  «c»') == 'ab "c"'


def test_instruction_without_functions_is_reported():
    async def check():
        with pytest.raises(AnalysisFailed):
            await analyze_instruction({'functions': []}, NovaCaller(FakeNova([]), 'ph-x', no_sleep), lambda *_: None)
    asyncio.run(check())


def test_library_overview_counts_work_waiting_per_department(registry):
    settings = nova_settings(registry)
    payload = prepared_instruction(registry)
    overview = {row['department_id']: row for row in send(registry, 'GET', '/library/overview', actor=1).json()}
    assert overview[str(registry[2])] == {'department_id': str(registry[2]), 'instructions': 1, 'confirmed': 0,
                                          'awaiting': 1, 'analyzed': 0, 'pending': 0}
    assert str(registry[4]) not in overview          # user 1 cannot read the other department
    assert verify(registry, payload['document']['id'], payload['edition']['id']).status_code == 200
    start(registry, payload['document']['id'], payload['edition']['id'])
    db = registry[0].app.state.store
    process(db, settings, claim_run(db, 900), stub_client(settings), no_sleep)
    row = {item['department_id']: item for item in send(registry, 'GET', '/library/overview', actor=1).json()}[str(registry[2])]
    assert (row['confirmed'], row['awaiting'], row['analyzed']) == (1, 0, 1) and row['pending'] >= 1


def registry_rows(registry, *, actor=3, admin=True, department=None):
    return send(registry, 'GET', f'/departments/{department or registry[2]}/analysis-registry', actor=actor, admin=admin)


def test_registry_lists_active_department_instructions_with_readiness(registry):
    from datetime import date, timedelta
    from test_documents import upload
    source = upload(registry, document_type='job_instruction', title='Инструкция специалиста',
                    role_id=str(registry[3])).json()
    assert upload(registry, title='Обычный порядок').status_code == 201
    assert upload(registry, library_scope='bank', document_type='job_instruction', title='Банковская',
                  actor=3, admin=True).status_code == 201
    deleted = upload(registry, document_type='job_instruction', title='Удалённая').json()
    assert send(registry, 'POST', f"/documents/{deleted['document']['id']}/delete", actor=3, admin=True,
                json={'reason': 'Проверка исключения'}).status_code == 200
    rows = registry_rows(registry).json()['items']
    assert [row['document_id'] for row in rows] == [source['document']['id']]
    assert rows[0]['eligibility'] == 'unverified' and rows[0]['analysis'] is None
    assert rows[0]['role_name'] == 'Главный специалист'
    assert registry_rows(registry, actor=2, admin=False).status_code == 200
    assert registry_rows(registry, actor=2, admin=False, department=registry[4]).status_code == 404

    today = date.today()
    assert verify(registry, source['document']['id'], source['edition']['id'],
                  valid_from=(today + timedelta(days=2)).isoformat()).status_code == 200
    assert registry_rows(registry).json()['items'][0]['eligibility'] == 'not_current'
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute("INSERT INTO instructions(id,department_id,title,created_by) VALUES(%s,%s,'Без файла',3)",
                     (uuid4(), registry[2]))
    assert {row['eligibility'] for row in registry_rows(registry).json()['items']} == {'not_current', 'missing_edition'}


def test_registry_shows_latest_analysis_and_expert_progress(registry):
    settings = nova_settings(registry)
    payload = prepared_instruction(registry)
    assert verify(registry, payload['document']['id'], payload['edition']['id']).status_code == 200
    run_id = start(registry, payload['document']['id'], payload['edition']['id']).json()['id']
    db = registry[0].app.state.store
    process(db, settings, claim_run(db, 900), stub_client(settings), no_sleep)
    row = registry_rows(registry).json()['items'][0]
    assert row['eligibility'] == 'ready' and row['analysis']['id'] == run_id
    assert row['analysis']['status'] == 'completed' and row['analysis']['reviewed'] == 0
    assert row['analysis']['findings'] >= 1
    send(registry, 'POST', f'/analysis-runs/{run_id}/reviews', actor=3, admin=True,
         json={'finding_id': 'F3', 'decision': 'confirmed'})
    assert registry_rows(registry).json()['items'][0]['analysis']['reviewed'] == 1


def test_manual_comparison_endpoints_are_gone(registry):
    identifier = uuid4()
    assert send(registry, 'GET', f'/departments/{registry[2]}/comparison-workflows', actor=3, admin=True).status_code == 404
    assert send(registry, 'POST', f'/documents/{identifier}/comparison-workflows', actor=3, admin=True,
                json={}).status_code in (404, 405)
