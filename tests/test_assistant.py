"""Тесты интеллектуального слоя: нормализатор, достаточность данных, планирование,
конфликт-резолвинг, поиск билетов, генерация протокола."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.calendar import STATUS_PLANNED, CalendarEvent
from app.models.user import ROLE_ADMIN, ROLE_USER, User
from app.services import availability, conflict_resolver
from app.services.assistant import (
    calendar_context,
    chat_history,
    normalizer,
    orchestrator,
    protocol_generator,
    travel_search,
)
from app.services.conflict_resolver import (
    ACTION_ASK_CONFIRMATION,
    ACTION_PROPOSE_RESCHEDULE_LOWER,
    ACTION_SCHEDULE_AS_IS,
    ACTION_SUGGEST_ALTERNATIVES,
    ProposedEvent,
)

SETTINGS = Settings()  # значения по умолчанию: рабочие часы 09–19, порог high=8
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
    u = User(email="u@test.local", full_name="U", password_hash=hash_password("x"),
             role=ROLE_USER, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _add_event(db, owner_id, day, h1, h2, priority=5, title="E", loc="online"):
    ev = CalendarEvent(
        owner_id=owner_id, title=title,
        start_at=datetime(2026, 7, day, h1, 0), end_at=datetime(2026, 7, day, h2, 0),
        timezone="Europe/Moscow", location_type=loc, priority=priority,
        status=STATUS_PLANNED, source="manual",
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# --------------------------------------------------------------------------- #
# Нормализатор                                                                 #
# --------------------------------------------------------------------------- #
def test_normalizer_local_create_event():
    nr = normalizer.normalize_local(SETTINGS, "Запланируй встречу с командой 07.07 в 15:00 онлайн", now=NOW)
    assert nr.intent == "create_event"
    assert nr.event.date == datetime(2026, 7, 7).date()
    assert nr.event.start_time.hour == 15
    assert nr.event.format == "online"
    assert nr.missing_fields == []


def test_normalizer_dify_enabled_falls_back_without_key():
    """dify.enabled=true, но ключа нет → мягкий откат на локальный парсер."""
    s = Settings()
    s.assistant.dify.enabled = True
    s.assistant.dify.api_key = ""  # нет ключа → DifyError → fallback
    nr = normalizer.normalize(s, "Найди билеты из Москвы в Казань 08.07", now=NOW)
    assert nr.intent == "find_tickets"
    assert nr.source == "dify-fallback"  # именно fallback, а не падение


def test_normalizer_missing_fields_create_event():
    nr = normalizer.normalize_local(SETTINGS, "запланируй встречу", now=NOW)
    assert nr.intent == "create_event"
    # нет даты/времени → ассистент не выполняет, а спрашивает
    assert "date" in nr.missing_fields
    assert "start_time" in nr.missing_fields
    # format не обязателен: по умолчанию офлайн (не блокирует создание)
    assert "format" not in nr.missing_fields
    assert nr.clarifying_question


def test_normalizer_missing_fields_tickets():
    nr = normalizer.normalize_local(SETTINGS, "нужны билеты в Казань", now=NOW)
    assert nr.intent == "find_tickets"
    assert "origin_city" in nr.missing_fields
    assert "departure_date" in nr.missing_fields


# --------------------------------------------------------------------------- #
# Поиск свободных слотов                                                       #
# --------------------------------------------------------------------------- #
def test_find_free_slots_avoids_busy(db, user):
    # занято 12–13 в понедельник
    _add_event(db, user.id, 6, 12, 13, title="Занято")
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id],
        datetime(2026, 7, 6, 9, 0), datetime(2026, 7, 6, 19, 0),
        duration_minutes=60, meeting_format="online",
    )
    assert slots, "должны найтись свободные окна"
    # ни один предложенный слот не пересекает 12–13
    for s in slots:
        assert not (s.start < datetime(2026, 7, 6, 13, 0) and s.end > datetime(2026, 7, 6, 12, 0))
    # первый слот — с 09:00
    assert slots[0].start == datetime(2026, 7, 6, 9, 0)
    assert slots[0].reason  # объяснение присутствует


def test_find_free_slots_multi_participant(db, user):
    u2 = User(email="p2@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    db.add(u2); db.commit(); db.refresh(u2)
    _add_event(db, user.id, 6, 9, 11, title="U busy")
    _add_event(db, u2.id, 6, 15, 17, title="U2 busy")
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id, u2.id],
        datetime(2026, 7, 6, 9, 0), datetime(2026, 7, 6, 19, 0), duration_minutes=60,
    )
    # общий слот не должен пересекать ни 9-11, ни 15-17
    for s in slots:
        assert not (s.start < datetime(2026, 7, 6, 11, 0) and s.end > datetime(2026, 7, 6, 9, 0))
        assert not (s.start < datetime(2026, 7, 6, 17, 0) and s.end > datetime(2026, 7, 6, 15, 0))


# --------------------------------------------------------------------------- #
# Конфликт-резолвинг (три обязательных случая)                                #
# --------------------------------------------------------------------------- #
def _proposed(day=6, h1=15, h2=16, priority=5):
    return ProposedEvent(
        start=datetime(2026, 7, day, h1, 0), end=datetime(2026, 7, day, h2, 0),
        priority=priority, format="online", title="Новая",
    )


def test_no_conflict_schedule_as_is(db, user):
    _add_event(db, user.id, 6, 10, 11, priority=5)
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, _proposed(h1=15, h2=16), [user.id])
    assert res.can_schedule is True
    assert res.recommended_action == ACTION_SCHEDULE_AS_IS
    assert res.conflicts == []


def test_conflict_equal_priority_asks_confirmation(db, user):
    _add_event(db, user.id, 6, 15, 16, priority=5, title="Существующая")
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, _proposed(priority=5), [user.id])
    assert res.can_schedule is False
    assert res.recommended_action == ACTION_ASK_CONFIRMATION
    assert len(res.conflicts) == 1
    assert res.alternative_slots  # предлагаются альтернативы


def test_conflict_new_higher_priority_proposes_reschedule(db, user):
    _add_event(db, user.id, 6, 15, 16, priority=3, title="Малозначимая")
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, _proposed(priority=7), [user.id])
    assert res.can_schedule is False
    assert res.recommended_action == ACTION_PROPOSE_RESCHEDULE_LOWER


def test_conflict_existing_higher_priority_suggests_alternatives(db, user):
    _add_event(db, user.id, 6, 15, 16, priority=7, title="Важная")
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, _proposed(priority=5), [user.id])
    assert res.can_schedule is False
    assert res.recommended_action == ACTION_SUGGEST_ALTERNATIVES


def test_conflict_high_priority_cannot_be_moved(db, user):
    # существующая с очень высоким приоритетом (>= порога 8) — не двигаем автоматически
    _add_event(db, user.id, 6, 15, 16, priority=9, title="Критичная")
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, _proposed(priority=6), [user.id])
    assert res.recommended_action == ACTION_SUGGEST_ALTERNATIVES
    assert res.conflicts[0].is_high_priority is True


def test_travel_buffer_warning_between_offline(db, user):
    # офлайн-встреча в другом городе прямо перед предложенной — предупреждение о дороге
    _add_event(db, user.id, 6, 13, 15, priority=5, title="Казань", loc="offline")
    db.query(CalendarEvent).filter_by(title="Казань").update({"city": "Казань"})
    db.commit()
    proposed = ProposedEvent(
        start=datetime(2026, 7, 6, 15, 15), end=datetime(2026, 7, 6, 16, 0),
        priority=5, format="offline", city="Москва", title="Москва",
    )
    res = conflict_resolver.resolve_conflicts(db, SETTINGS, proposed, [user.id])
    assert res.buffer_warnings, "должно быть предупреждение о нехватке времени на дорогу"


def test_find_free_slots_not_before_cuts_past(db, user):
    """BUG-08: «сегодня вечером» не предлагает утренние слоты."""
    evening = datetime(2026, 7, 6, 17, 3)  # сейчас 17:03
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id],
        datetime(2026, 7, 6, 9, 0), datetime(2026, 7, 6, 19, 0),
        duration_minutes=60, not_before=evening,
    )
    assert slots, "вечером ещё есть окно до конца рабочего дня"
    for s in slots:
        assert s.start >= datetime(2026, 7, 6, 17, 5)  # округление вверх до 5 минут


def test_find_free_slots_not_before_after_range_returns_empty(db, user):
    slots = availability.find_free_slots(
        db, SETTINGS, [user.id],
        datetime(2026, 7, 6, 9, 0), datetime(2026, 7, 6, 19, 0),
        duration_minutes=60, not_before=datetime(2026, 7, 6, 20, 0),
    )
    assert slots == []


def test_assistant_find_slots_today_excludes_past(db, user):
    """Ассистентский путь: «найди слот сегодня» вечером не предлагает утро."""
    now = datetime(2026, 7, 6, 17, 0)
    res = orchestrator.run(SETTINGS, db, user, "Найди свободное время сегодня", now=now)
    assert res.intent == "find_free_slots"
    for slot in res.alternative_slots:
        assert datetime.fromisoformat(slot["start_at"]) >= now


# --------------------------------------------------------------------------- #
# Поиск билетов (mock provider)                                               #
# --------------------------------------------------------------------------- #
def test_travel_mock_search_drops_departed_today():
    """BUG-08: на «сегодня» mock не показывает рейсы, которые уже ушли."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    opts = travel_search.search(SETTINGS, "Москва", "Казань", today, "any")
    now = datetime.now()
    assert all(o.depart_at >= now for o in opts)


def test_travel_mock_search_returns_options():
    opts = travel_search.search(SETTINGS, "Москва", "Казань", datetime(2030, 7, 8), "any")
    assert opts, "mock-провайдер должен вернуть варианты"
    assert all(o.price > 0 and o.duration_minutes > 0 for o in opts)
    modes = {o.mode for o in opts}
    assert {"plane", "train", "bus"} & modes
    # отсортировано по цене
    assert opts == sorted(opts, key=lambda o: o.price)


def test_travel_mock_filters_by_transport():
    opts = travel_search.search(SETTINGS, "Москва", "Сочи", datetime(2030, 7, 8), "flight")
    assert opts and all(o.mode == "plane" for o in opts)


def test_travel_provider_without_key_does_not_fallback_to_mock(monkeypatch):
    s = Settings()
    s.tickets.mode = "provider"
    s.tickets.provider.name = "travelpayouts"
    s.tickets.provider.api_key = ""
    monkeypatch.delenv("SMARTCAL_TICKETS_API_KEY", raising=False)

    with pytest.raises(travel_search.TicketProviderNotConfigured):
        travel_search.search(s, "Москва", "Казань", datetime(2030, 7, 8), "flight")


def test_travel_search_validates_route_and_date():
    with pytest.raises(travel_search.TicketValidationError):
        travel_search.search(SETTINGS, "Москва", "Москва", datetime(2030, 7, 8), "train")


def test_travel_site_links_do_not_require_api_key():
    params = travel_search.build_params("Москва", "Санкт-Петербург", datetime(2030, 7, 8), "any")
    links = travel_search.external_search_links(params)
    providers = {link.provider for link in links}

    assert {"Aviasales", "РЖД", "Туту", "Туту Автобусы"} <= providers
    assert all(link.url.startswith("https://") for link in links)


# --------------------------------------------------------------------------- #
# История чатов ассистента                                                    #
# --------------------------------------------------------------------------- #
def test_chat_history_access_is_scoped_to_owner(db, user):
    other = User(email="other@test.local", password_hash=hash_password("x"), role=ROLE_USER, is_active=True)
    admin = User(email="admin@test.local", password_hash=hash_password("x"), role=ROLE_ADMIN, is_active=True)
    db.add_all([other, admin])
    db.commit()
    db.refresh(other)
    db.refresh(admin)

    own_chat = chat_history.create_chat(db, user.id, "Мой чат")
    other_chat = chat_history.create_chat(db, other.id, "Чужой чат")
    chat_history.add_message(db, own_chat, "user", "Привет")
    chat_history.add_message(db, own_chat, "assistant", "Здравствуйте")

    assert chat_history.get_accessible_chat(db, user, own_chat.id).id == own_chat.id
    assert chat_history.get_accessible_chat(db, user, other_chat.id) is None
    assert chat_history.get_accessible_chat(db, admin, other_chat.id).id == other_chat.id

    payload = chat_history.serialize_chat(chat_history.get_chat(db, own_chat.id), include_messages=True)
    assert payload["userId"] == user.id
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]


# --------------------------------------------------------------------------- #
# Assistant calendar context                                                  #
# --------------------------------------------------------------------------- #
def test_calendar_context_resolves_employee_and_respects_access(db, user):
    admin = User(
        email="admin@test.local",
        full_name="Admin",
        password_hash=hash_password("x"),
        role=ROLE_ADMIN,
        is_active=True,
    )
    maria = User(
        email="maria@test.local",
        full_name="Мария Кузнецова",
        password_hash=hash_password("x"),
        role=ROLE_USER,
        is_active=True,
    )
    db.add_all([admin, maria])
    db.commit()
    db.refresh(admin)
    db.refresh(maria)
    _add_event(db, maria.id, 6, 10, 11, title="Занято у Марии")

    target = calendar_context.resolve_employee_query(db, SETTINGS, admin, "Маши Кузнецовой")
    assert target.id == maria.id

    requested = calendar_context.DateRange(
        datetime(2026, 7, 6, 0, 0),
        datetime(2026, 7, 7, 0, 0),
        "сегодня",
    )
    payload = calendar_context.employee_availability(
        db, SETTINGS, admin, maria, requested, requested_slot_duration=60
    )
    assert payload["employeeId"] == maria.id
    assert payload["busyIntervals"]
    assert payload["availableSlots"]
    for slot in payload["availableSlots"]:
        start = datetime.fromisoformat(slot["start_at"])
        end = datetime.fromisoformat(slot["end_at"])
        assert not (start < datetime(2026, 7, 6, 11, 0) and end > datetime(2026, 7, 6, 10, 0))

    with pytest.raises(calendar_context.CalendarAccessDenied):
        calendar_context.resolve_employee_query(db, SETTINGS, user, "Маши Кузнецовой")


def test_orchestrator_employee_slots_for_admin_only(db, user):
    admin = User(
        email="admin2@test.local",
        full_name="Admin",
        password_hash=hash_password("x"),
        role=ROLE_ADMIN,
        is_active=True,
    )
    maria = User(
        email="maria2@test.local",
        full_name="Мария Кузнецова",
        password_hash=hash_password("x"),
        role=ROLE_USER,
        is_active=True,
    )
    db.add_all([admin, maria])
    db.commit()
    db.refresh(admin)
    db.refresh(maria)
    _add_event(db, maria.id, 6, 10, 11, title="Фокус")

    res = orchestrator.run(
        SETTINGS,
        db,
        admin,
        "Свободные окошки Мария Кузнецова",
        now=NOW,
    )
    assert res.status == "done"
    assert res.cards and res.cards[0].kind == "employee_availability"
    assert res.cards[0].data["items"][0]["employeeId"] == maria.id
    assert "Мария Кузнецова" in res.reply
    assert res.alternative_slots

    denied = orchestrator.run(
        SETTINGS,
        db,
        user,
        "Покажи свободные слоты Маши Кузнецовой на сегодня",
        now=NOW,
    )
    assert denied.status == "error"
    assert "нет доступа" in denied.reply.lower()


# --------------------------------------------------------------------------- #
# Генерация протокола (mock parser)                                           #
# --------------------------------------------------------------------------- #
def test_protocol_mock_generation():
    text = (
        "Встреча по проекту Альфа.\n"
        "Участники: Иван, Мария.\n"
        "Решили: запустить пилот 15.08.\n"
        "Задача: подготовить ТЗ, ответственный Иван, срок до 20.07.\n"
        "Риск: нехватка ресурсов.\n"
        "Следующая встреча: статус по пилоту в понедельник.\n"
    )
    proto = protocol_generator.generate_local(SETTINGS, text)
    assert proto.summary
    assert any("пилот" in d.lower() for d in proto.decisions)
    assert any("тз" in a.lower() for a in proto.action_items)
    assert proto.risks
    assert proto.follow_up_meetings


def test_protocol_empty_text_returns_demo():
    proto = protocol_generator.generate_local(SETTINGS, "")
    # даже без текста возвращаем осмысленный demo-протокол
    assert proto.summary
    assert proto.action_items
