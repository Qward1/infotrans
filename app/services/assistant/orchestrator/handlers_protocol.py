"""Хендлеры протоколов: генерация из документа, follow-up встречи."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.assistant import ACTION_CREATE_EVENTS_FROM_PROTOCOL
from app.services import audit as audit_service
from app.services.assistant import normalizer, protocol_generator
from app.services.assistant.orchestrator.common import create_action
from app.services.assistant.schemas import (
    AssistantCard,
    AssistantResult,
    ProtocolData,
    SuggestedAction,
)


def handle_generate_protocol(settings, db, user, nr, result, now):
    from app.models.assistant import Document
    from app.services.assistant import document_reader

    doc = None
    if nr.protocol.source_document_id:
        doc = document_reader.get(db, nr.protocol.source_document_id)
    if doc is None:
        # берём последний загруженный документ пользователя
        doc = db.execute(
            select(Document).where(Document.owner_id == user.id)
            .order_by(Document.created_at.desc(), Document.id.desc()).limit(1)
        ).scalars().first()
    text = doc.text if doc else ""
    protocol = protocol_generator.generate(
        settings, text,
        source_document_id=doc.id if doc else None,
        target_event_id=nr.protocol.target_event_id, user_email=user.email)
    _emit_protocol(settings, db, user, protocol, result)


def build_protocol_from_document(settings, db, user, document, target_event_id=None) -> AssistantResult:
    """Используется маршрутом загрузки файла: сгенерировать протокол по документу."""
    result = AssistantResult(reply="", intent="generate_meeting_protocol",
                             mode="local", conversation_id=str(uuid.uuid4()))
    protocol = protocol_generator.generate(
        settings, document.text, source_document_id=document.id,
        target_event_id=target_event_id, user_email=user.email)
    _emit_protocol(settings, db, user, protocol, result)
    return result


def _emit_protocol(settings, db, user, protocol: ProtocolData, result: AssistantResult):
    result.status = "done"
    result.protocol = protocol.model_dump(mode="json")
    n_tasks = len(protocol.action_items)
    n_follow = len(protocol.follow_up_meetings)
    result.reply = (
        f"Готов протокол: {len(protocol.decisions)} решений, {n_tasks} задач(и), "
        f"{n_follow} встреч(и) к созданию."
    )
    result.cards.append(AssistantCard(kind="protocol", title="Протокол встречи", data=result.protocol))
    if protocol.action_items:
        result.cards.append(AssistantCard(kind="tasks", title="Задачи из протокола",
                                          data={"items": protocol.action_items,
                                                "responsibles": protocol.responsibles,
                                                "deadlines": protocol.deadlines}))
    # Черновик на создание follow-up встреч.
    if protocol.follow_up_meetings:
        events_payload = _followups_to_events(settings, protocol)
        action = create_action(db, user, ACTION_CREATE_EVENTS_FROM_PROTOCOL,
                               "Встречи из протокола", {"events": events_payload})
        # FN-05: предпросмотр — пользователь видит, ЧТО именно подтверждает.
        result.cards.append(AssistantCard(
            kind="followups", title="Встречи к созданию",
            data={"events": [
                {"title": e["title"], "start_at": e["start_at"], "end_at": e["end_at"],
                 "location_type": e["location_type"]}
                for e in events_payload
            ]}))
        result.suggested_actions.append(SuggestedAction(
            type="confirm", label=f"Создать {n_follow} встреч(и)", style="primary",
            action_id=action.action_id))
        result.suggested_actions.append(SuggestedAction(
            type="reject", label="Не создавать", style="ghost", action_id=action.action_id))
    audit_service.record(db, actor_user_id=user.id, action="generate_protocol",
                         entity_type="document", entity_id=protocol.source_document_id,
                         payload={"tasks": n_tasks, "follow_ups": n_follow})


def _followups_to_events(settings, protocol: ProtocolData) -> list[dict]:
    now = datetime.now()
    events = []
    for i, fu in enumerate(protocol.follow_up_meetings, start=1):
        # Грубая дата: через 7*i дней в 10:00, если нет распознанного намёка.
        d = normalizer.parse_date(fu.date_hint or "", now) if fu.date_hint else None
        start = datetime.combine(d, datetime.min.time()).replace(hour=10) if d else \
            (now + timedelta(days=7 * i)).replace(hour=10, minute=0, second=0, microsecond=0)
        dur = fu.duration_minutes or settings.scheduling.default_meeting_minutes
        events.append({
            "title": fu.title, "description": "Создано из протокола встречи",
            "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=dur)).isoformat(),
            "timezone": settings.app.timezone, "location_type": "online",
            "importance": "normal", "priority": 5, "status": "planned",
            "source": "protocol", "participants": fu.participants,
        })
    return events


def handle_create_from_protocol(settings, db, user, nr, result, now):
    # Из свободного текста создать встречи по протоколу редко возможно —
    # обычно это подтверждение действия. Подсказываем правильный путь.
    result.status = "info"
    result.reply = ("Чтобы создать встречи из протокола, сначала загрузите документ встречи — "
                    "я соберу протокол и предложу список встреч с кнопкой подтверждения.")
    result.suggested_actions.append(SuggestedAction(type="upload_document",
                                                    label="Загрузить документ", style="ghost"))
