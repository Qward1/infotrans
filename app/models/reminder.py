"""Напоминания о событиях."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# channel
CHANNEL_WEB = "web"
CHANNEL_MESSENGER = "messenger"
CHANNEL_EMAIL = "email"
CHANNELS = (CHANNEL_WEB, CHANNEL_MESSENGER, CHANNEL_EMAIL)

# status
REMINDER_SCHEDULED = "scheduled"
REMINDER_SENT = "sent"
REMINDER_CANCELLED = "cancelled"
REMINDER_STATUSES = (REMINDER_SCHEDULED, REMINDER_SENT, REMINDER_CANCELLED)


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), default=CHANNEL_WEB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=REMINDER_SCHEDULED, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    event = relationship("CalendarEvent", back_populates="reminders")
    user = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Reminder event={self.event_id} at {self.remind_at}>"
