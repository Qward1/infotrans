"""Страница «Документы / Протоколы»: загрузка и разбор документов встреч."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_user
from app.models.user import User
from app.services.assistant import document_reader
from app.templating import render

router = APIRouter(tags=["documents"])


@router.get("/documents")
def documents_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    docs = document_reader.list_for_user(db, user.id)
    return render(
        request,
        "documents.html",
        current_user=user,
        active="documents",
        documents=docs,
    )
