"""История чатов ассистента с проверкой владельца."""
from __future__ import annotations

from app.core.clock import local_now

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from app.models.assistant import (
    CHAT_ROLES,
    AssistantAction,
    AssistantChat,
    AssistantChatMessage,
)
from app.models.user import User

DEFAULT_TITLE = "Новый чат"
TITLE_MAX = 80
PUBLIC_MESSAGE_ROLES = {"user", "assistant"}
PUBLIC_PAYLOAD_KEYS = {
    "reply",
    "intent",
    "mode",
    "status",
    "conversation_id",
    "cards",
    "suggested_actions",
    "created_event",
    "updated_event",
    "alternative_slots",
    "travel_options",
    "protocol",
    "warnings",
}
DIAGNOSTIC_PAYLOAD_KEYS = {"intent", "mode", "status", "tool", "error", "detail"}


def title_from_message(message: str) -> str:
    """Сгенерировать короткое название по первому пользовательскому сообщению."""
    title = " ".join((message or "").split())
    if not title:
        return DEFAULT_TITLE
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rstrip() + "…"
    return title


def create_chat(db: Session, user_id: int, title: str | None = None) -> AssistantChat:
    chat = AssistantChat(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=(title or DEFAULT_TITLE).strip() or DEFAULT_TITLE,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def list_chats(
    db: Session,
    user: User,
    *,
    user_id: int | None = None,
    include_archived: bool = False,
) -> list[AssistantChat]:
    target_user_id = user.id if user_id is None else user_id
    if target_user_id != user.id and not user.is_admin:
        return []
    stmt = select(AssistantChat).where(AssistantChat.user_id == target_user_id)
    if not include_archived:
        stmt = stmt.where(AssistantChat.is_archived.is_(False))
    stmt = stmt.order_by(AssistantChat.updated_at.desc(), AssistantChat.created_at.desc())
    return list(db.scalars(stmt).all())


def get_chat(db: Session, chat_id: str) -> AssistantChat | None:
    stmt = (
        select(AssistantChat)
        .options(selectinload(AssistantChat.messages))
        .where(AssistantChat.id == chat_id)
    )
    return db.scalars(stmt).first()


def get_accessible_chat(db: Session, user: User, chat_id: str) -> AssistantChat | None:
    chat = get_chat(db, chat_id)
    if chat is None:
        return None
    if chat.user_id != user.id and not user.is_admin:
        return None
    return chat


def add_message(
    db: Session,
    chat: AssistantChat,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> AssistantChatMessage:
    if role not in CHAT_ROLES:
        raise ValueError(f"Недопустимая роль сообщения: {role}")
    msg = AssistantChatMessage(
        chat_id=chat.id,
        role=role,
        content=content or "",
        payload_json=json.dumps(payload or {}, ensure_ascii=False) if payload else "",
    )
    chat.updated_at = local_now()
    if chat.title == DEFAULT_TITLE and role == "user":
        chat.title = title_from_message(content)
    db.add(msg)
    db.add(chat)
    db.commit()
    db.refresh(msg)
    db.refresh(chat)
    return msg


def rename_chat(db: Session, chat: AssistantChat, title: str) -> AssistantChat:
    cleaned = " ".join((title or "").split())
    if not cleaned:
        raise ValueError("Название чата не может быть пустым")
    chat.title = cleaned[:255]
    chat.updated_at = local_now()
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def set_archived(db: Session, chat: AssistantChat, archived: bool = True) -> AssistantChat:
    chat.is_archived = archived
    chat.updated_at = local_now()
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def delete_chat(db: Session, chat: AssistantChat) -> None:
    db.delete(chat)
    db.commit()


def message_payload(message: AssistantChatMessage) -> dict[str, Any]:
    if not message.payload_json:
        return {}
    try:
        data = json.loads(message.payload_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _safe_payload(message: AssistantChatMessage, viewer: User | None = None) -> dict[str, Any]:
    payload = message_payload(message)
    if not payload:
        return {}
    if message.role in PUBLIC_MESSAGE_ROLES:
        if viewer is not None and not viewer.is_admin:
            return {key: payload[key] for key in PUBLIC_PAYLOAD_KEYS if key in payload}
        return payload
    return {key: payload[key] for key in DIAGNOSTIC_PAYLOAD_KEYS if key in payload}


def serialize_message(message: AssistantChatMessage, *, viewer: User | None = None) -> dict[str, Any]:
    return {
        "id": message.id,
        "chatId": message.chat_id,
        "role": message.role,
        "content": message.content,
        "payload": _safe_payload(message, viewer),
        "createdAt": message.created_at.isoformat(),
    }


def _action_states(db: Session, serialized_messages: list[dict[str, Any]]) -> dict[str, str]:
    """Актуальные статусы действий, упомянутых в сообщениях (BUG-09).

    История хранит snapshot ответа с кнопками confirm/reject; статус действия
    с тех пор мог измениться — собираем свежие статусы одним запросом."""
    action_ids: set[str] = set()
    for message in serialized_messages:
        payload = message.get("payload") or {}
        for action in payload.get("suggested_actions") or []:
            if isinstance(action, dict) and action.get("action_id"):
                action_ids.add(action["action_id"])
    if not action_ids:
        return {}
    rows = db.execute(
        select(AssistantAction.action_id, AssistantAction.status).where(
            AssistantAction.action_id.in_(action_ids)
        )
    ).all()
    return {action_id: status for action_id, status in rows}


def serialize_chat(
    chat: AssistantChat,
    *,
    include_messages: bool = False,
    viewer: User | None = None,
) -> dict[str, Any]:
    payload = {
        "id": chat.id,
        "userId": chat.user_id,
        "title": chat.title,
        "createdAt": chat.created_at.isoformat(),
        "updatedAt": chat.updated_at.isoformat(),
        "isArchived": chat.is_archived,
    }
    if include_messages:
        messages = sorted(chat.messages, key=lambda message: (message.created_at, message.id or 0))
        if viewer is not None and not viewer.is_admin:
            messages = [message for message in messages if message.role in PUBLIC_MESSAGE_ROLES]
        serialized = [serialize_message(message, viewer=viewer) for message in messages]
        db = object_session(chat)
        states = _action_states(db, serialized) if db is not None else {}
        if states:
            for message in serialized:
                if message.get("payload"):
                    message["payload"]["actions_state"] = states
        payload["messages"] = serialized
    return payload
