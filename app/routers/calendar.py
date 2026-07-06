"""Страница календаря (недельный вид)."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_user
from app.models.user import User
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service
from app.templating import render

router = APIRouter(tags=["calendar"])


def _parse_ref(week: str | None) -> datetime:
    if week:
        try:
            return datetime.strptime(week, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


@router.get("/calendar")
def calendar_page(
    request: Request,
    week: str | None = Query(default=None, description="Любая дата недели, YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ref = _parse_ref(week)
    start, end, events = calendar_service.list_week(db, user.id, ref)

    # Помечаем события, которые пересекаются с другими (индикатор конфликта).
    conflict_ids: set[int] = set()
    for c in scheduling_service.conflicts_for_user(db, user.id, start, end):
        if c.first.event_id:
            conflict_ids.add(c.first.event_id)
        if c.second.event_id:
            conflict_ids.add(c.second.event_id)

    # Раскладываем события по дням недели для сетки.
    days = []
    for offset in range(7):
        day_start = start + timedelta(days=offset)
        day_end = day_start + timedelta(days=1)
        day_events = [e for e in events if e.start_at < day_end and e.end_at > day_start]
        days.append({"date": day_start, "events": day_events})

    prev_week = (start - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (start + timedelta(days=7)).strftime("%Y-%m-%d")

    return render(
        request,
        "calendar.html",
        current_user=user,
        active="calendar",
        week_start=start,
        week_end=end - timedelta(days=1),
        days=days,
        conflict_ids=conflict_ids,
        prev_week=prev_week,
        next_week=next_week,
        this_week=datetime.now().strftime("%Y-%m-%d"),
        now_date=datetime.now().date(),
        hours=list(range(8, 21)),
    )
