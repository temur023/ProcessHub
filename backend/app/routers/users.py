"""Список пользователей платформы — только для главного администратора."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.services.auth import current_user
from app.services.store import PlatformUser, User, get_store

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=List[PlatformUser], summary="Все пользователи: роль, вход и активность")
def list_users(user: User = Depends(current_user)):
    if not user.isAdmin:
        raise HTTPException(403, 'Список пользователей доступен главному администратору')
    return get_store().list_users()
