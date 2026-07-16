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

# BUG-14: жёсткий лимит размера загружаемого документа.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ


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
    filename = file.filename or "file"
    # BUG-14: неподдерживаемое расширение отклоняем ДО чтения файла в память.
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in document_reader.SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(document_reader.SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Формат «{ext or 'без расширения'}» не поддерживается. Загрузите {supported}.",
        )
    # Лимит размера: читаем чанками, чтобы не держать в памяти сверхлимитный файл.
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ — загрузите документ меньшего размера.",
            )
        chunks.append(chunk)
    raw = b"".join(chunks)

    if conversation_id:
        chat = chat_history.get_accessible_chat(db, user, conversation_id)
        if chat is None or chat.is_archived:
            raise HTTPException(status_code=404, detail="Чат не найден")
        if chat.user_id != user.id:
            raise HTTPException(status_code=403, detail="Нельзя писать в чужой чат")
    else:
        chat = chat_history.create_chat(db, user.id, chat_history.title_from_message(filename))
    chat_history.add_message(db, chat, "user", "📎 " + filename)

    extracted = document_reader.extract(filename, file.content_type or "", raw)
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
