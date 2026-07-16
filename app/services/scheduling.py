"""Планирование: поиск свободных слотов и разрешение конфликтов по приоритетам.

Логика намеренно вынесена в чистые функции над списком «занятых» интервалов —
это упрощает юнит-тесты и позволит на следующем этапе переиспользовать её из
ассистента (нормализация запроса через LLM → вызов ``find_free_slots``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.calendar import STATUS_CANCELLED, CalendarEvent
from app.services import calendar as calendar_service


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime
    priority: int = 5
    title: str = ""
    event_id: int | None = None


@dataclass(frozen=True)
class FreeSlot:
    start: datetime
    end: datetime

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class Conflict:
    first: BusyInterval
    second: BusyInterval

    @property
    def keep(self) -> BusyInterval:
        """Событие, которое стоит сохранить (более высокий приоритет)."""
        return self.first if self.first.priority >= self.second.priority else self.second

    @property
    def drop(self) -> BusyInterval:
        """Событие-кандидат на перенос/отмену."""
        return self.second if self.keep is self.first else self.first


def _merge(intervals: list[BusyInterval]) -> list[tuple[datetime, datetime]]:
    """Слить пересекающиеся занятые интервалы в непрерывные отрезки."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda i: i.start)
    merged: list[tuple[datetime, datetime]] = [(ordered[0].start, ordered[0].end)]
    for iv in ordered[1:]:
        last_start, last_end = merged[-1]
        if iv.start <= last_end:  # пересечение/касание
            merged[-1] = (last_start, max(last_end, iv.end))
        else:
            merged.append((iv.start, iv.end))
    return merged


def find_free_slots(
    busy: list[BusyInterval],
    range_start: datetime,
    range_end: datetime,
    duration_minutes: int = 60,
    work_start: time = time(9, 0),
    work_end: time = time(19, 0),
) -> list[FreeSlot]:
    """Найти свободные окна нужной длительности внутри рабочих часов.

    Перебираем календарные дни в диапазоне, для каждого строим рабочий интервал
    [work_start, work_end], вычитаем занятые отрезки и возвращаем оставшиеся окна
    не короче ``duration_minutes``.
    """
    duration = timedelta(minutes=max(1, duration_minutes))
    merged = _merge(busy)
    slots: list[FreeSlot] = []

    day = range_start.date()
    last_day = (range_end - timedelta(seconds=1)).date()
    while day <= last_day:
        day_start = max(datetime.combine(day, work_start), range_start)
        day_end = min(datetime.combine(day, work_end), range_end)
        cursor = day_start
        for b_start, b_end in merged:
            if b_end <= cursor or b_start >= day_end:
                continue
            if b_start > cursor and (b_start - cursor) >= duration:
                slots.append(FreeSlot(cursor, b_start))
            cursor = max(cursor, b_end)
        if cursor < day_end and (day_end - cursor) >= duration:
            slots.append(FreeSlot(cursor, day_end))
        day += timedelta(days=1)
    return slots


def detect_conflicts(intervals: list[BusyInterval]) -> list[Conflict]:
    """Найти попарно пересекающиеся события (кандидаты на конфликт-резолвинг)."""
    ordered = sorted(intervals, key=lambda i: i.start)
    conflicts: list[Conflict] = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a, b = ordered[i], ordered[j]
            if b.start >= a.end:
                break  # дальше все начинаются позже конца a
            if a.start < b.end and b.start < a.end:
                conflicts.append(Conflict(a, b))
    return conflicts


# --------------------------------------------------------------------------- #
# Интеграция с БД                                                              #
# --------------------------------------------------------------------------- #
def _events_to_busy(events: list[CalendarEvent]) -> list[BusyInterval]:
    return [
        BusyInterval(
            start=e.start_at,
            end=e.end_at,
            priority=e.priority,
            title=e.title,
            event_id=e.id,
        )
        for e in events
        if e.status != STATUS_CANCELLED
    ]


def free_slots_for_user(
    db: Session,
    user_id: int,
    range_start: datetime,
    range_end: datetime,
    duration_minutes: int = 60,
) -> list[FreeSlot]:
    """Свободные окна пользователя с учётом встреч, где он участник (BUG-02)."""
    events = calendar_service.list_events_for_user(
        db, user_id, range_start, range_end, include_cancelled=False
    )
    return find_free_slots(
        _events_to_busy(events), range_start, range_end, duration_minutes
    )


def conflicts_for_user(
    db: Session, user_id: int, range_start: datetime, range_end: datetime
) -> list[Conflict]:
    """Конфликты пользователя с учётом встреч, где он участник (BUG-02)."""
    events = calendar_service.list_events_for_user(
        db, user_id, range_start, range_end, include_cancelled=False
    )
    return detect_conflicts(_events_to_busy(events))
