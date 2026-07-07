"""Контекст многошагового диалога ассистента.

Оркестратор по природе stateless: каждое сообщение нормализуется отдельно.
Этот модуль связывает соседние реплики в один сценарий. Если на прошлом шаге
ассистент задал уточняющий вопрос (``needs_clarification``), новое сообщение
считается ответом и «дозаполняет» недостающие поля прошлого интента, не
сбрасывая его и не уводя пользователя в приветствие.

Логика универсальна: она не хардкодит конкретные фразы, а переиспользует парсеры
нормализатора (дата/время/формат/город/участники) поверх ранее извлечённых данных,
плюс аккуратно распознаёт «свободный ответ» (например, тему встречи).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assistant import (
    CHAT_ROLE_ASSISTANT,
    AssistantChat,
    AssistantChatMessage,
)
from app.services.assistant import normalizer
from app.services.assistant.schemas import NormalizedRequest

logger = logging.getLogger("smartcal.conversation")

# Интенты, которые собираются пошагово (можно «дозаполнять» ответами).
SLOT_FILLING_INTENTS = {
    "create_event",
    "update_event",
    "move_event",
    "create_reminder",
    "find_tickets",
}
# Интенты, у которых основной контейнер данных — event.
_EVENT_INTENTS = {"create_event", "update_event", "move_event", "create_reminder"}


@dataclass
class PriorTurn:
    """Снимок предыдущего ответа ассистента (для продолжения сценария)."""

    intent: str = "unknown"
    status: str = "info"
    missing_fields: list[str] = field(default_factory=list)
    extracted: dict = field(default_factory=dict)


def load_prior_turn(db: Session, conversation_id: str | None) -> PriorTurn | None:
    """Прочитать последний ответ ассистента в чате как контекст для нового сообщения."""
    if not conversation_id:
        return None
    chat = db.get(AssistantChat, conversation_id)
    if chat is None:
        return None
    msg = db.execute(
        select(AssistantChatMessage)
        .where(
            AssistantChatMessage.chat_id == conversation_id,
            AssistantChatMessage.role == CHAT_ROLE_ASSISTANT,
        )
        .order_by(AssistantChatMessage.created_at.desc(), AssistantChatMessage.id.desc())
        .limit(1)
    ).scalars().first()
    if msg is None or not msg.payload_json:
        return None
    try:
        payload = json.loads(msg.payload_json)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return PriorTurn(
        intent=payload.get("intent", "unknown"),
        status=payload.get("status", "info"),
        missing_fields=list(payload.get("missing_fields", []) or []),
        extracted=payload.get("extracted", {}) or {},
    )


def should_continue(prior: PriorTurn | None, nr: NormalizedRequest) -> bool:
    """Считать ли новое сообщение продолжением незавершённого сценария."""
    if prior is None:
        return False
    if prior.intent not in SLOT_FILLING_INTENTS:
        return False
    if prior.status != "needs_clarification":
        return False
    # Пользователь явно начал ДРУГУЮ задачу — не продолжаем, а переключаемся.
    if nr.intent != "unknown" and nr.intent != prior.intent:
        return False
    return True


def continue_request(
    prior: PriorTurn,
    message: str,
    now: datetime | None = None,
) -> NormalizedRequest:
    """Собрать запрос-продолжение: прошлые данные + разбор нового сообщения."""
    now = now or datetime.now()
    base = {**(prior.extracted or {})}
    base["intent"] = prior.intent
    try:
        merged = NormalizedRequest.model_validate(base)
    except Exception:  # noqa: BLE001 — на всякий случай не роняем диалог
        merged = NormalizedRequest(intent=prior.intent)
    merged.original_text = message
    merged.language = normalizer.detect_language(message)
    merged.source = "local"
    merged.confidence = 0.9

    if prior.intent in _EVENT_INTENTS:
        structured = _overlay_event(merged, message, now)
        if prior.intent in {"create_event", "update_event", "move_event"}:
            _maybe_capture_title(merged, message, structured)
    elif prior.intent == "find_tickets":
        _overlay_travel(merged, message, now)

    merged.missing_fields = normalizer.compute_missing(merged)
    merged.clarifying_question = normalizer.build_clarifying_question(merged.missing_fields)
    return merged


# --------------------------------------------------------------------------- #
# Наложение новых данных поверх ранее извлечённых                             #
# --------------------------------------------------------------------------- #
def _overlay_event(merged: NormalizedRequest, message: str, now: datetime) -> bool:
    """Дозаполнить event данными из нового сообщения. Вернуть True, если найдено
    структурированное значение (дата/время/формат/…)."""
    ev = merged.event
    found = False

    d = normalizer.parse_date(message, now)
    if d:
        ev.date = d
        found = True
    st, et, dur = normalizer.parse_time_and_duration(message)
    if st:
        ev.start_time = st
        found = True
    if et:
        ev.end_time = et
        found = True
    if dur:
        ev.duration_minutes = dur
        found = True
    fmt = normalizer.parse_format(message)
    if fmt:
        ev.format = fmt
        found = True
    prio, imp = normalizer.parse_priority(message)
    if prio is not None:
        ev.priority = prio
        ev.importance = imp
        found = True
    city = normalizer._first_city(message)
    if city:
        ev.city = city
        found = True
    url = normalizer._extract_url(message)
    if url:
        ev.meeting_url = url
        if not ev.format:
            ev.format = "online"
        found = True
    for email in normalizer._EMAIL_RE.findall(message):
        if email not in ev.participants:
            ev.participants.append(email)
            found = True

    # Для переноса встречи новое время дублируется в target_event для поиска.
    if merged.intent == "move_event" and d and not merged.target_event.date_hint:
        merged.target_event.date_hint = d.isoformat()
    return found


def _overlay_travel(merged: NormalizedRequest, message: str, now: datetime) -> None:
    tr = merged.travel
    d = normalizer.parse_date(message, now)
    if d and tr.departure_date is None:
        tr.departure_date = d
    origin, dest = normalizer._extract_cities(message)
    if origin and not tr.origin_city:
        tr.origin_city = origin
    if dest and not tr.destination_city:
        tr.destination_city = dest
    # Одиночный город в ответе на «в какой город?» / «из какого города?».
    single = normalizer._first_city(message)
    if single:
        if not tr.destination_city:
            tr.destination_city = single
        elif not tr.origin_city:
            tr.origin_city = single
    transport = normalizer._transport_type(message)
    if transport != "any":
        tr.transport_type = transport


_TITLE_MAX = 80


def _maybe_capture_title(merged: NormalizedRequest, message: str, structured: bool) -> None:
    """Если тема ещё не задана и ответ — это «свободный текст» (не дата/время),
    трактовать сообщение как название/тему встречи."""
    ev = merged.event
    if ev.title:
        return
    if structured:
        return
    cleaned = " ".join(message.strip().strip(" .,:;!?\"'«»").split())
    if not cleaned or len(cleaned) > _TITLE_MAX:
        return
    if normalizer._EMAIL_RE.search(cleaned):
        return
    ev.title = cleaned[0].upper() + cleaned[1:]
    if merged.intent in {"update_event", "move_event"} and not merged.target_event.title:
        merged.target_event.title = ev.title
