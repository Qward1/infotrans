"""Схемы ассистента / чата / билетов (заглушки под будущую Dify-интеграцию)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # conversation_id позволит на следующем этапе поддержать многоходовые диалоги.
    conversation_id: str | None = None
    assistant: str | None = None


class ChatAction(BaseModel):
    """Структурированное действие, которое ассистент предлагает выполнить."""

    type: str  # create_event | find_slots | search_tickets | make_protocol | info
    label: str
    payload: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    intent: str = "smalltalk"
    mode: str = "mock"  # mock | dify | llm
    conversation_id: str | None = None
    actions: list[ChatAction] = Field(default_factory=list)


class FreeSlot(BaseModel):
    start_at: datetime
    end_at: datetime
    score: float = 1.0


class TicketOption(BaseModel):
    provider: str
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
