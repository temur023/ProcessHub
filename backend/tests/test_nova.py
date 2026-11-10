"""Подключение к Nova RAG Gateway: правила из документации ДИИ — без сети банка.

Gateway подменяется ``httpx.MockTransport``: тесты видят, что ушло в Nova
(заголовки, тело), и проверяют, как клиент ведёт себя на каждом коде ответа.
"""
import json
import os
import threading
import time
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.nova import NovaError, get_nova, nova_settings
from app.services.nova.fake import FakeNova
from app.services.nova.http_client import (
    RATE_LIMIT_RETRIES,
    UNAVAILABLE_PAUSE,
    UNAVAILABLE_RETRIES,
    NovaHttpClient,
)

BASE = 'http://gateway.test:11450'
CHUNK = {'index': 1, 'score': 0.83, 'text': "Kassa hujjatlari tekshiriladi.",
         'metadata': {'document': 'Kassa nizomi', 'chapter': '2-bob', 'clause': '22-band', 'version': None}}


def gateway(*responses):
    """Gateway, отвечающий по очереди заданными ответами; запросы запоминаются."""
    queue = list(responses)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return reply(request) if callable(reply) else reply

    return httpx.MockTransport(handler), seen


def ok(body):
    return httpx.Response(200, json=body, headers={'X-Request-ID': 'gw-1'})


def client(transport, **kw):
    sleeps = []
    nova = NovaHttpClient(BASE, 'test-key', transport=transport, sleep=sleeps.append, **kw)
    return nova, sleeps


class RequestsTest(unittest.TestCase):
    def test_models_send_the_key_and_a_request_id(self):
        transport, seen = gateway(ok({'object': 'list', 'data': [{'id': 'ops_policies', 'object': 'model'}]}))
        nova, _ = client(transport)
        self.assertEqual(nova.models(), ['ops_policies'])
        self.assertEqual(seen[0].headers['Authorization'], 'Bearer test-key')
        self.assertTrue(seen[0].headers['X-Request-ID'].startswith('sqb-'))
        self.assertEqual(str(seen[0].url), f'{BASE}/v1/models')

    def test_platform_address_keeps_the_rag_prefix(self):
        # В Nova Platform Gateway — RAG-сервис под /api/v1/services/rag: пути клиента дописываются к нему.
        transport, seen = gateway(ok({'object': 'list', 'data': []}), ok({'collection': 'ops_policies', 'chunks': []}),
                                  ok({'choices': [{'message': {'content': 'готово'}}]}), ok({'status': 'ok'}))
        nova = NovaHttpClient(f'{BASE}/api/v1/services/rag/', 'test-key', transport=transport)
        nova.models(), nova.retrieve('касса'), nova.complete('Ответь одним словом.', [{'role': 'user', 'content': 'да'}])
        self.assertTrue(nova.health())
        self.assertEqual([r.url.path for r in seen], [f'/api/v1/services/rag{p}' for p in
                                                      ('/v1/models', '/retrieve', '/v1/chat/completions', '/health')])

    def test_retrieve_returns_clauses_with_their_place_in_the_document(self):
        transport, seen = gateway(ok({'collection': 'ops_policies', 'chunks': [CHUNK]}))
        nova, _ = client(transport)
        [chunk] = nova.retrieve('проверка кассовых документов')
        self.assertEqual(json.loads(seen[0].content), {'query': 'проверка кассовых документов', 'collection': 'ops_policies'})
        self.assertEqual((chunk.document, chunk.chapter, chunk.clause, chunk.score), ('Kassa nizomi', '2-bob', '22-band', 0.83))
        self.assertNotIn('version', chunk.metadata)   # пустые поля metadata не тащим

    def test_nothing_found_is_an_empty_list_not_an_error(self):
        transport, _ = gateway(ok({'collection': 'ops_policies', 'chunks': []}))
        nova, _ = client(transport)
        self.assertEqual(nova.retrieve('пункт, которого нет'), [])

    def test_empty_query_is_not_sent(self):
        transport, seen = gateway(ok({}))
        nova, _ = client(transport)
        with self.assertRaises(ValueError):
            nova.retrieve('   ')
        self.assertEqual(seen, [])

    def test_complete_passes_our_instructions_without_rag(self):
        transport, seen = gateway(ok({'choices': [{'message': {'role': 'assistant', 'content': 'Перевод'}}]}))
        nova, _ = client(transport)
        answer = nova.complete('Переведи на русский.', [{'role': 'user', 'content': 'Kassa hujjatlari.'}])
        self.assertEqual(answer, 'Перевод')
        body = json.loads(seen[0].content)
        self.assertEqual((body['use_rag'], body['system_prompt']), (False, 'Переведи на русский.'))
        self.assertEqual(body['messages'], [{'role': 'user', 'content': 'Kassa hujjatlari.'}])

    def test_system_role_message_is_rejected_before_sending(self):
        transport, seen = gateway(ok({}))
        nova, _ = client(transport)
        with self.assertRaises(ValueError):
            nova.complete('Указания', [{'role': 'system', 'content': 'x'}, {'role': 'user', 'content': 'y'}])
        self.assertEqual(seen, [])


class RetryRulesTest(unittest.TestCase):
    def test_429_waits_retry_after_and_repeats(self):
        transport, seen = gateway(httpx.Response(429, json={'detail': 'busy'}, headers={'Retry-After': '2'}),
                                  ok({'data': [{'id': 'ops_policies'}]}))
        nova, sleeps = client(transport)
        self.assertEqual(nova.models(), ['ops_policies'])
        self.assertEqual((len(seen), sleeps), (2, [2.0]))

    def test_429_gives_up_after_its_retries(self):
        transport, seen = gateway(httpx.Response(429, json={'detail': 'busy'}))
        nova, sleeps = client(transport)
        with self.assertRaises(NovaError) as caught:
            nova.models()
        self.assertEqual(caught.exception.kind, 'rate_limited')
        self.assertEqual((len(seen), len(sleeps)), (RATE_LIMIT_RETRIES + 1, RATE_LIMIT_RETRIES))

    def test_502_is_retried_with_a_pause(self):
        transport, seen = gateway(httpx.Response(502, json={'detail': 'Backend unavailable'}),
                                  ok({'chunks': [CHUNK]}))
        nova, sleeps = client(transport)
        self.assertEqual(len(nova.retrieve('kassa')), 1)
        self.assertEqual(sleeps, [UNAVAILABLE_PAUSE])

    def test_502_gives_up_after_one_or_two_retries(self):
        transport, seen = gateway(httpx.Response(502, json={'detail': 'LLM returned HTTP 503'}))
        nova, _ = client(transport)
        with self.assertRaises(NovaError) as caught:
            nova.retrieve('kassa')
        self.assertEqual((caught.exception.kind, caught.exception.message), ('unavailable', 'LLM returned HTTP 503'))
        self.assertEqual(len(seen), UNAVAILABLE_RETRIES + 1)

    def test_client_errors_are_not_retried(self):
        cases = [
            (401, {'detail': 'Invalid API key'}, 'auth'),
            (403, {'detail': 'No access to collection'}, 'access'),
            (400, {'detail': 'Specify knowledge base: ops_policies'}, 'bad_request'),
            (422, {'detail': [{'loc': ['body', 'query'], 'msg': 'empty'}]}, 'bad_request'),
        ]
        for status, body, kind in cases:
            with self.subTest(status=status):
                transport, seen = gateway(httpx.Response(status, json=body, headers={'X-Request-ID': 'gw-42'}))
                nova, sleeps = client(transport)
                with self.assertRaises(NovaError) as caught:
                    nova.retrieve('kassa')
                err = caught.exception
                self.assertEqual((err.kind, err.status, err.request_id), (kind, status, 'gw-42'))
                self.assertIn(str(body['detail'])[:10], err.message)
                self.assertEqual((len(seen), sleeps), (1, []))

    def test_500_may_come_without_json(self):
        transport, seen = gateway(httpx.Response(500, text='Internal Server Error'))
        nova, _ = client(transport)
        with self.assertRaises(NovaError) as caught:
            nova.models()
        self.assertEqual((caught.exception.kind, caught.exception.message), ('internal', 'Internal Server Error'))
        self.assertEqual(len(seen), 1)

    def test_timeout_and_no_network_are_named(self):
        for exc, kind in ((httpx.ReadTimeout('slow'), 'timeout'), (httpx.ConnectError('refused'), 'network')):
            with self.subTest(kind=kind):
                def fail(request, exc=exc):
                    raise exc
                nova, _ = client(httpx.MockTransport(fail))
                with self.assertRaises(NovaError) as caught:
                    nova.models()
                self.assertEqual(caught.exception.kind, kind)
                self.assertTrue(caught.exception.request_id.startswith('sqb-'))

    def test_no_more_than_max_parallel_requests_at_once(self):
        lock = threading.Lock()
        state = {'now': 0, 'peak': 0}

        def slow(request):
            with lock:
                state['now'] += 1
                state['peak'] = max(state['peak'], state['now'])
            time.sleep(0.05)
            with lock:
                state['now'] -= 1
            return ok({'chunks': []})

        nova, _ = client(httpx.MockTransport(slow), max_parallel=2)
        threads = [threading.Thread(target=nova.retrieve, args=('kassa',)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(state['peak'], 2)


def nova_env(**values):
    """Окружение без настоящих NOVA_*: тесты не должны зависеть от машины."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith('NOVA_')}
    clean.update(values)
    return mock.patch.dict(os.environ, clean, clear=True)


class SettingsTest(unittest.TestCase):
    def test_mode_follows_the_settings(self):
        with nova_env():
            self.assertEqual((nova_settings().mode, get_nova()), ('off', None))
        with nova_env(NOVA_FAKE='1'):
            self.assertEqual(nova_settings().mode, 'fake')
            self.assertIsInstance(get_nova(), FakeNova)
        with nova_env(NOVA_BASE_URL=BASE, NOVA_API_KEY='k', NOVA_MAX_PARALLEL='3', NOVA_TIMEOUT='120'):
            s = nova_settings()
            self.assertEqual((s.mode, s.max_parallel, s.timeout, s.collection), ('http', 3, 120.0, 'ops_policies'))
            self.assertIsInstance(get_nova(), NovaHttpClient)
            self.assertIs(get_nova(), get_nova())   # один клиент — один общий лимит запросов

    def test_key_without_address_stays_off(self):
        with nova_env(NOVA_API_KEY='k'):
            self.assertEqual(nova_settings().mode, 'off')

    def test_proxy_is_used_only_when_set(self):
        with nova_env(NOVA_BASE_URL=BASE, NOVA_API_KEY='k', NOVA_PROXY=' http://proxy.test:8181 '):
            self.assertEqual(nova_settings().proxy, 'http://proxy.test:8181')
            self.assertIsInstance(get_nova(), NovaHttpClient)
        with nova_env(NOVA_BASE_URL=BASE, NOVA_API_KEY='k'):
            self.assertEqual(nova_settings().proxy, '')


class StatusEndpointTest(unittest.TestCase):
    URL = '/api/v1/nova/status'

    def setUp(self):
        self.client = TestClient(app)

    def test_not_configured(self):
        with nova_env():
            body = self.client.get(self.URL).json()
        self.assertEqual((body['mode'], body['connected'], body['errorKind']), ('off', False, 'not_configured'))
        self.assertIn('NOVA_BASE_URL', body['error'])

    def test_fake_is_connected(self):
        with nova_env(NOVA_FAKE='1'):
            body = self.client.get(self.URL).json()
        self.assertEqual((body['mode'], body['connected'], body['collections'], body['collectionAvailable']),
                         ('fake', True, ['ops_policies'], True))
        self.assertIsNone(body['error'])

    def test_rejected_key_is_explained(self):
        transport, _ = gateway(httpx.Response(401, json={'detail': 'Invalid API key'}, headers={'X-Request-ID': 'gw-7'}))
        nova, _ = client(transport)
        with nova_env(NOVA_BASE_URL=BASE, NOVA_API_KEY='k'), mock.patch('app.routers.nova.get_nova', return_value=nova):
            body = self.client.get(self.URL).json()
        self.assertEqual((body['mode'], body['connected'], body['errorKind'], body['requestId']), ('http', False, 'auth', 'gw-7'))
        self.assertIn('NOVA_API_KEY', body['error'])
        self.assertIn('Invalid API key', body['error'])

    def test_key_without_the_policies_base(self):
        transport, _ = gateway(ok({'data': [{'id': 'other_base'}]}))
        nova, _ = client(transport)
        with nova_env(NOVA_BASE_URL=BASE, NOVA_API_KEY='k'), mock.patch('app.routers.nova.get_nova', return_value=nova):
            body = self.client.get(self.URL).json()
        self.assertEqual((body['connected'], body['collections'], body['collectionAvailable']), (True, ['other_base'], False))
        self.assertIn('ops_policies', body['error'])


if __name__ == '__main__':
    unittest.main()
