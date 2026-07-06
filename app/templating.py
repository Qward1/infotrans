"""Общая конфигурация Jinja2 и хелпер рендеринга страниц."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import BASE_DIR, get_settings

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def render(
    request: Request,
    name: str,
    current_user: Any = None,
    status_code: int = 200,
    **context: Any,
):
    """Отрендерить шаблон с общим контекстом (настройки, текущий пользователь)."""
    settings = get_settings()
    ctx: dict[str, Any] = {
        "settings": settings,
        "app_name": settings.app.name,
        "current_user": current_user,
        "active": context.pop("active", ""),
    }
    ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
