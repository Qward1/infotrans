"""Участники встречи (EventParticipant)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# response_status
RESP_PENDING = "pending"
RESP_ACCEPTED = "accepted"
RESP_DECLINED = "declined"
RESP_TENTATIVE = "tentative"
RESPONSE_STATUSES = (RESP_PENDING, RESP_ACCEPTED, RESP_DECLINED, RESP_TENTATIVE)


class EventParticipant(Base):
    __tablename__ = "event_participants"
    # ARCH-07: индекс под «где пользователь участник».
    __table_args__ = (Index("ix_participants_user", "user_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="attendee", nullable=False)
    # Задел модели (миграций нет): поля ниже пока не используются в UI/логике.
    priority_for_event: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 0..10
    response_status: Mapped[str] = mapped_column(String(16), default=RESP_PENDING, nullable=False)

    event = relationship("CalendarEvent", back_populates="participants")
    user = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EventParticipant event={self.event_id} user={self.user_id}>"
