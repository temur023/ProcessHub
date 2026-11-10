from contextlib import asynccontextmanager
from uuid import UUID
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from .auth import principal
from .config import Settings
from .store import Store, migrate
from .access_routes import router as access_router
from .registry_routes import router as registry_router
from .rbac_routes import router as rbac_router
from .preparation_routes import router as preparation_router
from .lifecycle_routes import router as lifecycle_router
from .document_routes import router as document_router
from .document_models import MAX_BUNDLE_BYTES
from .role_library_routes import router as role_library_router
from .analysis_routes import router as analysis_router


class InstructionInput(BaseModel):
    department_id: UUID
    role_id: UUID | None = None
    title: str = Field(min_length=1, max_length=300)

    @field_validator('title')
    @classmethod
    def normalize_title(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Укажите название инструкции')
        return value


class NameInput(BaseModel):
    name: str = Field(min_length=1, max_length=300)

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Укажите название')
        return value


class CatalogDepartmentInput(BaseModel):
    catalog_item_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=300)
    number: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0)

    @field_validator('catalog_item_id', 'name', 'number')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Пустой элемент каталога')
        return value


class CatalogDepartmentsInput(BaseModel):
    space_id: str = Field(min_length=1, max_length=128)
    departments: list[CatalogDepartmentInput] = Field(min_length=1, max_length=500)

    @field_validator('departments')
    @classmethod
    def unique_items(cls, value):
        if len({item.catalog_item_id for item in value}) != len(value):
            raise ValueError('Повторная папка в переносе')
        return value


def user(request: Request):
    return request.state.actor


def store(request: Request):
    return request.app.state.store


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.from_env()
        app.state.settings = config
        migrate(config.database_url)
        app.state.store = Store(config.database_url)
        yield
        app.state.store.close()

    app = FastAPI(title='Process Hub Knowledge API', version='0.1.0', lifespan=lifespan)
    app.include_router(access_router)
    app.include_router(registry_router)
    app.include_router(rbac_router)
    app.include_router(preparation_router)
    app.include_router(lifecycle_router)
    app.include_router(document_router)
    app.include_router(role_library_router)
    app.include_router(analysis_router)

    @app.middleware('http')
    async def trusted_hub_context(request, call_next):
        if request.url.path == '/health':
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        try:
            # До multipart-парсинга: ограничение действует даже без Content-Length.
            body = bytearray()
            bundle = request.url.path == '/documents/upload' or request.url.path.endswith('/editions')
            body_limit = (MAX_BUNDLE_BYTES + 1024 * 1024 if bundle
                          else app.state.settings.max_file_bytes + 128 * 1024)
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > body_limit:
                    raise HTTPException(413, 'Комплект не должен превышать 40 МБ' if bundle
                                        else 'Файл не должен превышать 10 МБ')
            request._body = bytes(body)
            request.state.actor = await principal(request)
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except HTTPException as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status_code)

    @app.get('/health')
    def health(db=Depends(store)):
        with db.pool.connection() as conn:
            conn.execute('SELECT 1')
        return {'status': 'ok', 'service': 'knowledge', 'version': '0.1.0'}

    @app.get('/departments')
    def departments(actor=Depends(user), db=Depends(store)):
        return db.departments(actor)

    @app.get('/access')
    def access(actor=Depends(user)):
        return {'user_id': actor.id, 'is_platform_admin': actor.is_admin}

    # Внутренний контракт переноса: браузерный прокси Hub его не публикует.
    @app.post('/catalog-departments')
    def import_departments(data: CatalogDepartmentsInput, actor=Depends(user), db=Depends(store)):
        return db.import_departments(actor, data)

    @app.post('/departments/{department_id}/roles', status_code=201)
    def create_role(department_id: UUID, data: NameInput, actor=Depends(user), db=Depends(store)):
        return db.create_role(actor, department_id, data.name)

    @app.get('/departments/{department_id}/roles')
    def roles(department_id: UUID, actor=Depends(user), db=Depends(store)):
        return db.roles(actor, department_id)

    @app.get('/departments/{department_id}/instructions')
    def instructions(department_id: UUID, actor=Depends(user), db=Depends(store)):
        return db.instructions(actor, department_id)

    @app.post('/instructions', status_code=201)
    def create_instruction(data: InstructionInput, actor=Depends(user), db=Depends(store)):
        return db.create_instruction(actor, data)

    @app.get('/instructions/{instruction_id}/versions')
    def versions(instruction_id: UUID, actor=Depends(user), db=Depends(store)):
        return db.versions(actor, instruction_id)

    @app.get('/instruction-versions/{version_id}/original')
    def download_original(version_id: UUID, actor=Depends(user), db=Depends(store)):
        row = db.original(actor, version_id)
        return Response(bytes(row['content']), media_type=row['media_type'], headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['filename'], safe=''),
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    return app


app = create_app()
