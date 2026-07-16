"""Тесты этапа 4: шаг сетки слотов (FN-02), owner_name конфликтов (FN-03),
предпросмотр протокола (FN-05), предпочтения билетов (FN-06), напоминания (FN-08),
конфликты статистики (BUG-18), реалистичность офлайна (FN-13), BUG-24/25."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.assistant import Document
from app.models.calendar import STATUS_CANCELLED, CalendarEvent
from app.models.reminder import Reminder
from app.models.user import ROLE_USER, User
from app.services import availability, conflict_resolver, reminder_service, stats
from app.services.assistant import normalizer, notification_service, orchestrator

SETTINGS = Settings()
SETTINGS.tickets.mode = "mock"
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
    u = User(email="u4@test.local", full_name="Юзер Тестов", password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _event(db, owner_id, start, minutes=60, title="E", loc="online", city="",
           priority=5, status="planned"):
    ev = CalendarEvent(
        owner_id=owner_id, title=title, start_at=start,
        end_at=start + timedelta(minutes=minutes),
        location_type=loc, city=city, priority=priority, status=status,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# --------------------------------------------------------------------------- #
# FN-02: шаг сетки и равномерное распределение                                 #
# --------------------------------------------------------------------------- #
def test_slots_generated_with_granularity(db, user):
    """Свободный день даёт варианты с шагом slot_granularity, а не одно окно."""
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id],
        datetime(2026, 7, 6, 9, 0), datetime(2026, 7, 6, 19, 0),
        duration_minutes=60,
    )
    assert len(slots) == SETTINGS.scheduling.max_alternatives  # конфиг работает
    starts = [s.start for s in slots]
    assert starts == sorted(starts)  # сортировка по времени
    step = timedelta(minutes=SETTINGS.scheduling.slot_granularity_minutes)
    assert starts[1] - starts[0] == step  # шаг сетки


def test_slots_distributed_across_days(db, user):
    """Варианты распределяются по дням, а не все в понедельник."""
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id],
        datetime(2026, 7, 6, 0, 0), datetime(2026, 7, 8, 0, 0),
        duration_minutes=60,
    )
    days = {s.start.date() for s in slots}
    assert len(days) >= 2, "слоты должны покрыть оба свободных дня"


# --------------------------------------------------------------------------- #
# FN-03: имя владельца конфликтующей встречи                                   #
# --------------------------------------------------------------------------- #
def test_conflict_info_has_owner_name(db, user):
    _event(db, user.id, datetime(2026, 7, 6, 15, 0), title="Занято")
    proposed = conflict_resolver.ProposedEvent(
        start=datetime(2026, 7, 6, 15, 0), end=datetime(2026, 7, 6, 16, 0), priority=5,
    )
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, proposed, [user.id])
    assert res.conflicts
    assert res.conflicts[0].owner_name == "Юзер Тестов"
    assert res.conflicts[0].to_dict()["owner_name"] == "Юзер Тестов"


# --------------------------------------------------------------------------- #
# FN-05: предпросмотр встреч из протокола                                      #
# --------------------------------------------------------------------------- #
def test_protocol_followups_preview_card(db, user):
    text = "Решили: ок.\nСледующая встреча: обсудить прототип через неделю.\n"
    doc = Document(owner_id=user.id, filename="p.txt", content_type="text/plain",
                   size_bytes=len(text), text=text)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    res = orchestrator.build_protocol_from_document(SETTINGS, db, user, doc)
    followups = [c for c in res.cards if c.kind == "followups"]
    assert followups, "должна быть карточка предпросмотра встреч"
    events = followups[0].data["events"]
    assert events and events[0]["title"]
    assert events[0]["start_at"]  # дата видна до подтверждения


# --------------------------------------------------------------------------- #
# FN-06/BUG-26: предпочтения и бюджет применяются на сервере                   #
# --------------------------------------------------------------------------- #
def test_tickets_direct_and_budget_filters(db, user):
    res = orchestrator.run(
        SETTINGS, db, user,
        "Найди прямые билеты из Москвы в Казань 20.07.2030 до 9000 руб", now=NOW,
    )
    assert res.intent == "find_tickets"
    assert res.travel_options, res.reply
    for o in res.travel_options:
        assert o["transfers"] == 0
        assert o["price"] <= 9000
    assert "без пересадок" in res.reply


def test_tickets_fastest_sorts_by_duration(db, user):
    res = orchestrator.run(
        SETTINGS, db, user,
        "Найди самые быстрые билеты из Москвы в Казань 20.07.2030", now=NOW,
    )
    durations = [o["duration_minutes"] for o in res.travel_options]
    assert durations == sorted(durations)
    assert "Быстрее всего" in res.reply


# --------------------------------------------------------------------------- #
# FN-08: наступившие напоминания отправляются                                  #
# --------------------------------------------------------------------------- #
def test_send_due_reminders(db, user):
    ev = _event(db, user.id, datetime.now() + timedelta(hours=1), title="Скоро")
    due = Reminder(event_id=ev.id, user_id=user.id,
                   remind_at=datetime.now() - timedelta(minutes=1), channel="web")
    future = Reminder(event_id=ev.id, user_id=user.id,
                      remind_at=datetime.now() + timedelta(hours=1), channel="web")
    db.add_all([due, future])
    db.commit()

    sent = reminder_service.send_due_reminders(db, SETTINGS)
    assert sent == 1
    db.refresh(due)
    db.refresh(future)
    assert due.status == "sent"
    assert future.status == "scheduled"  # ещё не наступило
    assert notification_service.unread_count(db, user.id) == 1
    # повторный вызов ничего не шлёт
    assert reminder_service.send_due_reminders(db, SETTINGS) == 0


# --------------------------------------------------------------------------- #
# BUG-18: конфликты статистики без N+1                                         #
# --------------------------------------------------------------------------- #
def test_stats_conflicts_count(db, user):
    other = User(email="o4s@test.local", password_hash=hash_password("x"),
                 role=ROLE_USER, is_active=True)
    db.add(other)
    db.commit()
    db.refresh(other)
    now = datetime.now()
    _event(db, user.id, now + timedelta(days=1), title="A")
    _event(db, user.id, now + timedelta(days=1, minutes=30), title="B")  # конфликт
    _event(db, other.id, now + timedelta(days=2), title="C")  # без конфликта
    assert stats.conflicts_count(db) == 1


# --------------------------------------------------------------------------- #
# FN-13: нереалистичный офлайн предлагает онлайн                               #
# --------------------------------------------------------------------------- #
def test_unrealistic_offline_suggests_online(db, user):
    # Утром очная встреча в Москве; в 15:00 пользователь просит очную в Новосибирске.
    _event(db, user.id, datetime(2026, 7, 7, 9, 0), title="Утро в Москве",
           loc="offline", city="Москва")
    res = orchestrator.run(
        SETTINGS, db, user,
        "Запланируй очную встречу по проекту 07.07 в 15:00 в Новосибирске", now=NOW,
    )
    assert res.intent == "create_event"
    assert any("Дорога из Москва" in w for w in res.warnings), res.warnings
    assert any(a.label == "Сделать онлайн" for a in res.suggested_actions)


# --------------------------------------------------------------------------- #
# «время завтра…» не превращается в поиск сотрудника «завтра»                  #
# --------------------------------------------------------------------------- #
def test_find_slots_tomorrow_not_treated_as_employee(db, user):
    from app.services.assistant import calendar_context

    assert calendar_context.extract_employee_queries(
        "Найди свободное время завтра на 30 минут"
    ) == []
    res = orchestrator.run(SETTINGS, db, user, "Найди свободное время завтра на 30 минут", now=NOW)
    assert res.intent == "find_free_slots"
    assert res.status == "done"
    assert res.alternative_slots, res.reply


# --------------------------------------------------------------------------- #
# BUG-24/25: формы дней недели и счётчик без отменённых                        #
# --------------------------------------------------------------------------- #
def test_weekday_case_forms_parsed():
    assert normalizer.parse_date("перенеси на среду", NOW) == datetime(2026, 7, 8).date()
    assert normalizer.parse_date("выбери средний вариант", NOW) is None  # не ловим мусор
    assert normalizer.parse_date("к пятнице", NOW) == datetime(2026, 7, 10).date()
    assert normalizer.parse_date("в субботу", NOW) == datetime(2026, 7, 11).date()


def test_show_calendar_excludes_cancelled_from_count(db, user):
    _event(db, user.id, datetime(2026, 7, 6, 10, 0), title="Активная")
    _event(db, user.id, datetime(2026, 7, 6, 12, 0), title="Отменённая", status=STATUS_CANCELLED)
    res = orchestrator.run(SETTINGS, db, user, "Покажи календарь на сегодня", now=NOW)
    assert res.intent == "show_calendar"
    assert "1 встреч" in res.reply  # отменённая не в счёте
    card = next(c for c in res.cards if c.kind == "calendar")
    assert len(card.data["events"]) == 2  # но в карточке видна (зачёркнутой)
