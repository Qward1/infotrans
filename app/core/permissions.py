"""Зависимости авторизации и проверки ролей.

Сессия хранится в подписанном cookie (Starlette ``SessionMiddleware``).
``request.session["user_id"]`` — идентификатор вошедшего пользователя.
"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User


class NotAuthenticated(Exception):
    """Пользователь не вошёл в систему."""


class NotAuthorized(Exception):
    """Недостаточно прав (нужна роль admin)."""


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Текущий пользователь или None (для страниц, где вход не обязателен)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Требуется вошедший активный пользователь."""
    user = get_current_user_optional(request, db)
    if user is None:
        raise NotAuthenticated()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """Требуется роль admin."""
    if not user.is_admin:
        raise NotAuthorized()
    return user


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()
