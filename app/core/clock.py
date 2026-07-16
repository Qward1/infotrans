"""Единая точка «сейчас» (ARCH-08, BUG-12).

Соглашение проекта: все datetime в БД — наивные, в локальном времени
``settings.app.timezone``. ``local_now()`` возвращает наивное «сейчас» в этом
поясе, поэтому расчёты не съезжают, даже если системный пояс сервера другой.
Тестам достаточно замокать одну эту функцию.

Полный переход на aware-UTC — за рамками MVP (зафиксировано в README).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings


def local_now() -> datetime:
    """Наивное «сейчас» в поясе приложения (``settings.app.timezone``)."""
    tz_name = get_settings().app.timezone
    try:
        return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return datetime.now()
