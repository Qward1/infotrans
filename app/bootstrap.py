"""Инициализация при старте: таблицы, seed-админ, demo-данные.

Demo-наполнение управляется YAML (``demo.seed_on_startup``) и полностью
идемпотентно: повторный запуск не дублирует пользователей, встречи, уведомления
и документы (используется маркер — специальный demo-пользователь).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, init_db
from app.core.security import hash_password
from app.core.clock import local_now
from app.models.assistant import Document, Notification
from app.models.audit import AuditLog
from app.models.calendar import (
    CalendarEvent,
    LOC_HYBRID,
    LOC_OFFLINE,
    LOC_ONLINE,
    SOURCE_MANUAL,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PLANNED,
)
from app.models.meeting import EventParticipant
from app.models.user import ROLE_USER, User
from app.services import users as users_service

logger = logging.getLogger("smartcal.bootstrap")

# Пароль для всех demo-сотрудников (кроме админа — он из YAML seed_admin).
_DEMO_PASSWORD = "user12345"

# Каталог demo-сотрудников: email → полное имя.
_DEMO_USERS = [
    ("user@demo.local", "Иван Сотрудников"),
    ("anna@demo.local", "Анна Петрова"),
    ("maria@demo.local", "Мария Кузнецова"),
    ("petr@demo.local", "Пётр Волков"),
    ("olga@demo.local", "Ольга Морозова"),
]


def _monday(now: datetime) -> datetime:
    """Понедельник текущей недели, 00:00."""
    d = now.date()
    monday = d - timedelta(days=d.weekday())
    return datetime(monday.year, monday.month, monday.day)


def _ensure_demo_users(db: Session) -> dict[str, User]:
    """Создать (при отсутствии) demo-сотрудников. Возвращает карту email → User."""
    result: dict[str, User] = {}
    for email, full_name in _DEMO_USERS:
        user = users_service.get_by_email(db, email)
        if user is None:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(_DEMO_PASSWORD),
                role=ROLE_USER,
                is_active=True,
            )
            db.add(user)
        result[email] = user
    db.commit()
    for user in result.values():
        db.refresh(user)
    return result


def _event_specs() -> list[dict]:
    """Декларативное описание demo-встреч (без абсолютных дат — они считаются от Пн)."""
    O, F, H = LOC_ONLINE, LOC_OFFLINE, LOC_HYBRID
    # week: 0 — текущая неделя, 1 — следующая; day: 0=Пн..6=Вс.
    return [
        # --- Текущая неделя ---
        {"owner": "admin", "title": "Планёрка команды", "week": 0, "day": 0, "h": 10, "m": 0,
         "dur": 60, "loc": O, "url": "https://meet.example.local/planerka", "prio": 7,
         "imp": "high", "participants": ["user@demo.local", "anna@demo.local", "maria@demo.local"]},
        {"owner": "user@demo.local", "title": "1:1 с руководителем", "week": 0, "day": 0, "h": 12,
         "m": 0, "dur": 30, "loc": O, "url": "https://meet.example.local/one-on-one", "prio": 6},
        {"owner": "anna@demo.local", "title": "Дизайн-ревью", "week": 0, "day": 0, "h": 15, "m": 0,
         "dur": 60, "loc": H, "city": "Москва", "address": "ул. Тверская, 1",
         "url": "https://meet.example.local/design", "prio": 5, "imp": "normal"},

        # Конфликт у Ивана во вторник (две пересекающиеся встречи).
        {"owner": "user@demo.local", "title": "Синк по проекту", "week": 0, "day": 1, "h": 11, "m": 0,
         "dur": 60, "loc": O, "url": "https://meet.example.local/sync", "prio": 6},
        {"owner": "user@demo.local", "title": "Звонок с заказчиком", "week": 0, "day": 1, "h": 11, "m": 30,
         "dur": 60, "loc": O, "url": "https://meet.example.local/client", "prio": 8, "imp": "high"},

        {"owner": "admin", "title": "Встреча с подрядчиком", "week": 0, "day": 1, "h": 14, "m": 0,
         "dur": 90, "loc": F, "city": "Москва", "address": "ул. Тверская, 1", "prio": 8, "imp": "high"},
        {"owner": "maria@demo.local", "title": "Интервью кандидата", "week": 0, "day": 1, "h": 16, "m": 0,
         "dur": 45, "loc": O, "url": "https://meet.example.local/interview", "prio": 4},

        {"owner": "petr@demo.local", "title": "Поездка в Санкт-Петербург", "week": 0, "day": 2, "h": 9,
         "m": 0, "dur": 540, "loc": F, "city": "Санкт-Петербург", "address": "Невский пр., 10",
         "prio": 9, "imp": "critical"},
        {"owner": "anna@demo.local", "title": "Согласование макетов", "week": 0, "day": 2, "h": 13, "m": 0,
         "dur": 60, "loc": O, "url": "https://meet.example.local/mockups", "prio": 5},
        {"owner": "olga@demo.local", "title": "Планирование спринта", "week": 0, "day": 2, "h": 11, "m": 0,
         "dur": 90, "loc": H, "city": "Москва", "address": "Ленинградский пр., 37",
         "url": "https://meet.example.local/sprint", "prio": 7, "imp": "high",
         "participants": ["user@demo.local", "petr@demo.local"]},

        {"owner": "user@demo.local", "title": "Демо продукта заказчику", "week": 0, "day": 3, "h": 15,
         "m": 0, "dur": 60, "loc": O, "url": "https://meet.example.local/demo", "prio": 9, "imp": "critical",
         "participants": ["admin", "anna@demo.local"]},
        {"owner": "maria@demo.local", "title": "Ретроспектива", "week": 0, "day": 3, "h": 17, "m": 0,
         "dur": 45, "loc": O, "url": "https://meet.example.local/retro", "prio": 3, "imp": "low"},
        {"owner": "admin", "title": "Бюджет на квартал", "week": 0, "day": 4, "h": 10, "m": 0,
         "dur": 60, "loc": F, "city": "Москва", "address": "ул. Тверская, 1", "prio": 8, "imp": "high"},
        {"owner": "olga@demo.local", "title": "Кофе с командой", "week": 0, "day": 4, "h": 16, "m": 30,
         "dur": 30, "loc": F, "city": "Москва", "prio": 1, "imp": "low", "status": STATUS_CANCELLED},

        # --- Следующая неделя ---
        {"owner": "admin", "title": "Стратегическая сессия", "week": 1, "day": 0, "h": 10, "m": 0,
         "dur": 120, "loc": H, "city": "Москва", "address": "Ленинградский пр., 37",
         "url": "https://meet.example.local/strategy", "prio": 9, "imp": "critical",
         "participants": ["user@demo.local", "anna@demo.local", "maria@demo.local", "olga@demo.local"]},
        {"owner": "user@demo.local", "title": "Ревью кода", "week": 1, "day": 1, "h": 14, "m": 0,
         "dur": 60, "loc": O, "url": "https://meet.example.local/codereview", "prio": 5},
        {"owner": "anna@demo.local", "title": "Встреча с типографией", "week": 1, "day": 2, "h": 11, "m": 0,
         "dur": 90, "loc": F, "city": "Санкт-Петербург", "address": "Лиговский пр., 50", "prio": 6},
        {"owner": "petr@demo.local", "title": "Онбординг нового сотрудника", "week": 1, "day": 3, "h": 10,
         "m": 0, "dur": 60, "loc": O, "url": "https://meet.example.local/onboarding", "prio": 4},
        {"owner": "maria@demo.local", "title": "Квартальный отчёт", "week": 1, "day": 4, "h": 15, "m": 0,
         "dur": 60, "loc": H, "city": "Москва", "url": "https://meet.example.local/report", "prio": 7, "imp": "high"},
    ]


def _seed_events(db: Session, users: dict[str, User], admin: User, tz: str, now: datetime) -> None:
    monday = _monday(now)
    email_to_user = {**users, "admin": admin}
    for spec in _event_specs():
        owner = email_to_user.get(spec["owner"])
        if owner is None:
            continue
        start = monday + timedelta(
            weeks=spec["week"], days=spec["day"], hours=spec["h"], minutes=spec.get("m", 0)
        )
        end = start + timedelta(minutes=spec["dur"])
        # Статус: явно заданный, иначе — прошедшие считаем проведёнными.
        status = spec.get("status")
        if status is None:
            status = STATUS_COMPLETED if end < now else STATUS_PLANNED
        prio = spec.get("prio", 5)
        event = CalendarEvent(
            owner_id=owner.id,
            created_by_id=owner.id,
            updated_by_id=owner.id,
            title=spec["title"],
            description=spec.get("desc", ""),
            start_at=start,
            end_at=end,
            timezone=tz,
            location_type=spec["loc"],
            city=spec.get("city", ""),
            address=spec.get("address", ""),
            meeting_url=spec.get("url", ""),
            importance=spec.get("imp", "high" if prio >= 8 else "normal"),
            priority=prio,
            status=status,
            source=SOURCE_MANUAL,
        )
        db.add(event)
        db.flush()  # нужен event.id для участников
        for pemail in spec.get("participants", []):
            puser = email_to_user.get(pemail)
            if puser and puser.id != owner.id:
                db.add(EventParticipant(event_id=event.id, user_id=puser.id, role="attendee"))
    db.commit()


def _seed_notifications(db: Session, users: dict[str, User], admin: User, now: datetime) -> None:
    """Несколько demo-уведомлений с разными каналами/статусами."""
    ivan = users["user@demo.local"]
    anna = users["anna@demo.local"]
    specs = [
        (ivan, "messenger", "Новая встреча", "Встреча «Демо продукта заказчику» запланирована на четверг 15:00.", "unread", 5),
        (ivan, "messenger", "Приглашение на встречу", "Вас пригласили на «Планёрка команды», Пн 10:00.", "unread", 60),
        (ivan, "web", "Напоминание", "Через 30 минут: «1:1 с руководителем».", "read", 180),
        (ivan, "email", "Конфликт расписания", "Во вторник пересекаются «Синк по проекту» и «Звонок с заказчиком».", "unread", 240),
        (anna, "messenger", "Новая встреча", "«Дизайн-ревью» запланировано на понедельник 15:00.", "read", 90),
        (admin, "web", "Отчёт готов", "Еженедельная статистика по встречам команды обновлена.", "unread", 30),
    ]
    for user, channel, title, text, status, mins_ago in specs:
        db.add(
            Notification(
                user_id=user.id,
                channel=channel,
                title=title,
                text=text,
                status=status,
                created_at=now - timedelta(minutes=mins_ago),
                meta_json=json.dumps({}, ensure_ascii=False),
            )
        )
    db.commit()


_DEMO_PROTOCOL_TEXT = """Протокол встречи: Запуск проекта «Умный календарь»
Участники: Иван Сотрудников, Анна Петрова, Мария Кузнецова

Решили: утвердить план работ на ближайший квартал.
Решение: перейти на еженедельные статус-встречи по понедельникам.

Задача: подготовить техническое задание по интеграции, ответственный Иван, срок до пятницы.
Задача: согласовать бюджет с финансовым отделом, ответственный Мария.
Задача: собрать требования к отчётности, ответственная Анна, срок до 20 числа.

Риск: возможен сдвиг сроков из-за нехватки ресурсов в команде разработки.

Следующая встреча: обсудить готовность ТЗ на следующей неделе.
Следующая встреча: демонстрация первого прототипа через две недели.
"""


def _seed_document(db: Session, users: dict[str, User], now: datetime) -> None:
    """Один demo-документ (как будто загруженный протокол встречи)."""
    ivan = users["user@demo.local"]
    db.add(
        Document(
            owner_id=ivan.id,
            filename="Протокол_запуск_проекта.txt",
            content_type="text/plain",
            size_bytes=len(_DEMO_PROTOCOL_TEXT.encode("utf-8")),
            text=_DEMO_PROTOCOL_TEXT,
            created_at=now - timedelta(hours=3),
        )
    )
    db.commit()


def _seed_audit(db: Session, users: dict[str, User], admin: User, now: datetime) -> None:
    """Пара записей журнала (в т.ч. переносы) — чтобы статистика была живой."""
    ivan = users["user@demo.local"]
    entries = [
        (admin.id, "reschedule_event", "event", 3, {"reason": "конфликт приоритетов"}, 120),
        (ivan.id, "reschedule_event", "event", 4, {"reason": "запрос заказчика"}, 60),
        (ivan.id, "search_tickets", "travel", None, {"origin": "Москва", "destination": "Санкт-Петербург"}, 45),
    ]
    for actor_id, action, etype, eid, payload, mins_ago in entries:
        db.add(
            AuditLog(
                actor_user_id=actor_id,
                action=action,
                entity_type=etype,
                entity_id=eid,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now - timedelta(minutes=mins_ago),
            )
        )
    db.commit()


def _seed_demo(db: Session, settings: Settings) -> None:
    """Идемпотентное demo-наполнение. Маркер — наличие demo-пользователя."""
    if not settings.demo.seed_on_startup:
        return
    # BUG-23: маркер «уже наполняли» — demo-пользователь, а не встречи.
    # Иначе после удаления всех встреч рестарт возвращал demo-данные.
    if users_service.get_by_email(db, _DEMO_USERS[0][0]) is not None:
        return

    admin = users_service.get_by_email(db, settings.seed_admin.email)
    if admin is None:
        return

    now = local_now().replace(second=0, microsecond=0)
    tz = settings.app.timezone

    users = _ensure_demo_users(db)
    _seed_events(db, users, admin, tz, now)
    _seed_notifications(db, users, admin, now)
    _seed_document(db, users, now)
    _seed_audit(db, users, admin, now)
    logger.info(
        "Demo-данные созданы: %d пользователей, встречи на 2 недели, уведомления и документ. "
        "Вход: admin=%s / пользователи=%s (пароль %s).",
        len(users) + 1,
        settings.seed_admin.email,
        ", ".join(e for e, _ in _DEMO_USERS),
        _DEMO_PASSWORD,
    )


def bootstrap() -> None:
    """Полная инициализация приложения (идемпотентна)."""
    from app.services.assistant import orchestrator

    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        created = users_service.ensure_seed_admin(db, settings)
        if created is not None:
            logger.info("Создан seed-админ: %s", created.email)
        _seed_demo(db, settings)
        # BUG-22: протухшие pending-черновики помечаем expired, чтобы не копились.
        expired = orchestrator.expire_stale_actions(db)
        if expired:
            logger.info("Помечено просроченных черновиков действий: %d", expired)
