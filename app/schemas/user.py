"""Схемы пользователей."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import ROLES, ROLE_USER

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    value = (value or "").strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("Некорректный email")
    return value


class UserCreate(BaseModel):
    email: str
    full_name: str = ""
    password: str = Field(min_length=6, max_length=256)
    role: str = ROLE_USER
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role должно быть одним из {ROLES}")
        return v


class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)
    role: str | None = None
    is_active: bool | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_email(v)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLES:
            raise ValueError(f"role должно быть одним из {ROLES}")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
