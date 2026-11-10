"""
SQB Process Hub — FastAPI Backend
Main application entry point
"""
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.routers import processes, import_export, analytics, canvas, catalog, auth, profile, knowledge, systems, roles, search, nova, users, attachments
from app.services.auth import current_user
from app.services.canvas_cache import warm_up_saved
from app.services.store import close_store, get_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # База открывается при запуске, а не на первом запросе: без неё сервер
    # должен сразу сказать, что поднять, а не отвечать 500 на всё подряд.
    # Пустая база здесь же забирает карты из прежних JSON-файлов.
    store = get_store()
    # Линии всех карт — заранее, в фоне: запуск не ждёт (весь банк — ~40 с).
    threading.Thread(target=warm_up_saved, args=(store,), name='canvas-warm-saved', daemon=True).start()
    yield
    close_store()


app = FastAPI(
    lifespan=lifespan,
    title="SQB Process Hub API",
    description=(
        "Backend API для платформы автоматизации бизнес-процессов "
        "АКБ «Узпромстройбанк» (SQB). "
        "Обеспечивает парсинг draw.io диаграмм, управление PIX-реестрами "
        "и интеграцию с Infomaximum Processet."
    ),
    version="1.0.0",
    contact={
        "name": "SQB Digital Banking Dept.",
        "url": "https://sqb.uz",
    },
    license_info={
        "name": "Proprietary — АКБ «Узпромстройбанк»",
    },
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ──────────────── CORS ────────────────
# Allow local dev (React Vite :5173) and production domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "https://sqb-process-hub.vercel.app",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://.+\.(e2b\.app|vercel\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────── Сжатие ответов ────────────────
# Карта «1.53» — 2,9 МБ JSON, сжатая — 146 КБ за 16 мс. Несжатую dev-прокси
# Vite везёт секунду при каждом открытии карты, а сеть до сервера — и дольше.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# ──────────────── Routers ────────────────
# Всё, кроме входа и проверки здоровья, — только с сессией сотрудника.
signed_in = [Depends(current_user)]
app.include_router(auth.router,           prefix="/api/v1")
app.include_router(processes.router,      prefix="/api/v1", dependencies=signed_in)
app.include_router(import_export.router,  prefix="/api/v1", dependencies=signed_in)
app.include_router(analytics.router,      prefix="/api/v1", dependencies=signed_in)
app.include_router(canvas.router,         prefix="/api/v1", dependencies=signed_in)
app.include_router(catalog.router,        prefix="/api/v1", dependencies=signed_in)
app.include_router(profile.router,        prefix="/api/v1", dependencies=signed_in)
app.include_router(knowledge.router,      prefix="/api/v1", dependencies=signed_in)
app.include_router(systems.router,        prefix="/api/v1", dependencies=signed_in)
app.include_router(roles.router,          prefix="/api/v1", dependencies=signed_in)
app.include_router(search.router,         prefix="/api/v1", dependencies=signed_in)
app.include_router(nova.router,           prefix="/api/v1", dependencies=signed_in)
app.include_router(users.router,          prefix="/api/v1", dependencies=signed_in)
app.include_router(attachments.router,    prefix="/api/v1", dependencies=signed_in)


# ──────────────── Health / Root ────────────────
@app.get("/", tags=["health"], summary="Root health check")
def root():
    return JSONResponse({
        "service": "SQB Process Hub API",
        "version": "1.0.0",
        "status": "online",
        "docs": "/api/docs"
    })


@app.get("/api/health", tags=["health"], summary="Health check endpoint")
def health():
    return {"status": "ok", "version": "1.0.0"}
