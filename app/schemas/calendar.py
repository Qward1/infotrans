"""Схемы событий календаря."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.calendar import (
    LOC_OFFLINE,
    LOCATION_TYPES,
    SOURCE_MANUAL,
    SOURCES,
    STATUS_PLANNED,
    STATUSES,
)


class EventBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    start_at: datetime
    end_at: datetime
    timezone: str = "Europe/Moscow"
    location_type: str = LOC_OFFLINE
    city: str = ""
    address: str = ""
    meeting_url: str = ""
    importance: str = "normal"
    priority: int = Field(default=5, ge=0, le=10)

    @field_validator("location_type")
    @classmethod
    def _loc(cls, v: str) -> str:
        if v not in LOCATION_TYPES:
            raise ValueError(f"location_type должно быть одним из {LOCATION_TYPES}")
        return v

    @model_validator(mode="after")
    def _check_times(self) -> "EventBase":
        if self.end_at <= self.start_at:
            raise ValueError("end_at должно быть позже start_at")
        return self


class EventCreate(EventBase):
    owner_id: int | None = None
    status: str = STATUS_PLANNED
    source: str = SOURCE_MANUAL
    participants: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"status должно быть одним из {STATUSES}")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in SOURCES:
            raise ValueError(f"source должно быть одним из {SOURCES}")
        return v


class EventUpdate(BaseModel):
    """Частичное обновление. Валидация времени — на уровне сервиса."""

    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    location_type: str | None = None
    city: str | None = None
    address: str | None = None
    meeting_url: str | None = None
    importance: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10)
    status: str | None = None
    participants: list[str] | None = None

    @field_validator("location_type")
    @classmethod
    def _loc(cls, v: str | None) -> str | None:
        if v is not None and v not in LOCATION_TYPES:
            raise ValueError(f"location_type должно быть одним из {LOCATION_TYPES}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is not None and v not in STATUSES:
            raise ValueError(f"status должно быть одним из {STATUSES}")
        return v


def serialize_event(
    event,
    *,
    include_participants: bool = False,
    conflict_ids: frozenset | set = frozenset(),
    calendar_owner=None,
    viewer=None,
) -> dict:
    """Единая сериализация события в dict (ARCH-04).

    Базовый словарь используют чат-карточки ассистента; расширенный
    (``include_participants=True``) — календарный payload: участники, имя
    владельца, флаги конфликтов/приглашения/прав.
    """
    data = {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "timezone": event.timezone,
        "location_type": event.location_type,
        "city": event.city,
        "address": event.address,
        "meeting_url": event.meeting_url,
        "importance": event.importance,
        "priority": event.priority,
        "status": event.status,
        "source": event.source,
        "owner_id": event.owner_id,
    }
    if include_participants:
        owner_obj = event.owner
        data["owner_name"] = (owner_obj.full_name or owner_obj.email) if owner_obj else ""
        data["created_by_id"] = event.created_by_id
        data["updated_by_id"] = event.updated_by_id
        data["participants"] = [
            {
                "user_id": p.user_id,
                "full_name": p.user.full_name or p.user.email,
                "email": p.user.email,
            }
            for p in event.participants
        ]
        data["is_conflict"] = event.id in conflict_ids
        if calendar_owner is not None:
            # Владелец календаря приглашён на чужую встречу (BUG-01/FN-01).
            data["is_participant"] = event.owner_id != calendar_owner.id
        if viewer is not None:
            # Редактировать/удалять может только владелец события или админ.
            data["can_edit"] = viewer.is_admin or event.owner_id == viewer.id
    return data


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    timezone: str
    location_type: str
    city: str
    address: str
    meeting_url: str
    importance: str
    priority: int
    owner_id: int
    created_by_id: int | None = None
    updated_by_id: int | None = None
    status: str
    source: str
    created_at: datetime
    updated_at: datetime
