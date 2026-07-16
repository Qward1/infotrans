"""Оркестратор ассистента-секретаря.

Единая точка входа чата: нормализует запрос → проверяет достаточность данных →
роутит по интенту → вызывает нужные сервисы (планирование, конфликты, билеты,
протоколы) → собирает ``AssistantResult`` с карточками и предложенными действиями.

Действия, затрагивающие календарь/других участников, не выполняются сразу:
создаётся черновик ``AssistantAction``, который пользователь подтверждает через
``confirm_action`` (эндпоинты /api/assistant/actions/{id}/confirm|reject).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
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
from app.schemas.calendar import EventCreate, EventUpdate
from app.services import audit as audit_service
from app.services import availability, calendar as calendar_service
from app.services import conflict_resolver, event_notifications, location_service
from app.services import users as users_service
from app.services.assistant import (
    calendar_context,
    conversation,
    dify_client,
    morphology,
    normalizer,
    notification_service,
    protocol_generator,
    travel_search,
)
from app.services.assistant.travel_search import TicketSearchError
from app.services.assistant.schemas import (
    AssistantCard,
    AssistantResult,
    NormalizedRequest,
    ProtocolData,
    SuggestedAction,
)

logger = logging.getLogger("smartcal.orchestrator")

_MODE_BY_SOURCE = {
    "local": "local",
    "dify": "dify",
    "dify-fallback": "dify-fallback",
    "llm-fallback": "llm",
}


# --------------------------------------------------------------------------- #
# Утилиты                                                                      #
# --------------------------------------------------------------------------- #
def _compose_datetimes(nr: NormalizedRequest, settings: Settings, now: datetime) -> tuple[datetime, datetime]:
    ev = nr.event
    d = ev.date or now.date()
    if ev.start_time:
        start = datetime.combine(d, ev.start_time)
    else:
        wh_start, _ = availability.parse_working_hours(settings)
        start = datetime.combine(d, wh_start)
    if ev.end_time:
        end = datetime.combine(d, ev.end_time)
    else:
        dur = ev.duration_minutes or settings.scheduling.default_meeting_minutes
        end = start + timedelta(minutes=dur)
    if end <= start:
        end = start + timedelta(minutes=settings.scheduling.default_meeting_minutes)
    return start, end


def _resolve_participant_ids(db: Session, emails: list[str]) -> tuple[list[int], list[str]]:
    ids: list[int] = []
    unresolved: list[str] = []
    for email in emails:
        u = users_service.get_by_email(db, email)
        if u:
            ids.append(u.id)
        else:
            unresolved.append(email)
    return ids, unresolved


def _event_out(event: CalendarEvent) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "timezone": event.timezone,
        "location_type": event.location_type,
        "city": event.city,
        "address": event.address,
        "meeting_url": event.meeting_url,
        "importance": event.importance,
        "priority": event.priority,
        "status": event.status,
        "source": event.source,
        "owner_id": event.owner_id,
    }


def _event_payload(nr: NormalizedRequest, start: datetime, end: datetime, settings: Settings) -> dict:
    ev = nr.event
    return {
        "title": (ev.title or "Встреча").strip(),
        "description": ev.description or "",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": ev.timezone or settings.app.timezone,
        "location_type": ev.format or "offline",
        "city": ev.city or "",
        "address": ev.address or "",
        "meeting_url": ev.meeting_url or "",
        "importance": ev.importance or "normal",
        "priority": ev.priority if ev.priority is not None else 5,
        "status": "planned",
        "source": "assistant",
        "participants": ev.participants,
    }


def create_action(
    db: Session,
    user: User,
    action_type: str,
    title: str,
    payload: dict,
    ttl_minutes: int = 1440,
) -> AssistantAction:
    action = AssistantAction(
        action_id=str(uuid.uuid4()),
        user_id=user.id,
        type=action_type,
        status=ACTION_PENDING,
        title=title[:255],
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        expires_at=datetime.now() + timedelta(minutes=ttl_minutes),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


# --------------------------------------------------------------------------- #
# Главная точка входа                                                          #
# --------------------------------------------------------------------------- #
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
    now = now or datetime.now()
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
        _enrich_create_event(settings, db, user, nr)

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
                                style="ghost", payload=_prefill_from_nr(nr))
            )
        if "source_document" in nr.missing_fields:
            result.suggested_actions.append(
                SuggestedAction(type="upload_document", label="Загрузить документ", style="ghost")
            )
        return result

    # Непонятный запрос: приветствие показываем только в начале диалога,
    # а не посреди сценария (когда уже был обмен репликами).
    if nr.intent == "unknown":
        _handle_unknown(settings, db, user, nr, result, now, greet=prior is None)
        return result

    dispatch = {
        "create_event": _handle_create_event,
        "find_free_slots": _handle_find_slots,
        "find_tickets": _handle_find_tickets,
        "show_calendar": _handle_show_calendar,
        "summarize_schedule": _handle_summarize,
        "generate_meeting_protocol": _handle_generate_protocol,
        "create_events_from_protocol": _handle_create_from_protocol,
        "create_reminder": _handle_create_reminder,
        "delete_event": _handle_target_action,
        "cancel_event": _handle_target_action,
        "update_event": _handle_target_action,
        "move_event": _handle_target_action,
    }
    handler = dispatch.get(nr.intent, _handle_unknown)
    handler(settings, db, user, nr, result, now)
    return result


# --------------------------------------------------------------------------- #
# «Голос» секретаря: озвучивание ответа ассистентом smart_calendar_secretary   #
# --------------------------------------------------------------------------- #
def _secretary_context(result: AssistantResult) -> dict:
    """Факты бэкенда для секретаря: он перефразирует их, но НЕ выдумывает новые.

    Детерминированный ``reply`` (draft) уже содержит все конкретные факты — даты,
    имена, счётчики. Отдаём его как «черновик» + структурную сводку, чтобы LLM
    только улучшил формулировку (тон, грамматику, склонение имён), не искажая суть.
    """
    return {
        "intent": result.intent,
        "status": result.status,
        "draft_reply": result.reply,
        "clarifying_question": result.clarifying_question or "",
        "missing_fields": result.missing_fields,
        "cards": [{"kind": c.kind, "title": c.title} for c in result.cards],
        "actions": [{"type": a.type, "label": a.label} for a in result.suggested_actions],
        "warnings": result.warnings,
    }


def _apply_secretary_voice(
    settings: Settings,
    user: User,
    message: str,
    result: AssistantResult,
    conversation_id: str,
) -> None:
    """Заменить детерминированный ответ репликой секретаря (LLM), если включён Dify.

    Бэкенд остаётся источником истины: интент, карточки, действия и статусы уже
    посчитаны — секретарь меняет только текст ``reply``. Любой сбой → тихо
    оставляем детерминированный ответ (мягкий откат, как и у нормализатора)."""
    if not settings.assistant.dify.enabled:
        return
    # Если нормализация уже упала на локальный режим — Dify недоступен,
    # второй вызов только потратит таймаут. Оставляем детерминированный текст.
    if result.mode == "dify-fallback":
        return
    if not (result.reply or "").strip():
        return
    try:
        reply = dify_client.secretary_reply(
            settings,
            message,
            _secretary_context(result),
            user_email=user.email,
            conversation_id=conversation_id,
        )
    except Exception as exc:  # noqa: BLE001 — намеренно широкий: любой сбой → откат
        logger.warning("smart_calendar_secretary failed, keep deterministic reply: %s", exc)
        result.mode = "dify-fallback"
        return
    if reply and reply.strip():
        result.reply = reply.strip()


# --------------------------------------------------------------------------- #
# Обогащение сценария создания встречи                                         #
# --------------------------------------------------------------------------- #
_AUTO_TITLE_RE = re.compile(r"(?i)^встреча\s+(?:с|со)\s+(.+)$")
_NAME_ONLY_RE = re.compile(r"[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*)*")


def _is_auto_person_title(title: str | None) -> bool:
    """Является ли название авто-заглушкой «Встреча с <Имя>» (а не реальной темой)."""
    if not title:
        return True
    m = _AUTO_TITLE_RE.match(title.strip())
    if not m:
        return False
    return bool(_NAME_ONLY_RE.fullmatch(m.group(1).strip()))


def _resolve_people(db: Session, settings: Settings, actor: User, queries: list[str]) -> tuple[list[User], list[str]]:
    """Сопоставить имена участников («Маша Кузнецова») пользователям системы."""
    resolved: list[User] = []
    unresolved: list[str] = []
    seen: set[int] = {actor.id}
    for query in queries:
        matches = calendar_context.search_employees(
            db, settings, actor, query, limit=3, include_inaccessible=True
        )
        if matches:
            top = matches[0]
            if top.id not in seen:
                resolved.append(top)
                seen.add(top.id)
        else:
            unresolved.append(query)
    return resolved, unresolved


def _participant_names(db: Session, emails: list[str]) -> list[str]:
    names: list[str] = []
    for email in emails:
        u = users_service.get_by_email(db, email)
        names.append((u.full_name or u.email) if u else email)
    return names


def _join_names(names: list[str], case: str | None = None) -> str:
    """Склеить имена участников в перечисление. ``case`` (напр. ``"ablt"``) склоняет
    каждое имя — нужно для фраз «встречу с Марией и Иваном»."""
    if not names:
        return ""
    if case:
        names = [morphology.inflect_full_name(n, case) for n in names]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " и " + names[-1]


def _event_subject(db: Session, nr: NormalizedRequest) -> str:
    ev = nr.event
    if ev.title and not _is_auto_person_title(ev.title):
        return f"«{ev.title}»"
    names = _participant_names(db, ev.participants)
    if names:
        return "встречу с " + _join_names(names, case=morphology.INSTRUMENTAL)
    return "встречу"


def _create_event_question(db: Session, nr: NormalizedRequest) -> str | None:
    missing = nr.missing_fields
    if not missing:
        return None
    if "date" in missing or "start_time" in missing:
        return f"На какой день и время назначить {_event_subject(db, nr)}?"
    if "title" in missing:
        return "Какую тему указать для встречи?"
    return normalizer.build_clarifying_question(missing)


def _enrich_create_event(settings: Settings, db: Session, user: User, nr: NormalizedRequest) -> None:
    ev = nr.event
    # 1. Участники, названные по имени («встреча с Машей Кузнецовой»).
    queries = calendar_context.extract_employee_queries(nr.original_text)
    resolved, _unresolved = _resolve_people(db, settings, user, queries)
    for participant in resolved:
        if participant.email not in ev.participants:
            ev.participants.append(participant.email)
    # 2. Авто-название «Встреча с <Имя>» не считаем заданной темой —
    #    участник уже сохранён отдельно, поэтому спросим тему явно.
    if ev.participants and _is_auto_person_title(ev.title):
        ev.title = None
    # 3. Пересчёт недостающих полей и человеко-понятный вопрос.
    nr.missing_fields = normalizer.compute_missing(nr)
    question = _create_event_question(db, nr)
    if question:
        nr.clarifying_question = question


def _prefill_from_nr(nr: NormalizedRequest) -> dict:
    ev = nr.event
    payload: dict = {"source": "assistant"}
    if ev.title:
        payload["title"] = ev.title
    if ev.date and ev.start_time:
        start = datetime.combine(ev.date, ev.start_time)
        payload["start_at"] = start.isoformat(timespec="minutes")
        dur = ev.duration_minutes or 60
        payload["end_at"] = (start + timedelta(minutes=dur)).isoformat(timespec="minutes")
    if ev.format:
        payload["location_type"] = ev.format
    if ev.city:
        payload["city"] = ev.city
    if ev.participants:
        payload["participants"] = list(ev.participants)
    return payload


def _employee_options(users: list[User], settings: Settings) -> str:
    return ", ".join(
        f"{calendar_context.employee_summary(user, settings)['fullName']} ({user.email})"
        for user in users
    )


def _resolve_employee_targets(
    settings: Settings,
    db: Session,
    user: User,
    nr: NormalizedRequest,
    result: AssistantResult,
) -> list[User] | None:
    queries = calendar_context.extract_employee_queries(nr.original_text)
    if not queries:
        return []

    targets: list[User] = []
    for query in queries:
        try:
            target = calendar_context.resolve_employee_query(db, settings, user, query)
        except calendar_context.EmployeeAmbiguous as exc:
            result.status = "needs_clarification"
            result.reply = (
                f"Нашёл несколько сотрудников по запросу «{exc.query}»: "
                f"{_employee_options(exc.candidates, settings)}. Уточните, кого выбрать."
            )
            return None
        except calendar_context.EmployeeNotFound:
            result.status = "needs_clarification"
            result.reply = f"Не нашёл сотрудника «{query}». Уточните имя или email."
            return None
        except calendar_context.CalendarAccessDenied:
            result.status = "error"
            result.reply = "У вас нет доступа к календарю этого сотрудника."
            return None
        if target.id not in {existing.id for existing in targets}:
            targets.append(target)
    return targets


# --------------------------------------------------------------------------- #
# Хендлеры интентов                                                            #
# --------------------------------------------------------------------------- #
def _handle_create_event(settings, db, user, nr, result, now):
    start, end = _compose_datetimes(nr, settings, now)
    payload = _event_payload(nr, start, end, settings)

    participant_ids, unresolved = _resolve_participant_ids(db, payload["participants"])
    all_ids = list(dict.fromkeys([user.id, *participant_ids]))
    if unresolved:
        result.warnings.append("Не нашёл в системе: " + ", ".join(unresolved))

    proposed = conflict_resolver.ProposedEvent(
        start=start, end=end,
        priority=payload["priority"], format=payload["location_type"],
        city=payload["city"], address=payload["address"], title=payload["title"],
    )
    resolve = conflict_resolver.resolve_conflicts(db, settings, proposed, all_ids, not_before=now)

    # Реалистичность очного формата (разные города / нехватка времени на дорогу).
    online_suggested = False
    if payload["location_type"] == "offline" and resolve.buffer_warnings:
        online_suggested = True

    others = [pid for pid in all_ids if pid != user.id]

    if resolve.can_schedule:
        if not others and not resolve.buffer_warnings:
            # Личная встреча без конфликтов — создаём сразу.
            event = _create_event_row(db, user, payload, settings)
            result.status = "done"
            result.created_event = _event_out(event)
            result.reply = f"✅ Создал встречу «{event.title}» {start:%d.%m %H:%M}–{end:%H:%M}."
            result.cards.append(AssistantCard(kind="created_event", title="Встреча создана",
                                              data=_event_out(event)))
            notification_service.notify(
                db, settings, user,
                text=f"Встреча «{event.title}» запланирована на {start:%d.%m %H:%M}.",
                title="Новая встреча", meta={"event_id": event.id})
            return
        # Есть участники/буфер — подтверждаем.
        action = create_action(db, user, ACTION_CREATE_EVENT, payload["title"], payload)
        result.status = "needs_confirmation"
        dur_min = int((end - start).total_seconds() // 60)
        names = _participant_names(db, payload["participants"])
        with_str = (" с " + _join_names(names, case=morphology.INSTRUMENTAL)) if names else ""
        result.reply = (
            f"Готов создать встречу «{payload['title']}»{with_str} "
            f"{start:%d.%m в %H:%M} ({dur_min} мин). Подтвердите создание."
        )
        result.cards.append(AssistantCard(kind="created_event", title="Черновик встречи", data=payload))
        result.suggested_actions.append(
            SuggestedAction(type="confirm", label="Создать встречу", style="primary", action_id=action.action_id))
        result.suggested_actions.append(
            SuggestedAction(type="reject", label="Отмена", style="ghost", action_id=action.action_id))
    else:
        # Конфликт.
        result.status = "conflict"
        result.reply = resolve.explanation
        result.cards.append(AssistantCard(
            kind="conflict", title="Конфликт расписания",
            data={"recommended_action": resolve.recommended_action,
                  "conflicts": [c.to_dict() for c in resolve.conflicts],
                  "explanation": resolve.explanation}))
        result.alternative_slots = [s.to_dict() for s in resolve.alternative_slots]
        if resolve.alternative_slots:
            result.cards.append(AssistantCard(
                kind="alternative_slots", title="Свободные альтернативы",
                data={"slots": result.alternative_slots}))
            # Кнопки на 1-2 ближайших слота (открывают форму на фронте).
            for s in resolve.alternative_slots[:2]:
                result.suggested_actions.append(SuggestedAction(
                    type="create_event", label=f"Взять {s.start:%d.%m %H:%M}", style="ghost",
                    payload={**payload, "start_at": s.start.isoformat(timespec="minutes"),
                             "end_at": s.end.isoformat(timespec="minutes")}))

        if resolve.recommended_action == conflict_resolver.ACTION_PROPOSE_RESCHEDULE_LOWER:
            # План переноса менее приоритетной встречи (только после подтверждения).
            lower = min(resolve.conflicts, key=lambda c: c.priority)
            plan = _build_reschedule_plan(db, settings, lower, all_ids, now)
            if plan:
                action = create_action(db, user, ACTION_MOVE_EVENT,
                                       f"Перенос «{lower.title}»", plan)
                result.cards.append(AssistantCard(
                    kind="reschedule_plan", title="Предлагаемый перенос",
                    data={**plan, "conflict": lower.to_dict()}))
                result.suggested_actions.insert(0, SuggestedAction(
                    type="confirm", label=f"Перенести «{lower.title}»", style="primary",
                    action_id=action.action_id))
                # FN-04: у каждого черновика есть пара confirm/reject.
                result.suggested_actions.insert(1, SuggestedAction(
                    type="reject", label="Не переносить", style="ghost",
                    action_id=action.action_id))
        elif resolve.recommended_action == conflict_resolver.ACTION_ASK_CONFIRMATION:
            action = create_action(db, user, ACTION_CREATE_EVENT,
                                   payload["title"], {**payload, "force": True})
            result.suggested_actions.insert(0, SuggestedAction(
                type="confirm", label="Поставить несмотря на конфликт", style="danger",
                action_id=action.action_id))
            # FN-04: черновик можно корректно закрыть, а не бросать pending.
            result.suggested_actions.insert(1, SuggestedAction(
                type="reject", label="Не ставить", style="ghost",
                action_id=action.action_id))

    if online_suggested:
        online_payload = {**payload, "location_type": "online",
                          "meeting_url": payload.get("meeting_url") or "https://meet.example.local/new"}
        action = create_action(db, user, ACTION_CREATE_EVENT, payload["title"] + " (онлайн)", online_payload)
        result.warnings.append(
            "Очный формат может быть нереалистичен из-за времени на дорогу — можно перевести встречу в онлайн.")
        result.suggested_actions.append(SuggestedAction(
            type="confirm", label="Сделать онлайн", style="ghost", action_id=action.action_id))


def _build_reschedule_plan(db, settings, conflict, participant_ids, now) -> dict | None:
    """Предложить новое время для менее приоритетной встречи."""
    event = calendar_service.get_event(db, conflict.event_id)
    if event is None:
        return None
    duration = int((event.end_at - event.start_at).total_seconds() // 60)
    search_start = event.start_at.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = availability.find_free_slots(
        db, settings, [event.owner_id], search_start, search_start + timedelta(days=14),
        duration_minutes=duration, city=event.city, address=event.address,
        meeting_format=event.location_type, not_before=now,
    )
    # берём первый слот, не совпадающий с текущим временем
    for s in slots:
        if s.start != event.start_at:
            return {"event_id": event.id, "start_at": s.start.isoformat(),
                    "end_at": s.end.isoformat(), "old_start_at": event.start_at.isoformat()}
    return None


def _handle_find_slots(settings, db, user, nr, result, now):
    ev = nr.event
    duration = ev.duration_minutes or settings.scheduling.default_meeting_minutes
    requested_targets = _resolve_employee_targets(settings, db, user, nr, result)
    if requested_targets is None:
        return
    if requested_targets:
        requested_range = calendar_context.infer_date_range(nr.original_text, settings, now)
        availability_items = [
            calendar_context.employee_availability(
                db,
                settings,
                user,
                target,
                requested_range,
                requested_slot_duration=duration,
            )
            for target in requested_targets
        ]
        result.status = "done"
        result.cards.append(
            AssistantCard(
                kind="employee_availability",
                title="Занятость сотрудников",
                data={"items": availability_items},
            )
        )
        if len(availability_items) == 1:
            item = availability_items[0]
            result.alternative_slots = item["availableSlots"]
            slots_count = len(item["availableSlots"])
            busy_count = len(item["busyIntervals"])
            if slots_count:
                result.reply = (
                    f"{item['name']}: нашёл {slots_count} свободных окон на {item['requestedRange']['label']} "
                    f"длительностью от {duration} мин. Занятых интервалов: {busy_count}."
                )
                for slot in item["availableSlots"][:3]:
                    result.suggested_actions.append(
                        SuggestedAction(
                            type="create_event",
                            label=f"Занять {datetime.fromisoformat(slot['start_at']):%d.%m %H:%M}",
                            style="ghost",
                            payload={
                                "owner_id": item["employeeId"],
                                "start_at": slot["start_at"],
                                "end_at": slot["end_at"],
                                "source": "assistant",
                            },
                        )
                    )
            else:
                result.reply = (
                    f"{item['name']}: свободных слотов на {item['requestedRange']['label']} "
                    f"длительностью {duration} мин не найдено."
                )
            return

        lines = []
        any_slots = False
        for item in availability_items:
            count = len(item["availableSlots"])
            any_slots = any_slots or count > 0
            lines.append(f"• {item['name']}: свободных окон {count}, занятых интервалов {len(item['busyIntervals'])}")
        result.reply = (
            f"Проверил занятость на {availability_items[0]['requestedRange']['label']} "
            f"(слот от {duration} мин):\n" + "\n".join(lines)
        )
        if not any_slots:
            result.reply += "\nСвободных слотов не найдено."
        return

    participant_ids, unresolved = _resolve_participant_ids(db, ev.participants)
    all_ids = list(dict.fromkeys([user.id, *participant_ids]))
    if unresolved:
        result.warnings.append("Не нашёл участников: " + ", ".join(unresolved))

    if ev.date:
        range_start = datetime.combine(ev.date, availability.parse_working_hours(settings)[0])
        range_end = datetime.combine(ev.date, availability.parse_working_hours(settings)[1])
    else:
        range_start = now
        range_end = now + timedelta(days=7)

    slots = availability.find_free_slots(
        db, settings, all_ids, range_start, range_end, duration_minutes=duration,
        city=ev.city or "", address=ev.address or "", meeting_format=ev.format or "offline",
        not_before=now)

    result.status = "done"
    result.alternative_slots = [s.to_dict() for s in slots]
    if not slots:
        result.reply = "Свободных окон в рабочих часах не нашлось. Попробуйте другой диапазон или короче встречу."
        return
    who = "у вас" if len(all_ids) <= 1 else f"у всех {len(all_ids)} участников"
    result.reply = f"Нашёл {len(slots)} свободных окон ({who}), длительность {duration} мин."
    # UX-06: контекст диалога (тема/участники/формат) едет вместе со слотами,
    # чтобы «Занять» открывал полностью заполненную форму.
    prefill = {k: v for k, v in _prefill_from_nr(nr).items() if k not in {"start_at", "end_at"}}
    result.cards.append(AssistantCard(kind="alternative_slots", title="Свободные окна",
                                       data={"slots": result.alternative_slots, "prefill": prefill}))
    for s in slots[:3]:
        result.suggested_actions.append(SuggestedAction(
            type="create_event", label=f"Занять {s.start:%d.%m %H:%M}", style="ghost",
            payload={**prefill, "start_at": s.start.isoformat(timespec="minutes"),
                     "end_at": s.end.isoformat(timespec="minutes"), "source": "assistant"}))


def _handle_find_tickets(settings, db, user, nr, result, now):
    tr = nr.travel
    depart = datetime.combine(tr.departure_date, datetime.min.time()) if tr.departure_date else None
    ret = datetime.combine(tr.return_date, datetime.min.time()) if tr.return_date else None
    if settings.tickets.mode == "sites":
        try:
            params = travel_search.build_params(
                tr.origin_city or "",
                tr.destination_city or "",
                depart,
                tr.transport_type,
                return_date=ret,
                preferences=tr.preferences,
            )
            sources = [link.to_dict() for link in travel_search.external_search_links(params)]
        except TicketSearchError as exc:
            result.status = "error"
            result.reply = f"Не удалось подготовить поиск билетов: {exc}"
            return
        result.status = "done"
        result.reply = (
            f"Подготовил поиск {tr.origin_city} → {tr.destination_city}"
            + (f" на {tr.departure_date:%d.%m}" if tr.departure_date else "")
            + ". Откройте подходящий сайт — актуальные цены и места будут там."
        )
        result.cards.append(AssistantCard(kind="travel_sources", title="Поиск на сайтах", data={"sources": sources}))
        audit_service.record(db, actor_user_id=user.id, action="search_tickets", entity_type="travel",
                             payload={"origin": tr.origin_city, "destination": tr.destination_city,
                                      "source_mode": "sites", "count": len(sources)})
        return
    try:
        options = travel_search.search(
            settings,
            tr.origin_city or "",
            tr.destination_city or "",
            depart,
            tr.transport_type,
            return_date=ret,
            preferences=tr.preferences,
        )
    except TicketSearchError as exc:
        result.status = "error"
        result.reply = f"Не удалось выполнить поиск билетов: {exc}"
        return
    options = options[:6]
    result.status = "done"
    result.travel_options = [o.model_dump(mode="json") for o in options]
    if not options:
        result.reply = "Не удалось подобрать варианты — уточните города и дату."
        return
    best = options[0]
    result.reply = (
        f"Нашёл {len(options)} вариантов {tr.origin_city} → {tr.destination_city}"
        + (f" на {tr.departure_date:%d.%m}" if tr.departure_date else "")
        + f". Дешевле всего: {travel_search.explain_option(best)}."
    )
    result.cards.append(AssistantCard(kind="travel_options", title="Варианты поездки",
                                       data={"origin": tr.origin_city, "destination": tr.destination_city,
                                             "options": result.travel_options}))
    audit_service.record(db, actor_user_id=user.id, action="search_tickets", entity_type="travel",
                         payload={"origin": tr.origin_city, "destination": tr.destination_city,
                                  "count": len(options)})


def _handle_show_calendar(settings, db, user, nr, result, now):
    targets = _resolve_employee_targets(settings, db, user, nr, result)
    if targets is None:
        return
    target = targets[0] if targets else user
    requested_range = calendar_context.infer_date_range(nr.original_text, settings, now)
    events = calendar_service.list_events_in_range(
        db,
        target.id,
        requested_range.start,
        requested_range.end,
        include_cancelled=True,
    )
    result.status = "done"
    who = "у вас" if target.id == user.id else f"у {target.full_name or target.email}"
    result.reply = f"На период {requested_range.label} {who} {len(events)} встреч."
    result.cards.append(AssistantCard(kind="calendar", title="Календарь недели",
                                       data={"week_start": requested_range.start.isoformat(),
                                             "employee": calendar_context.employee_summary(target, settings),
                                             "events": [_event_out(e) for e in events]}))


def _handle_summarize(settings, db, user, nr, result, now):
    targets = _resolve_employee_targets(settings, db, user, nr, result)
    if targets is None:
        return
    target = targets[0] if targets else user
    upcoming = calendar_service.upcoming_events(db, target.id, limit=8)
    conflicts = _pairwise_conflicts(db, target.id, now)
    result.status = "done"
    lines = [f"• {e.start_at:%d.%m %H:%M} {e.title}" for e in upcoming[:6]]
    who = "ваши" if target.id == user.id else f"{target.full_name or target.email}"
    result.reply = (
        f"Ближайшие встречи {who} ({len(upcoming)}):\n" + "\n".join(lines)
        if upcoming else "Ближайших встреч нет."
    )
    result.cards.append(AssistantCard(kind="summary", title="Сводка расписания",
                                       data={"employee": calendar_context.employee_summary(target, settings),
                                             "upcoming": [_event_out(e) for e in upcoming],
                                             "conflicts": conflicts}))


def _pairwise_conflicts(db, user_id, now) -> list[dict]:
    from app.services import scheduling
    conflicts = scheduling.conflicts_for_user(db, user_id, now, now + timedelta(days=14))
    return [{"keep": c.keep.title, "drop": c.drop.title,
             "keep_priority": c.keep.priority, "drop_priority": c.drop.priority}
            for c in conflicts[:5]]


def _handle_generate_protocol(settings, db, user, nr, result, now):
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


def _handle_create_from_protocol(settings, db, user, nr, result, now):
    # Из свободного текста создать встречи по протоколу редко возможно —
    # обычно это подтверждение действия. Подсказываем правильный путь.
    result.status = "info"
    result.reply = ("Чтобы создать встречи из протокола, сначала загрузите документ встречи — "
                    "я соберу протокол и предложу список встреч с кнопкой подтверждения.")
    result.suggested_actions.append(SuggestedAction(type="upload_document",
                                                    label="Загрузить документ", style="ghost"))


def _handle_create_reminder(settings, db, user, nr, result, now):
    event = _resolve_target_event(db, user, nr, now)
    if event is None:
        result.status = "needs_clarification"
        result.reply = "Не нашёл встречу для напоминания. Уточните название или дату."
        return
    minutes = 60
    if nr.event.reminder and nr.event.reminder.minutes_before:
        minutes = nr.event.reminder.minutes_before
    remind_at = event.start_at - timedelta(minutes=minutes)
    payload = {"event_id": event.id, "remind_at": remind_at.isoformat(),
               "channel": (nr.event.reminder.channel if nr.event.reminder else "web")}
    action = create_action(db, user, ACTION_CREATE_REMINDER, f"Напоминание «{event.title}»", payload)
    result.status = "needs_confirmation"
    result.reply = f"Поставлю напоминание за {minutes} мин до «{event.title}» ({remind_at:%d.%m %H:%M}). Подтвердить?"
    result.cards.append(AssistantCard(kind="reminder", title="Напоминание",
                                       data={"event": _event_out(event), "remind_at": remind_at.isoformat(),
                                             "minutes_before": minutes}))
    result.suggested_actions.append(SuggestedAction(type="confirm", label="Поставить напоминание",
                                                    style="primary", action_id=action.action_id))
    result.suggested_actions.append(SuggestedAction(type="reject", label="Отмена", style="ghost",
                                                    action_id=action.action_id))


def _clarify_target_not_found(db, user, nr, result) -> None:
    """Уточняющий ответ, когда целевая встреча не найдена (BUG-04)."""
    result.status = "needs_clarification"
    title_query = (nr.target_event.title or "").strip()
    own_upcoming = [
        e for e in calendar_service.upcoming_events(db, user.id, limit=6)
        if e.owner_id == user.id
    ][:3]
    options = "; ".join(f"«{e.title}» {e.start_at:%d.%m %H:%M}" for e in own_upcoming)
    if title_query:
        result.reply = f"Не нашёл встречу с названием «{title_query}»."
    else:
        result.reply = "Не нашёл подходящую встречу."
    if options:
        result.reply += f" Ближайшие ваши встречи: {options}. Уточните название, дату или id (#123)."
    else:
        result.reply += " Уточните название, дату или id (#123)."


def _handle_target_action(settings, db, user, nr, result, now):
    event = _resolve_target_event(db, user, nr, now)
    if event is None:
        _clarify_target_not_found(db, user, nr, result)
        return

    if nr.intent == "delete_event":
        payload = {"event_id": event.id}
        action = create_action(db, user, ACTION_DELETE_EVENT, f"Удаление «{event.title}»", payload)
        verb, label = "удалить", "Удалить встречу"
        style = "danger"
    elif nr.intent == "cancel_event":
        # Отмена — смена статуса, не удаление (BUG-03): история и статистика сохраняются.
        payload = {"event_id": event.id}
        action = create_action(db, user, ACTION_CANCEL_EVENT, f"Отмена «{event.title}»", payload)
        verb, label = "отменить", "Отменить встречу"
        style = "danger"
    elif nr.intent == "move_event":
        start, end = _compose_datetimes(nr, settings, now)
        payload = {"event_id": event.id, "start_at": start.isoformat(), "end_at": end.isoformat()}
        action = create_action(db, user, ACTION_MOVE_EVENT, f"Перенос «{event.title}»", payload)
        verb, label = "перенести", f"Перенести на {start:%d.%m %H:%M}"
        style = "primary"
    else:  # update_event
        fields = {}
        if nr.event.format:
            fields["location_type"] = nr.event.format
        if nr.event.priority is not None:
            fields["priority"] = nr.event.priority
        if nr.event.title:
            fields["title"] = nr.event.title
        payload = {"event_id": event.id, "fields": fields}
        action = create_action(db, user, ACTION_UPDATE_EVENT, f"Изменение «{event.title}»", payload)
        verb, label = "изменить", "Применить изменения"
        style = "primary"

    result.status = "needs_confirmation"
    result.reply = f"Нашёл встречу «{event.title}» ({event.start_at:%d.%m %H:%M}). Подтвердите, чтобы {verb}."
    result.cards.append(AssistantCard(kind="created_event", title="Целевая встреча", data=_event_out(event)))
    result.suggested_actions.append(SuggestedAction(type="confirm", label=label, style=style,
                                                    action_id=action.action_id))
    result.suggested_actions.append(SuggestedAction(type="reject", label="Отмена", style="ghost",
                                                    action_id=action.action_id))


def _resolve_target_event(db, user, nr, now) -> CalendarEvent | None:
    te = nr.target_event
    if te.event_id:
        ev = calendar_service.get_event(db, te.event_id)
        if ev and (ev.owner_id == user.id or user.is_admin):
            return ev
        # Явный id не найден / нет прав — не подставляем другую встречу.
        return None
    # поиск по названию/дате среди событий пользователя
    stmt = select(CalendarEvent).where(
        CalendarEvent.owner_id == user.id,
        CalendarEvent.status != STATUS_CANCELLED,
    ).order_by(CalendarEvent.start_at.asc())
    events = list(db.execute(stmt).scalars().all())
    # Ключ поиска — только target_event.title: в event.title для move/update
    # лежит НОВОЕ название или мусор разбора («Перенеси встречу»), не цель.
    title = (te.title or "").strip().lower()
    date_hint = None
    if te.date_hint:
        try:
            date_hint = datetime.fromisoformat(te.date_hint).date()
        except ValueError:
            date_hint = None
    candidates = events
    if title:
        candidates = [e for e in events if title in e.title.lower()]
        if not candidates:
            # BUG-04: название задано, совпадений нет — уточняем, а не берём «любую».
            return None
    if date_hint:
        dc = [e for e in candidates if e.start_at.date() == date_hint]
        if dc:
            candidates = dc
        elif not title:
            # Дата — единственный ориентир, и на неё встреч нет.
            return None
    if not title and not date_hint and nr.intent in {"delete_event", "cancel_event"}:
        # Разрушительные операции без ориентиров не выполняем «на ближайшей».
        return None
    # ближайшее будущее
    future = [e for e in candidates if e.end_at >= now]
    pool = future or candidates
    return pool[0] if pool else None


def _handle_unknown(settings, db, user, nr, result, now, greet=True):
    result.status = "info"
    if greet:
        result.reply = (
            f"Здравствуйте, {user.full_name or 'коллега'}! Я ассистент-секретарь. Могу:\n"
            "• создать/перенести/удалить встречу;\n"
            "• найти свободное время (в т.ч. для нескольких участников);\n"
            "• подобрать билеты и объяснить варианты;\n"
            "• собрать протокол из документа и создать встречи по нему.\n"
            "Например: «Запланируй встречу с командой завтра в 15:00 онлайн»."
        )
    else:
        # Середина диалога — без приветствия, просто помогаем сформулировать.
        result.reply = (
            "Не совсем понял запрос. Могу создать или перенести встречу, найти свободное "
            "время, подобрать билеты или собрать протокол. Уточните, что нужно — например: "
            "«Запланируй встречу с Иваном завтра в 15:00»."
        )


# --------------------------------------------------------------------------- #
# Создание строки события + участников                                        #
# --------------------------------------------------------------------------- #
def _create_event_row(db, user, payload: dict, settings) -> CalendarEvent:
    data = EventCreate(
        title=payload["title"], description=payload.get("description", ""),
        start_at=payload["start_at"], end_at=payload["end_at"],
        timezone=payload.get("timezone", settings.app.timezone),
        location_type=payload.get("location_type", "offline"),
        city=payload.get("city", ""), address=payload.get("address", ""),
        meeting_url=payload.get("meeting_url", ""),
        importance=payload.get("importance", "normal"),
        priority=payload.get("priority", 5),
        status="planned", source=payload.get("source", "assistant"),
        participants=payload.get("participants", []),
    )
    event = calendar_service.create_event(db, user.id, data, actor_id=user.id)
    audit_service.record(db, actor_user_id=user.id, action="create_event",
                         entity_type="event", entity_id=event.id, payload={"title": event.title})
    return event


# --------------------------------------------------------------------------- #
# Подтверждение / отклонение действий                                          #
# --------------------------------------------------------------------------- #
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
    now = now or datetime.now()
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
        participant_ids, _ = _resolve_participant_ids(db, payload.get("participants", []))
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
    now = datetime.now()
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
            event = _create_event_row(db, user, payload, settings)
            out["created_event"] = _event_out(event)
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
            out["updated_event"] = _event_out(event)
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
            out["updated_event"] = _event_out(event)
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
            out["updated_event"] = _event_out(event)
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
                event = _create_event_row(db, user, ev_payload, settings)
                created.append(_event_out(event))
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
