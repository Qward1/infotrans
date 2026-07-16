"""Тесты этапа 2: статусы действий в истории (BUG-09), reject в конфликтной
ветке (FN-04), префилл слотов (UX-06), рабочие часы в payload (UX-01),
справочник сотрудников (UX-05), точечное чтение уведомлений (FN-07)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.calendar import CalendarEvent
from app.models.user import ROLE_USER, User
from app.routers.calendar import calendar_payload
from app.services.assistant import calendar_context, chat_history, notification_service, orchestrator

SETTINGS = Settings()
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


@pytest.fixture()
def user(db):
    u = User(email="u2@test.local", full_name="ユзер", password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _guest(db, email="g@test.local", name="Гость"):
    u = User(email=email, full_name=name, password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# --------------------------------------------------------------------------- #
# BUG-09: история чата отдаёт актуальные статусы действий                      #
# --------------------------------------------------------------------------- #
def test_chat_history_includes_action_states(db, user):
    _guest(db)
    chat = chat_history.create_chat(db, user.id, "Тест")
    res = orchestrator.run(SETTINGS, db, user, "встреча с g@test.local 11.07 в 14:00 онлайн",
                           conversation_id=chat.id, now=NOW)
    assert res.status == "needs_confirmation"
    chat_history.add_message(db, chat, "assistant", res.reply, res.model_dump(mode="json"))
    action_id = next(a.action_id for a in res.suggested_actions if a.type == "confirm")

    # До подтверждения — pending.
    data = chat_history.serialize_chat(chat_history.get_chat(db, chat.id), include_messages=True)
    states = data["messages"][-1]["payload"]["actions_state"]
    assert states[action_id] == "pending"

    # После подтверждения история отражает confirmed.
    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is True
    data = chat_history.serialize_chat(chat_history.get_chat(db, chat.id), include_messages=True)
    states = data["messages"][-1]["payload"]["actions_state"]
    assert states[action_id] == "confirmed"


# --------------------------------------------------------------------------- #
# FN-04: каждый черновик в конфликтной ветке имеет пару confirm/reject         #
# --------------------------------------------------------------------------- #
def test_conflict_branch_has_reject_for_each_draft(db, user):
    # Существующая встреча того же приоритета → ask_confirmation с force-черновиком.
    db.add(CalendarEvent(owner_id=user.id, title="Существующая",
                         start_at=datetime(2026, 7, 7, 15, 0), end_at=datetime(2026, 7, 7, 16, 0),
                         location_type="online", priority=5))
    db.commit()
    res = orchestrator.run(SETTINGS, db, user, "встреча по проекту 07.07 в 15:00 онлайн", now=NOW)
    assert res.status == "conflict"
    confirm_ids = {a.action_id for a in res.suggested_actions if a.type == "confirm" and a.action_id}
    reject_ids = {a.action_id for a in res.suggested_actions if a.type == "reject" and a.action_id}
    assert confirm_ids, "в конфликтной ветке должен быть force-черновик"
    assert confirm_ids <= reject_ids, "у каждого confirm должна быть парная reject-кнопка"


# --------------------------------------------------------------------------- #
# UX-06: слоты едут в модалку с контекстом диалога                             #
# --------------------------------------------------------------------------- #
def test_find_slots_payload_carries_context(db, user):
    guest = _guest(db, "ctx@test.local", "Контекстный")
    res = orchestrator.run(
        SETTINGS, db, user,
        "Найди свободный слот завтра онлайн, пригласи ctx@test.local", now=NOW,
    )
    assert res.intent == "find_free_slots"
    buttons = [a for a in res.suggested_actions if a.type == "create_event"]
    assert buttons
    for b in buttons:
        assert b.payload.get("participants") == ["ctx@test.local"]
        assert b.payload.get("location_type") == "online"
    slot_card = next(c for c in res.cards if c.kind == "alternative_slots")
    assert slot_card.data["prefill"].get("participants") == ["ctx@test.local"]


# --------------------------------------------------------------------------- #
# UX-01: календарь отдаёт рабочие часы и текущее время                         #
# --------------------------------------------------------------------------- #
def test_calendar_payload_working_hours(db, user):
    payload = calendar_payload(db, user, "week", NOW)
    assert payload["working_hours"]["start"] == "09:00"
    assert payload["working_hours"]["end"] == "19:00"
    assert "now" in payload


# --------------------------------------------------------------------------- #
# UX-05: справочник сотрудников доступен обычному пользователю                 #
# --------------------------------------------------------------------------- #
def test_employee_search_open_to_regular_users(db, user):
    _guest(db, "maria-dir@test.local", "Мария Справочная")
    found = calendar_context.search_employees(db, SETTINGS, user, "Мария")
    assert any(u.email == "maria-dir@test.local" for u in found)
    # Но чужой календарь по-прежнему закрыт.
    with pytest.raises(calendar_context.CalendarAccessDenied):
        calendar_context.resolve_employee_query(db, SETTINGS, user, "Мария Справочная")


# --------------------------------------------------------------------------- #
# FN-07: точечное чтение уведомления                                           #
# --------------------------------------------------------------------------- #
def test_mark_single_notification_read(db, user):
    other = _guest(db, "other-n@test.local", "Другой")
    note = notification_service.notify(db, SETTINGS, user, text="Тест", title="Тест")
    foreign = notification_service.notify(db, SETTINGS, other, text="Чужое", title="Чужое")

    assert notification_service.mark_read(db, user.id, note.id) is True
    db.refresh(note)
    assert note.status == "read"
    # Чужое уведомление владельцу другого пользователя недоступно.
    assert notification_service.mark_read(db, user.id, foreign.id) is False
    db.refresh(foreign)
    assert foreign.status == "unread"
