"""Помощники для работы под reverse-proxy с под-путём (root path / base path).

Приложение может быть развёрнуто не в корне домена, а под префиксом
(например, ``/jnserver/1120/application``). Прокси при этом обычно «срезает»
префикс и форвардит запрос как ``/…``, но ссылки/редиректы/статику в HTML нужно
отдавать С префиксом, иначе браузер уйдёт в корень домена.

Источник префикса (по приоритету):
1. заголовок ``X-Forwarded-Prefix`` от прокси;
2. ``scope['root_path']`` (если задан через uvicorn ``--root-path`` или FastAPI);
3. ``app.root_path`` из YAML-конфига.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core.config import get_settings


def base_path(request: Request) -> str:
    """Внешний префикс пути (без завершающего слэша) или ""."""
    forwarded = request.headers.get("x-forwarded-prefix")
    if forwarded:
        return forwarded.rstrip("/")
    root = request.scope.get("root_path") or ""
    if root:
        return root.rstrip("/")
    return (get_settings().app.root_path or "").rstrip("/")


def local_redirect(request: Request, path: str, status_code: int = 303) -> RedirectResponse:
    """Redirect с учётом внешнего префикса пути."""
    if not path.startswith("/"):
        path = "/" + path
    return RedirectResponse(url=base_path(request) + path, status_code=status_code)
