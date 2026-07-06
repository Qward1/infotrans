"""Точка входа FastAPI-приложения «Умный календарь».

Запуск:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.bootstrap import bootstrap
from app.core.config import BASE_DIR, get_settings
from app.core.permissions import NotAuthenticated, NotAuthorized
from app.core.urls import local_redirect
from app.routers import (
    admin,
    api,
    auth,
    calendar,
    chat,
    dashboard,
    documents,
    notifications,
    settings as settings_router,
    travel,
)
from app.templating import render

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация БД / seed-админ / demo-данные при старте.
    bootstrap()
    yield


settings = get_settings()

app = FastAPI(
    title=settings.app.name,
    description="Умный цифровой календарь встреч и поездок (MVP).",
    version="0.1.0",
    lifespan=lifespan,
    # Префикс пути за reverse-proxy (пусто → корень). Заголовок X-Forwarded-Prefix
    # имеет приоритет (см. app/core/urls.py).
    root_path=settings.app.root_path,
)

# Сессии на подписанных cookie (секрет — из YAML).
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app.secret_key,
    session_cookie=settings.security.session_cookie,
    max_age=settings.security.session_max_age,
    same_site="lax",
    https_only=False,
)

# Статика.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


# --------------------------------------------------------------------------- #
# Обработчики ошибок авторизации: страницы редиректят, API отдаёт JSON.        #
# --------------------------------------------------------------------------- #
@app.exception_handler(NotAuthenticated)
async def _not_authenticated(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=401, content={"detail": "Требуется вход"})
    return local_redirect(request, "/login", status_code=303)


@app.exception_handler(NotAuthorized)
async def _not_authorized(request: Request, exc: NotAuthorized):
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=403, content={"detail": "Недостаточно прав"})
    return render(request, "403.html", active="", status_code=403)


# Роутеры (порядок не важен, кроме перекрытий путей).
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(calendar.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(travel.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(settings_router.router)
app.include_router(api.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok", "app": settings.app.name}
