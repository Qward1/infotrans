"""Сериализация событий и префиллы форм для карточек ассистента."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models.calendar import CalendarEvent
from app.schemas.calendar import serialize_event
from app.services.assistant.schemas import NormalizedRequest


def event_out(event: CalendarEvent) -> dict:
    """Базовый словарь события для чат-карточек (ARCH-04: общая точка)."""
    return serialize_event(event)


def event_payload(nr: NormalizedRequest, start: datetime, end: datetime, settings) -> dict:
    ev = nr.event
    return {
        "title": (ev.title or "Встреча").strip(),
        "description": ev.description or "",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": ev.timezone or settings.app.timezone,
        "location_type": ev.format or "offline",
        "city": ev.city or "",
        "address": ev.address or "",
        "meeting_url": ev.meeting_url or "",
        "importance": ev.importance or "normal",
        "priority": ev.priority if ev.priority is not None else 5,
        "status": "planned",
        "source": "assistant",
        "participants": ev.participants,
    }


def prefill_from_nr(nr: NormalizedRequest) -> dict:
    """Префилл формы события из распознанного запроса (UX-06)."""
    ev = nr.event
    payload: dict = {"source": "assistant"}
    if ev.title:
        payload["title"] = ev.title
    if ev.date and ev.start_time:
        start = datetime.combine(ev.date, ev.start_time)
        payload["start_at"] = start.isoformat(timespec="minutes")
        dur = ev.duration_minutes or 60
        payload["end_at"] = (start + timedelta(minutes=dur)).isoformat(timespec="minutes")
    if ev.format:
        payload["location_type"] = ev.format
    if ev.city:
        payload["city"] = ev.city
    if ev.participants:
        payload["participants"] = list(ev.participants)
    return payload
