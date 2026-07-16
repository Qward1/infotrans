"""Уведомления об изменениях событий — единая точка для API и ассистента.

Часть ARCH-02: раньше приглашения уходили только из ``confirm_action``
(ассистентский путь), а создание через модалку/API молчало (BUG-13). Теперь оба
пути зовут одни и те же функции.
"""
from __future__ import annotations

from app.core.config import Settings
from app.models.calendar import CalendarEvent
from app.models.user import User
from app.services.assistant import notification_service


def notify_created(db, settings: Settings, event: CalendarEvent, actor: User) -> None:
    """Уведомить владельца (если создавал не он) и участников о новой встрече."""
    if event.owner is not None and event.owner_id != actor.id:
        notification_service.notify(
            db, settings, event.owner,
            text=f"Встреча «{event.title}» запланирована на {event.start_at:%d.%m %H:%M}.",
            title="Новая встреча", meta={"event_id": event.id},
        )
    for p in event.participants:
        if p.user is None or p.user_id == actor.id or p.user_id == event.owner_id:
            continue
        notification_service.notify(
            db, settings, p.user,
            text=f"Вас пригласили на «{event.title}» {event.start_at:%d.%m %H:%M}.",
            title="Приглашение на встречу", meta={"event_id": event.id},
        )


def notify_moved(db, settings: Settings, event: CalendarEvent, actor: User) -> None:
    """Уведомить владельца и участников о переносе времени встречи."""
    recipients: list[User] = []
    if event.owner is not None and event.owner_id != actor.id:
        recipients.append(event.owner)
    for p in event.participants:
        if p.user is None or p.user_id == actor.id or p.user_id == event.owner_id:
            continue
        recipients.append(p.user)
    for user in recipients:
        notification_service.notify(
            db, settings, user,
            text=f"«{event.title}» перенесена на {event.start_at:%d.%m %H:%M}.",
            title="Перенос встречи", meta={"event_id": event.id},
        )


def notify_cancelled(db, settings: Settings, event: CalendarEvent, actor: User) -> None:
    """Уведомить владельца и участников об отмене встречи."""
    recipients: list[User] = []
    if event.owner is not None and event.owner_id != actor.id:
        recipients.append(event.owner)
    for p in event.participants:
        if p.user is None or p.user_id == actor.id or p.user_id == event.owner_id:
            continue
        recipients.append(p.user)
    for user in recipients:
        notification_service.notify(
            db, settings, user,
            text=f"Встреча «{event.title}» {event.start_at:%d.%m %H:%M} отменена.",
            title="Встреча отменена", meta={"event_id": event.id},
        )
