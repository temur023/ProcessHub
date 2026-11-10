import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str
    hub_secret: str
    nova_url: str = ''
    nova_key: str = ''
    nova_collection: str = ''
    max_file_bytes: int = 10 * 1024 * 1024
    # Прокси только для запросов к Nova, когда прямого доступа нет; системный прокси клиент не берёт.
    nova_proxy: str = ''

    @classmethod
    def from_env(cls):
        secret = os.environ.get('SQB_KNOWLEDGE_SECRET', '')
        if len(secret) < 32:
            raise RuntimeError('SQB_KNOWLEDGE_SECRET должен содержать не менее 32 символов')
        database_url = os.environ.get('KNOWLEDGE_DATABASE_URL', '')
        if not database_url:
            raise RuntimeError('Укажите KNOWLEDGE_DATABASE_URL отдельной базы документов')
        nova_url = os.environ.get('NOVA_BASE_URL', '').rstrip('/')
        if nova_url:
            parsed = urlparse(nova_url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise RuntimeError('NOVA_BASE_URL должен быть адресом Gateway без ключа и параметров')
        nova_proxy = os.environ.get('NOVA_PROXY', '').strip()
        if nova_proxy:
            parsed = urlparse(nova_proxy)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
                raise RuntimeError('NOVA_PROXY должен быть адресом прокси вида http://host:port')
        return cls(database_url, secret, nova_url, os.environ.get('NOVA_API_KEY', ''),
                   os.environ.get('NOVA_COLLECTION', ''), nova_proxy=nova_proxy)

    @property
    def nova_configured(self):
        return bool(self.nova_url and self.nova_key and self.nova_collection)
