"""Страница системных настроек: просмотр конфигурации и безопасное редактирование.

Единственный источник настроек — ``config/config.yaml`` (без ``.env``).
Секреты на странице маскируются. Админ может отредактировать безопасный
whitelist скалярных полей: перед записью создаётся backup ``config.yaml.bak``,
YAML сохраняется валидным, кэш настроек сбрасывается.
"""
from __future__ import annotations

import re
import shutil

import yaml
from fastapi import APIRouter, Depends, Form, Request

from app.core.config import CONFIG_PATH, get_settings
from app.core.permissions import require_admin, require_user
from app.core.urls import local_redirect
from app.models.user import User
from app.templating import render

router = APIRouter(tags=["settings"])


def _mask(value: str) -> str:
    """Скрыть секрет, показав только хвост."""
    if not value:
        return "—"
    if len(value) <= 6:
        return "•••"
    return "••••••" + value[-4:]


def _safe_view(settings) -> dict:
    sc = settings.scheduling
    return {
        "app": {
            "name": settings.app.name,
            "timezone": settings.app.timezone,
            "debug": settings.app.debug,
            "secret_key": _mask(settings.app.secret_key),
        },
        "database": {"url": settings.database.url, "echo": settings.database.echo},
        "scheduling": {
            "working_hours": f"{sc.working_hours.start}–{sc.working_hours.end}",
            "wh_start": sc.working_hours.start,
            "wh_end": sc.working_hours.end,
            "default_meeting_minutes": sc.default_meeting_minutes,
            "default_travel_buffer_minutes": sc.default_travel_buffer_minutes,
            "high_priority_threshold": sc.high_priority_threshold,
            "max_alternatives": sc.max_alternatives,
            "slot_granularity_minutes": sc.slot_granularity_minutes,
        },
        "assistant": {
            "dify_enabled": settings.assistant.dify.enabled,
            "dify_base_url": settings.assistant.dify.base_url,
            "dify_api_key": _mask(settings.assistant.dify.api_key),
            "dify_assistant": settings.assistant.dify.default_assistant,
            "llm_enabled": settings.assistant.llm.enabled,
            "llm_model": settings.assistant.llm.model,
            "llm_api_key": _mask(settings.assistant.llm.api_key),
        },
        "tickets": {
            "mode": settings.tickets.mode,
            "currency": settings.tickets.currency,
            "avg_speed_kmh": settings.tickets.avg_speed_kmh,
            "provider": settings.tickets.provider.name,
        },
        "notifications": {
            "mode": settings.notifications.mode,
            "default_channel": settings.notifications.default_channel,
            "channels": settings.notifications.channels,
            "messenger_enabled": settings.notifications.messenger.enabled,
            "messenger_provider": settings.notifications.messenger.provider,
        },
    }


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(require_user), saved: int = 0):
    settings = get_settings()
    return render(
        request,
        "settings.html",
        current_user=user,
        active="settings",
        safe=_safe_view(settings),
        config_path=str(CONFIG_PATH),
        is_admin=user.is_admin,
        saved=bool(saved),
    )


def _set_nested(data: dict, path: list[str], value) -> None:
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _save_config(updates: list[tuple[list[str], object]]) -> None:
    """Записать whitelisted-значения в YAML c backup предыдущего файла."""
    data = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        # Backup предыдущей версии (перезаписывается при каждом сохранении).
        shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(".yaml.bak"))
    for path, value in updates:
        _set_nested(data, path, value)
    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    # Сбрасываем кэш, чтобы новые значения применились без перезапуска.
    get_settings.cache_clear()


# BUG-21: рабочие часы валидируются, а не «молча падают на дефолт».
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


@router.post("/settings")
def settings_save(
    request: Request,
    app_name: str = Form(...),
    timezone: str = Form(...),
    wh_start: str = Form(...),
    wh_end: str = Form(...),
    default_meeting_minutes: int = Form(...),
    default_travel_buffer_minutes: int = Form(...),
    high_priority_threshold: int = Form(...),
    max_alternatives: int = Form(...),
    default_channel: str = Form(...),
    dify_enabled: str = Form(default="off"),
    dify_base_url: str = Form(default=""),
    admin: User = Depends(require_admin),
):
    """Безопасное сохранение whitelisted-полей (только admin). Секреты не трогаем."""
    wh_start = wh_start.strip()
    wh_end = wh_end.strip()
    errors: list[str] = []
    if not _TIME_RE.match(wh_start):
        errors.append("Начало рабочего дня должно быть в формате ЧЧ:ММ.")
    if not _TIME_RE.match(wh_end):
        errors.append("Конец рабочего дня должен быть в формате ЧЧ:ММ.")
    if not errors and wh_start >= wh_end:
        errors.append("Начало рабочего дня должно быть раньше конца.")
    if errors:
        # Показываем ошибку и введённые значения (не redirect и не тихий дефолт).
        settings = get_settings()
        safe = _safe_view(settings)
        safe["app"]["name"] = app_name.strip()
        safe["app"]["timezone"] = timezone.strip()
        safe["scheduling"]["wh_start"] = wh_start
        safe["scheduling"]["wh_end"] = wh_end
        safe["scheduling"]["default_meeting_minutes"] = default_meeting_minutes
        safe["scheduling"]["default_travel_buffer_minutes"] = default_travel_buffer_minutes
        safe["scheduling"]["high_priority_threshold"] = high_priority_threshold
        safe["scheduling"]["max_alternatives"] = max_alternatives
        return render(
            request,
            "settings.html",
            current_user=admin,
            active="settings",
            safe=safe,
            config_path=str(CONFIG_PATH),
            is_admin=True,
            saved=False,
            error=" ".join(errors),
            status_code=400,
        )
    updates: list[tuple[list[str], object]] = [
        (["app", "name"], app_name.strip() or "Умный календарь"),
        (["app", "timezone"], timezone.strip() or "Europe/Moscow"),
        (["scheduling", "working_hours", "start"], wh_start),
        (["scheduling", "working_hours", "end"], wh_end),
        (["scheduling", "default_meeting_minutes"], max(15, min(600, default_meeting_minutes))),
        (["scheduling", "default_travel_buffer_minutes"], max(0, min(600, default_travel_buffer_minutes))),
        # FN-10: реальные параметры планировщика доступны для правки.
        (["scheduling", "high_priority_threshold"], max(0, min(10, high_priority_threshold))),
        (["scheduling", "max_alternatives"], max(1, min(10, max_alternatives))),
        (["notifications", "default_channel"], default_channel.strip() or "messenger"),
        (["assistant", "dify", "enabled"], dify_enabled == "on"),
        (["assistant", "dify", "base_url"], dify_base_url.strip() or "https://api.dify.ai/v1"),
    ]
    _save_config(updates)
    return local_redirect(request, "/settings?saved=1")
