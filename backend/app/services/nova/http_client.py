"""Nova RAG Gateway по HTTP — по правилам из документации ДИИ.

* Ключ — в заголовке ``Authorization: Bearer``; он живёт только на сервере.
* У каждого запроса свой ``X-Request-ID``: он пишется в журнал платформы, и
  по нему ДИИ находит запрос, найденные пункты и время ответа.
* 429 (слишком много запросов одновременно) — ждём ``Retry-After`` и
  повторяем; 502 (недоступен поиск или модель) — 1–2 повтора с паузой.
  400, 401, 403, 422 и 500 не повторяются: повтор их не исправит.
* Одновременно — не больше ``max_parallel`` запросов: ограничение ключа
  считается по запросам в работе, и пакетная сверка иначе упиралась бы в 429.
* Тайм-аут — 300 с: под нагрузкой ответ модели бывает долгим.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx

from app.services.nova.port import ErrorKind, Message, NovaChunk, NovaError

log = logging.getLogger(__name__)

#: Сколько раз повторять при 429: Gateway просит подождать и повторить.
RATE_LIMIT_RETRIES = 3
#: Пауза при 429, если Gateway не прислал ``Retry-After``, с.
DEFAULT_RETRY_AFTER = 5.0
#: Повторов при 502 — «1–2 раза с паузой 10–30 с».
UNAVAILABLE_RETRIES = 2
UNAVAILABLE_PAUSE = 15.0

_KINDS: Dict[int, ErrorKind] = {
    400: 'bad_request', 401: 'auth', 403: 'access', 422: 'bad_request',
    429: 'rate_limited', 502: 'unavailable',
}


class NovaHttpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        collection: str = 'ops_policies',
        timeout: float = 300.0,
        max_parallel: int = 2,
        proxy: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.collection = collection
        # proxy — только для этого клиента: остальные запросы сервиса идут как раньше.
        self._client = httpx.Client(
            base_url=base_url.rstrip('/'), timeout=timeout, transport=transport, proxy=proxy,
            headers={'Authorization': f'Bearer {api_key}'},
        )
        self._slots = threading.BoundedSemaphore(max(1, max_parallel))
        self._sleep = sleep

    def models(self) -> List[str]:
        data = self._request('GET', '/v1/models')
        return [m['id'] for m in data.get('data') or [] if isinstance(m, dict) and m.get('id')]

    def retrieve(self, query: str, collection: Optional[str] = None) -> List[NovaChunk]:
        if not query.strip():
            raise ValueError('Пустой запрос поиска: Gateway ответил бы 422')
        data = self._request('POST', '/retrieve', json={'query': query, 'collection': collection or self.collection})
        return [
            NovaChunk(
                index=int(c.get('index') or i + 1),
                score=float(c.get('score') or 0),
                text=str(c.get('text') or ''),
                metadata={k: str(v) for k, v in (c.get('metadata') or {}).items() if v is not None},
            )
            for i, c in enumerate(data.get('chunks') or [])
            if isinstance(c, dict)
        ]

    def complete(self, system_prompt: str, messages: Sequence[Message]) -> str:
        if any(m.get('role') == 'system' for m in messages):
            # Вместе с system_prompt это 400, а без него модель получит два системных сообщения.
            raise ValueError('Указания модели передаются в system_prompt, а не сообщением с ролью system')
        data = self._request('POST', '/v1/chat/completions', json={
            'use_rag': False, 'system_prompt': system_prompt, 'messages': list(messages),
        })
        try:
            return data['choices'][0]['message']['content'] or ''
        except (KeyError, IndexError, TypeError) as err:
            raise NovaError('internal', 'Ответ Nova без текста модели') from err

    def health(self) -> bool:
        try:
            self._request('GET', '/health', retry=False)
        except NovaError:
            return False
        return True

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None,
                 retry: bool = True) -> Dict[str, Any]:
        rate_limited = unavailable = 0
        with self._slots:
            while True:
                request_id = f'sqb-{uuid.uuid4().hex[:24]}'
                try:
                    res = self._client.request(method, path, json=json, headers={'X-Request-ID': request_id})
                except httpx.TimeoutException as err:
                    log.warning('nova %s %s: тайм-аут (X-Request-ID %s)', method, path, request_id)
                    raise NovaError('timeout', f'нет ответа за {self._client.timeout.read:g} с',
                                    request_id=request_id) from err
                except httpx.TransportError as err:
                    log.warning('nova %s %s: нет связи — %s', method, path, err)
                    raise NovaError('network', str(err) or type(err).__name__, request_id=request_id) from err
                request_id = res.headers.get('X-Request-ID', request_id)
                log.info('nova %s %s → %s (X-Request-ID %s)', method, path, res.status_code, request_id)
                if res.status_code == 200:
                    try:
                        body = res.json()
                    except ValueError as err:
                        raise NovaError('internal', 'Nova ответила не в JSON', 200, request_id) from err
                    return body if isinstance(body, dict) else {'data': body}
                if retry and res.status_code == 429 and rate_limited < RATE_LIMIT_RETRIES:
                    rate_limited += 1
                    self._sleep(_retry_after(res))
                    continue
                if retry and res.status_code == 502 and unavailable < UNAVAILABLE_RETRIES:
                    unavailable += 1
                    self._sleep(UNAVAILABLE_PAUSE)
                    continue
                raise NovaError(_KINDS.get(res.status_code, 'internal'), _detail(res), res.status_code, request_id)


def _retry_after(res: httpx.Response) -> float:
    try:
        return max(0.0, float(res.headers.get('Retry-After', DEFAULT_RETRY_AFTER)))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def _detail(res: httpx.Response) -> str:
    """Причина ошибки: ``detail`` из JSON, а у 500 — начало текста (он может быть не в JSON)."""
    try:
        detail = res.json().get('detail')
    except (ValueError, AttributeError):
        detail = None
    if detail is None:
        return (res.text or f'HTTP {res.status_code}').strip()[:300]
    return detail if isinstance(detail, str) else str(detail)[:300]
