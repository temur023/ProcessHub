"""Настройки подключения к Nova — из переменных окружения сервиса api.

``NOVA_BASE_URL``     адрес RAG-сервиса Nova Platform, например
                      ``http://nova-gateway.internal:11450/api/v1/services/rag``; к нему дописываются /v1/…, /retrieve, /health
``NOVA_API_KEY``      ключ ``nova_…`` с https://nova.example.internal/keys; только на сервере, не в git
``NOVA_COLLECTION``   база знаний положений, по умолчанию ``ops_policies``
``NOVA_MAX_PARALLEL`` сколько запросов одновременно, по умолчанию 2
``NOVA_TIMEOUT``      тайм-аут запроса, с, по умолчанию 300
``NOVA_PROXY``        прокси только для запросов к Nova (``http://host:port``), если прямого доступа нет
``NOVA_FAKE``         ``1`` — заглушка без сети вместо Gateway (разработка)

Без адреса и ключа подключение выключено: платформа работает как раньше,
а проверка подключения говорит, чего не хватает.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Optional

from app.services.nova.fake import FakeNova
from app.services.nova.http_client import NovaHttpClient
from app.services.nova.port import NovaPort

Mode = Literal['off', 'fake', 'http']


@dataclass(frozen=True)
class NovaSettings:
    base_url: str = ''
    api_key: str = ''
    collection: str = 'ops_policies'
    timeout: float = 300.0
    max_parallel: int = 2
    fake: bool = False
    proxy: str = ''

    @property
    def mode(self) -> Mode:
        if self.fake:
            return 'fake'
        return 'http' if self.base_url and self.api_key else 'off'


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def nova_settings() -> NovaSettings:
    return NovaSettings(
        base_url=(os.environ.get('NOVA_BASE_URL') or '').strip(),
        api_key=(os.environ.get('NOVA_API_KEY') or '').strip(),
        collection=(os.environ.get('NOVA_COLLECTION') or 'ops_policies').strip(),
        timeout=_number('NOVA_TIMEOUT', 300),
        max_parallel=max(1, int(_number('NOVA_MAX_PARALLEL', 2))),
        fake=(os.environ.get('NOVA_FAKE') or '').strip().lower() in ('1', 'true', 'yes'),
        proxy=(os.environ.get('NOVA_PROXY') or '').strip(),
    )


@lru_cache(maxsize=4)
def _build(settings: NovaSettings) -> Optional[NovaPort]:
    if settings.mode == 'fake':
        return FakeNova(collections=[settings.collection])
    if settings.mode == 'http':
        return NovaHttpClient(settings.base_url, settings.api_key, settings.collection,
                              settings.timeout, settings.max_parallel, proxy=settings.proxy or None)
    return None


def get_nova() -> Optional[NovaPort]:
    """Подключение к Nova по текущим настройкам; ``None`` — не настроено.

    Один клиент на набор настроек: лимит одновременных запросов общий для
    всех запросов платформы, как и у ключа на стороне Gateway.
    """
    return _build(nova_settings())
