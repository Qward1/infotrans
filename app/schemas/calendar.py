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
