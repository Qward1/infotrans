"""Страница чат-ассистента и загрузка документов."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import require_user
from app.models.user import User
from app.services import audit as audit_service
from app.services import users as users_service
from app.services.assistant import chat_history, document_reader, orchestrator
from app.templating import render

router = APIRouter(tags=["chat"])


@router.get("/chat")
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    dify = settings.assistant.dify
    mode = "dify" if dify.enabled else "local"
    return render(
        request,
        "chat.html",
        current_user=user,
        active="chat",
        assistant_mode=mode,
        chat_users=users_service.list_active_users(db) if user.is_admin else [],
    )


@router.post("/chat/upload")
async def chat_upload(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Приём документа встречи: извлекаем текст, сохраняем, собираем протокол."""
    settings = get_settings()
    if conversation_id:
        chat = chat_history.get_accessible_chat(db, user, conversation_id)
        if chat is None or chat.is_archived:
            raise HTTPException(status_code=404, detail="Чат не найден")
        if chat.user_id != user.id:
            raise HTTPException(status_code=403, detail="Нельзя писать в чужой чат")
    else:
        chat = chat_history.create_chat(db, user.id, chat_history.title_from_message(file.filename or "Документ"))
    chat_history.add_message(db, chat, "user", "📎 " + (file.filename or "Документ"))

    raw = await file.read()
    extracted = document_reader.extract(file.filename or "file", file.content_type or "", raw)
    document = document_reader.save(db, owner_id=user.id, extracted=extracted)
    audit_service.record(
        db,
        actor_user_id=user.id,
        action="upload_document",
        entity_type="document",
        entity_id=document.id,
        payload={"filename": document.filename, "size": document.size_bytes},
    )
    # Сразу генерируем протокол по загруженному документу.
    result = orchestrator.build_protocol_from_document(settings, db, user, document)
    result.conversation_id = chat.id
    payload = result.model_dump(mode="json")
    payload["document_id"] = document.id
    payload["warnings"] = list(extracted.warnings) + list(result.warnings)
    payload["filename"] = document.filename
    payload["size_bytes"] = document.size_bytes
    chat_history.add_message(db, chat, "assistant", result.reply, payload)
    return payload
