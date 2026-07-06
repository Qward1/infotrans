"""Роуты аутентификации: страница входа, вход, выход."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import (
    get_current_user_optional,
    login_user,
    logout_user,
)
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.templating import render

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html", active="login")


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth_service.authenticate(db, email, password)
    if user is None:
        return render(
            request,
            "login.html",
            active="login",
            error="Неверный email или пароль, либо аккаунт отключён.",
            email=email,
        )
    login_user(request, user)
    audit_service.record(
        db, actor_user_id=user.id, action="login", entity_type="user", entity_id=user.id
    )
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if user is not None:
        audit_service.record(
            db, actor_user_id=user.id, action="logout", entity_type="user", entity_id=user.id
        )
    logout_user(request)
    return RedirectResponse("/login", status_code=303)
