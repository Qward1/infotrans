"""Тесты сервиса планирования: свободные слоты и конфликты."""
from __future__ import annotations

from datetime import datetime, time

from app.services.scheduling import (
    BusyInterval,
    detect_conflicts,
    find_free_slots,
)


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute)


def test_free_slots_empty_day_returns_full_working_window():
    slots = find_free_slots(
        busy=[],
        range_start=dt(6, 0),
        range_end=dt(7, 0),  # один день
        duration_minutes=60,
        work_start=time(9, 0),
        work_end=time(18, 0),
    )
    assert len(slots) == 1
    assert slots[0].start == dt(6, 9)
    assert slots[0].end == dt(6, 18)


def test_free_slots_around_busy_interval():
    busy = [BusyInterval(dt(6, 12), dt(6, 13), priority=5, title="Обед")]
    slots = find_free_slots(
        busy=busy,
        range_start=dt(6, 0),
        range_end=dt(7, 0),
        duration_minutes=60,
        work_start=time(9, 0),
        work_end=time(18, 0),
    )
    # Ожидаем два окна: 09-12 и 13-18.
    assert (slots[0].start, slots[0].end) == (dt(6, 9), dt(6, 12))
    assert (slots[1].start, slots[1].end) == (dt(6, 13), dt(6, 18))


def test_free_slots_respects_duration():
    # Занято 09-17, остаётся только 17-18 (60 минут).
    busy = [BusyInterval(dt(6, 9), dt(6, 17))]
    slots = find_free_slots(
        busy, dt(6, 0), dt(7, 0), duration_minutes=90,
        work_start=time(9, 0), work_end=time(18, 0),
    )
    # 60-минутного окна не хватает под 90 минут.
    assert slots == []


def test_overlapping_busy_intervals_are_merged():
    busy = [
        BusyInterval(dt(6, 10), dt(6, 12)),
        BusyInterval(dt(6, 11), dt(6, 13)),  # пересекается с предыдущим
    ]
    slots = find_free_slots(
        busy, dt(6, 0), dt(7, 0), duration_minutes=30,
        work_start=time(9, 0), work_end=time(18, 0),
    )
    # Свободно 09-10 и 13-18.
    assert (slots[0].start, slots[0].end) == (dt(6, 9), dt(6, 10))
    assert (slots[1].start, slots[1].end) == (dt(6, 13), dt(6, 18))


def test_detect_conflicts_and_priority_resolution():
    a = BusyInterval(dt(6, 10), dt(6, 12), priority=8, title="Важная")
    b = BusyInterval(dt(6, 11), dt(6, 13), priority=3, title="Обычная")
    c = BusyInterval(dt(6, 14), dt(6, 15), priority=5, title="Отдельная")
    conflicts = detect_conflicts([a, b, c])
    assert len(conflicts) == 1
    conflict = conflicts[0]
    # Сохраняем более приоритетное событие, переносим менее приоритетное.
    assert conflict.keep.title == "Важная"
    assert conflict.drop.title == "Обычная"


def test_no_conflicts_when_disjoint():
    a = BusyInterval(dt(6, 10), dt(6, 11))
    b = BusyInterval(dt(6, 11), dt(6, 12))  # касание, не пересечение
    assert detect_conflicts([a, b]) == []
