"""Страница календаря (день/неделя/месяц)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import require_user
from app.models.user import User
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service
from app.services import users as users_service
from app.templating import render

router = APIRouter(tags=["calendar"])


MONTH_NAMES = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _now(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        return datetime.now()


def _parse_ref(value: str | None, fallback: datetime) -> datetime:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            pass
    return fallback


def _label(view: str, start: datetime, end: datetime) -> str:
    if view == "day":
        return start.strftime("%d.%m.%Y")
    if view == "month":
        return f"{MONTH_NAMES[start.month]} {start.year}"
    return f"{start.strftime('%d.%m.%Y')} — {(end - timedelta(days=1)).strftime('%d.%m.%Y')}"


def _add_period(view: str, start: datetime, delta: int) -> datetime:
    if view == "day":
        return start + timedelta(days=delta)
    if view == "week":
        return start + timedelta(days=7 * delta)
    month = start.month + delta
    year = start.year
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1
    return datetime(year, month, 1)


def resolve_calendar_owner(db: Session, current_user: User, user_id: int | None) -> User:
    """Проверить доступ к календарю и вернуть владельца календаря."""
    if user_id is None or user_id == current_user.id:
        return current_user
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Нет доступа к календарю пользователя")
    target = users_service.get_by_id(db, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return target


def calendar_payload(
    db: Session,
    user: User,
    view: str,
    ref: datetime,
    owner: User | None = None,
) -> dict:
    """JSON-представление календарного периода для страницы и AJAX."""
    settings = get_settings()
    owner = owner or user
    view = calendar_service.normalize_view(view)
    period_start, period_end = calendar_service.period_bounds(view, ref)
    range_start, range_end, events = calendar_service.list_period(db, owner.id, view, ref)
    today = _now(settings.app.timezone).date()

    conflict_ids: set[int] = set()
    for c in scheduling_service.conflicts_for_user(db, owner.id, range_start, range_end):
        if c.first.event_id:
            conflict_ids.add(c.first.event_id)
        if c.second.event_id:
            conflict_ids.add(c.second.event_id)

    days = []
    current = range_start
    while current < range_end:
        days.append(
            {
                "date": current.date().isoformat(),
                "is_today": current.date() == today,
                "is_current_month": current.month == period_start.month,
            }
        )
        current += timedelta(days=1)

    def event_out(e):
        participants = [
            {
                "user_id": p.user_id,
                "full_name": p.user.full_name or p.user.email,
                "email": p.user.email,
            }
            for p in e.participants
        ]
        event_owner = e.owner
        owner_name = (
            (event_owner.full_name or event_owner.email)
            if event_owner
            else (owner.full_name or owner.email)
        )
        return {
            "id": e.id,
            "title": e.title,
            "description": e.description,
            "start_at": e.start_at.isoformat(),
            "end_at": e.end_at.isoformat(),
            "timezone": e.timezone,
            "location_type": e.location_type,
            "city": e.city,
            "address": e.address,
            "meeting_url": e.meeting_url,
            "importance": e.importance,
            "priority": e.priority,
            "owner_id": e.owner_id,
            "owner_name": owner_name,
            "created_by_id": e.created_by_id,
            "updated_by_id": e.updated_by_id,
            "participants": participants,
            "status": e.status,
            "source": e.source,
            "is_conflict": e.id in conflict_ids,
            # Владелец календаря приглашён на чужую встречу (BUG-01/FN-01).
            "is_participant": e.owner_id != owner.id,
            # Редактировать/удалять может только владелец события или админ.
            "can_edit": user.is_admin or e.owner_id == user.id,
        }

    prev_date = _add_period(view, period_start, -1).date().isoformat()
    next_date = _add_period(view, period_start, 1).date().isoformat()
    return {
        "view": view,
        "date": period_start.date().isoformat(),
        "timezone": settings.app.timezone,
        "owner": {
            "id": owner.id,
            "full_name": owner.full_name or owner.email,
            "email": owner.email,
            "is_current_user": owner.id == user.id,
        },
        "viewer": {"id": user.id, "is_admin": user.is_admin},
        "admin_view": user.is_admin and owner.id != user.id,
        "can_edit": user.is_admin or owner.id == user.id,
        "label": _label(view, period_start, period_end),
        "period_start": period_start.date().isoformat(),
        "period_end": (period_end - timedelta(days=1)).date().isoformat(),
        "range_start": range_start.date().isoformat(),
        "range_end": (range_end - timedelta(days=1)).date().isoformat(),
        "today": today.isoformat(),
        "prev_date": prev_date,
        "next_date": next_date,
        "days": days,
        "hours": list(range(0, 24)),
        "events": [event_out(e) for e in events],
        "conflict_ids": sorted(conflict_ids),
    }


@router.get("/calendar")
def calendar_page(
    request: Request,
    view: str = Query(default="week", description="day | week | month"),
    date: str | None = Query(default=None, description="Дата периода, YYYY-MM-DD"),
    week: str | None = Query(default=None, description="Legacy: любая дата недели, YYYY-MM-DD"),
    user_id: int | None = Query(default=None, description="Для admin: календарь пользователя"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    ref = _parse_ref(date or week, _now(settings.app.timezone))
    if week and not date:
        view = "week"
    owner = resolve_calendar_owner(db, user, user_id)
    payload = calendar_payload(db, user, view, ref, owner)

    return render(
        request,
        "calendar.html",
        current_user=user,
        active="calendar",
        calendar_payload=payload,
        calendar_users=users_service.list_active_users(db) if user.is_admin else [],
    )
