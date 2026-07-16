"""Уведомления пользователю.

Provider-интерфейс + mock-провайдер, который пишет уведомления в таблицу
``notifications`` (и в audit log) — их видно в UI. Реальный мессенджер
(MAX/Telegram/email) подключается новым провайдером без изменения вызовов.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.assistant import NOTIFY_READ, Notification
from app.models.user import User
from app.services import audit as audit_service

logger = logging.getLogger("smartcal.notifications")


class NotificationProvider(ABC):
    name = "abstract"

    @abstractmethod
    def send_message(
        self, db: Session, user: User, text: str, channel: str, title: str, meta: dict
    ) -> Notification: ...


class MockNotificationProvider(NotificationProvider):
    """Сохраняет уведомление в БД и audit log (demo-канал)."""

    name = "mock"

    def send_message(self, db, user, text, channel, title, meta):
        note = Notification(
            user_id=user.id,
            channel=channel,
            title=title or "Уведомление",
            text=text,
            meta_json=json.dumps(meta or {}, ensure_ascii=False, default=str),
        )
        db.add(note)
        audit_service.record(
            db,
            actor_user_id=user.id,
            action="notification_sent",
            entity_type="notification",
            payload={"channel": channel, "title": title},
            commit=False,
        )
        db.commit()
        db.refresh(note)
        logger.info("Notification -> %s [%s]: %s", user.email, channel, title)
        return note


def get_provider(settings: Settings) -> NotificationProvider:
    # На следующем этапе: выбор реального провайдера по settings.notifications.
    return MockNotificationProvider()


def notify(
    db: Session,
    settings: Settings,
    user: User,
    text: str,
    title: str = "",
    channel: str | None = None,
    meta: dict | None = None,
) -> Notification:
    channel = channel or settings.notifications.default_channel
    provider = get_provider(settings)
    return provider.send_message(db, user, text, channel, title, meta or {})


def list_for_user(db: Session, user_id: int, limit: int = 30) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def unread_count(db: Session, user_id: int) -> int:
    from sqlalchemy import func

    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.status != NOTIFY_READ)
    )
    return int(db.execute(stmt).scalar_one())


def mark_read(db: Session, user_id: int, notification_id: int) -> bool:
    """Пометить одно уведомление прочитанным (только владелец). FN-07."""
    note = db.get(Notification, notification_id)
    if note is None or note.user_id != user_id:
        return False
    if note.status != NOTIFY_READ:
        note.status = NOTIFY_READ
        db.commit()
    return True


def mark_all_read(db: Session, user_id: int) -> int:
    notes = db.execute(
        select(Notification).where(
            Notification.user_id == user_id, Notification.status != NOTIFY_READ
        )
    ).scalars().all()
    for n in notes:
        n.status = NOTIFY_READ
    db.commit()
    return len(notes)
