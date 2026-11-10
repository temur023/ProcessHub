"""Короткоживущий подписанный контекст от Hub, привязанный к запросу.

Браузер не выбирает user_id. Подпись проверяется до доступа к базе.
Это внутренний протокол двух сервисов; токен не выдаётся браузеру.
"""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    id: int
    is_admin: bool = False


async def principal(request: Request) -> Principal:
    try:
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer ') or len(header) > 4096:
            raise ValueError()
        payload, signature = header[7:].split('.')
        expected = hmac.new(request.app.state.settings.hub_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        now = int(time.time())
        iat, exp, subject = claims['iat'], claims['exp'], claims['sub']
        is_admin = claims.get('platform_admin', False)
        if type(is_admin) is not bool:
            raise ValueError()
        if type(iat) is not int or type(exp) is not int or type(subject) is not int or subject <= 0:
            raise ValueError()
        if claims['iss'] != 'process-hub' or claims['aud'] != 'knowledge' or not iat <= now < exp or exp - iat > 60:
            raise ValueError()
        target = request.url.path + ('?' + request.url.query if request.url.query else '')
        if claims['method'] != request.method or claims['target'] != target:
            raise ValueError()
        if claims['body'] != hashlib.sha256(await request.body()).hexdigest():
            raise ValueError()
        return Principal(subject, is_admin)
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(401, 'Недействительный контекст доступа') from None
