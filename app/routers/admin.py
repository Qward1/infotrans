"""Админ-страницы: пользователи и статистика."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.user import ROLES, User
from app.services import audit as audit_service
from app.services import stats as stats_service
from app.services import users as users_service
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    users = users_service.list_users(db)
    # Базовая статистика: число встреч по каждому сотруднику.
    event_counts = {row["user_id"]: row["total"] for row in stats_service.per_user_stats(db)}
    active_admins = users_service.count_active_admins(db)
    return render(
        request,
        "admin_users.html",
        current_user=user,
        active="admin_users",
        users=users,
        roles=ROLES,
        event_counts=event_counts,
        active_admins=active_admins,
    )


@router.get("/stats")
def admin_stats(
    request: Request,
    week: str | None = Query(default=None, description="Неделя статистики, YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # FN-09: навигация по неделям недельной статистики.
    reference = datetime.now()
    if week:
        try:
            reference = datetime.strptime(week, "%Y-%m-%d")
        except ValueError:
            pass
    overview = stats_service.system_overview(db)
    status_counts = stats_service.status_breakdown(db)
    per_user = stats_service.per_user_stats(db)
    weekly = stats_service.weekly_load(db, reference)
    formats = stats_service.format_breakdown(db)
    priorities = stats_service.priority_breakdown(db)
    days = stats_service.busiest_days(db)
    audit = audit_service.list_recent(db, limit=50)
    # Подмешиваем имена акторов для читаемого журнала.
    user_map = {u.id: (u.full_name or u.email) for u in users_service.list_users(db)}
    return render(
        request,
        "admin_stats.html",
        current_user=user,
        active="admin_stats",
        overview=overview,
        status_counts=status_counts,
        per_user=per_user,
        weekly=weekly,
        week_prev=(weekly["week_start"] - timedelta(days=7)).strftime("%Y-%m-%d"),
        week_next=(weekly["week_start"] + timedelta(days=7)).strftime("%Y-%m-%d"),
        formats=formats,
        priorities=priorities,
        busiest_days=days,
        audit=audit,
        user_map=user_map,
    )
