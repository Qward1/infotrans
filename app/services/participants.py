"""Единая точка резолва участников встречи (ARCH-02).

Раньше «участник» резолвился двумя разными способами: ассистент понимал только
email, календарный сервис — id/email/точное имя. Теперь оба пути зовут
``resolve``; политика реакции на нерезолвленных (ошибка или предупреждение) —
на стороне вызывающего.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import users as users_service


def resolve(
    db: Session,
    values: list[str] | None,
    owner_id: int | None = None,
) -> tuple[list[int], list[str]]:
    """Сопоставить значения (id / email / точное имя) активным пользователям.

    Возвращает ``(ids, unresolved)``. Владелец (``owner_id``) и дубли
    исключаются из результата.
    """
    ids: list[int] = []
    unresolved: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        user = None
        if value.isdigit():
            user = users_service.get_by_id(db, int(value))
        if user is None and "@" in value:
            user = users_service.get_by_email(db, value)
        if user is None:
            matches = [
                u for u in users_service.search_users(db, value, active_only=True, limit=5)
                if (u.full_name or "").strip().lower() == value.lower()
            ]
            user = matches[0] if len(matches) == 1 else None
        if user is None or not user.is_active:
            unresolved.append(value)
            continue
        if user.id != owner_id and user.id not in ids:
            ids.append(user.id)
    return ids, unresolved
