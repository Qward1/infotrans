"""Сервис аутентификации."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.user import User
from app.services import users as users_service


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Вернуть пользователя, если email/пароль верны и аккаунт активен."""
    user = users_service.get_by_email(db, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
