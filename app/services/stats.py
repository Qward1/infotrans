"""Агрегированная статистика для админ-панели.

Всё считается из данных календаря (SQLAlchemy-агрегации) и журнала аудита.
Никаких внешних BI-инструментов — графики рисуются простыми HTML/CSS-полосами.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.calendar import (
    CalendarEvent,
    LOC_HYBRID,
    LOC_OFFLINE,
    LOC_ONLINE,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PLANNED,
)
from app.models.user import User
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service

# Действия аудита, которые считаем «переносами» встреч.
_RESCHEDULE_ACTIONS = ("reschedule_event", "move_event")

_WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _duration_minutes_expr():
    """Длительность встречи в минутах (SQLite julianday-разница)."""
    return (func.julianday(CalendarEvent.end_at) - func.julianday(CalendarEvent.start_at)) * 24 * 60


def system_overview(db: Session) -> dict:
    """Ключевые счётчики по системе (включая среднюю длительность и переносы)."""
    now = datetime.now()
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
    active_users = db.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    ).scalar_one()
    total_events = db.execute(select(func.count()).select_from(CalendarEvent)).scalar_one()
    upcoming = db.execute(
        select(func.count())
        .select_from(CalendarEvent)
        .where(CalendarEvent.start_at >= now, CalendarEvent.status != STATUS_CANCELLED)
    ).scalar_one()
    avg_duration = db.execute(
        select(func.avg(_duration_minutes_expr())).where(
            CalendarEvent.status != STATUS_CANCELLED
        )
    ).scalar_one()
    reschedules = db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action.in_(_RESCHEDULE_ACTIONS))
    ).scalar_one()
    return {
        "total_users": int(total_users),
        "active_users": int(active_users),
        "total_events": int(total_events),
        "upcoming_events": int(upcoming),
        "avg_duration_minutes": int(round(avg_duration)) if avg_duration else 0,
        "conflicts": conflicts_count(db),
        "reschedules": int(reschedules),
    }


def conflicts_count(db: Session) -> int:
    """Число пересекающихся пар встреч по всем пользователям (за ±30 дней).

    BUG-18: один запрос всех не-отменённых событий окна и группировка по
    владельцу в Python — вместо отдельного запроса на каждого пользователя.
    Статистика намеренно считается по владельцам (см. BUG-32): конфликт пары
    встреч учитывается один раз, а не для каждого приглашённого.
    """
    now = datetime.now()
    start, end = now - timedelta(days=30), now + timedelta(days=30)
    stmt = select(CalendarEvent).where(
        CalendarEvent.status != STATUS_CANCELLED,
        CalendarEvent.start_at < end,
        CalendarEvent.end_at > start,
    )
    by_owner: dict[int, list[CalendarEvent]] = {}
    for event in db.execute(stmt).scalars():
        by_owner.setdefault(event.owner_id, []).append(event)
    total = 0
    for events in by_owner.values():
        busy = [
            scheduling_service.BusyInterval(
                start=e.start_at, end=e.end_at, priority=e.priority,
                title=e.title, event_id=e.id,
            )
            for e in events
        ]
        total += len(scheduling_service.detect_conflicts(busy))
    return total


def weekly_load(db: Session, reference: datetime | None = None) -> dict:
    """Нагрузка по встречам за текущую неделю: часы и количество на сотрудника."""
    reference = reference or datetime.now()
    week_start, week_end = calendar_service.week_bounds(reference)
    minutes = _duration_minutes_expr()
    stmt = (
        select(
            User.id,
            User.full_name,
            User.email,
            func.coalesce(func.sum(minutes), 0.0),
            func.count(CalendarEvent.id),
        )
        .select_from(User)
        .outerjoin(
            CalendarEvent,
            (CalendarEvent.owner_id == User.id)
            & (CalendarEvent.start_at < week_end)
            & (CalendarEvent.end_at > week_start)
            & (CalendarEvent.status != STATUS_CANCELLED),
        )
        .group_by(User.id)
        .order_by(func.coalesce(func.sum(minutes), 0.0).desc(), User.id.asc())
    )
    rows = []
    for uid, name, email, mins, count in db.execute(stmt):
        rows.append(
            {
                "user_id": uid,
                "full_name": name or email,
                "email": email,
                "minutes": int(round(mins or 0)),
                "hours": round((mins or 0) / 60, 1),
                "meetings": int(count or 0),
            }
        )
    return {
        "week_start": week_start,
        "week_end": week_end - timedelta(days=1),
        "rows": rows,
    }


def format_breakdown(db: Session) -> list[dict]:
    """Распределение встреч по формату online/offline/hybrid."""
    labels = {LOC_ONLINE: "Онлайн", LOC_OFFLINE: "Очно", LOC_HYBRID: "Гибрид"}
    rows = db.execute(
        select(CalendarEvent.location_type, func.count())
        .where(CalendarEvent.status != STATUS_CANCELLED)
        .group_by(CalendarEvent.location_type)
    ).all()
    counts = {k: 0 for k in labels}
    for loc, count in rows:
        counts[loc] = counts.get(loc, 0) + int(count)
    return [
        {"key": key, "label": labels[key], "count": counts.get(key, 0)}
        for key in (LOC_ONLINE, LOC_OFFLINE, LOC_HYBRID)
    ]


def priority_breakdown(db: Session) -> list[dict]:
    """Встречи по приоритетам (сгруппированы в 4 корзины 0–10)."""
    buckets = [
        ("Низкий", "0–3", 0, 3),
        ("Средний", "4–6", 4, 6),
        ("Высокий", "7–8", 7, 8),
        ("Критический", "9–10", 9, 10),
    ]
    result = []
    for label, rng, lo, hi in buckets:
        count = db.execute(
            select(func.count())
            .select_from(CalendarEvent)
            .where(
                CalendarEvent.status != STATUS_CANCELLED,
                CalendarEvent.priority >= lo,
                CalendarEvent.priority <= hi,
            )
        ).scalar_one()
        result.append({"label": label, "range": rng, "count": int(count)})
    return result


def busiest_days(db: Session) -> list[dict]:
    """Загрузка по дням недели (кол-во встреч) — для «топ загруженных дней»."""
    counts = [0] * 7
    rows = db.execute(
        select(CalendarEvent.start_at).where(CalendarEvent.status != STATUS_CANCELLED)
    ).all()
    for (start_at,) in rows:
        counts[start_at.weekday()] += 1
    return [
        {"weekday": i, "label": _WEEKDAY_NAMES[i], "count": counts[i]} for i in range(7)
    ]


def status_breakdown(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(CalendarEvent.status, func.count()).group_by(CalendarEvent.status)
    ).all()
    counts = {s: 0 for s in (STATUS_PLANNED, STATUS_COMPLETED, STATUS_CANCELLED)}
    for status, count in rows:
        counts[status] = int(count)
    return counts


def per_user_stats(db: Session) -> list[dict]:
    """По каждому сотруднику: всего/запланировано/проведено/отменено событий."""
    total_col = func.count(CalendarEvent.id)
    planned_col = func.sum(case((CalendarEvent.status == STATUS_PLANNED, 1), else_=0))
    completed_col = func.sum(case((CalendarEvent.status == STATUS_COMPLETED, 1), else_=0))
    cancelled_col = func.sum(case((CalendarEvent.status == STATUS_CANCELLED, 1), else_=0))

    stmt = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.role,
            total_col,
            planned_col,
            completed_col,
            cancelled_col,
        )
        .select_from(User)
        .outerjoin(CalendarEvent, CalendarEvent.owner_id == User.id)
        .group_by(User.id)
        .order_by(total_col.desc(), User.id.asc())
    )
    result = []
    for uid, name, email, role, total, planned, completed, cancelled in db.execute(stmt):
        result.append(
            {
                "user_id": uid,
                "full_name": name or email,
                "email": email,
                "role": role,
                "total": int(total or 0),
                "planned": int(planned or 0),
                "completed": int(completed or 0),
                "cancelled": int(cancelled or 0),
            }
        )
    return result
