"""Сервис событий календаря: CRUD и выборки по диапазону/дню/неделе/месяцу."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.calendar import CalendarEvent, STATUS_CANCELLED
from app.models.meeting import EventParticipant
from app.schemas.calendar import EventCreate, EventUpdate
from app.services import users as users_service

CALENDAR_VIEWS = ("day", "week", "month")


def get_event(db: Session, event_id: int) -> CalendarEvent | None:
    return db.get(CalendarEvent, event_id)


def _resolve_participant_ids(db: Session, participants: list[str], owner_id: int) -> list[int]:
    ids: list[int] = []
    unresolved: list[str] = []
    for raw in participants or []:
        value = str(raw or "").strip()
        if not value:
            continue
        user = None
        if value.isdigit():
            user = users_service.get_by_id(db, int(value))
        if user is None and "@" in value:
            user = users_service.get_by_email(db, value)
        if user is None:
            matches = [
                u for u in users_service.search_users(db, value, active_only=True, limit=5)
                if (u.full_name or "").strip().lower() == value.lower()
            ]
            user = matches[0] if len(matches) == 1 else None
        if user is None or not user.is_active:
            unresolved.append(value)
            continue
        if user.id != owner_id and user.id not in ids:
            ids.append(user.id)
    if unresolved:
        raise ValueError("Не найдены участники: " + ", ".join(unresolved))
    return ids


def _set_participants(db: Session, event: CalendarEvent, participants: list[str]) -> None:
    db.query(EventParticipant).filter(EventParticipant.event_id == event.id).delete()
    for user_id in _resolve_participant_ids(db, participants, event.owner_id):
        db.add(EventParticipant(event_id=event.id, user_id=user_id, role="attendee"))


def create_event(
    db: Session,
    owner_id: int,
    data: EventCreate,
    *,
    actor_id: int | None = None,
) -> CalendarEvent:
    event = CalendarEvent(
        owner_id=owner_id,
        created_by_id=actor_id or owner_id,
        updated_by_id=actor_id or owner_id,
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
    db.flush()
    _set_participants(db, event, data.participants)
    db.commit()
    db.refresh(event)
    return event


def update_event(
    db: Session,
    event: CalendarEvent,
    data: EventUpdate,
    *,
    actor_id: int | None = None,
) -> CalendarEvent:
    payload = data.model_dump(exclude_unset=True)
    participants = payload.pop("participants", None)
    for field, value in payload.items():
        if value is None and field in {"title"}:
            continue
        setattr(event, field, value)
    # Проверка согласованности времени после применения изменений.
    if event.end_at <= event.start_at:
        raise ValueError("end_at должно быть позже start_at")
    if participants is not None:
        _set_participants(db, event, participants)
    if actor_id is not None:
        event.updated_by_id = actor_id
    event.updated_at = datetime.now()
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


def day_bounds(reference: datetime | date | None = None) -> tuple[datetime, datetime]:
    """Границы дня (00:00 — следующий день 00:00), содержащего reference."""
    if reference is None:
        reference = datetime.now()
    ref_date = reference.date() if isinstance(reference, datetime) else reference
    start = datetime(ref_date.year, ref_date.month, ref_date.day)
    return start, start + timedelta(days=1)


def month_bounds(reference: datetime | date | None = None) -> tuple[datetime, datetime]:
    """Границы месяца (1-е число 00:00 — 1-е число следующего месяца 00:00)."""
    if reference is None:
        reference = datetime.now()
    ref_date = reference.date() if isinstance(reference, datetime) else reference
    start = datetime(ref_date.year, ref_date.month, 1)
    if ref_date.month == 12:
        end = datetime(ref_date.year + 1, 1, 1)
    else:
        end = datetime(ref_date.year, ref_date.month + 1, 1)
    return start, end


def month_grid_bounds(reference: datetime | date | None = None) -> tuple[datetime, datetime]:
    """Границы видимой месячной сетки: полные недели Пн—Вс вокруг месяца."""
    start, end = month_bounds(reference)
    grid_start = start - timedelta(days=start.weekday())
    last_day = end - timedelta(days=1)
    grid_end = last_day + timedelta(days=(6 - last_day.weekday()) + 1)
    return grid_start, grid_end


def normalize_view(view: str | None) -> str:
    """Нормализовать имя вида календаря."""
    view = (view or "week").strip().lower()
    return view if view in CALENDAR_VIEWS else "week"


def period_bounds(view: str, reference: datetime | date | None = None) -> tuple[datetime, datetime]:
    """Границы выбранного периода календаря."""
    view = normalize_view(view)
    if view == "day":
        return day_bounds(reference)
    if view == "month":
        return month_bounds(reference)
    return week_bounds(reference)


def list_period(
    db: Session,
    owner_id: int,
    view: str,
    reference: datetime | date | None = None,
) -> tuple[datetime, datetime, list[CalendarEvent]]:
    """События выбранного периода.

    Для месячного вида возвращаем события всей видимой сетки, включая дни соседних
    месяцев, чтобы сетка не подгружала события отдельными запросами.
    """
    view = normalize_view(view)
    if view == "month":
        start, end = month_grid_bounds(reference)
    else:
        start, end = period_bounds(view, reference)
    events = list_events_in_range(db, owner_id, start, end)
    return start, end, events


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
