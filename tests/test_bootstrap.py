"""Тесты demo-наполнения: идемпотентность и маркер seed (BUG-23)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bootstrap import _seed_demo
from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.calendar import CalendarEvent
from app.models.user import ROLE_ADMIN, User

SETTINGS = Settings()  # seed_admin по умолчанию: admin@demo.local


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
def admin(db):
    u = User(email=SETTINGS.seed_admin.email, full_name="Admin",
             password_hash=hash_password("x"), role=ROLE_ADMIN, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_seed_demo_populates_empty_db(db, admin):
    _seed_demo(db, SETTINGS)
    assert db.query(CalendarEvent).count() > 0
    assert db.query(User).filter_by(email="user@demo.local").first() is not None


def test_seed_demo_not_repeated_after_events_deleted(db, admin):
    """BUG-23: пользователь удалил все встречи → рестарт НЕ возвращает demo-данные."""
    _seed_demo(db, SETTINGS)
    db.query(CalendarEvent).delete()
    db.commit()
    assert db.query(CalendarEvent).count() == 0

    _seed_demo(db, SETTINGS)  # повторный bootstrap
    assert db.query(CalendarEvent).count() == 0, "demo-встречи не должны пересоздаваться"


def test_seed_demo_respects_flag(db, admin):
    settings = Settings()
    settings.demo.seed_on_startup = False
    _seed_demo(db, settings)
    assert db.query(CalendarEvent).count() == 0
