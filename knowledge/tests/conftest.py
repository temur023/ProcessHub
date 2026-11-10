import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge_service.config import Settings
from knowledge_service.main import create_app
from knowledge_service.store import migrate

SECRET = 'test-secret-for-knowledge-' + 'x' * 40
URL = os.environ.get('KNOWLEDGE_TEST_DATABASE_URL', 'postgresql://postgres:knowledge-test-only@127.0.0.1:55433/knowledge_test')


@pytest.fixture
def registry():
    # Каждый тест в собственной схеме выделенной тестовой базы; рабочие
    # схемы не очищаются. options также передаются пулу/мигратору.
    schema = 'test_' + uuid4().hex
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    url = URL + ('&' if '?' in URL else '?') + f'options=-csearch_path%3D{schema}'
    settings = Settings(url, SECRET)
    migrate(url)
    dept, role, other = uuid4(), uuid4(), uuid4()
    with psycopg.connect(url) as conn:
        conn.execute('INSERT INTO departments(id,name) VALUES (%s,%s),(%s,%s)', (dept, 'Департамент цифрового бизнеса', other, 'Чужой департамент'))
        conn.execute('INSERT INTO roles(id,department_id,name) VALUES (%s,%s,%s)', (role, dept, 'Главный специалист'))
        conn.execute('INSERT INTO access_grants(user_id,department_id,permission) VALUES (1,%s,\'owner\'),(2,%s,\'reader\')', (dept, dept))
    try:
        with TestClient(create_app(settings)) as client:
            yield client, settings, dept, role, other
    finally:
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
