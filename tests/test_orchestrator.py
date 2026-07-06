"""Интеграционные тесты оркестратора: авто-создание, подтверждение действий,
уведомления, протокол по документу."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.assistant import ACTION_CONFIRMED, AssistantAction, Document
from app.models.calendar import CalendarEvent
from app.models.user import ROLE_USER, User
from app.services.assistant import notification_service, orchestrator

SETTINGS = Settings()
NOW = datetime(2026, 7, 6, 8, 0)


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
    u = User(email="owner@test.local", full_name="Owner", password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_solo_event_auto_created(db, user):
    res = orchestrator.run(SETTINGS, db, user, "встреча 10.07 в 15:00 онлайн по релизу", now=NOW)
    assert res.intent == "create_event"
    assert res.status == "done"
    assert res.created_event is not None
    assert db.query(CalendarEvent).count() == 1


def test_event_with_participant_needs_confirmation_then_created(db, user):
    guest = User(email="guest@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    res = orchestrator.run(SETTINGS, db, user, "встреча с guest@test.local 11.07 в 14:00 онлайн", now=NOW)
    assert res.status == "needs_confirmation"
    assert db.query(CalendarEvent).count() == 0  # ещё не создано
    action_id = next(a.action_id for a in res.suggested_actions if a.type == "confirm")

    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is True
    assert db.query(CalendarEvent).count() == 1
    action = db.query(AssistantAction).filter_by(action_id=action_id).first()
    assert action.status == ACTION_CONFIRMED
    # приглашённому ушло уведомление
    assert notification_service.unread_count(db, guest.id) >= 1


def test_reject_action(db, user):
    guest = User(email="g2@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    res = orchestrator.run(SETTINGS, db, user, "встреча с g2@test.local 12.07 в 10:00 онлайн", now=NOW)
    action_id = next(a.action_id for a in res.suggested_actions if a.type == "confirm")
    out = orchestrator.reject_action(db, user, action_id)
    assert out["ok"] is True
    assert db.query(CalendarEvent).count() == 0
    # повторное подтверждение отклонённого — ошибка
    out2 = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out2["ok"] is False


def test_protocol_from_document_creates_followup_action(db, user):
    text = (
        "Решили: утвердить план.\n"
        "Задача: подготовить отчёт, ответственный Иван, срок до 20.07.\n"
        "Следующая встреча: обсудить отчёт на следующей неделе.\n"
    )
    doc = Document(owner_id=user.id, filename="m.txt", content_type="text/plain",
                   size_bytes=len(text), text=text)
    db.add(doc); db.commit(); db.refresh(doc)

    res = orchestrator.build_protocol_from_document(SETTINGS, db, user, doc)
    assert res.intent == "generate_meeting_protocol"
    assert res.protocol is not None
    assert res.protocol["action_items"]
    # предложено создать follow-up встречи
    confirm = [a for a in res.suggested_actions if a.type == "confirm"]
    assert confirm, "должно быть предложение создать встречи из протокола"

    out = orchestrator.confirm_action(db, SETTINGS, user, confirm[0].action_id)
    assert out["ok"] is True
    assert db.query(CalendarEvent).count() >= 1


def test_needs_clarification_when_missing(db, user):
    res = orchestrator.run(SETTINGS, db, user, "запланируй встречу", now=NOW)
    assert res.status == "needs_clarification"
    assert res.missing_fields
    assert db.query(CalendarEvent).count() == 0
