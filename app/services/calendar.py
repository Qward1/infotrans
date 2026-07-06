"""Сервис событий календаря: CRUD и выборки по диапазону/неделе."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.calendar import CalendarEvent, STATUS_CANCELLED
from app.schemas.calendar import EventCreate, EventUpdate


def get_event(db: Session, event_id: int) -> CalendarEvent | None:
    return db.get(CalendarEvent, event_id)


def create_event(db: Session, owner_id: int, data: EventCreate) -> CalendarEvent:
    event = CalendarEvent(
        owner_id=owner_id,
        title=data.title.strip(),
        description=data.description,
        start_at=data.start_at,
        end_at=data.end_at,
        timezone=data.timezone,
        location_type=data.location_type,
        city=data.city.strip(),
        address=data.address.strip(),
        meeting_url=data.meeting_url.strip(),
        importance=data.importance,
        priority=data.priority,
        status=data.status,
        source=data.source,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def update_event(db: Session, event: CalendarEvent, data: EventUpdate) -> CalendarEvent:
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        if value is None and field in {"title"}:
            continue
        setattr(event, field, value)
    # Проверка согласованности времени после применения изменений.
    if event.end_at <= event.start_at:
        raise ValueError("end_at должно быть позже start_at")
    db.commit()
    db.refresh(event)
    return event


def delete_event(db: Session, event: CalendarEvent) -> None:
    db.delete(event)
    db.commit()


def list_events_in_range(
    db: Session,
    owner_id: int,
    range_start: datetime,
    range_end: datetime,
    include_cancelled: bool = True,
) -> list[CalendarEvent]:
    """События владельца, пересекающиеся с [range_start, range_end)."""
    conditions = [
        CalendarEvent.owner_id == owner_id,
        CalendarEvent.start_at < range_end,
        CalendarEvent.end_at > range_start,
    ]
    if not include_cancelled:
        conditions.append(CalendarEvent.status != STATUS_CANCELLED)
    stmt = (
        select(CalendarEvent)
        .where(and_(*conditions))
        .order_by(CalendarEvent.start_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def week_bounds(reference: datetime | date | None = None) -> tuple[datetime, datetime]:
    """Границы недели (Пн 00:00 — следующий Пн 00:00), содержащей reference."""
    if reference is None:
        reference = datetime.now()
    if isinstance(reference, datetime):
        ref_date = reference.date()
    else:
        ref_date = reference
    monday = ref_date - timedelta(days=ref_date.weekday())
    start = datetime(monday.year, monday.month, monday.day)
    return start, start + timedelta(days=7)


def list_week(
    db: Session, owner_id: int, reference: datetime | None = None
) -> tuple[datetime, datetime, list[CalendarEvent]]:
    start, end = week_bounds(reference)
    events = list_events_in_range(db, owner_id, start, end)
    return start, end, events


def upcoming_events(db: Session, owner_id: int, limit: int = 5) -> list[CalendarEvent]:
    now = datetime.now()
    stmt = (
        select(CalendarEvent)
        .where(
            CalendarEvent.owner_id == owner_id,
            CalendarEvent.end_at >= now,
            CalendarEvent.status != STATUS_CANCELLED,
        )
        .order_by(CalendarEvent.start_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
