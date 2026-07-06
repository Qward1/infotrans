"""Утилиты безопасности: хэширование паролей.

По умолчанию используется ``pbkdf2_sha256`` — реализация из стандартной
библиотеки Python (через passlib), без нативных зависимостей, что делает
backend максимально переносимым. Хэши ``bcrypt`` при этом тоже проверяются,
если такие пользователи уже заведены.
"""
from __future__ import annotations

from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated="auto",
    default="pbkdf2_sha256",
)


def hash_password(plain: str) -> str:
    """Вернуть хэш пароля (для хранения в БД)."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против хранимого хэша."""
    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        # Неизвестный/повреждённый формат хэша.
        return False


def needs_rehash(hashed: str) -> bool:
    """Нужно ли перехэшировать пароль (например, при смене схемы)."""
    try:
        return pwd_context.needs_update(hashed)
    except ValueError:
        return True
