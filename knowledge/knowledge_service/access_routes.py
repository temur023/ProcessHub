"""Назначения в документах: проверенный Hub ID, неизменяемый аудит, отзыв."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

router = APIRouter()


def actor(request: Request):
    return request.state.actor


def store(request: Request):
    return request.app.state.store


class AccessInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: int = Field(gt=0, strict=True)
    permission: Literal['reader', 'owner', 'reviewer', 'all']
    enabled: StrictBool
    reason: str = Field(min_length=1, max_length=300)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Укажите основание изменения доступа')
        return value


@router.get('/departments/{department_id}/access')
def access_list(department_id: UUID, current=Depends(actor), db=Depends(store)):
    db.require_admin(current)
    with db.pool.connection() as conn:
        db.authorize(conn, current, department_id)
        return conn.execute('SELECT user_id,array_agg(permission ORDER BY permission) AS permissions,min(granted_at) AS granted_at '
                            'FROM access_grants WHERE department_id=%s GROUP BY user_id ORDER BY user_id', (department_id,)).fetchall()


@router.post('/departments/{department_id}/access')
def change_access(department_id: UUID, data: AccessInput, current=Depends(actor), db=Depends(store)):
    db.require_admin(current)
    if data.enabled and data.permission == 'all':
        raise HTTPException(422, 'Выберите конкретное право доступа')
    with db.pool.connection() as conn:
        db.authorize(conn, current, department_id)
        # Сериализует одновременные назначение/отзыв для папки.
        conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (department_id,))
        if data.enabled:
            changed = conn.execute('INSERT INTO access_grants(user_id,department_id,permission) VALUES (%s,%s,%s) '
                                   'ON CONFLICT DO NOTHING RETURNING permission', (data.user_id, department_id, data.permission)).fetchall()
        elif data.permission == 'all':
            changed = conn.execute('DELETE FROM access_grants WHERE user_id=%s AND department_id=%s RETURNING permission',
                                   (data.user_id, department_id)).fetchall()
        else:
            changed = conn.execute('DELETE FROM access_grants WHERE user_id=%s AND department_id=%s AND permission=%s RETURNING permission',
                                   (data.user_id, department_id, data.permission)).fetchall()
        for item in changed:
            conn.execute('INSERT INTO audit_events(actor_id,department_id,action,entity_id,details) VALUES (%s,%s,%s,%s,%s)',
                         (current.id, department_id, 'access.granted' if data.enabled else 'access.revoked', department_id,
                          Jsonb({'user_id':data.user_id,'permission':item['permission'],'reason':data.reason})))
        permissions = [r['permission'] for r in conn.execute('SELECT permission FROM access_grants WHERE user_id=%s AND department_id=%s ORDER BY permission',
                                                           (data.user_id, department_id)).fetchall()]
        return {'user_id':data.user_id,'permissions':permissions,'changed':len(changed)}


@router.get('/departments/{department_id}/audit')
def audit_list(department_id: UUID, current=Depends(actor), db=Depends(store)):
    db.require_admin(current)
    with db.pool.connection() as conn:
        db.authorize(conn, current, department_id)
        # Ограниченный журнал текущего департамента. Оригиналы/секреты не возвращаются.
        return conn.execute('SELECT id,actor_id,action,entity_id,details,created_at FROM audit_events '
                            'WHERE department_id=%s ORDER BY id DESC LIMIT 100', (department_id,)).fetchall()
