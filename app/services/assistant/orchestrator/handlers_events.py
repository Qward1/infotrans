"""Хендлеры событий: создание, перенос/отмена/удаление, календарь, сводка."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.assistant import (
    ACTION_CANCEL_EVENT,
    ACTION_CREATE_EVENT,
    ACTION_CREATE_REMINDER,
    ACTION_DELETE_EVENT,
    ACTION_MOVE_EVENT,
    ACTION_UPDATE_EVENT,
)
from app.models.calendar import STATUS_CANCELLED, CalendarEvent
from app.models.user import User
from app.services import availability, calendar as calendar_service
from app.services import conflict_resolver, location_service
from app.services import users as users_service
from app.services.assistant import calendar_context, morphology, normalizer, notification_service
from app.services.assistant.orchestrator.common import (
    compose_datetimes,
    create_action,
    create_event_row,
    resolve_participant_ids,
)
from app.services.assistant.orchestrator.serializers import event_out, event_payload
from app.services.assistant.schemas import AssistantCard, AssistantResult, NormalizedRequest, SuggestedAction

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


def participant_names(db: Session, emails: list[str]) -> list[str]:
    names: list[str] = []
    for email in emails:
        u = users_service.get_by_email(db, email)
        names.append((u.full_name or u.email) if u else email)
    return names


def join_names(names: list[str], case: str | None = None) -> str:
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
    names = participant_names(db, ev.participants)
    if names:
        return "встречу с " + join_names(names, case=morphology.INSTRUMENTAL)
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


def enrich_create_event(settings: Settings, db: Session, user: User, nr: NormalizedRequest) -> None:
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


def _employee_options(users: list[User], settings: Settings) -> str:
    return ", ".join(
        f"{calendar_context.employee_summary(user, settings)['fullName']} ({user.email})"
        for user in users
    )


def resolve_employee_targets(
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
# Создание встречи (с конфликт-резолвингом)                                    #
# --------------------------------------------------------------------------- #
def handle_create_event(settings, db, user, nr, result, now):
    start, end = compose_datetimes(nr, settings, now)
    payload = event_payload(nr, start, end, settings)

    participant_ids, unresolved = resolve_participant_ids(db, payload["participants"])
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
    # FN-13: успеет ли пользователь доехать из города последней очной встречи дня.
    if payload["location_type"] == "offline" and payload["city"]:
        origin_city = _last_offline_city_before(db, user.id, start)
        if origin_city and not location_service.is_offline_realistic(
            origin_city, payload["city"], settings
        ):
            travel_min = location_service.intercity_travel_minutes(
                origin_city, payload["city"], settings
            )
            hours = max(1, round(travel_min / 60))
            result.warnings.append(
                f"Дорога из {origin_city} в {payload['city']} займёт ~{hours}ч — "
                "очная встреча может быть нереалистичной, предлагаю онлайн."
            )
            online_suggested = True

    others = [pid for pid in all_ids if pid != user.id]

    if resolve.can_schedule:
        if not others and not resolve.buffer_warnings:
            # Личная встреча без конфликтов — создаём сразу.
            event = create_event_row(db, user, payload, settings)
            result.status = "done"
            result.created_event = event_out(event)
            result.reply = f"✅ Создал встречу «{event.title}» {start:%d.%m %H:%M}–{end:%H:%M}."
            result.cards.append(AssistantCard(kind="created_event", title="Встреча создана",
                                              data=event_out(event)))
            notification_service.notify(
                db, settings, user,
                text=f"Встреча «{event.title}» запланирована на {start:%d.%m %H:%M}.",
                title="Новая встреча", meta={"event_id": event.id})
            return
        # Есть участники/буфер — подтверждаем.
        action = create_action(db, user, ACTION_CREATE_EVENT, payload["title"], payload)
        result.status = "needs_confirmation"
        dur_min = int((end - start).total_seconds() // 60)
        names = participant_names(db, payload["participants"])
        with_str = (" с " + join_names(names, case=morphology.INSTRUMENTAL)) if names else ""
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


def _last_offline_city_before(db, user_id, start) -> str | None:
    """Город последней очной встречи пользователя в тот же день до ``start`` (FN-13)."""
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(CalendarEvent)
        .where(
            CalendarEvent.owner_id == user_id,
            CalendarEvent.status != STATUS_CANCELLED,
            CalendarEvent.location_type.in_(("offline", "hybrid")),
            CalendarEvent.end_at <= start,
            CalendarEvent.start_at >= day_start,
            CalendarEvent.city != "",
        )
        .order_by(CalendarEvent.end_at.desc())
        .limit(1)
    )
    event = db.execute(stmt).scalars().first()
    return event.city if event else None


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


# --------------------------------------------------------------------------- #
# Просмотр календаря и сводка                                                  #
# --------------------------------------------------------------------------- #
def handle_show_calendar(settings, db, user, nr, result, now):
    targets = resolve_employee_targets(settings, db, user, nr, result)
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
    # BUG-25: отменённые не считаем в «у вас N встреч», но показываем в карточке.
    active_count = sum(1 for e in events if e.status != STATUS_CANCELLED)
    who = "у вас" if target.id == user.id else f"у {target.full_name or target.email}"
    result.reply = f"На период {requested_range.label} {who} {active_count} встреч."
    result.cards.append(AssistantCard(kind="calendar", title="Календарь недели",
                                       data={"week_start": requested_range.start.isoformat(),
                                             "employee": calendar_context.employee_summary(target, settings),
                                             "events": [event_out(e) for e in events]}))


def handle_summarize(settings, db, user, nr, result, now):
    targets = resolve_employee_targets(settings, db, user, nr, result)
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
                                             "upcoming": [event_out(e) for e in upcoming],
                                             "conflicts": conflicts}))


def _pairwise_conflicts(db, user_id, now) -> list[dict]:
    from app.services import scheduling
    conflicts = scheduling.conflicts_for_user(db, user_id, now, now + timedelta(days=14))
    return [{"keep": c.keep.title, "drop": c.drop.title,
             "keep_priority": c.keep.priority, "drop_priority": c.drop.priority}
            for c in conflicts[:5]]


# --------------------------------------------------------------------------- #
# Целевые действия: перенос / отмена / удаление / изменение / напоминание      #
# --------------------------------------------------------------------------- #
def handle_create_reminder(settings, db, user, nr, result, now):
    event = resolve_target_event(db, user, nr, now)
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
                                       data={"event": event_out(event), "remind_at": remind_at.isoformat(),
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


def handle_target_action(settings, db, user, nr, result, now):
    event = resolve_target_event(db, user, nr, now)
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
        start, end = compose_datetimes(nr, settings, now)
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
    result.cards.append(AssistantCard(kind="created_event", title="Целевая встреча", data=event_out(event)))
    result.suggested_actions.append(SuggestedAction(type="confirm", label=label, style=style,
                                                    action_id=action.action_id))
    result.suggested_actions.append(SuggestedAction(type="reject", label="Отмена", style="ghost",
                                                    action_id=action.action_id))


def resolve_target_event(db, user, nr, now) -> CalendarEvent | None:
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


def handle_unknown(settings, db, user, nr, result, now, greet=True):
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
