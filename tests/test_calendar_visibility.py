"""Тесты видимости встреч участника (BUG-01/BUG-02/FN-01):
календарь и планировщик учитывают встречи, где пользователь — участник."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.security import hash_password
from app.models.calendar import CalendarEvent
from app.models.meeting import EventParticipant
from app.models.user import ROLE_USER, User
from app.routers.calendar import calendar_payload
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service

NOW = datetime(2026, 7, 6, 8, 0)  # понедельник


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _user(db, email: str, name: str) -> User:
    u = User(email=email, full_name=name, password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _event(db, owner: User, title: str, start: datetime, minutes: int = 60,
           participants: list[User] | None = None) -> CalendarEvent:
    event = CalendarEvent(
        owner_id=owner.id, created_by_id=owner.id, updated_by_id=owner.id,
        title=title, start_at=start, end_at=start + timedelta(minutes=minutes),
        location_type="online", priority=5,
    )
    db.add(event)
    db.flush()
    for p in participants or []:
        db.add(EventParticipant(event_id=event.id, user_id=p.id, role="attendee"))
    db.commit()
    db.refresh(event)
    return event


def test_participant_sees_event_in_range(db):
    owner = _user(db, "owner@test.local", "Owner")
    guest = _user(db, "guest@test.local", "Guest")
    ev = _event(db, owner, "Планёрка", NOW.replace(hour=10), participants=[guest])
    _event(db, owner, "Личная встреча владельца", NOW.replace(hour=15))

    events = calendar_service.list_events_for_user(
        db, guest.id, NOW, NOW + timedelta(days=1)
    )
    assert [e.id for e in events] == [ev.id]


def test_owner_sees_both_owned_and_invited(db):
    owner = _user(db, "o2@test.local", "Owner")
    other = _user(db, "x2@test.local", "Other")
    own = _event(db, owner, "Своя", NOW.replace(hour=9))
    invited = _event(db, other, "Чужая с приглашением", NOW.replace(hour=12),
                     participants=[owner])
    events = calendar_service.list_events_for_user(db, owner.id, NOW, NOW + timedelta(days=1))
    assert {e.id for e in events} == {own.id, invited.id}


def test_calendar_payload_marks_participant_events(db):
    owner = _user(db, "o3@test.local", "Owner Three")
    guest = _user(db, "g3@test.local", "Guest Three")
    _event(db, owner, "Приглашение", NOW.replace(hour=11), participants=[guest])
    _event(db, guest, "Своя встреча", NOW.replace(hour=14))

    payload = calendar_payload(db, guest, "week", NOW)
    by_title = {e["title"]: e for e in payload["events"]}
    assert set(by_title) == {"Приглашение", "Своя встреча"}

    invited = by_title["Приглашение"]
    assert invited["is_participant"] is True
    assert invited["can_edit"] is False  # права не расширяются: гость не владелец
    assert invited["owner_name"] == "Owner Three"  # владелец события, а не календаря

    own = by_title["Своя встреча"]
    assert own["is_participant"] is False
    assert own["can_edit"] is True


def test_upcoming_events_includes_participation(db):
    owner = _user(db, "o4@test.local", "Owner")
    guest = _user(db, "g4@test.local", "Guest")
    start = datetime.now() + timedelta(days=1)
    ev = _event(db, owner, "Завтрашняя", start, participants=[guest])
    upcoming = calendar_service.upcoming_events(db, guest.id)
    assert [e.id for e in upcoming] == [ev.id]


# --------------------------------------------------------------------------- #
# BUG-02: слоты и конфликты учитывают участие                                  #
# --------------------------------------------------------------------------- #
def test_free_slots_exclude_participant_events(db):
    owner = _user(db, "o5@test.local", "Owner")
    guest = _user(db, "g5@test.local", "Guest")
    # Гость приглашён на встречу 10:00–11:00.
    _event(db, owner, "Занято участием", NOW.replace(hour=10), participants=[guest])

    slots = scheduling_service.free_slots_for_user(
        db, guest.id, NOW, NOW + timedelta(days=1), duration_minutes=60
    )
    for s in slots:
        assert not (s.start < NOW.replace(hour=11) and s.end > NOW.replace(hour=10)), (
            f"слот {s.start}–{s.end} пересекает встречу-участие"
        )


def test_conflicts_include_participant_events(db):
    owner = _user(db, "o6@test.local", "Owner")
    guest = _user(db, "g6@test.local", "Guest")
    # Своя встреча гостя и пересекающееся приглашение.
    _event(db, guest, "Своя", NOW.replace(hour=10))
    _event(db, owner, "Приглашение внахлёст", NOW.replace(hour=10, minute=30),
           participants=[guest])
    conflicts = scheduling_service.conflicts_for_user(
        db, guest.id, NOW, NOW + timedelta(days=1)
    )
    assert len(conflicts) == 1
