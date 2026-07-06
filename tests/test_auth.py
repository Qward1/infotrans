"""Тесты утилит паролей и сервиса аутентификации."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.security import hash_password, verify_password
from app.models.user import ROLE_USER, User
from app.services import auth as auth_service


def test_hash_and_verify():
    h = hash_password("s3cret-pass")
    assert h and h != "s3cret-pass"
    assert verify_password("s3cret-pass", h) is True
    assert verify_password("wrong", h) is False
    # Схема по умолчанию — pbkdf2_sha256 (переносимо, без нативных зависимостей).
    assert h.startswith("$pbkdf2-sha256$")


def test_verify_empty_hash():
    assert verify_password("anything", "") is False


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_authenticate_flow(db):
    user = User(
        email="jane@test.local",
        full_name="Jane",
        password_hash=hash_password("password1"),
        role=ROLE_USER,
        is_active=True,
    )
    db.add(user)
    db.commit()

    assert auth_service.authenticate(db, "jane@test.local", "password1") is not None
    # email нечувствителен к регистру
    assert auth_service.authenticate(db, "JANE@test.local", "password1") is not None
    # неверный пароль
    assert auth_service.authenticate(db, "jane@test.local", "nope") is None
    # неизвестный пользователь
    assert auth_service.authenticate(db, "ghost@test.local", "x") is None


def test_inactive_user_cannot_authenticate(db):
    user = User(
        email="bob@test.local",
        password_hash=hash_password("password1"),
        role=ROLE_USER,
        is_active=False,
    )
    db.add(user)
    db.commit()
    assert auth_service.authenticate(db, "bob@test.local", "password1") is None
