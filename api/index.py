"""Точка входа бэкенда на Vercel: FastAPI из backend/app как Python-функция."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

# На Vercel писать можно только в /tmp.
os.environ.setdefault('SQB_CANVAS_CACHE', '/tmp/canvas_cache')
# База Neon из интеграции Vercel.
if not os.environ.get('SQB_DATABASE_URL') and os.environ.get('DATABASE_URL'):
    os.environ['SQB_DATABASE_URL'] = os.environ['DATABASE_URL']

from app.main import app  # noqa: E402,F401

# Несколько экземпляров функции стартуют одновременно и вместе создают таблицы:
# PostgreSQL отвечает «tuple concurrently updated». Открываем хранилище заранее,
# с повтором; lifespan затем берёт уже открытое.
import random  # noqa: E402
import time  # noqa: E402

from app.services import store as _store_module  # noqa: E402

for _attempt in range(6):
    try:
        _store_module.get_store()
        break
    except Exception:
        if _attempt == 5:
            raise
        _store_module._store = None
        time.sleep(0.5 + random.random() * 1.5)

# ── Сервис документов в той же функции ──────────────────────────────────────
# В Docker бэкенд ходит к сервису документов по сети. Здесь это был бы круг
# через публичный адрес и вторая функция со своим холодным стартом, поэтому
# приложение документов вызывается прямо в процессе (httpx.ASGITransport).
import types  # noqa: E402

import httpx  # noqa: E402

if os.environ.get('KNOWLEDGE_DATABASE_URL') and len(os.environ.get('SQB_KNOWLEDGE_SECRET', '')) >= 32:
    sys.path.insert(0, str(ROOT / 'knowledge'))
    from knowledge_service.config import Settings as _KnowledgeSettings  # noqa: E402
    from knowledge_service.main import app as _knowledge_app  # noqa: E402
    from knowledge_service.store import Store as _KnowledgeStore, migrate as _knowledge_migrate  # noqa: E402

    from app.routers import knowledge as _knowledge_router  # noqa: E402

    _settings = _KnowledgeSettings.from_env()
    for _attempt in range(6):
        try:
            _knowledge_migrate(_settings.database_url)
            break
        except Exception:
            if _attempt == 5:
                raise
            time.sleep(0.5 + random.random() * 1.5)
    _knowledge_app.state.settings = _settings
    _knowledge_app.state.store = _KnowledgeStore(_settings.database_url)

    class _InProcessClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop('verify', None)
            kwargs['transport'] = httpx.ASGITransport(app=_knowledge_app)
            super().__init__(*args, **kwargs)

    _shim = types.SimpleNamespace(**{name: getattr(httpx, name) for name in dir(httpx) if not name.startswith('__')})
    _shim.AsyncClient = _InProcessClient
    _knowledge_router.httpx = _shim
    os.environ['SQB_KNOWLEDGE_URL'] = 'http://knowledge.internal'
