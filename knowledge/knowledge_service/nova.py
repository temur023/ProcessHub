"""Документированный Gateway: только поиск и модель без встроенного RAG.

Полный источник намеренно не имитируется повтором /retrieve. Для него нужен
отдельно согласованный контракт Nova, см. docs/knowledge/implementation.md.
"""
import hashlib
import json
import re

import httpx
from pydantic import BaseModel, Field, ValidationError


class NovaError(Exception):
    def __init__(self, code, message, request_id=None, retry_after=None):
        super().__init__(message)
        self.code, self.request_id, self.retry_after = code, request_id, retry_after


class Chunk(BaseModel):
    index: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    score: float | None = None
    metadata: dict = Field(default_factory=dict)


class SearchResult(BaseModel):
    collection: str
    chunks: list[Chunk] = Field(max_length=100)


def _request_id(value):
    # Our own ID lets the AI department find a request in Gateway logs.
    return {'X-Request-ID': value} if value and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) else {}


class NovaClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.nova_url or 'http://unconfigured.invalid',
            headers={'Authorization': f'Bearer {settings.nova_key}'}, timeout=httpx.Timeout(300, connect=10),
            follow_redirects=False, trust_env=False, transport=transport, proxy=settings.nova_proxy or None)

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        if not self.settings.nova_configured:
            raise NovaError('not_configured', 'Заполните адрес, ключ и коллекцию Nova')
        try:
            async with self.client.stream(method, path, **kwargs) as response:
                request_id = response.headers.get('x-request-id')
                if request_id and not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', request_id):
                    request_id = None
                retry_after = response.headers.get('retry-after')
                if retry_after and (not retry_after.isdecimal() or len(retry_after) > 4):
                    retry_after = None
                if response.status_code != 200:
                    raise NovaError(f'http_{response.status_code}', 'Nova отклонила запрос', request_id, retry_after)
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 4 * 1024 * 1024:
                        raise NovaError('response_too_large', 'Ответ Nova превышает допустимый размер', request_id)
        except httpx.TimeoutException:
            # Тайм-аут не означает, что Nova не приняла запрос.
            raise NovaError('timeout', 'Nova не ответила в установленное время') from None
        except httpx.HTTPError:
            raise NovaError('unreachable', 'Нет связи с Nova') from None
        try:
            return json.loads(payload), request_id
        except ValueError:
            raise NovaError('invalid_response', 'Nova вернула некорректный ответ', request_id) from None

    async def search_candidates(self, query, request_id=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 20_000:
            raise NovaError('invalid_query', 'Поисковый запрос пуст')
        data, request_id = await self.request('POST', '/retrieve', json={
            'query': query, 'collection': self.settings.nova_collection}, headers=_request_id(request_id))
        try:
            result = SearchResult.model_validate(data)
            if result.collection != self.settings.nova_collection:
                raise ValueError()
        except (ValidationError, ValueError):
            raise NovaError('invalid_response', 'Nova вернула некорректные источники', request_id) from None
        return result, request_id

    async def search_queries(self, queries, max_queries=6):
        """Ограниченный подбор кандидатов; никогда не утверждает полноту документа."""
        unique_queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if not unique_queries or len(unique_queries) > max_queries:
            raise NovaError('query_budget', 'Превышен бюджет запросов для одной функции')
        chunks, seen, trace = [], set(), []
        for query in unique_queries:
            result, request_id = await self.search_candidates(query)
            added = 0
            for chunk in result.chunks:
                # Документ/версия остаются частью ключа; одинаковый текст разных
                # источников нельзя сливать. Все прочие метаданные сохраняются.
                key = (json.dumps(chunk.metadata, sort_keys=True, ensure_ascii=False),
                       hashlib.sha256(chunk.text.encode()).hexdigest())
                known_source = bool(chunk.metadata.get('document') and chunk.metadata.get('version'))
                if not known_source or key not in seen:
                    seen.add(key)
                    chunks.append(chunk)
                    added += 1
            trace.append({'request_id': request_id, 'returned': len(result.chunks), 'added': added})
        return {'chunks': chunks, 'trace': trace, 'source_complete': False}

    async def get_policy_snapshot(self, document_id, version_id):
        raise NovaError('full_source_unavailable', 'Полный источник требует согласованного API или экспорта Nova')

    async def analyze(self, system_prompt, text, *, temperature=None, request_id=None):
        if (not isinstance(system_prompt, str) or not system_prompt.strip() or len(system_prompt) > 10_000
                or not isinstance(text, str) or not text.strip() or len(text) > 200_000):
            raise NovaError('invalid_input', 'Укажите инструкции модели и текст в пределах бюджета запроса')
        body = {'model': 'any', 'use_rag': False, 'stream': False, 'system_prompt': system_prompt,
                'messages': [{'role': 'user', 'content': text}]}
        if temperature is not None:
            body['temperature'] = temperature
        data, request_id = await self.request('POST', '/v1/chat/completions', json=body,
                                              headers=_request_id(request_id))
        try:
            content = data['choices'][0]['message']['content']
            if data['choices'][0].get('finish_reason') not in (None, 'stop'):
                raise NovaError('incomplete_response', 'Nova не завершила ответ модели', request_id)
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise NovaError('invalid_response', 'Nova вернула некорректный ответ модели', request_id) from None
        # Это сырой ответ: до публикации находок обязательны схема и цитаты.
        return content, request_id

    async def probe(self):
        health, health_id = await self.request('GET', '/health')
        models, models_id = await self.request('GET', '/v1/models')
        if not isinstance(health, dict) or not isinstance(models, dict) or not isinstance(models.get('data'), list):
            raise NovaError('invalid_response', 'Некорректный диагностический ответ Nova')
        collections = [x.get('id') for x in models['data'] if isinstance(x, dict)]
        return {'gateway_reachable': True, 'collection_available': self.settings.nova_collection in collections,
                'search_verified': False, 'model_verified': False, 'full_source_verified': False,
                'request_ids': [health_id, models_id]}

    async def check_integration(self, query, model_check=False):
        """Explicit operator check. Output contains no queries or source text."""
        report = await self.probe()
        if not report['collection_available']:
            raise NovaError('collection_unavailable', 'У ключа Nova нет выбранной базы знаний')
        result, request_id = await self.search_candidates(query)
        report.update(search_reachable=True, search_verified=bool(result.chunks),
                      returned_chunks=len(result.chunks),
                      sources_with_document=sum(bool(c.metadata.get('document')) for c in result.chunks),
                      sources_with_version=sum(bool(c.metadata.get('version')) for c in result.chunks),
                      sources_with_clause=sum(bool(c.metadata.get('clause')) for c in result.chunks))
        report['request_ids'].append(request_id)
        if model_check:
            # Synthetic smoke test, with no bank document sent to the model.
            _, model_id = await self.analyze('Ответь одним словом на русском языке.', 'Напиши слово: готово')
            report.update(model_verified=True, model_quality_verified=False)
            report['request_ids'].append(model_id)
        return report
