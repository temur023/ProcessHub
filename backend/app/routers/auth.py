"""Регистрация, вход и выход сотрудника.

Остальной API открыт только с сессией (``app.main``): так у каждой версии карты
в истории есть автор.
"""
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.services.auth import (
    COOKIE, EMAIL_DOMAIN, check_new_password, clean_name, current_user, hash_password, new_token, token_hash,
    verify_password,
)
from app.services.store import User, UserExists, get_store

router = APIRouter(prefix="/auth", tags=["auth"])

#: Срок самой cookie. Когда сессия истекает, решает сервер (30 дней без
#: запросов), а cookie лишь не должна умереть раньше у того, кто работает каждый день.
COOKIE_SECONDS = 400 * 24 * 3600


class RegisterBody(BaseModel):
    name: str
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


def _sign_in(request: Request, response: Response, user: User) -> User:
    token, digest = new_token()
    get_store().create_session(digest, user.id)
    response.set_cookie(COOKIE, token, max_age=COOKIE_SECONDS, path='/', httponly=True, samesite='lax',
                        secure=request.url.scheme == 'https')
    return user


@router.post("/register", response_model=User, summary="Регистрация сотрудника с рабочей почтой; сразу входит")
def register(body: RegisterBody, request: Request, response: Response):
    name = clean_name(body.name)
    email = body.email.strip().lower()
    if not re.fullmatch(rf'[^@\s]+@{re.escape(EMAIL_DOMAIN)}', email):
        raise HTTPException(422, f'Нужна рабочая почта @{EMAIL_DOMAIN}')
    check_new_password(body.password)
    try:
        user = get_store().create_user(email, name, hash_password(body.password))
    except UserExists:
        raise HTTPException(409, 'Эта почта уже зарегистрирована — войдите')
    return _sign_in(request, response, user)


@router.post("/login", response_model=User, summary="Вход по почте и паролю")
def login(body: LoginBody, request: Request, response: Response):
    found = get_store().find_user(body.email.strip().lower())
    if not found or not verify_password(body.password, found[1]):
        raise HTTPException(401, 'Неверная почта или пароль')
    return _sign_in(request, response, found[0])


@router.post("/logout", status_code=204, summary="Выход: сессия закрывается")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        get_store().delete_session(token_hash(token))
    response.delete_cookie(COOKIE, path='/')


def demo_email():
    """Account for one-click sign-in on a test stand; empty on a working server."""
    email = os.environ.get('SQB_DEMO_LOGIN', '').strip().lower()
    return email if re.fullmatch(rf'[^@\s]+@{re.escape(EMAIL_DOMAIN)}', email) else ''


@router.get("/options", summary="Какие способы входа доступны")
def options():
    email = demo_email()
    return {'demo': bool(email), 'demoEmail': email or None}


@router.post("/demo", response_model=User, summary="Вход в один клик на тестовом стенде")
def demo(request: Request, response: Response):
    email = demo_email()
    if not email:
        raise HTTPException(404, 'Быстрый вход выключен')
    found = get_store().find_user(email)
    if found:
        user = found[0]
    else:
        # A new demo account gets no rights; nobody knows its random password.
        try:
            user = get_store().create_user(email, 'Демо-пользователь', hash_password(secrets.token_urlsafe(32)))
        except UserExists:
            user = get_store().find_user(email)[0]
    return _sign_in(request, response, user)


@router.get("/me", response_model=User, summary="Кто вошёл")
def me(user: User = Depends(current_user)):
    return user
