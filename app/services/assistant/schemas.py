"""Строгие схемы слоя ассистента.

Ключевой объект — ``NormalizedRequest``: к нему приводится любой пользовательский
текст (локальным нормализатором или Dify/LLM). Дальше оркестратор работает только
с этой структурой, а не с сырым текстом.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

# ------------------------------ Intents ------------------------------------ #
INTENTS = (
    "create_event",
    "update_event",
    "delete_event",
    "show_calendar",
    "find_free_slots",
    "find_tickets",
    "create_reminder",
    "move_event",
    "generate_meeting_protocol",
    "create_events_from_protocol",
    "summarize_schedule",
    "unknown",
)


# --------------------------- Вложенные структуры --------------------------- #
class ReminderData(BaseModel):
    minutes_before: int | None = None
    remind_at: dt.datetime | None = None
    channel: str = "web"  # web | messenger | email


class EventData(BaseModel):
    title: str | None = None
    description: str | None = None
    date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    duration_minutes: int | None = None
    timezone: str | None = None
    format: str | None = None  # online | offline | hybrid
    importance: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10)
    city: str | None = None
    address: str | None = None
    meeting_url: str | None = None
    participants: list[str] = Field(default_factory=list)  # email или имена
    responsible_person: str | None = None
    reminder: ReminderData | None = None


class TravelData(BaseModel):
    origin_city: str | None = None
    destination_city: str | None = None
    departure_date: dt.date | None = None
    return_date: dt.date | None = None
    transport_type: str = "any"  # flight | train | any
    preferences: list[str] = Field(default_factory=list)
    budget: float | None = None


class FollowUpMeeting(BaseModel):
    title: str
    date_hint: str | None = None
    participants: list[str] = Field(default_factory=list)
    duration_minutes: int | None = None


class ProtocolData(BaseModel):
    target_event_id: int | None = None
    source_document_id: int | None = None
    summary: str = ""
    participants: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    responsibles: list[str] = Field(default_factory=list)
    deadlines: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_up_meetings: list[FollowUpMeeting] = Field(default_factory=list)


class TargetEvent(BaseModel):
    event_id: int | None = None
    title: str | None = None
    date_hint: str | None = None


# ------------------------------ Результат ----------------------------------- #
class NormalizedRequest(BaseModel):
    intent: str = "unknown"
    confidence: float = 0.0
    original_text: str = ""
    language: str = "ru"
    missing_fields: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    source: str = "local"  # local | dify | llm | dify-fallback

    event: EventData = Field(default_factory=EventData)
    travel: TravelData = Field(default_factory=TravelData)
    protocol: ProtocolData = Field(default_factory=ProtocolData)
    target_event: TargetEvent = Field(default_factory=TargetEvent)


# --------------------- Ответ оркестратора (для /api/chat) ------------------- #
class SuggestedAction(BaseModel):
    """Кнопка/действие, которое пользователь может подтвердить или запустить."""

    type: str
    label: str
    style: str = "primary"  # primary | ghost | danger
    action_id: str | None = None  # если действие сохранено как черновик в БД
    payload: dict = Field(default_factory=dict)


class AssistantCard(BaseModel):
    """Структурированная карточка для UI (встреча, конфликт, слоты, билеты…)."""

    kind: str  # created_event | conflict | alternative_slots | travel_options |
    #            protocol | tasks | reschedule_plan | summary | reminder | calendar
    title: str
    data: dict = Field(default_factory=dict)


class AssistantResult(BaseModel):
    reply: str
    intent: str = "unknown"
    mode: str = "local"  # local | dify | dify-fallback | llm
    confidence: float = 0.0
    language: str = "ru"
    status: str = "info"  # done | needs_clarification | needs_confirmation |
    #                        conflict | info | error
    conversation_id: str | None = None

    extracted: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None

    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    cards: list[AssistantCard] = Field(default_factory=list)

    created_event: dict | None = None
    updated_event: dict | None = None
    alternative_slots: list[dict] = Field(default_factory=list)
    travel_options: list[dict] = Field(default_factory=list)
    protocol: dict | None = None
    warnings: list[str] = Field(default_factory=list)
