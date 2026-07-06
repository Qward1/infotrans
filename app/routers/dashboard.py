"""Главная и дашборд."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import get_current_user_optional, require_user
from app.core.urls import local_redirect
from app.models.user import User
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service
from app.templating import render

router = APIRouter(tags=["dashboard"])


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if user is None:
        return local_redirect(request, "/login")
    return local_redirect(request, "/dashboard")


_WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    now = datetime.now()
    week_start, week_end, week_events = calendar_service.list_week(db, user.id, now)
    upcoming = calendar_service.upcoming_events(db, user.id, limit=6)

    free = scheduling_service.free_slots_for_user(db, user.id, now, now + timedelta(days=7))
    conflicts = scheduling_service.conflicts_for_user(db, user.id, now, now + timedelta(days=14))

    # Встречи сегодня (не отменённые), отсортированы по времени.
    today = now.date()
    today_events = sorted(
        (e for e in week_events if e.start_at.date() == today and e.status != "cancelled"),
        key=lambda e: e.start_at,
    )

    # Нагрузка по дням недели (кол-во встреч на каждый день Пн–Вс) — для мини-графика.
    week_load = []
    max_load = 0
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        count = sum(1 for e in week_events if e.start_at.date() == day and e.status != "cancelled")
        max_load = max(max_load, count)
        week_load.append({"label": _WEEKDAY_NAMES[offset], "count": count, "is_today": day == today})

    # Приветствие по времени суток.
    hour = now.hour
    if hour < 6:
        greeting = "Доброй ночи"
    elif hour < 12:
        greeting = "Доброе утро"
    elif hour < 18:
        greeting = "Добрый день"
    else:
        greeting = "Добрый вечер"

    stats = {
        "week_events": len([e for e in week_events if e.status != "cancelled"]),
        "today": len(today_events),
        "upcoming": len(upcoming),
        "free_slots": len(free),
        "conflicts": len(conflicts),
    }
    return render(
        request,
        "dashboard.html",
        current_user=user,
        active="dashboard",
        stats=stats,
        upcoming=upcoming,
        today_events=today_events,
        conflicts=conflicts[:5],
        week_load=week_load,
        max_load=max_load,
        week_start=week_start,
        greeting=greeting,
    )
