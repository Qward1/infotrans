"""Главная точка входа оркестратора: run → нормализация → диспетчеризация."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.clock import local_now
from app.models.user import User
from app.services.assistant import conversation, normalizer
from app.services.assistant.orchestrator import handlers_events, handlers_protocol, handlers_slots, handlers_travel
from app.services.assistant.orchestrator.serializers import prefill_from_nr
from app.services.assistant.orchestrator.voice import _apply_secretary_voice
from app.services.assistant.schemas import AssistantResult, SuggestedAction

_MODE_BY_SOURCE = {
    "local": "local",
    "dify": "dify",
    "dify-fallback": "dify-fallback",
    "llm-fallback": "llm",
}


def run(
    settings: Settings,
    db: Session,
    user: User,
    message: str,
    conversation_id: str | None = None,
    now: datetime | None = None,
) -> AssistantResult:
    """Обработать сообщение и вернуть результат.

    Оркестратор — источник истины: он делает всю работу (нормализация → сервисы →
    карточки/действия) и формирует детерминированный ``reply``. Если включён Dify,
    финальный текст ответа «озвучивает» ассистент ``smart_calendar_secretary`` —
    поверх фактов бэкенда, с мягким откатом на детерминированный текст при сбое.
    """
    now = now or local_now()
    conversation_id = conversation_id or str(uuid.uuid4())
    result = _run_core(settings, db, user, message, conversation_id, now)
    _apply_secretary_voice(settings, user, message, result, conversation_id)
    return result


def _run_core(
    settings: Settings,
    db: Session,
    user: User,
    message: str,
    conversation_id: str,
    now: datetime,
) -> AssistantResult:
    # Контекст предыдущего хода: если ассистент задавал уточняющий вопрос,
    # новое сообщение продолжает тот же сценарий, а не начинает новый.
    prior = conversation.load_prior_turn(db, conversation_id)
    nr = normalizer.normalize(settings, message, user_email=user.email, conversation_id=conversation_id, now=now)
    if conversation.should_continue(prior, nr):
        nr = conversation.continue_request(prior, message, now)

    # Обогащение сценария создания встречи: участники по имени, тема, вопрос.
    if nr.intent == "create_event":
        handlers_events.enrich_create_event(settings, db, user, nr)

    mode = _MODE_BY_SOURCE.get(nr.source, "local")

    result = AssistantResult(
        reply="",
        intent=nr.intent,
        mode=mode,
        confidence=nr.confidence,
        language=nr.language,
        conversation_id=conversation_id,
        extracted=nr.model_dump(mode="json", include={"event", "travel", "protocol", "target_event"}),
        missing_fields=nr.missing_fields,
        clarifying_question=nr.clarifying_question,
    )

    # Не хватает данных → уточняющий вопрос, действие не выполняем.
    if nr.missing_fields and nr.intent not in {"unknown", "show_calendar", "summarize_schedule"}:
        result.status = "needs_clarification"
        result.reply = nr.clarifying_question or "Уточните, пожалуйста, детали запроса."
        if nr.intent == "create_event":
            result.suggested_actions.append(
                SuggestedAction(type="open_event_form", label="Заполнить форму встречи",
                                style="ghost", payload=prefill_from_nr(nr))
            )
        if "source_document" in nr.missing_fields:
            result.suggested_actions.append(
                SuggestedAction(type="upload_document", label="Загрузить документ", style="ghost")
            )
        return result

    # Непонятный запрос: приветствие показываем только в начале диалога,
    # а не посреди сценария (когда уже был обмен репликами).
    if nr.intent == "unknown":
        handlers_events.handle_unknown(settings, db, user, nr, result, now, greet=prior is None)
        return result

    dispatch = {
        "create_event": handlers_events.handle_create_event,
        "find_free_slots": handlers_slots.handle_find_slots,
        "find_tickets": handlers_travel.handle_find_tickets,
        "show_calendar": handlers_events.handle_show_calendar,
        "summarize_schedule": handlers_events.handle_summarize,
        "generate_meeting_protocol": handlers_protocol.handle_generate_protocol,
        "create_events_from_protocol": handlers_protocol.handle_create_from_protocol,
        "create_reminder": handlers_events.handle_create_reminder,
        "delete_event": handlers_events.handle_target_action,
        "cancel_event": handlers_events.handle_target_action,
        "update_event": handlers_events.handle_target_action,
        "move_event": handlers_events.handle_target_action,
    }
    handler = dispatch.get(nr.intent, handlers_events.handle_unknown)
    handler(settings, db, user, nr, result, now)
    return result
