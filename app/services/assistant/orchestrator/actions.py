"""Подтверждение / отклонение черновиков действий (паттерн подтверждения)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.clock import local_now
from app.models.assistant import (
    ACTION_CANCEL_EVENT,
    ACTION_CONFIRMED,
    ACTION_CREATE_EVENT,
    ACTION_CREATE_EVENTS_FROM_PROTOCOL,
    ACTION_CREATE_REMINDER,
    ACTION_DELETE_EVENT,
    ACTION_EXPIRED,
    ACTION_IN_PROGRESS,
    ACTION_MOVE_EVENT,
    ACTION_PENDING,
    ACTION_REJECTED,
    ACTION_UPDATE_EVENT,
    AssistantAction,
)
from app.models.calendar import STATUS_CANCELLED, CalendarEvent
from app.models.reminder import Reminder
from app.models.user import User
from app.schemas.calendar import EventUpdate
from app.services import audit as audit_service
from app.services import calendar as calendar_service
from app.services import conflict_resolver, event_notifications
from app.services.assistant import notification_service
from app.services.assistant.orchestrator.common import (
    create_event_row,
    logger,
    resolve_participant_ids,
)
from app.services.assistant.orchestrator.serializers import event_out


def _get_action(db, user, action_id) -> AssistantAction | None:
    action = db.execute(
        select(AssistantAction).where(AssistantAction.action_id == action_id)
    ).scalars().first()
    if action is None or action.user_id != user.id:
        return None
    return action


def _ensure_event_mutable(event: CalendarEvent, user: User) -> None:
    if event.owner_id != user.id and not user.is_admin:
        raise ValueError("Нет доступа к этому событию")


def reject_action(db: Session, user: User, action_id: str) -> dict:
    action = _get_action(db, user, action_id)
    if action is None:
        return {"ok": False, "detail": "Действие не найдено"}
    if action.status == ACTION_PENDING:
        action.status = ACTION_REJECTED
        db.commit()
    return {"ok": True, "status": action.status}


def expire_stale_actions(db: Session, now: datetime | None = None) -> int:
    """Пометить протухшие pending-черновики как expired (BUG-22, один UPDATE)."""
    now = now or local_now()
    result = db.execute(
        update(AssistantAction)
        .where(
            AssistantAction.status == ACTION_PENDING,
            AssistantAction.expires_at.is_not(None),
            AssistantAction.expires_at < now,
        )
        .values(status=ACTION_EXPIRED)
    )
    db.commit()
    return int(result.rowcount or 0)


def _recheck_conflicts(db, settings, user, action, payload) -> list | None:
    """Повторная проверка конфликтов перед исполнением (BUG-06).

    Между черновиком и подтверждением расписание могло измениться. Для
    create/move без явного ``force`` пересчитываем конфликты; список непуст →
    исполнение отклоняется."""
    if action.type not in (ACTION_CREATE_EVENT, ACTION_MOVE_EVENT) or payload.get("force"):
        return None

    if action.type == ACTION_CREATE_EVENT:
        participant_ids, _ = resolve_participant_ids(db, payload.get("participants", []))
        all_ids = list(dict.fromkeys([user.id, *participant_ids]))
        proposed = conflict_resolver.ProposedEvent(
            start=datetime.fromisoformat(payload["start_at"]),
            end=datetime.fromisoformat(payload["end_at"]),
            priority=payload.get("priority", 5),
            format=payload.get("location_type", "offline"),
            city=payload.get("city", ""),
            address=payload.get("address", ""),
            title=payload.get("title", "Встреча"),
        )
    else:  # ACTION_MOVE_EVENT
        event = calendar_service.get_event(db, payload.get("event_id"))
        if event is None:
            return None  # стандартная ошибка «Событие не найдено» дальше по коду
        participant_ids = [p.user_id for p in event.participants]
        all_ids = list(dict.fromkeys([event.owner_id, *participant_ids]))
        proposed = conflict_resolver.ProposedEvent(
            start=datetime.fromisoformat(payload["start_at"]),
            end=datetime.fromisoformat(payload["end_at"]),
            priority=event.priority,
            format=event.location_type,
            city=event.city,
            address=event.address,
            title=event.title,
            exclude_event_id=event.id,
        )
    resolve = conflict_resolver.resolve_conflicts(db, settings, proposed, all_ids)
    return None if resolve.can_schedule else resolve.conflicts


def confirm_action(db: Session, settings: Settings, user: User, action_id: str) -> dict:
    action = _get_action(db, user, action_id)
    if action is None:
        return {"ok": False, "detail": "Действие не найдено"}
    if action.status != ACTION_PENDING:
        return {"ok": False, "detail": f"Действие уже {action.status}"}

    # BUG-05: просроченный черновик не исполняем (подтверждение из старой вкладки).
    now = local_now()
    if action.expires_at and action.expires_at < now:
        action.status = ACTION_EXPIRED
        db.commit()
        return {"ok": False, "detail": "Черновик устарел — сформируйте запрос заново"}

    # BUG-07: атомарный захват статуса — двум параллельным confirm достанется один.
    captured = db.execute(
        update(AssistantAction)
        .where(AssistantAction.id == action.id, AssistantAction.status == ACTION_PENDING)
        .values(status=ACTION_IN_PROGRESS)
    )
    db.commit()
    if captured.rowcount != 1:
        db.refresh(action)
        return {"ok": False, "detail": f"Действие уже {action.status}"}
    db.refresh(action)

    payload = json.loads(action.payload_json or "{}")

    # BUG-06: между черновиком и подтверждением мог появиться новый конфликт.
    conflicts = _recheck_conflicts(db, settings, user, action, payload)
    if conflicts:
        action.status = ACTION_PENDING  # черновик остаётся, пользователь решает сам
        db.commit()
        summary = "; ".join(f"«{c.title}» {c.start:%d.%m %H:%M}" for c in conflicts[:3])
        return {
            "ok": False,
            "detail": f"Появился конфликт: {summary}. Выберите другое время или подтвердите заново.",
            "conflicts": [c.to_dict() for c in conflicts],
        }

    out: dict = {"ok": True, "type": action.type}

    try:
        if action.type == ACTION_CREATE_EVENT:
            event = create_event_row(db, user, payload, settings)
            out["created_event"] = event_out(event)
            out["message"] = f"Встреча «{event.title}» создана."
            notification_service.notify(db, settings, user,
                text=f"Встреча «{event.title}» создана на {event.start_at:%d.%m %H:%M}.",
                title="Новая встреча", meta={"event_id": event.id})
            event_notifications.notify_created(db, settings, event, user)

        elif action.type in (ACTION_MOVE_EVENT,):
            event = calendar_service.get_event(db, payload["event_id"])
            if event is None:
                raise ValueError("Событие не найдено")
            _ensure_event_mutable(event, user)
            event = calendar_service.update_event(
                db,
                event,
                EventUpdate(
                    start_at=datetime.fromisoformat(payload["start_at"]),
                    end_at=datetime.fromisoformat(payload["end_at"]),
                ),
                actor_id=user.id,
            )
            out["updated_event"] = event_out(event)
            out["message"] = f"Встреча «{event.title}» перенесена на {event.start_at:%d.%m %H:%M}."
            notification_service.notify(db, settings, user,
                text=f"«{event.title}» перенесена на {event.start_at:%d.%m %H:%M}.",
                title="Перенос встречи", meta={"event_id": event.id})
            event_notifications.notify_moved(db, settings, event, user)

        elif action.type == ACTION_UPDATE_EVENT:
            event = calendar_service.get_event(db, payload["event_id"])
            if event is None:
                raise ValueError("Событие не найдено")
            _ensure_event_mutable(event, user)
            event = calendar_service.update_event(
                db,
                event,
                EventUpdate.model_validate(payload.get("fields") or {}),
                actor_id=user.id,
            )
            out["updated_event"] = event_out(event)
            out["message"] = f"Встреча «{event.title}» обновлена."

        elif action.type == ACTION_DELETE_EVENT:
            event = calendar_service.get_event(db, payload["event_id"])
            if event is None:
                raise ValueError("Событие не найдено")
            _ensure_event_mutable(event, user)
            title = event.title
            calendar_service.delete_event(db, event)
            out["message"] = f"Встреча «{title}» удалена."

        elif action.type == ACTION_CANCEL_EVENT:
            event = calendar_service.get_event(db, payload["event_id"])
            if event is None:
                raise ValueError("Событие не найдено")
            _ensure_event_mutable(event, user)
            event = calendar_service.update_event(
                db, event, EventUpdate(status=STATUS_CANCELLED), actor_id=user.id
            )
            out["updated_event"] = event_out(event)
            out["message"] = f"Встреча «{event.title}» отменена."
            event_notifications.notify_cancelled(db, settings, event, user)

        elif action.type == ACTION_CREATE_REMINDER:
            reminder = Reminder(event_id=payload["event_id"], user_id=user.id,
                                remind_at=datetime.fromisoformat(payload["remind_at"]),
                                channel=payload.get("channel", "web"))
            db.add(reminder); db.commit(); db.refresh(reminder)
            out["message"] = "Напоминание поставлено."
            notification_service.notify(db, settings, user,
                text=f"Напоминание установлено на {reminder.remind_at:%d.%m %H:%M}.",
                title="Напоминание", meta={"reminder_id": reminder.id})

        elif action.type == ACTION_CREATE_EVENTS_FROM_PROTOCOL:
            created = []
            for ev_payload in payload.get("events", []):
                event = create_event_row(db, user, ev_payload, settings)
                created.append(event_out(event))
            out["created_events"] = created
            out["message"] = f"Создано встреч из протокола: {len(created)}."
            notification_service.notify(db, settings, user,
                text=f"Из протокола создано встреч: {len(created)}.",
                title="Встречи из протокола", meta={"count": len(created)})

        else:
            raise ValueError(f"Неизвестный тип действия: {action.type}")

    except Exception as exc:  # noqa: BLE001
        db.rollback()
        action.status = ACTION_PENDING
        db.commit()
        logger.exception("confirm_action failed")
        return {"ok": False, "detail": str(exc)}

    action.status = ACTION_CONFIRMED
    action.result_json = json.dumps(out, ensure_ascii=False, default=str)
    db.commit()
    audit_service.record(db, actor_user_id=user.id, action="confirm_action",
                         entity_type="assistant_action", entity_id=action.id,
                         payload={"type": action.type})
    return out
