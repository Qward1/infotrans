"""Интеграционные тесты оркестратора: авто-создание, подтверждение действий,
уведомления, протокол по документу."""
from __future__ import annotations

from datetime import datetime, timedelta

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


def _make_event(db, user, title="Синк по проекту", start=datetime(2026, 7, 7, 11, 0)):
    ev = CalendarEvent(
        owner_id=user.id, created_by_id=user.id, updated_by_id=user.id,
        title=title, start_at=start, end_at=start + timedelta(hours=1),
        location_type="online", priority=5,
    )
    db.add(ev); db.commit(); db.refresh(ev)
    return ev


def test_cancel_event_sets_status_instead_of_delete(db, user):
    """BUG-03: «отмени встречу» меняет статус на cancelled, не удаляя запись."""
    ev = _make_event(db, user)
    res = orchestrator.run(SETTINGS, db, user, "Отмени встречу «Синк по проекту»", now=NOW)
    assert res.intent == "cancel_event"
    assert res.status == "needs_confirmation"
    assert db.get(CalendarEvent, ev.id).status == "planned"  # до подтверждения — без изменений

    action_id = next(a.action_id for a in res.suggested_actions if a.type == "confirm")
    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is True
    row = db.get(CalendarEvent, ev.id)
    assert row is not None, "отмена не должна удалять запись"
    assert row.status == "cancelled"


def test_delete_event_removes_row(db, user):
    """«Удали встречу» по-прежнему физически удаляет запись."""
    ev = _make_event(db, user, title="Черновик встречи")
    res = orchestrator.run(SETTINGS, db, user, "Удали встречу «Черновик встречи»", now=NOW)
    assert res.intent == "delete_event"
    action_id = next(a.action_id for a in res.suggested_actions if a.type == "confirm")
    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is True
    assert db.get(CalendarEvent, ev.id) is None


def test_delete_unknown_title_asks_clarification(db, user):
    """BUG-04: несуществующее название → уточнение, а не «ближайшая попавшаяся»."""
    ev = _make_event(db, user, title="Синк по проекту")
    res = orchestrator.run(SETTINGS, db, user, "Удали встречу «Бюджет»", now=NOW)
    assert res.status == "needs_clarification"
    assert "Не нашёл" in res.reply
    assert not [a for a in res.suggested_actions if a.type == "confirm"]
    assert db.get(CalendarEvent, ev.id) is not None


def test_move_event_by_date_still_resolves(db, user):
    """Fallback по дате сохраняется: «перенеси встречу завтра» находит встречу."""
    start = NOW.replace(hour=11) + timedelta(days=1)
    ev = _make_event(db, user, title="Синк по проекту", start=start)
    res = orchestrator.run(SETTINGS, db, user, "Перенеси встречу завтра на 15:00", now=NOW)
    assert res.intent == "move_event"
    assert res.status == "needs_confirmation"
    assert any(c.data.get("id") == ev.id for c in res.cards if c.kind == "created_event")


def test_needs_clarification_when_missing(db, user):
    res = orchestrator.run(SETTINGS, db, user, "запланируй встречу", now=NOW)
    assert res.status == "needs_clarification"
    assert res.missing_fields
    assert db.query(CalendarEvent).count() == 0


def _pending_action_id(db, user, message):
    """Создать черновик через диалог и вернуть action_id confirm-кнопки."""
    res = orchestrator.run(SETTINGS, db, user, message, now=NOW)
    assert res.status == "needs_confirmation", res.reply
    return next(a.action_id for a in res.suggested_actions if a.type == "confirm")


def test_expired_action_is_not_executed(db, user):
    """BUG-05: подтверждение просроченного черновика не исполняется."""
    guest = User(email="exp@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    action_id = _pending_action_id(db, user, "встреча с exp@test.local 11.07 в 14:00 онлайн")

    action = db.query(AssistantAction).filter_by(action_id=action_id).first()
    action.expires_at = datetime.now() - timedelta(hours=1)
    db.commit()

    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is False
    assert "устарел" in out["detail"]
    assert db.query(CalendarEvent).count() == 0
    db.refresh(action)
    assert action.status == "expired"


def test_expire_stale_actions_cleanup(db, user):
    """BUG-22: пакетная чистка протухших pending-черновиков."""
    guest = User(email="stale@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    action_id = _pending_action_id(db, user, "встреча с stale@test.local 11.07 в 16:00 онлайн")
    action = db.query(AssistantAction).filter_by(action_id=action_id).first()
    action.expires_at = datetime.now() - timedelta(days=2)
    db.commit()

    assert orchestrator.expire_stale_actions(db) == 1
    db.refresh(action)
    assert action.status == "expired"
    # повторный вызов ничего не находит
    assert orchestrator.expire_stale_actions(db) == 0


def test_confirm_rechecks_conflicts(db, user):
    """BUG-06: конфликт, появившийся между черновиком и confirm, блокирует исполнение."""
    guest = User(email="cc@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    action_id = _pending_action_id(db, user, "встреча с cc@test.local 11.07 в 14:00 онлайн")

    # Пока черновик ждал подтверждения, у владельца появилась встреча на то же время.
    _make_event(db, user, title="Внезапная встреча", start=datetime(2026, 7, 11, 14, 0))

    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is False
    assert "конфликт" in out["detail"].lower()
    assert out["conflicts"]
    # черновик возвращён в pending, встреча ассистентом не создана
    action = db.query(AssistantAction).filter_by(action_id=action_id).first()
    assert action.status == "pending"
    assert db.query(CalendarEvent).count() == 1  # только «Внезапная встреча»


def test_in_progress_action_cannot_be_confirmed_twice(db, user):
    """BUG-07: захваченное (in_progress) действие не исполняется параллельно."""
    guest = User(email="race@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(guest); db.commit()
    action_id = _pending_action_id(db, user, "встреча с race@test.local 11.07 в 09:00 онлайн")
    action = db.query(AssistantAction).filter_by(action_id=action_id).first()
    action.status = "in_progress"
    db.commit()

    out = orchestrator.confirm_action(db, SETTINGS, user, action_id)
    assert out["ok"] is False
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
