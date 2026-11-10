import asyncio
import json

import httpx
import pytest

from knowledge_service.config import Settings
from knowledge_service.nova import NovaClient, NovaError

CONFIG = Settings('unused', 'x' * 32, 'http://nova.test', 'test-key', 'policies')


def run(coro):
    return asyncio.run(coro)


def test_multiple_queries_exceed_five_without_claiming_completeness():
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        offset = 0 if body['query'] == 'русский' else 4
        chunks = [dict(index=i+1, text=f'пункт {i}', metadata={'document': 'policy', 'version': '1', 'clause': str(i)}) for i in range(offset, offset + 5)]
        return httpx.Response(200, json={'collection': 'policies', 'chunks': chunks}, headers={'X-Request-ID': str(offset)})
    async def check():
        client = NovaClient(CONFIG, httpx.MockTransport(respond))
        try:
            data = await client.search_queries(['русский', 'узбекский', 'русский'])
            assert len(data['chunks']) == 9
            assert data['source_complete'] is False
            assert len(calls) == 2
            assert calls[0] == {'query': 'русский', 'collection': 'policies'}
            with pytest.raises(NovaError, match='бюджет'):
                await client.search_queries([str(i) for i in range(7)])
            with pytest.raises(NovaError) as error:
                await client.get_policy_snapshot('policy', '1')
            assert error.value.code == 'full_source_unavailable'
        finally:
            await client.close()
    run(check())


def test_missing_metadata_is_not_deduplicated_as_known_source():
    async def check():
        client = NovaClient(CONFIG, httpx.MockTransport(lambda r: httpx.Response(200, json={'collection': 'policies', 'chunks': [{'index': 1, 'text': 'same'}]})))
        try:
            assert len((await client.search_queries(['one', 'two']))['chunks']) == 2
        finally:
            await client.close()
    run(check())


@pytest.mark.parametrize('status', [400, 401, 403, 422, 429, 500, 502])
def test_errors_are_not_retried_or_leaked(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={'detail': 'bank document secret'}, headers={'Retry-After': '12', 'X-Request-ID': 'r-123'})
    async def check():
        client = NovaClient(CONFIG, httpx.MockTransport(respond))
        try:
            with pytest.raises(NovaError) as error:
                await client.search_candidates('function')
            assert error.value.code == f'http_{status}'
            assert error.value.retry_after == '12'
            assert error.value.request_id == 'r-123'
            assert 'secret' not in str(error.value)
            assert len(calls) == 1
        finally:
            await client.close()
    run(check())


def test_empty_search_and_model_payload():
    def respond(request):
        body = json.loads(request.content)
        if request.url.path == '/retrieve':
            return httpx.Response(200, json={'collection': 'policies', 'chunks': []})
        assert body['use_rag'] is False and body['stream'] is False
        assert body['system_prompt'] == 'Compare as data'
        assert body['messages'] == [{'role': 'user', 'content': 'instruction'}]
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"draft":true}'}}]})
    async def check():
        client = NovaClient(CONFIG, httpx.MockTransport(respond))
        try:
            result, _ = await client.search_candidates('function')
            assert result.chunks == []
            content, _ = await client.analyze('Compare as data', 'instruction')
            assert content == '{"draft":true}'
        finally:
            await client.close()
    run(check())


def test_timeout_and_malformed_response():
    async def check():
        for response, code in [(httpx.Response(200, json={'wrong': True}), 'invalid_response'), (httpx.Response(200, text='bad'), 'invalid_response')]:
            client = NovaClient(CONFIG, httpx.MockTransport(lambda r: response))
            try:
                with pytest.raises(NovaError) as error:
                    await client.search_candidates('function')
                assert error.value.code == code
            finally:
                await client.close()
        def timeout(request):
            raise httpx.ReadTimeout('private request', request=request)
        client = NovaClient(CONFIG, httpx.MockTransport(timeout))
        try:
            with pytest.raises(NovaError) as error:
                await client.search_candidates('function')
            assert error.value.code == 'timeout'
        finally:
            await client.close()
    run(check())


def test_probe_does_not_claim_search_or_model_quality():
    def respond(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'status': 'ok'})
        assert request.url.path == '/v1/models'
        return httpx.Response(200, json={'data': [{'id': 'policies'}]})
    async def check():
        client = NovaClient(CONFIG, httpx.MockTransport(respond))
        try:
            result = await client.probe()
            assert result['gateway_reachable'] and result['collection_available']
            assert not result['search_verified'] and not result['model_verified'] and not result['full_source_verified']
        finally:
            await client.close()
    run(check())


def test_explicit_check_records_readiness_without_printing_bank_text():
    def respond(request):
        if request.url.path == '/health':
            return httpx.Response(200,json={'status':'ok'})
        if request.url.path == '/v1/models':
            return httpx.Response(200,json={'data':[{'id':'policies'}]})
        if request.url.path == '/retrieve':
            return httpx.Response(200,json={'collection':'policies','chunks':[{'index':1,'text':'private bank policy','metadata':{'document':'policy'}}]})
        assert json.loads(request.content)['use_rag'] is False
        return httpx.Response(200,json={'choices':[{'message':{'content':'готово'},'finish_reason':'stop'}]})
    async def check():
        client = NovaClient(CONFIG,httpx.MockTransport(respond))
        try:
            report = await client.check_integration('bank query',True)
            assert report['search_verified'] and report['model_verified']
            assert report['sources_with_version'] == 0
            assert not report['full_source_verified'] and not report['model_quality_verified']
            assert 'private' not in json.dumps(report) and 'bank query' not in json.dumps(report)
        finally:
            await client.close()
    run(check())


def test_platform_address_keeps_the_rag_prefix():
    # В Nova Platform Gateway — RAG-сервис под /api/v1/services/rag: пути клиента дописываются к нему.
    seen = []
    def respond(request):
        seen.append(request.url.path)
        path = request.url.path.removeprefix('/api/v1/services/rag')
        if path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'policies'}]})
        if path == '/retrieve':
            return httpx.Response(200, json={'collection': 'policies', 'chunks': []})
        if path == '/v1/chat/completions':
            return httpx.Response(200, json={'choices': [{'message': {'content': 'готово'}, 'finish_reason': 'stop'}]})
        return httpx.Response(200, json={'status': 'ok'})
    settings = Settings('unused', 'x' * 32, 'http://nova.test/api/v1/services/rag', 'test-key', 'policies')
    async def check():
        client = NovaClient(settings, httpx.MockTransport(respond))
        try:
            await client.check_integration('касса', model_check=True)
        finally:
            await client.close()
    run(check())
    assert seen == [f'/api/v1/services/rag{p}' for p in ('/health', '/v1/models', '/retrieve', '/v1/chat/completions')]


def test_wrong_collection_and_incomplete_model_response_fail_safely():
    async def check():
        client = NovaClient(CONFIG,httpx.MockTransport(lambda r:httpx.Response(200,json={'collection':'foreign','chunks':[]})))
        try:
            with pytest.raises(NovaError):
                await client.search_candidates('query')
        finally:
            await client.close()
        client = NovaClient(CONFIG,httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'partial answer'},'finish_reason':'length'}]})))
        try:
            with pytest.raises(NovaError) as error:
                await client.analyze('Compare','source')
            assert error.value.code == 'incomplete_response'
        finally:
            await client.close()
    run(check())
