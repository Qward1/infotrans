"""Сервис пользователей: CRUD и seed-админ."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password
from app.models.user import ROLE_ADMIN, User
from app.schemas.user import UserCreate, UserUpdate


def get_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    email = (email or "").strip().lower()
    stmt = select(User).where(func.lower(User.email) == email)
    return db.execute(stmt).scalars().first()


def list_users(db: Session) -> list[User]:
    stmt = select(User).order_by(User.created_at.asc(), User.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_active_users(db: Session) -> list[User]:
    stmt = (
        select(User)
        .where(User.is_active.is_(True))
        .order_by(User.full_name.asc(), User.email.asc(), User.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def search_users(db: Session, query: str = "", *, active_only: bool = True, limit: int = 20) -> list[User]:
    """Поиск сотрудников по имени/email для админских UI и assistant tools."""
    users = list_active_users(db) if active_only else list_users(db)
    q = " ".join((query or "").strip().lower().split())
    if not q:
        return users[:limit]
    matched = [
        user for user in users
        if q in (user.full_name or "").lower() or q in user.email.lower()
    ]
    return matched[:limit]


def count_users(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(User)).scalar_one())


def count_active_admins(db: Session) -> int:
    """Число активных администраторов (для защиты «последнего админа»)."""
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == ROLE_ADMIN, User.is_active.is_(True))
    )
    return int(db.execute(stmt).scalar_one())


def create_user(db: Session, data: UserCreate) -> User:
    if get_by_email(db, data.email):
        raise ValueError("Пользователь с таким email уже существует")
    user = User(
        email=data.email.strip().lower(),
        full_name=data.full_name.strip(),
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=data.is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user: User, data: UserUpdate) -> User:
    if data.email is not None and data.email != user.email:
        clash = get_by_email(db, data.email)
        if clash is not None and clash.id != user.id:
            raise ValueError("Пользователь с таким email уже существует")
        user.email = data.email
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password:
        user.password_hash = hash_password(data.password)
    db.commit()
    db.refresh(user)
    return user


def ensure_seed_admin(db: Session, settings: Settings) -> User | None:
    """Создать первого администратора, если пользователей ещё нет.

    Если seed-админ уже существует — пароль НЕ перезаписывается.
    Возвращает созданного пользователя либо None, если ничего не делали.
    """
    seed = settings.seed_admin
    existing = get_by_email(db, seed.email)
    if existing is not None:
        return None
    # Создаём seed-админа только когда таблица пуста, чтобы не плодить админов.
    if count_users(db) > 0:
        return None
    admin = User(
        email=seed.email.strip().lower(),
        full_name=seed.full_name.strip() or "Administrator",
        password_hash=hash_password(seed.password),
        role=ROLE_ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
