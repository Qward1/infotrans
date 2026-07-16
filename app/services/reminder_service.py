"""Отправка наступивших напоминаний (FN-08).

Планировщика в MVP нет — лёгкая asyncio-задача в ``lifespan`` раз в 60 секунд
вызывает ``send_due_reminders``. Функция чистая по эффекту: находит
``scheduled``-напоминания с ``remind_at <= now``, шлёт уведомление владельцу и
помечает их ``sent`` — её удобно вызывать из тестов напрямую.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.clock import local_now
from app.models.reminder import REMINDER_CANCELLED, REMINDER_SCHEDULED, REMINDER_SENT, Reminder
from app.services.assistant import notification_service

logger = logging.getLogger("smartcal.reminders")


def send_due_reminders(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Отправить все наступившие напоминания. Возвращает число отправленных."""
    now = now or local_now()
    due = db.execute(
        select(Reminder)
        .options(selectinload(Reminder.event), selectinload(Reminder.user))
        .where(Reminder.status == REMINDER_SCHEDULED, Reminder.remind_at <= now)
    ).scalars().all()

    sent = 0
    for reminder in due:
        event = reminder.event
        if event is None or reminder.user is None:
            reminder.status = REMINDER_CANCELLED
            continue
        notification_service.notify(
            db, settings, reminder.user,
            text=f"Напоминание: «{event.title}» начнётся {event.start_at:%d.%m в %H:%M}.",
            title="Напоминание о встрече",
            channel=reminder.channel or None,
            meta={"event_id": event.id, "reminder_id": reminder.id},
        )
        reminder.status = REMINDER_SENT
        sent += 1
    if due:
        db.commit()
    if sent:
        logger.info("Отправлено напоминаний: %d", sent)
    return sent
