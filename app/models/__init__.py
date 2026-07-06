"""ORM-модели. Импорт здесь регистрирует их в ``Base.metadata``."""
from app.models.assistant import (
    AssistantAction,
    AssistantChat,
    AssistantChatMessage,
    Document,
    Notification,
)
from app.models.audit import AuditLog
from app.models.calendar import CalendarEvent
from app.models.meeting import EventParticipant
from app.models.reminder import Reminder
from app.models.user import User

__all__ = [
    "User",
    "CalendarEvent",
    "EventParticipant",
    "Reminder",
    "AuditLog",
    "AssistantChat",
    "AssistantChatMessage",
    "AssistantAction",
    "Document",
    "Notification",
]
