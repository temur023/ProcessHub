"""Сервис документов (knowledge) на Vercel: вторая Python-функция проекта.

Бэкенд ходит к нему по SQB_KNOWLEDGE_URL=https://<домен>/_knowledge; префикс
снимается здесь, сервис видит те же пути, что и в Docker.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'knowledge'))

import random  # noqa: E402
import time  # noqa: E402

import knowledge_service.main as _main  # noqa: E402

_migrate = _main.migrate


def _migrate_with_retry(url):
    # Одновременный старт экземпляров: миграции в одной базе конфликтуют.
    for attempt in range(6):
        try:
            return _migrate(url)
        except Exception:
            if attempt == 5:
                raise
            time.sleep(0.5 + random.random() * 1.5)


_main.migrate = _migrate_with_retry
_knowledge = _main.app

PREFIX = '/_knowledge'


async def app(scope, receive, send):
    if scope['type'] in ('http', 'websocket'):
        path = scope.get('path', '')
        if path.startswith(PREFIX):
            scope = dict(scope, path=path[len(PREFIX):] or '/', raw_path=None)
    await _knowledge(scope, receive, send)
