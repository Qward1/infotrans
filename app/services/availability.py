"""Поиск свободных слотов для одного или нескольких участников.

Строится поверх примитивов ``scheduling`` (BusyInterval / find_free_slots), но
дополнительно учитывает: рабочие часы из YAML, объединённую занятость всех
участников, буферы на дорогу между офлайн-встречами и предупреждения.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.calendar import STATUS_CANCELLED, CalendarEvent
from app.models.meeting import EventParticipant
from app.services import location_service
from app.services.scheduling import BusyInterval
from app.services.scheduling import find_free_slots as _scheduling_free_slots


@dataclass
class SlotSuggestion:
    start: datetime
    end: datetime
    reason: str
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def to_dict(self) -> dict:
        return {
            "start_at": self.start.isoformat(),
            "end_at": self.end.isoformat(),
            "duration_minutes": self.duration_minutes,
            "reason": self.reason,
            "warnings": self.warnings,
        }


def parse_working_hours(settings: Settings) -> tuple[time, time]:
    def _p(value: str, default: time) -> time:
        try:
            hh, mm = value.split(":")
            return time(int(hh), int(mm))
        except (ValueError, AttributeError):
            return default

    wh = settings.scheduling.working_hours
    return _p(wh.start, time(9, 0)), _p(wh.end, time(19, 0))


def participant_events(
    db: Session,
    user_ids: list[int],
    range_start: datetime,
    range_end: datetime,
) -> list[CalendarEvent]:
    """События, в которых заняты указанные пользователи (как владельцы или участники)."""
    if not user_ids:
        return []
    participant_event_ids = select(EventParticipant.event_id).where(
        EventParticipant.user_id.in_(user_ids)
    )
    stmt = (
        select(CalendarEvent)
        .where(
            CalendarEvent.status != STATUS_CANCELLED,
            CalendarEvent.start_at < range_end,
            CalendarEvent.end_at > range_start,
            or_(
                CalendarEvent.owner_id.in_(user_ids),
                CalendarEvent.id.in_(participant_event_ids),
            ),
        )
        .order_by(CalendarEvent.start_at.asc())
    )
    # Дедуп по id (событие может попасть и как владелец, и как участник).
    seen: dict[int, CalendarEvent] = {}
    for ev in db.execute(stmt).scalars().all():
        seen[ev.id] = ev
    return list(seen.values())


def _place_of(event: CalendarEvent) -> location_service.Place:
    return location_service.Place(
        format=event.location_type, city=event.city, address=event.address
    )


def _travel_warnings(
    events: list[CalendarEvent],
    slot_start: datetime,
    slot_end: datetime,
    meeting_place: location_service.Place,
    settings: Settings,
) -> list[str]:
    """Предупреждения о нехватке времени на дорогу до/после соседних встреч."""
    warnings: list[str] = []
    if not meeting_place.is_physical:
        return warnings

    # Ближайшее событие ДО слота и ПОСЛЕ слота.
    before = [e for e in events if e.end_at <= slot_start]
    after = [e for e in events if e.start_at >= slot_end]
    if before:
        prev = max(before, key=lambda e: e.end_at)
        need = location_service.travel_buffer_minutes(_place_of(prev), meeting_place, settings)
        gap = int((slot_start - prev.end_at).total_seconds() // 60)
        if need > gap:
            warnings.append(
                f"До встречи «{prev.title}» нужно ~{location_service.describe_buffer(need)}, "
                f"а свободно только {gap} мин"
            )
    if after:
        nxt = min(after, key=lambda e: e.start_at)
        need = location_service.travel_buffer_minutes(meeting_place, _place_of(nxt), settings)
        gap = int((nxt.start_at - slot_end).total_seconds() // 60)
        if need > gap:
            warnings.append(
                f"После встречи нужно ~{location_service.describe_buffer(need)} до «{nxt.title}», "
                f"а свободно только {gap} мин"
            )
    return warnings


def _ceil_to_minutes(value: datetime, minutes: int = 5) -> datetime:
    """Округлить время вверх до ближайшего шага в ``minutes`` минут."""
    discard = timedelta(
        minutes=value.minute % minutes, seconds=value.second, microseconds=value.microsecond
    )
    if discard:
        value = value - discard + timedelta(minutes=minutes)
    return value


def find_free_slots(
    db: Session,
    settings: Settings,
    participant_ids: list[int],
    range_start: datetime,
    range_end: datetime,
    duration_minutes: int | None = None,
    city: str = "",
    address: str = "",
    meeting_format: str = "offline",
    limit: int | None = None,
    not_before: datetime | None = None,
) -> list[SlotSuggestion]:
    """Общие свободные окна всех участников с объяснением и предупреждениями.

    ``not_before`` отсекает прошлое (BUG-08): слоты не начинаются раньше этого
    времени (округляется вверх до 5 минут).
    """
    duration = duration_minutes or settings.scheduling.default_meeting_minutes
    work_start, work_end = parse_working_hours(settings)
    limit = limit or settings.scheduling.max_alternatives

    if not_before is not None:
        floor = _ceil_to_minutes(not_before)
        if floor > range_start:
            range_start = floor
        if range_start >= range_end:
            return []

    events = participant_events(db, participant_ids, range_start, range_end)
    busy = [
        BusyInterval(start=e.start_at, end=e.end_at, priority=e.priority,
                     title=e.title, event_id=e.id)
        for e in events
    ]
    windows = _scheduling_free_slots(busy, range_start, range_end, duration, work_start, work_end)

    # FN-02: внутри каждого окна варианты с шагом slot_granularity_minutes,
    # равномерно по дням (не все limit-варианты в один день), сортировка по времени.
    granularity = max(5, settings.scheduling.slot_granularity_minutes)
    candidates_by_day: dict = {}
    for w in windows:
        cursor = w.start
        while cursor + timedelta(minutes=duration) <= w.end:
            candidates_by_day.setdefault(cursor.date(), []).append(cursor)
            cursor += timedelta(minutes=granularity)

    picked: list[datetime] = []
    day_queues = [list(v) for _, v in sorted(candidates_by_day.items())]
    while len(picked) < limit and any(day_queues):
        for queue in day_queues:
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    picked.sort()

    meeting_place = location_service.Place(format=meeting_format, city=city, address=address)
    n = len(participant_ids)
    who = "у вас" if n <= 1 else f"у всех {n} участников"

    suggestions: list[SlotSuggestion] = []
    for slot_start in picked:
        slot_end = slot_start + timedelta(minutes=duration)
        reason = (
            f"Свободно {who} · {slot_start:%a %d.%m %H:%M}–{slot_end:%H:%M} · "
            f"в рабочих часах {work_start:%H:%M}–{work_end:%H:%M}"
        )
        warnings = _travel_warnings(events, slot_start, slot_end, meeting_place, settings)
        suggestions.append(SlotSuggestion(slot_start, slot_end, reason, warnings))
    return suggestions
