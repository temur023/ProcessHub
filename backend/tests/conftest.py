"""Общая настройка тестов.

Сохранение процесса прогревает раскладку холста в фоне (``canvas_cache``),
и без этой строки тесты писали бы свои страницы в настоящий кэш
``app/data/canvas_cache``.

Хранилище карт (``app.services.store``): по умолчанию — в памяти, чтобы
тесты шли без Docker. С ``SQB_TEST_DATABASE_URL`` весь набор идёт на
PostgreSQL: база очищается в начале прогона, а прежние JSON-файлы в неё не
переносятся (переменные уводят их в пустую временную папку).

API открыт только с сессией сотрудника. Тесты ходят в него от имени одного
зарегистрированного сотрудника (``signed_in``); тесты самого входа эту
подмену снимают (``tests/test_auth.py``).
"""
import os
import tempfile

import pytest

# Присваивание, а не setdefault: в образе API переменная задана и ведёт в
# рабочий том кэша.
os.environ['SQB_CANVAS_CACHE'] = os.path.join(tempfile.mkdtemp(), 'canvas_cache')

_empty = tempfile.mkdtemp()
os.environ['SQB_PROCESS_STORE'] = os.path.join(_empty, 'process_store.json')
os.environ['SQB_CATALOG_STORE'] = os.path.join(_empty, 'catalog.json')

TEST_DATABASE_URL = os.environ.get('SQB_TEST_DATABASE_URL')
if TEST_DATABASE_URL:
    import psycopg

    os.environ['SQB_DATABASE_URL'] = TEST_DATABASE_URL
    os.environ.pop('SQB_STORE', None)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute('drop table if exists process_attachments, process_sources, process_versions, processes, catalog_items, role_rates, platform_access_audit, platform_admins, sessions, users cascade')
else:
    os.environ['SQB_STORE'] = 'memory'


@pytest.fixture(autouse=True, scope='session')
def signed_in():
    from app.main import app
    from app.services.auth import current_user, hash_password
    from app.services.store import get_store

    user = get_store().create_user('tester@sqb.uz', 'Тестовый Сотрудник', hash_password('test-password'))
    app.dependency_overrides[current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(current_user, None)
