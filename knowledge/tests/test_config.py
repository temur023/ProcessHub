import pytest

from knowledge_service.config import Settings


def test_missing_secrets_fail_closed(monkeypatch):
    monkeypatch.delenv('SQB_KNOWLEDGE_SECRET', raising=False)
    with pytest.raises(RuntimeError, match='SQB_KNOWLEDGE_SECRET'):
        Settings.from_env()
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', 'x' * 32)
    monkeypatch.delenv('KNOWLEDGE_DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='KNOWLEDGE_DATABASE_URL'):
        Settings.from_env()


def test_nova_optional_and_key_is_not_in_url(monkeypatch):
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', 'x' * 32)
    monkeypatch.setenv('KNOWLEDGE_DATABASE_URL', 'postgresql://test')
    monkeypatch.delenv('NOVA_BASE_URL', raising=False)
    assert Settings.from_env().nova_configured is False
    for url in ('file:///tmp/data', 'http://key@gateway', 'http://gateway?key=value'):
        monkeypatch.setenv('NOVA_BASE_URL', url)
        with pytest.raises(RuntimeError):
            Settings.from_env()
    monkeypatch.setenv('NOVA_BASE_URL', 'http://gateway:11450/')
    monkeypatch.setenv('NOVA_API_KEY', 'test-key')
    monkeypatch.setenv('NOVA_COLLECTION', 'policies')
    assert Settings.from_env().nova_configured is True
    # Nova Platform: адрес RAG-сервиса — с путём.
    monkeypatch.setenv('NOVA_BASE_URL', 'http://nova-gateway.internal:11450/api/v1/services/rag/')
    assert Settings.from_env().nova_url == 'http://nova-gateway.internal:11450/api/v1/services/rag'


def test_nova_proxy_is_optional_and_checked(monkeypatch):
    monkeypatch.setenv('SQB_KNOWLEDGE_SECRET', 'x' * 32)
    monkeypatch.setenv('KNOWLEDGE_DATABASE_URL', 'postgresql://test')
    monkeypatch.delenv('NOVA_BASE_URL', raising=False)
    monkeypatch.delenv('NOVA_PROXY', raising=False)
    assert Settings.from_env().nova_proxy == ''
    for proxy in ('ftp://proxy:21', 'proxy:8181', 'http://proxy:8181?x=1'):
        monkeypatch.setenv('NOVA_PROXY', proxy)
        with pytest.raises(RuntimeError, match='NOVA_PROXY'):
            Settings.from_env()
    monkeypatch.setenv('NOVA_PROXY', ' http://preview-server.internal:8181 ')
    assert Settings.from_env().nova_proxy == 'http://preview-server.internal:8181'
