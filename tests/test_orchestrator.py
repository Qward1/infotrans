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
from app.services.assistant import chat_history, notification_service, orchestrator

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


def _turn(db, user, chat, message, now):
    """Смоделировать один ход API-чата: user-сообщение → run → assistant-сообщение."""
    chat_history.add_message(db, chat, "user", message)
    result = orchestrator.run(SETTINGS, db, user, message, conversation_id=chat.id, now=now)
    chat_history.add_message(db, chat, "assistant", result.reply, result.model_dump(mode="json"))
    return result


def test_multi_turn_meeting_keeps_context(db, user):
    """«Назначь встречу с Машей → Завтра на 10 → Тест» без сброса контекста."""
    maria = User(email="maria@test.local", full_name="Мария Кузнецова",
                 password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(maria); db.commit()
    chat = chat_history.create_chat(db, user.id, "Тест")

    r1 = _turn(db, user, chat, "Назначь встречу с Машей Кузнецовой", NOW)
    assert r1.intent == "create_event"
    assert r1.status == "needs_clarification"
    assert "день" in r1.reply.lower() and "врем" in r1.reply.lower()
    # участник распознан по имени и сохранён в контексте
    assert "maria@test.local" in r1.extracted["event"]["participants"]

    r2 = _turn(db, user, chat, "Завтра на 10", NOW)
    assert r2.intent == "create_event"  # контекст НЕ потерян
    assert r2.status == "needs_clarification"
    assert "тем" in r2.reply.lower()  # теперь спрашиваем тему
    assert r2.extracted["event"]["participants"] == ["maria@test.local"]

    r3 = _turn(db, user, chat, "Тест", NOW)
    assert r3.intent == "create_event"  # всё ещё тот же сценарий, не приветствие
    assert r3.status == "needs_confirmation"
    assert "Тест" in r3.reply
    # Имя участника склоняется в творительный падеж («с Марией Кузнецовой»);
    # без pymorphy — мягкий fallback на именительный.
    assert ("Марией Кузнецовой" in r3.reply) or ("Мария Кузнецова" in r3.reply)
    # ничего не создано без подтверждения
    assert db.query(CalendarEvent).count() == 0
    action_id = next(a.action_id for a in r3.suggested_actions if a.type == "confirm")
    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is True
    assert db.query(CalendarEvent).count() == 1


def test_unknown_midconversation_does_not_greet(db, user):
    chat = chat_history.create_chat(db, user.id, "Тест")
    _turn(db, user, chat, "Назначь встречу с командой", NOW)
    # ответ не по теме посреди диалога — не должен возвращать приветствие «Здравствуйте»
    r = _turn(db, user, chat, "asdqwe zzz", NOW)
    assert "Здравствуйте" not in r.reply


# --------------------------------------------------------------------------- #
# «Голос» секретаря (smart_calendar_secretary): озвучивание ответа через Dify  #
# --------------------------------------------------------------------------- #
def _dify_settings():
    s = Settings()
    s.assistant.dify.enabled = True
    return s


def _result(**kwargs):
    from app.services.assistant.schemas import AssistantResult

    base = dict(reply="✅ Создал встречу.", intent="create_event", mode="dify", status="done")
    base.update(kwargs)
    return AssistantResult(**base)


def test_secretary_voice_overrides_reply(monkeypatch, user):
    from app.services.assistant import dify_client

    captured = {}

    def fake_secretary(settings, message, context, user_email=None, conversation_id=None):
        captured["context"] = context
        captured["message"] = message
        return "  Готово! Встреча создана.  "

    monkeypatch.setattr(dify_client, "secretary_reply", fake_secretary)
    result = _result()
    orchestrator._apply_secretary_voice(_dify_settings(), user, "создай встречу", result, "conv-1")

    assert result.reply == "Готово! Встреча создана."       # текст секретаря, обрезанный
    assert captured["message"] == "создай встречу"           # исходная реплика уходит в Dify
    assert captured["context"]["draft_reply"] == "✅ Создал встречу."  # факты бэкенда переданы


def test_secretary_voice_soft_fallback_on_error(monkeypatch, user):
    from app.services.assistant import dify_client

    def boom(*args, **kwargs):
        raise dify_client.DifyError("network down")

    monkeypatch.setattr(dify_client, "secretary_reply", boom)
    result = _result()
    orchestrator._apply_secretary_voice(_dify_settings(), user, "создай встречу", result, "c")

    assert result.reply == "✅ Создал встречу."  # детерминированный текст сохранён
    assert result.mode == "dify-fallback"


def test_secretary_voice_noop_when_dify_disabled(monkeypatch, user):
    from app.services.assistant import dify_client

    def must_not_call(*args, **kwargs):  # pragma: no cover
        raise AssertionError("secretary_reply не должен вызываться при dify.enabled=false")

    monkeypatch.setattr(dify_client, "secretary_reply", must_not_call)
    result = _result(mode="local")
    orchestrator._apply_secretary_voice(Settings(), user, "msg", result, "c")

    assert result.reply == "✅ Создал встречу."


def test_secretary_voice_skipped_after_normalizer_fallback(monkeypatch, user):
    from app.services.assistant import dify_client

    def must_not_call(*args, **kwargs):  # pragma: no cover
        raise AssertionError("после отката нормализатора секретаря не зовём")

    monkeypatch.setattr(dify_client, "secretary_reply", must_not_call)
    result = _result(mode="dify-fallback")
    orchestrator._apply_secretary_voice(_dify_settings(), user, "msg", result, "c")

    assert result.reply == "✅ Создал встречу."
