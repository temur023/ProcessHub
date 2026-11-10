"""Подключение к Nova RAG Gateway — базе знаний положений и языковой модели банка.

Платформа работает через порт (``NovaPort``): ``NovaHttpClient`` ходит в
Gateway по HTTP, ``FakeNova`` отвечает без сети. Какой из них включён,
решают настройки сервиса (``get_nova``).
"""
from app.services.nova.port import NovaChunk, NovaError, NovaPort
from app.services.nova.settings import NovaSettings, get_nova, nova_settings

__all__ = ['NovaChunk', 'NovaError', 'NovaPort', 'NovaSettings', 'get_nova', 'nova_settings']
