"""JSON API: чат, календарь, события, пользователи, статистика, билеты."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import require_admin, require_user
from app.models.user import User
from app.routers.calendar import calendar_payload, resolve_calendar_owner
from app.schemas.assistant import (
    AssistantChatCreate,
    AssistantChatMessageCreate,
    AssistantChatUpdate,
    ChatRequest,
)
from app.schemas.calendar import EventCreate, EventOut, EventUpdate
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services import audit as audit_service
from app.services import calendar as calendar_service
from app.services import event_notifications
from app.services import scheduling as scheduling_service
from app.services import stats as stats_service
from app.services import tickets as tickets_service
from app.services import users as users_service
from app.services.assistant import (
    calendar_context,
    chat_history,
    document_reader,
    notification_service,
    orchestrator,
)
from app.services.assistant.schemas import AssistantResult
from app.services.assistant.travel_search import (
    TicketProviderError,
    TicketProviderNotConfigured,
    TicketValidationError,
)

router = APIRouter(prefix="/api", tags=["api"])


# --------------------------------------------------------------------------- #
# Чат / ассистент                                                             #
# --------------------------------------------------------------------------- #
@router.post("/chat", response_model=AssistantResult)
def api_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    if payload.conversation_id:
        chat = chat_history.get_accessible_chat(db, user, payload.conversation_id)
        if chat is None or chat.is_archived:
            raise HTTPException(status_code=404, detail="Чат не найден")
        if chat.user_id != user.id:
            raise HTTPException(status_code=403, detail="Нельзя писать в чужой чат")
    else:
        chat = chat_history.create_chat(db, user.id, chat_history.title_from_message(payload.message))

    chat_history.add_message(db, chat, "user", payload.message)
    result = orchestrator.run(settings, db, user, payload.message, chat.id)
    result.conversation_id = chat.id
    chat_history.add_message(db, chat, "assistant", result.reply, result.model_dump(mode="json"))
    audit_service.record(
        db,
        actor_user_id=user.id,
        action="chat",
        entity_type="assistant_chat",
        entity_id=None,
        payload={"intent": result.intent, "mode": result.mode, "status": result.status},
    )
    return result


@router.get("/assistant/chats")
def api_list_assistant_chats(
    user_id: int | None = Query(default=None),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if user_id is not None and user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Нет доступа к чатам пользователя")
    if user_id is not None and user_id != user.id:
        target = users_service.get_by_id(db, user_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
    chats = chat_history.list_chats(db, user, user_id=user_id, include_archived=include_archived)
    return {"items": [chat_history.serialize_chat(chat, viewer=user) for chat in chats]}


@router.post("/assistant/chats", status_code=201)
def api_create_assistant_chat(
    payload: AssistantChatCreate | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    chat = chat_history.create_chat(db, user.id, payload.title if payload else None)
    return chat_history.serialize_chat(chat, include_messages=True, viewer=user)


@router.get("/assistant/chats/{chat_id}")
def api_get_assistant_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    chat = chat_history.get_accessible_chat(db, user, chat_id)
    if chat is None or chat.is_archived:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return chat_history.serialize_chat(chat, include_messages=True, viewer=user)


@router.patch("/assistant/chats/{chat_id}")
def api_update_assistant_chat(
    chat_id: str,
    payload: AssistantChatUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    chat = chat_history.get_accessible_chat(db, user, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.user_id != user.id:
        raise HTTPException(status_code=403, detail="Нельзя изменять чужой чат")
    try:
        if payload.title is not None:
            chat = chat_history.rename_chat(db, chat, payload.title)
        if payload.is_archived is not None:
            chat = chat_history.set_archived(db, chat, payload.is_archived)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return chat_history.serialize_chat(chat, include_messages=True, viewer=user)


@router.delete("/assistant/chats/{chat_id}", status_code=204)
def api_delete_assistant_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    chat = chat_history.get_accessible_chat(db, user, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.user_id != user.id:
        raise HTTPException(status_code=403, detail="Нельзя удалять чужой чат")
    chat_history.delete_chat(db, chat)
    return None


@router.post("/assistant/chats/{chat_id}/messages", status_code=201)
def api_add_assistant_chat_message(
    chat_id: str,
    payload: AssistantChatMessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    chat = chat_history.get_accessible_chat(db, user, chat_id)
    if chat is None or chat.is_archived:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.user_id != user.id:
        raise HTTPException(status_code=403, detail="Нельзя писать в чужой чат")
    if payload.role in {"system", "tool"} and not user.is_admin:
        raise HTTPException(status_code=403, detail="Недопустимая роль сообщения")
    try:
        message = chat_history.add_message(db, chat, payload.role, payload.content, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return chat_history.serialize_message(message, viewer=user)


@router.post("/assistant/actions/{action_id}/confirm")
def api_confirm_action(
    action_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    result = orchestrator.confirm_action(db, settings, user, action_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail", "Не удалось выполнить действие"))
    return result


@router.post("/assistant/actions/{action_id}/reject")
def api_reject_action(
    action_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    result = orchestrator.reject_action(db, user, action_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("detail", "Действие не найдено"))
    return result


# --------------------------------------------------------------------------- #
# Уведомления                                                                  #
# --------------------------------------------------------------------------- #
@router.get("/notifications")
def api_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    notes = notification_service.list_for_user(db, user.id)
    return {
        "unread": notification_service.unread_count(db, user.id),
        "items": [
            {
                "id": n.id,
                "title": n.title,
                "text": n.text,
                "channel": n.channel,
                "status": n.status,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
    }


@router.post("/notifications/read")
def api_notifications_read(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    count = notification_service.mark_all_read(db, user.id)
    return {"ok": True, "marked": count}


@router.post("/notifications/{notification_id}/read")
def api_notification_read_one(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Пометить одно уведомление прочитанным (FN-07)."""
    if not notification_service.mark_read(db, user.id, notification_id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return {"ok": True, "unread": notification_service.unread_count(db, user.id)}


# --------------------------------------------------------------------------- #
# Документы / протоколы                                                        #
# --------------------------------------------------------------------------- #
@router.get("/documents")
def api_documents(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    docs = document_reader.list_for_user(db, user.id)
    return {
        "items": [
            {
                "id": d.id,
                "filename": d.filename,
                "content_type": d.content_type,
                "size_bytes": d.size_bytes,
                "has_text": bool(d.text and d.text.strip()),
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ]
    }


@router.post("/documents/{document_id}/protocol")
def api_document_protocol(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Сформировать протокол по ранее загруженному документу."""
    settings = get_settings()
    document = document_reader.get(db, document_id)
    if document is None or (document.owner_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Документ не найден")
    result = orchestrator.build_protocol_from_document(settings, db, user, document)
    payload = result.model_dump(mode="json")
    payload["document_id"] = document.id
    payload["filename"] = document.filename
    return payload


# --------------------------------------------------------------------------- #
# Календарь / события                                                         #
# --------------------------------------------------------------------------- #
@router.get("/calendar/week", response_model=list[EventOut])
def api_calendar_week(
    week: str | None = Query(default=None, description="Дата недели YYYY-MM-DD"),
    user_id: int | None = Query(default=None, description="Для admin: календарь пользователя"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ref = datetime.now()
    if week:
        try:
            ref = datetime.strptime(week, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="week должно быть в формате YYYY-MM-DD")
    owner = resolve_calendar_owner(db, user, user_id)
    _, _, events = calendar_service.list_week(db, owner.id, ref)
    return events


@router.get("/calendar/range")
def api_calendar_range(
    view: str = Query(default="week", description="day | week | month"),
    date: str | None = Query(default=None, description="Дата периода YYYY-MM-DD"),
    user_id: int | None = Query(default=None, description="Для admin: календарь пользователя"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ref = datetime.now()
    if date:
        try:
            ref = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date должно быть в формате YYYY-MM-DD")
    owner = resolve_calendar_owner(db, user, user_id)
    return calendar_payload(db, user, view, ref, owner)


def _get_owned_event(db: Session, user: User, event_id: int):
    event = calendar_service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    if event.owner_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Нет доступа к этому событию")
    return event


@router.post("/events", response_model=EventOut, status_code=201)
def api_create_event(
    payload: EventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    owner = resolve_calendar_owner(db, user, payload.owner_id)
    try:
        event = calendar_service.create_event(db, owner.id, payload, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # BUG-13: приглашения участникам (и владельцу, если создал админ) — как у ассистента.
    event_notifications.notify_created(db, get_settings(), event, user)
    audit_service.record(
        db, actor_user_id=user.id, action="create_event", entity_type="event", entity_id=event.id,
        payload={"title": event.title, "owner_user_id": event.owner_id, "created_by": user.id},
    )
    return event


@router.patch("/events/{event_id}", response_model=EventOut)
def api_update_event(
    event_id: int,
    payload: EventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    event = _get_owned_event(db, user, event_id)
    old_times = (event.start_at, event.end_at)
    try:
        event = calendar_service.update_event(db, event, payload, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # BUG-13: при переносе времени уведомляем участников и владельца.
    if (event.start_at, event.end_at) != old_times:
        event_notifications.notify_moved(db, get_settings(), event, user)
    audit_service.record(
        db,
        actor_user_id=user.id,
        action="update_event",
        entity_type="event",
        entity_id=event.id,
        payload={"owner_user_id": event.owner_id, "updated_by": user.id},
    )
    return event


@router.delete("/events/{event_id}", status_code=204)
def api_delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    event = _get_owned_event(db, user, event_id)
    owner_user_id = event.owner_id
    calendar_service.delete_event(db, event)
    audit_service.record(
        db,
        actor_user_id=user.id,
        action="delete_event",
        entity_type="event",
        entity_id=event_id,
        payload={"owner_user_id": owner_user_id, "deleted_by": user.id},
    )
    return None


# --------------------------------------------------------------------------- #
# Планирование (свободные слоты / конфликты)                                  #
# --------------------------------------------------------------------------- #
@router.get("/scheduling/free-slots")
def api_free_slots(
    days: int = Query(default=7, ge=1, le=60),
    duration: int = Query(default=60, ge=15, le=600),
    user_id: int | None = Query(default=None, description="Для admin: календарь пользователя"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    owner = resolve_calendar_owner(db, user, user_id)
    start = datetime.now()
    end = start + timedelta(days=days)
    slots = scheduling_service.free_slots_for_user(db, owner.id, start, end, duration)
    return [
        {"start_at": s.start.isoformat(), "end_at": s.end.isoformat(), "minutes": s.duration_minutes}
        for s in slots
    ]


@router.get("/assistant/employees/search")
def api_assistant_employee_search(
    q: str = Query(default="", description="Имя или email сотрудника"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    employees = calendar_context.search_employees(db, settings, user, q, limit=20)
    return {"items": [calendar_context.employee_summary(employee, settings) for employee in employees]}


@router.get("/assistant/employees/{user_id}/availability")
def api_assistant_employee_availability(
    user_id: int,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    range_start: str | None = Query(default=None, description="ISO datetime"),
    range_end: str | None = Query(default=None, description="ISO datetime"),
    duration: int | None = Query(default=None, ge=15, le=600),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    settings = get_settings()
    target = users_service.get_by_id(db, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    try:
        requested_range = calendar_context.parse_api_range(
            settings,
            date_value=date,
            range_start=range_start,
            range_end=range_end,
        )
        return calendar_context.employee_availability(
            db,
            settings,
            user,
            target,
            requested_range,
            requested_slot_duration=duration,
        )
    except calendar_context.CalendarAccessDenied:
        raise HTTPException(status_code=403, detail="Нет доступа к календарю пользователя")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# Билеты                                                                       #
# --------------------------------------------------------------------------- #
@router.get("/tickets/search")
def api_tickets_search(
    origin: str = Query(...),
    destination: str = Query(...),
    date: str = Query(..., description="YYYY-MM-DD"),
    return_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    transport: str = Query(default="any", description="any | flight | train | bus"),
    passengers: int = Query(default=1, ge=1, le=9),
    sort: str = Query(default="price", description="price | departure | duration"),
    user: User = Depends(require_user),
):
    settings = get_settings()
    try:
        try:
            depart = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date должно быть в формате YYYY-MM-DD")
        ret = None
        if return_date:
            try:
                ret = datetime.strptime(return_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="return_date должно быть в формате YYYY-MM-DD")
        external_searches = tickets_service.external_searches(
            origin,
            destination,
            depart,
            transport,
            return_date=ret,
            passengers=passengers,
            sort_by=sort,
        )
        if settings.tickets.mode == "sites":
            return {
                "items": [],
                "external_searches": external_searches,
                "source_mode": "sites",
                "message": "Откройте актуальный поиск на сайте-источнике. Цены и места будут показаны там.",
            }
        options = tickets_service.search(
            settings,
            origin,
            destination,
            depart,
            transport,
            return_date=ret,
            passengers=passengers,
            sort_by=sort,
        )
    except TicketValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except TicketProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TicketProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "items": [o.model_dump() for o in options],
        "external_searches": external_searches,
        "source_mode": settings.tickets.mode,
    }


# --------------------------------------------------------------------------- #
# Пользователи                                                                 #
# --------------------------------------------------------------------------- #
@router.get("/users", response_model=list[UserOut])
def api_list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # Обычный пользователь видит только себя; админ — всех.
    if user.is_admin:
        return users_service.list_users(db)
    return [user]


@router.post("/admin/users", response_model=UserOut, status_code=201)
def api_create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        created = users_service.create_user(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record(
        db, actor_user_id=admin.id, action="create_user", entity_type="user", entity_id=created.id,
        payload={"email": created.email, "role": created.role},
    )
    return created


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def api_update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = users_service.get_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    # Защита «последнего админа»: нельзя разжаловать/отключить единственного активного
    # администратора (иначе можно потерять доступ к админке).
    demoting = target.role == "admin" and payload.role is not None and payload.role != "admin"
    deactivating = target.is_active and payload.is_active is False and target.role == "admin"
    if (demoting or deactivating) and users_service.count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Это единственный активный администратор — нельзя снять роль или отключить его.",
        )
    try:
        updated = users_service.update_user(db, target, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record(
        db, actor_user_id=admin.id, action="update_user", entity_type="user", entity_id=updated.id,
        payload={"role": updated.role, "is_active": updated.is_active},
    )
    return updated


# --------------------------------------------------------------------------- #
# Статистика                                                                   #
# --------------------------------------------------------------------------- #
@router.get("/admin/stats")
def api_admin_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return {
        "overview": stats_service.system_overview(db),
        "status_breakdown": stats_service.status_breakdown(db),
        "per_user": stats_service.per_user_stats(db),
    }
