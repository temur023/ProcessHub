"""Вход сотрудников: пароли, сессии и проверка запроса.

Пароль хранится только хэшем scrypt с солью. Сессия — случайный токен в
cookie с httpOnly: скрипт страницы его не видит. В базе лежит лишь SHA-256
токена, поэтому по таблице сессий войти нельзя. Сессия живёт
``SESSION_SECONDS`` от последнего запроса (``app.services.store``).
"""
import hashlib
import hmac
import secrets
from typing import Tuple

from fastapi import HTTPException, Request

from app.services.store import User, get_store

COOKIE = 'sqb_session'
#: Регистрироваться можно только с рабочей почтой.
EMAIL_DOMAIN = 'sqb.uz'
MIN_PASSWORD = 8

_SCRYPT = dict(n=2 ** 14, r=8, p=1)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f'scrypt${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split('$')
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(got.hex(), digest)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> Tuple[str, str]:
    """Токен для cookie и его отпечаток для базы."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def clean_name(raw: str) -> str:
    """Имя без лишних пробелов; пустое — 422."""
    name = ' '.join(raw.split())
    if not name:
        raise HTTPException(422, 'Укажите имя и фамилию: под ними ваши правки видны в истории')
    return name


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f'Пароль — не короче {MIN_PASSWORD} символов')


def current_user(request: Request) -> User:
    """Кто делает запрос; без живой сессии — 401."""
    token = request.cookies.get(COOKIE)
    user = get_store().session_user(token_hash(token)) if token else None
    if user is None:
        raise HTTPException(401, 'Войдите в систему')
    return user
