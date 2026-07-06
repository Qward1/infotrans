"""Страница «Уведомления»: полный список с каналом, статусом и связанной встречей."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_user
from app.models.user import User
from app.services import calendar as calendar_service
from app.services.assistant import notification_service
from app.templating import render

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
def notifications_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    notes = notification_service.list_for_user(db, user.id, limit=100)
    items = []
    for n in notes:
        related = None
        try:
            meta = json.loads(n.meta_json or "{}")
        except (ValueError, TypeError):
            meta = {}
        event_id = meta.get("event_id")
        if event_id:
            event = calendar_service.get_event(db, int(event_id))
            if event is not None:
                related = {"id": event.id, "title": event.title, "start_at": event.start_at}
        items.append({"note": n, "related": related})
    return render(
        request,
        "notifications.html",
        current_user=user,
        active="notifications",
        items=items,
        unread=notification_service.unread_count(db, user.id),
    )
