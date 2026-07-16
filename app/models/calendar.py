"""Модель события календаря (встречи/поездки)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# location_type
LOC_ONLINE = "online"
LOC_OFFLINE = "offline"
LOC_HYBRID = "hybrid"
LOCATION_TYPES = (LOC_ONLINE, LOC_OFFLINE, LOC_HYBRID)

# status
STATUS_PLANNED = "planned"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUSES = (STATUS_PLANNED, STATUS_CANCELLED, STATUS_COMPLETED)

# source
SOURCE_MANUAL = "manual"
SOURCE_ASSISTANT = "assistant"
SOURCE_PROTOCOL = "protocol"
SOURCES = (SOURCE_MANUAL, SOURCE_ASSISTANT, SOURCE_PROTOCOL)

# importance
IMPORTANCE_LEVELS = ("low", "normal", "high", "critical")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    # ARCH-07: составной индекс под выборки «события владельца в диапазоне».
    __table_args__ = (Index("ix_events_owner_start", "owner_id", "start_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)

    location_type: Mapped[str] = mapped_column(String(16), default=LOC_OFFLINE, nullable=False)
    city: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    meeting_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    importance: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 0..10

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PLANNED, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_MANUAL, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner = relationship("User", back_populates="events", foreign_keys=[owner_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])
    participants = relationship(
        "EventParticipant", back_populates="event", cascade="all, delete-orphan"
    )
    reminders = relationship(
        "Reminder", back_populates="event", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CalendarEvent {self.id} {self.title!r} @ {self.start_at}>"
