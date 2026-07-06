"""Подключение к БД и управление сессиями SQLAlchemy.

БД задаётся через YAML (``database.url``). По умолчанию — локальный SQLite,
но URL можно заменить на PostgreSQL/MySQL без изменения кода.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import BASE_DIR, get_settings


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


def _make_engine():
    settings = get_settings()
    url = settings.database.url

    # Для SQLite: гарантируем, что каталог для файла БД существует, и
    # разрешаем доступ из разных потоков (uvicorn --reload / threadpool).
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # sqlite:///./data/app.db -> относительный путь от корня проекта
        prefix = "sqlite:///"
        if url.startswith(prefix):
            db_path = url[len(prefix):]
            if db_path and not db_path.startswith(":memory:"):
                abs_path = (BASE_DIR / db_path).resolve()
                abs_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(
        url,
        echo=settings.database.echo,
        connect_args=connect_args,
        future=True,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Создать все таблицы (лёгкая «миграция» для MVP).

    Импорт моделей обязателен, чтобы они зарегистрировались в ``Base.metadata``.
    """
    from app import models  # noqa: F401  (регистрация моделей)

    Base.metadata.create_all(bind=engine)
