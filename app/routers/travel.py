"""Страница «Поиск билетов / поездок»."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request

from app.core.config import get_settings
from app.core.permissions import require_user
from app.models.user import User
from app.templating import render

router = APIRouter(tags=["travel"])


@router.get("/travel")
def travel_page(request: Request, user: User = Depends(require_user)):
    settings = get_settings()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return render(
        request,
        "travel.html",
        current_user=user,
        active="travel",
        currency=settings.tickets.currency,
        tickets_mode=settings.tickets.mode,
        default_date=tomorrow,
    )
