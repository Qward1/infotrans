"""Сервис журналирования действий (audit log)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    entity_type: str = "",
    entity_id: int | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditLog:
    """Записать событие в журнал аудита."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    return entry


def list_recent(db: Session, limit: int = 100) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())
