"""Схемы ассистента / чата / билетов (заглушки под будущую Dify-интеграцию)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # conversation_id позволит на следующем этапе поддержать многоходовые диалоги.
    conversation_id: str | None = None
    assistant: str | None = None


class AssistantChatCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class AssistantChatUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    is_archived: bool | None = None


class AssistantChatMessageCreate(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)


class TicketOption(BaseModel):
    provider: str
    carrier: str = ""
    mode: str  # train | plane | bus
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int
    transfers: int = 0
    price: float
    currency: str = "RUB"
    url: str = ""
    available_seats: int | None = None
    time_precision: str = "datetime"  # datetime | date
