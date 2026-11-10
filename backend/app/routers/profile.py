"""Профиль сотрудника: его статистика, смена имени и пароля."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.services.auth import (
    COOKIE, check_new_password, clean_name, current_user, hash_password, token_hash, verify_password,
)
from app.services.store import Profile, User, get_store

router = APIRouter(prefix="/profile", tags=["profile"])


class RenameBody(BaseModel):
    name: str


class PasswordBody(BaseModel):
    currentPassword: str
    newPassword: str


@router.get("", response_model=Profile, summary="Кто вошёл, что и на каких картах он сохранял")
def get_profile(user: User = Depends(current_user)):
    return get_store().profile(user.id)


@router.patch("", response_model=User, summary="Сменить имя: оно же — у всех его версий в истории")
def rename(body: RenameBody, user: User = Depends(current_user)):
    return get_store().rename_user(user.id, clean_name(body.name))


@router.post("/password", status_code=204, summary="Сменить пароль; остальные сессии закрываются")
def change_password(body: PasswordBody, request: Request, user: User = Depends(current_user)):
    found = get_store().find_user(user.email)
    # 403, а не 401: неверный текущий пароль — не конец сессии.
    if not found or not verify_password(body.currentPassword, found[1]):
        raise HTTPException(403, 'Текущий пароль неверный')
    check_new_password(body.newPassword)
    get_store().set_password(user.id, hash_password(body.newPassword),
                             keep_session=token_hash(request.cookies.get(COOKIE, '')))
