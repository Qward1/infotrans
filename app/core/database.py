"""Подключение к БД и управление сессиями SQLAlchemy.

БД задаётся через YAML (``database.url``). По умолчанию — локальный SQLite,
но URL можно заменить на PostgreSQL/MySQL без изменения кода.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
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
    _run_lightweight_migrations()


def _run_lightweight_migrations() -> None:
    """Минимальные ALTER TABLE / CREATE INDEX для существующих MVP-БД без Alembic."""
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "calendar_events" not in table_names:
            return
        columns = {col["name"] for col in inspector.get_columns("calendar_events")}
        if "created_by_id" not in columns:
            conn.execute(text("ALTER TABLE calendar_events ADD COLUMN created_by_id INTEGER"))
        if "updated_by_id" not in columns:
            conn.execute(text("ALTER TABLE calendar_events ADD COLUMN updated_by_id INTEGER"))
        conn.execute(
            text(
                "UPDATE calendar_events "
                "SET created_by_id = owner_id "
                "WHERE created_by_id IS NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE calendar_events "
                "SET updated_by_id = owner_id "
                "WHERE updated_by_id IS NULL"
            )
        )
        # ARCH-07: индексы под реальные запросы (create_all не добавляет их к
        # существующим таблицам; для новых БД продублированы в __table_args__).
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_events_owner_start "
            "ON calendar_events (owner_id, start_at)"
        ))
        if "notifications" in table_names:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_notifications_user_status "
                "ON notifications (user_id, status)"
            ))
        if "event_participants" in table_names:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_participants_user "
                "ON event_participants (user_id, event_id)"
            ))
