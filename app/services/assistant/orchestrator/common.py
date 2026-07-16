"""Общие утилиты оркестратора: черновики действий, создание событий, время."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.assistant import ACTION_PENDING, AssistantAction
from app.models.calendar import CalendarEvent
from app.models.user import User
from app.schemas.calendar import EventCreate
from app.services import audit as audit_service
from app.services import availability
from app.services import calendar as calendar_service
from app.services import participants as participants_service
from app.services.assistant.schemas import NormalizedRequest

logger = logging.getLogger("smartcal.orchestrator")


def compose_datetimes(nr: NormalizedRequest, settings: Settings, now: datetime) -> tuple[datetime, datetime]:
    ev = nr.event
    d = ev.date or now.date()
    if ev.start_time:
        start = datetime.combine(d, ev.start_time)
    else:
        wh_start, _ = availability.parse_working_hours(settings)
        start = datetime.combine(d, wh_start)
    if ev.end_time:
        end = datetime.combine(d, ev.end_time)
    else:
        dur = ev.duration_minutes or settings.scheduling.default_meeting_minutes
        end = start + timedelta(minutes=dur)
    if end <= start:
        end = start + timedelta(minutes=settings.scheduling.default_meeting_minutes)
    return start, end


def resolve_participant_ids(db: Session, values: list[str]) -> tuple[list[int], list[str]]:
    """ARCH-02: единый резолв (id/email/точное имя); нерезолвленные — в warnings."""
    return participants_service.resolve(db, values)


def create_action(
    db: Session,
    user: User,
    action_type: str,
    title: str,
    payload: dict,
    ttl_minutes: int = 1440,
) -> AssistantAction:
    action = AssistantAction(
        action_id=str(uuid.uuid4()),
        user_id=user.id,
        type=action_type,
        status=ACTION_PENDING,
        title=title[:255],
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        expires_at=datetime.now() + timedelta(minutes=ttl_minutes),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def create_event_row(db, user, payload: dict, settings) -> CalendarEvent:
    """Создать строку события + участников из payload черновика."""
    data = EventCreate(
        title=payload["title"], description=payload.get("description", ""),
        start_at=payload["start_at"], end_at=payload["end_at"],
        timezone=payload.get("timezone", settings.app.timezone),
        location_type=payload.get("location_type", "offline"),
        city=payload.get("city", ""), address=payload.get("address", ""),
        meeting_url=payload.get("meeting_url", ""),
        importance=payload.get("importance", "normal"),
        priority=payload.get("priority", 5),
        status="planned", source=payload.get("source", "assistant"),
        participants=payload.get("participants", []),
    )
    event = calendar_service.create_event(db, user.id, data, actor_id=user.id)
    audit_service.record(db, actor_user_id=user.id, action="create_event",
                         entity_type="event", entity_id=event.id, payload={"title": event.title})
    return event
