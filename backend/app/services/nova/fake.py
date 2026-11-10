"""Nova без сети: готовые пункты положений и ответ модели.

У разработчиков нет доступа к сети банка, а сверку надо собирать и
проверять до того, как ИБ откроет доступ. Заглушка ведёт себя как Gateway
по форме ответа и запоминает запросы — тесты проверяют, что ушло в Nova.
Тексты пунктов — образцы, не настоящие положения банка.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from app.services.nova.port import Message, NovaChunk

SAMPLE_CHUNKS: Tuple[NovaChunk, ...] = (
    NovaChunk(index=1, score=0.81, text="Namuna: bo'lim xodimi mijoz hujjatlarini qabul qiladi va tekshiradi.",
              metadata={'document': 'Namuna nizom', 'chapter': '2-bob', 'clause': '5-band', 'language': 'uz'}),
    NovaChunk(index=2, score=0.64, text="Namuna: hujjatlar tekshiruvdan so'ng rahbarga imzolash uchun yuboriladi.",
              metadata={'document': 'Namuna nizom', 'chapter': '2-bob', 'clause': '6-band', 'language': 'uz'}),
)


class FakeNova:
    def __init__(self, chunks: Sequence[NovaChunk] = SAMPLE_CHUNKS, reply: str = 'Namuna javob',
                 collections: Sequence[str] = ('ops_policies',)) -> None:
        self.chunks = list(chunks)
        self.reply = reply
        self.collections = list(collections)
        #: Что ушло бы в Nova: ('retrieve', query) или ('complete', system_prompt).
        self.calls: List[Tuple[str, str]] = []

    def models(self) -> List[str]:
        return list(self.collections)

    def retrieve(self, query: str, collection: Optional[str] = None) -> List[NovaChunk]:
        if not query.strip():
            raise ValueError('Пустой запрос поиска: Gateway ответил бы 422')
        self.calls.append(('retrieve', query))
        return list(self.chunks)

    def complete(self, system_prompt: str, messages: Sequence[Message]) -> str:
        if any(m.get('role') == 'system' for m in messages):
            raise ValueError('Указания модели передаются в system_prompt, а не сообщением с ролью system')
        self.calls.append(('complete', system_prompt))
        return self.reply

    def health(self) -> bool:
        return True
