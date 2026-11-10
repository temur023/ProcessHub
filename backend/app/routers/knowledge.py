"""Hub подтверждает сессию и проксирует только контракт сервиса документов."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from typing import Literal

from app.services.auth import current_user
from app.services.store import get_store

router = APIRouter(prefix='/knowledge', tags=['knowledge'])
# Корневые сертификаты читаются один раз: httpx загружал их заново в каждом
# новом клиенте (0,2–0,4 с на запрос), даже когда сервис документов — по HTTP.
_TLS = httpx.create_ssl_context(trust_env=False)

_UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
CONTRACT = {
    'GET': re.compile(rf'(access|access-roles|access-role-memberships|access-role-audit|departments|departments/{_UUID}/(roles|role-library|role-library/{_UUID}|instructions|deleted-instructions|access|audit|analysis-registry)|instructions/{_UUID}/versions|instruction-versions/{_UUID}/(original|preparation)|documents|documents/types|documents/{_UUID}|documents/{_UUID}/analysis-runs|analysis-runs/{_UUID}|nova/status|library/overview|documents/files/{_UUID}/(original|preview|preparation|structure))\Z'),
    'POST': re.compile(rf'(admin/users/lookup|access-roles|access-role-memberships|departments/{_UUID}/(roles|access|role-library)|instructions/upload|instructions/{_UUID}/(versions|delete|restore)|instruction-versions/{_UUID}/preparation(/cancel)?|documents/upload|documents/{_UUID}/(editions|delete|restore)|documents/{_UUID}/editions/{_UUID}/(verify|end-validity)|documents/files/{_UUID}/preparation(/cancel)?|documents/{_UUID}/analysis-runs|analysis-runs/{_UUID}/(cancel|reviews))\Z'),
    'PATCH': re.compile(rf'(access-roles/{_UUID}|departments/{_UUID}/nova-binding|documents/{_UUID}|departments/{_UUID}/role-library/{_UUID})\Z'),
    'DELETE': re.compile(rf'(documents/files/{_UUID}/preparation|departments/{_UUID}/role-library/{_UUID})\Z'),
}
DOCUMENT_QUERY_VALUES = {
    'library_scope': {'department', 'bank'},
    'document_type': {'job_instruction', 'department_regulation', 'procedure', 'methodology',
                      'order', 'form', 'organization', 'sop', 'analytics', 'correspondence', 'other'},
    'view': {'all', 'current', 'unverified', 'historical', 'analytics', 'trash'},
}


def validate_query(path: str, request: Request):
    """Only the document list accepts filters; the signed target includes them."""
    if not request.url.query:
        return
    if request.method == 'GET' and re.fullmatch(rf'documents/files/{_UUID}/preview', path):
        if request.url.query not in ('view=clean', 'view=markup'):
            raise HTTPException(404, 'Действие недоступно')
        return
    if path != 'documents' or request.method != 'GET' or len(request.url.query) > 2048:
        raise HTTPException(404, 'Действие недоступно')
    seen = set()
    for key, value in request.query_params.multi_items():
        if key in seen:
            raise HTTPException(404, 'Действие недоступно')
        seen.add(key)
        if key == 'department_id':
            valid = re.fullmatch(_UUID, value)
        else:
            valid = value in DOCUMENT_QUERY_VALUES.get(key, set())
        if not valid:
            raise HTTPException(404, 'Действие недоступно')


class AccountLookup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=1, max_length=320)


class AccessChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: int = Field(gt=0, strict=True)
    permission: Literal['reader', 'owner', 'reviewer', 'all']
    enabled: StrictBool
    reason: str = Field(min_length=1, max_length=300)


class RoleMembershipChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role_id: str = Field(pattern=rf'^{_UUID}$')
    user_id: int = Field(gt=0, strict=True)
    enabled: StrictBool
    reason: str = Field(min_length=1, max_length=300)


def context_token(secret, user_id, method, target, body, platform_admin=False):
    now = int(time.time())
    claims = {'iss': 'process-hub', 'aud': 'knowledge', 'sub': user_id, 'iat': now, 'exp': now + 60,
              'method': method, 'target': target, 'body': hashlib.sha256(body).hexdigest(), 'platform_admin': platform_admin}
    payload = base64.urlsafe_b64encode(json.dumps(claims, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


@router.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'DELETE'])
async def knowledge_proxy(path: str, request: Request, user=Depends(current_user)):
    if not CONTRACT[request.method].fullmatch(path):
        # Keep unsupported legacy methods outside the contract.
        if request.method == 'DELETE':
            raise HTTPException(405, 'Метод недоступен')
        raise HTTPException(404, 'Действие недоступно')
    validate_query(path, request)
    if request.method != 'GET' and request.headers.get('origin'):
        try:
            origin = urlparse(request.headers['origin'])
        except ValueError:
            raise HTTPException(403, 'Отправьте запрос из Process Hub') from None
        host = request.headers.get('host', '')
        if origin.scheme not in ('http', 'https') or origin.username or origin.password or origin.path or origin.query or origin.fragment or origin.netloc != host:
            raise HTTPException(403, 'Отправьте запрос из Process Hub')
    admin_action = path == 'admin/users/lookup' or path.startswith('access-role') or bool(re.fullmatch(rf'departments/{_UUID}/(access|audit)', path))
    if admin_action and not user.isAdmin:
        raise HTTPException(403, 'Управлять доступом может главный администратор')
    if path == 'documents' and request.query_params.get('library_scope') == 'bank' and not user.isAdmin:
        raise HTTPException(403, 'Общая библиотека пока доступна главному администратору')
    secret = os.environ.get('SQB_KNOWLEDGE_SECRET', '')
    url = os.environ.get('SQB_KNOWLEDGE_URL', '').rstrip('/')
    if len(secret) < 32 or not url:
        raise HTTPException(503, 'Раздел документов ещё не подключён. Обратитесь к администратору')
    body = bytearray()
    bundle_upload = request.method == 'POST' and (path == 'documents/upload' or bool(re.fullmatch(rf'documents/{_UUID}/editions', path)))
    body_limit = 41 * 1024 * 1024 if bundle_upload else 10 * 1024 * 1024 + 128 * 1024
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > body_limit:
            message = 'Общий объём файлов не должен превышать 40 МБ' if bundle_upload else 'Файл не должен превышать 10 МБ'
            raise HTTPException(413, message)
    body = bytes(body)
    if path == 'admin/users/lookup':
        try:
            lookup = AccountLookup.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, 'Укажите точную рабочую почту сотрудника') from None
        found = get_store().find_user(lookup.email.strip().lower())
        if not found:
            raise HTTPException(404, 'Аккаунт не найден. Сотруднику нужно зарегистрироваться в Process Hub')
        return JSONResponse(found[0].model_dump(), headers={'Cache-Control':'no-store'})
    if request.method == 'POST' and (path.endswith('/access') or path == 'access-role-memberships'):
        try:
            change = (RoleMembershipChange if path == 'access-role-memberships' else AccessChange).model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, 'Проверьте сотрудника, право и основание изменения доступа') from None
        account = get_store().public_users([change.user_id]).get(change.user_id)
        if not account:
            raise HTTPException(404, 'Аккаунт сотрудника не найден в Process Hub')
        if account.isAdmin:
            raise HTTPException(409, 'Главный администратор уже имеет доступ ко всем департаментам. Его права назначаются отдельно')
    target = '/' + path
    if request.url.query:
        target += '?' + request.url.query
    headers = {'Authorization': 'Bearer ' + context_token(secret, user.id, request.method, target, body, user.isAdmin)}
    if request.headers.get('content-type'):
        headers['Content-Type'] = request.headers['content-type']
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, verify=_TLS) as client:
            response = await client.request(request.method, url + target, content=body, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(503, 'Сервис документов временно недоступен. Повторите позже') from None
    if response.status_code == 401:
        # Ошибка межсервисной подписи не должна разлогинивать сотрудника Hub.
        raise HTTPException(503, 'Не удалось подтвердить доступ к сервису документов')
    if response.status_code >= 500 or 300 <= response.status_code < 400:
        raise HTTPException(503, 'Сервис документов временно недоступен. Повторите позже')
    if response.status_code == 200 and request.method == 'GET' and admin_action:
        try:
            entries = response.json()
            ids = set()
            for item in entries:
                for candidate in (item.get('user_id'), item.get('actor_id'), item.get('details', {}).get('user_id')):
                    if type(candidate) is int and candidate > 0:
                        ids.add(candidate)
            accounts = get_store().public_users(list(ids))
            for item in entries:
                for key, field in (('user_id','user'),('actor_id','actor')):
                    if key in item:
                        account = accounts.get(item[key])
                        item[field] = account.model_dump() if account else None
                if (subject := item.get('details', {}).get('user_id')):
                    account = accounts.get(subject)
                    item['subject'] = account.model_dump() if account else None
            return JSONResponse(entries, headers={'Cache-Control':'no-store'})
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(503, 'Не удалось загрузить назначения. Повторите позже') from None
    safe_headers = {k: v for k, v in response.headers.items() if k.lower() in ('content-type', 'content-disposition', 'x-content-type-options')}
    safe_headers['Cache-Control'] = 'no-store'
    return Response(response.content, status_code=response.status_code, headers=safe_headers)
