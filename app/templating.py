"""Общая конфигурация Jinja2 и хелпер рендеринга страниц."""
from __future__ import annotations

import time
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import BASE_DIR, get_settings
from app.core.urls import base_path

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Версия статики для инвалидации кэша браузера при рестарте/деплое.
# Пишется как ?v=… к ссылкам на CSS/JS в шаблонах.
ASSET_VER = str(int(time.time()))


def render(
    request: Request,
    name: str,
    current_user: Any = None,
    status_code: int = 200,
    **context: Any,
):
    """Отрендерить шаблон с общим контекстом (настройки, текущий пользователь).

    ``base`` — внешний префикс пути (для reverse-proxy под под-путём): все
    внутренние ссылки/статика/формы в шаблонах строятся как ``{{ base }}/…``.
    """
    settings = get_settings()
    ctx: dict[str, Any] = {
        "settings": settings,
        "app_name": settings.app.name,
        "current_user": current_user,
        "active": context.pop("active", ""),
        "base": base_path(request),
        "asset_ver": ASSET_VER,
    }
    ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
