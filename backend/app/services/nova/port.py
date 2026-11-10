"""Порт Nova: чем платформа пользуется в Nova RAG Gateway, без подробностей HTTP.

Сверке инструкций нужны три вещи: найти пункты положений (``retrieve``),
спросить модель по своим указаниям (``complete``) и проверить, что ключ
видит нужную базу знаний (``models``). Кто их выполняет — HTTP-клиент к
Gateway или заглушка без сети, — решают настройки (``settings.get_nova``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Protocol, Sequence

#: Сообщение для модели в формате OpenAI: роль ``user`` или ``assistant`` и текст.
Message = Dict[str, str]

ErrorKind = Literal[
    'not_configured',  # не заданы адрес и ключ
    'network',         # нет связи: адрес, порт, межсетевой экран
    'timeout',         # Nova не ответила за отведённое время
    'auth',            # 401: ключ неверный
    'access',          # 403: у ключа нет доступа к базе знаний
    'bad_request',     # 400, 422: запрос не по формату
    'rate_limited',    # 429: повторы не помогли
    'unavailable',     # 502: недоступен поиск или модель
    'internal',        # 500 и всё непредвиденное
]


@dataclass(frozen=True)
class NovaChunk:
    """Пункт положения, найденный поиском: текст и где он стоит в документе."""

    index: int
    #: Насколько пункт подходит к запросу — не вероятность правильности.
    score: float
    text: str
    #: document, chapter, clause, version, source_file, language — любое может отсутствовать.
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def document(self) -> str:
        return self.metadata.get('document', '')

    @property
    def chapter(self) -> str:
        return self.metadata.get('chapter', '')

    @property
    def clause(self) -> str:
        return self.metadata.get('clause', '')


class NovaError(Exception):
    """Запрос к Nova не удался. ``request_id`` (X-Request-ID) называют ДИИ при обращении."""

    def __init__(self, kind: ErrorKind, message: str, status: Optional[int] = None,
                 request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.request_id = request_id


class NovaPort(Protocol):
    def models(self) -> List[str]:
        """Базы знаний, доступные ключу (``GET /v1/models``)."""

    def retrieve(self, query: str, collection: Optional[str] = None) -> List[NovaChunk]:
        """Пункты положений под запрос — до 5, лучшие первыми; пусто — не найдено."""

    def complete(self, system_prompt: str, messages: Sequence[Message]) -> str:
        """Ответ модели по нашим указаниям, без поиска по базе знаний."""

    def health(self) -> bool:
        """Запущен ли сервис. Поиск и модель эта проверка не затрагивает."""
