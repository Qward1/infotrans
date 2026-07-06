"""Разрешение конфликтов расписания по приоритетам и локации.

Контракт (см. ТЗ):
    вход:  proposed event, participants, existing events
    выход: can_schedule, conflicts, recommended_action, alternative_slots, explanation

Правила:
* нет пересечений → schedule_as_is;
* новая встреча приоритетнее всех конфликтов → propose_reschedule_lower_priority
  (перенос только с подтверждением, т.к. затрагивает других);
* приоритеты равны → ask_user_confirmation (авто-перенос запрещён);
* существующая встреча приоритетнее или «высокоприоритетная» (≥ порога YAML) →
  suggest_alternatives (её нельзя двигать автоматически);
* дополнительно: буферы на дорогу между офлайн-встречами.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.calendar import CalendarEvent
from app.services import availability, location_service

# Значения recommended_action.
ACTION_SCHEDULE_AS_IS = "schedule_as_is"
ACTION_SUGGEST_ALTERNATIVES = "suggest_alternatives"
ACTION_PROPOSE_RESCHEDULE_LOWER = "propose_reschedule_lower_priority"
ACTION_ASK_CONFIRMATION = "ask_user_confirmation"


@dataclass
class ProposedEvent:
    start: datetime
    end: datetime
    priority: int = 5
    format: str = "offline"
    city: str = ""
    address: str = ""
    title: str = "Новая встреча"
    exclude_event_id: int | None = None  # при переносе/редактировании — исключить само событие

    @property
    def place(self) -> location_service.Place:
        return location_service.Place(format=self.format, city=self.city, address=self.address)


@dataclass
class ConflictInfo:
    event_id: int
    title: str
    start: datetime
    end: datetime
    priority: int
    owner_id: int
    overlap_minutes: int
    is_high_priority: bool

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "start_at": self.start.isoformat(),
            "end_at": self.end.isoformat(),
            "priority": self.priority,
            "owner_id": self.owner_id,
            "overlap_minutes": self.overlap_minutes,
            "is_high_priority": self.is_high_priority,
        }


@dataclass
class ResolveResult:
    can_schedule: bool
    recommended_action: str
    explanation: str
    conflicts: list[ConflictInfo] = field(default_factory=list)
    alternative_slots: list[availability.SlotSuggestion] = field(default_factory=list)
    buffer_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "can_schedule": self.can_schedule,
            "recommended_action": self.recommended_action,
            "explanation": self.explanation,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "alternative_slots": [s.to_dict() for s in self.alternative_slots],
            "buffer_warnings": self.buffer_warnings,
        }


def _overlap_minutes(a_start, a_end, b_start, b_end) -> int:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).total_seconds() / 60
    return int(delta) if delta > 0 else 0


def _buffer_warnings(
    proposed: ProposedEvent, neighbours: list[CalendarEvent], settings: Settings
) -> list[str]:
    """Проверить достаточность времени на дорогу до/после соседних офлайн-встреч."""
    warnings: list[str] = []
    if not proposed.place.is_physical:
        return warnings
    before = [e for e in neighbours if e.end_at <= proposed.start]
    after = [e for e in neighbours if e.start_at >= proposed.end]
    if before:
        prev = max(before, key=lambda e: e.end_at)
        place = location_service.Place(prev.location_type, prev.city, prev.address)
        need = location_service.travel_buffer_minutes(place, proposed.place, settings)
        gap = int((proposed.start - prev.end_at).total_seconds() // 60)
        if need > gap:
            warnings.append(
                f"Между «{prev.title}» и новой встречей нужно ~"
                f"{location_service.describe_buffer(need)}, а есть {gap} мин."
            )
    if after:
        nxt = min(after, key=lambda e: e.start_at)
        place = location_service.Place(nxt.location_type, nxt.city, nxt.address)
        need = location_service.travel_buffer_minutes(proposed.place, place, settings)
        gap = int((nxt.start_at - proposed.end).total_seconds() // 60)
        if need > gap:
            warnings.append(
                f"Между новой встречей и «{nxt.title}» нужно ~"
                f"{location_service.describe_buffer(need)}, а есть {gap} мин."
            )
    return warnings


def resolve_conflicts(
    db: Session,
    settings: Settings,
    proposed: ProposedEvent,
    participant_ids: list[int],
    existing_events: list[CalendarEvent] | None = None,
) -> ResolveResult:
    """Главная точка входа конфликт-резолвинга."""
    threshold = settings.scheduling.high_priority_threshold
    duration = int((proposed.end - proposed.start).total_seconds() // 60)

    # Собираем события участников в окне вокруг предложенного времени.
    if existing_events is None:
        window_start = proposed.start - timedelta(hours=12)
        window_end = proposed.end + timedelta(hours=12)
        neighbours = availability.participant_events(db, participant_ids, window_start, window_end)
    else:
        neighbours = list(existing_events)
    neighbours = [e for e in neighbours if e.id != proposed.exclude_event_id]

    # Жёсткие пересечения по времени.
    conflicts: list[ConflictInfo] = []
    for e in neighbours:
        ov = _overlap_minutes(proposed.start, proposed.end, e.start_at, e.end_at)
        if ov > 0:
            conflicts.append(
                ConflictInfo(
                    event_id=e.id,
                    title=e.title,
                    start=e.start_at,
                    end=e.end_at,
                    priority=e.priority,
                    owner_id=e.owner_id,
                    overlap_minutes=ov,
                    is_high_priority=e.priority >= threshold,
                )
            )

    buffer_warnings = _buffer_warnings(proposed, neighbours, settings)

    # Нет конфликтов по времени → планируем как есть.
    if not conflicts:
        explanation = "Пересечений по времени нет — можно ставить встречу."
        if buffer_warnings:
            explanation += " Обратите внимание на время на дорогу."
        return ResolveResult(
            can_schedule=True,
            recommended_action=ACTION_SCHEDULE_AS_IS,
            explanation=explanation,
            conflicts=[],
            alternative_slots=[],
            buffer_warnings=buffer_warnings,
        )

    max_conf_priority = max(c.priority for c in conflicts)
    any_high = any(c.is_high_priority for c in conflicts)

    if max_conf_priority > proposed.priority or any_high:
        recommended = ACTION_SUGGEST_ALTERNATIVES
        if any_high:
            explanation = (
                "Конфликтует встреча с высоким приоритетом — её нельзя двигать автоматически. "
                "Предлагаю выбрать другое время."
            )
        else:
            explanation = (
                "Конфликтующая встреча важнее новой — предлагаю альтернативные слоты."
            )
    elif max_conf_priority == proposed.priority:
        recommended = ACTION_ASK_CONFIRMATION
        explanation = (
            "Приоритеты встреч равны — автоматически ничего не переношу. "
            "Выберите: оставить конфликт, перенести существующую или взять другой слот."
        )
    else:
        recommended = ACTION_PROPOSE_RESCHEDULE_LOWER
        explanation = (
            "Новая встреча приоритетнее конфликтующих. Могу предложить план переноса "
            "менее приоритетной встречи — но только после вашего подтверждения."
        )

    # Ближайшие альтернативные слоты (для всех участников).
    search_start = proposed.start.replace(hour=0, minute=0, second=0, microsecond=0)
    search_end = search_start + timedelta(days=14)
    alternatives = availability.find_free_slots(
        db,
        settings,
        participant_ids,
        search_start,
        search_end,
        duration_minutes=duration,
        city=proposed.city,
        address=proposed.address,
        meeting_format=proposed.format,
    )

    return ResolveResult(
        can_schedule=False,
        recommended_action=recommended,
        explanation=explanation,
        conflicts=conflicts,
        alternative_slots=alternatives,
        buffer_warnings=buffer_warnings,
    )
