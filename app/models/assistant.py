"""Модели слоя ассистента: черновики действий, документы, уведомления."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# --- AssistantAction -------------------------------------------------------- #
# Действие, предложенное ассистентом и требующее подтверждения пользователя.
ACTION_PENDING = "pending"
ACTION_CONFIRMED = "confirmed"
ACTION_REJECTED = "rejected"
ACTION_EXPIRED = "expired"

# Типы действий (что произойдёт при подтверждении).
ACTION_CREATE_EVENT = "create_event"
ACTION_UPDATE_EVENT = "update_event"
ACTION_DELETE_EVENT = "delete_event"
ACTION_MOVE_EVENT = "move_event"
ACTION_CREATE_REMINDER = "create_reminder"
ACTION_CREATE_EVENTS_FROM_PROTOCOL = "create_events_from_protocol"
ACTION_RESCHEDULE_CONFLICT = "reschedule_conflict"


class AssistantAction(Base):
    __tablename__ = "assistant_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ACTION_PENDING, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssistantAction {self.action_id} {self.type} {self.status}>"


# --- Document --------------------------------------------------------------- #
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="SET NULL"), index=True, nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    owner = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document {self.id} {self.filename!r}>"


# --- Notification ----------------------------------------------------------- #
NOTIFY_UNREAD = "unread"
NOTIFY_READ = "read"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), default="messenger", nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=NOTIFY_UNREAD, nullable=False)
    meta_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    user = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification {self.id} u={self.user_id} {self.status}>"
