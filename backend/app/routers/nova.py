"""Проверка подключения к Nova: дошли ли до Gateway, принят ли ключ, видна ли база положений."""
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.nova import NovaError, get_nova, nova_settings

router = APIRouter(prefix="/nova", tags=["nova"])

#: Что сказать сотруднику, кроме причины от самой Nova, — по виду ошибки.
HINTS: Dict[str, str] = {
    'not_configured': 'Подключение к Nova не настроено: задайте NOVA_BASE_URL и NOVA_API_KEY.',
    'network': 'Нет связи с Nova: проверьте адрес и порт в NOVA_BASE_URL и доступ, выданный ИБ.',
    'timeout': 'Nova не ответила вовремя: сервис перегружен или недоступна сеть.',
    'auth': 'Nova не приняла ключ (401): проверьте NOVA_API_KEY или запросите ключ в ДИИ.',
    'access': 'У ключа нет доступа к базе знаний (403): запросите доступ в ДИИ.',
    'rate_limited': 'Слишком много запросов к Nova одновременно (429): повторите позже.',
    'unavailable': 'В Nova недоступен поиск или модель (502): повторите позже или сообщите в ДИИ.',
    'bad_request': 'Nova отклонила запрос как неверный.',
    'internal': 'Внутренняя ошибка Nova: сообщите в ДИИ номер запроса.',
}


class NovaStatus(BaseModel):
    #: off — не настроено, fake — заглушка без сети, http — настоящий Gateway.
    mode: Literal['off', 'fake', 'http']
    connected: bool
    #: База знаний положений, с которой работает сверка.
    collection: str
    #: Базы знаний, доступные ключу.
    collections: List[str] = []
    collectionAvailable: bool = False
    error: Optional[str] = None
    errorKind: Optional[str] = None
    #: X-Request-ID неудачного запроса — его называют ДИИ.
    requestId: Optional[str] = None


@router.get("/status", response_model=NovaStatus, summary="Подключение к Nova: связь, ключ и база знаний положений")
def nova_status():
    settings = nova_settings()
    nova = get_nova()
    if nova is None:
        return NovaStatus(mode='off', connected=False, collection=settings.collection,
                          error=HINTS['not_configured'], errorKind='not_configured')
    try:
        collections = nova.models()
    except NovaError as err:
        hint = HINTS.get(err.kind, HINTS['internal'])
        # Код ответа есть — причину назвала сама Nova; нет — до неё не дошли.
        source = 'Ответ Nova' if err.status else 'Подробности'
        return NovaStatus(mode=settings.mode, connected=False, collection=settings.collection,
                          error=f'{hint} {source}: {err.message}', errorKind=err.kind, requestId=err.request_id)
    return NovaStatus(mode=settings.mode, connected=True, collection=settings.collection,
                      collections=collections, collectionAvailable=settings.collection in collections,
                      error=None if settings.collection in collections else
                      f'Ключ не видит базу знаний {settings.collection}: запросите доступ в ДИИ.')
