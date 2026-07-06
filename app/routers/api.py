"""JSON API: чат, календарь, события, пользователи, статистика, билеты."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import require_admin, require_user
from app.models.user import User
from app.schemas.assistant import ChatRequest
from app.schemas.calendar import EventCreate, EventOut, EventUpdate
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services import audit as audit_service
from app.services import calendar as calendar_service
from app.services import scheduling as scheduling_service
from app.services import stats as stats_service
from app.services import tickets as tickets_service
from app.services import users as users_service
from app.services.assistant import document_reader, notification_service, orchestrator
from app.services.assistant.schemas import AssistantResult

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
    result = orchestrator.run(settings, db, user, payload.message, payload.conversation_id)
    audit_service.record(
        db,
        actor_user_id=user.id,
        action="chat",
        entity_type="assistant",
        payload={"intent": result.intent, "mode": result.mode, "status": result.status},
    )
    return result


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
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ref = datetime.now()
    if week:
        try:
            ref = datetime.strptime(week, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="week должно быть в формате YYYY-MM-DD")
    _, _, events = calendar_service.list_week(db, user.id, ref)
    return events


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
    event = calendar_service.create_event(db, user.id, payload)
    audit_service.record(
        db, actor_user_id=user.id, action="create_event", entity_type="event", entity_id=event.id,
        payload={"title": event.title},
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
    try:
        event = calendar_service.update_event(db, event, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_service.record(
        db, actor_user_id=user.id, action="update_event", entity_type="event", entity_id=event.id
    )
    return event


@router.delete("/events/{event_id}", status_code=204)
def api_delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    event = _get_owned_event(db, user, event_id)
    calendar_service.delete_event(db, event)
    audit_service.record(
        db, actor_user_id=user.id, action="delete_event", entity_type="event", entity_id=event_id
    )
    return None


# --------------------------------------------------------------------------- #
# Планирование (свободные слоты / конфликты)                                  #
# --------------------------------------------------------------------------- #
@router.get("/scheduling/free-slots")
def api_free_slots(
    days: int = Query(default=7, ge=1, le=60),
    duration: int = Query(default=60, ge=15, le=600),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    start = datetime.now()
    end = start + timedelta(days=days)
    slots = scheduling_service.free_slots_for_user(db, user.id, start, end, duration)
    return [
        {"start_at": s.start.isoformat(), "end_at": s.end.isoformat(), "minutes": s.duration_minutes}
        for s in slots
    ]


# --------------------------------------------------------------------------- #
# Билеты                                                                       #
# --------------------------------------------------------------------------- #
@router.get("/tickets/search")
def api_tickets_search(
    origin: str = Query(...),
    destination: str = Query(...),
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    transport: str = Query(default="any", description="flight | train | any"),
    user: User = Depends(require_user),
):
    settings = get_settings()
    depart = None
    if date:
        try:
            depart = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date должно быть в формате YYYY-MM-DD")
    options = tickets_service.search(settings, origin, destination, depart, transport)
    return [o.model_dump() for o in options]


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
