"""Backend tools для ассистента: сотрудники, занятость и свободные слоты."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.calendar import CalendarEvent
from app.models.user import User
from app.services import availability, scheduling
from app.services import users as users_service


class CalendarContextError(Exception):
    """Базовая ошибка assistant calendar tools."""


class CalendarAccessDenied(CalendarContextError):
    """Пользователь не может видеть календарь выбранного сотрудника."""


class EmployeeNotFound(CalendarContextError):
    """Сотрудник не найден."""


class EmployeeAmbiguous(CalendarContextError):
    """По имени найдено несколько сотрудников."""

    def __init__(self, query: str, candidates: list[User]):
        super().__init__(query)
        self.query = query
        self.candidates = candidates


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime
    label: str


_ALIASES = {
    "маша": "мария",
    "маши": "мария",
    "машу": "мария",
    "марии": "мария",
    "марией": "мария",
    "петя": "петр",
    "пети": "петр",
    "петра": "петр",
    "петру": "петр",
    "анны": "анна",
    "анне": "анна",
    "анну": "анна",
}
_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "овой", "евой", "ыми", "ими",
    "ой", "ей", "ым", "им", "ом", "ем", "ая", "яя", "ую", "юю", "ах", "ях",
    "а", "я", "ы", "и", "у", "ю", "е",
)
# (?:^|\s+): временное слово в НАЧАЛЕ фрагмента («время завтра…») тоже отсекается,
# иначе «завтра» превращается в имя сотрудника.
_TEMPORAL_SPLIT_RE = re.compile(
    r"(?:^|\s+)(?:на|за|сегодня|завтра|послезавтра|следующ\w*|эт\w*|текущ\w*|"
    r"недел\w*|день|дня|дату|утро|вечер|после|до|в\s+\d|\d{1,2}[.\s])\b",
    re.I,
)


def _norm(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я@\s.\-]", " ", value)
    return " ".join(value.split())


def _tokens(value: str) -> list[str]:
    return [token for token in _norm(value).replace("@", " ").replace(".", " ").split() if token]


def _stem(token: str) -> str:
    token = _ALIASES.get(token, token)
    for ending in _ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 3:
            return token[: -len(ending)]
    return token


def _employee_tokens(user: User) -> list[str]:
    values = [user.full_name or "", user.email.split("@", 1)[0], user.email]
    tokens: list[str] = []
    for value in values:
        tokens.extend(_tokens(value))
    return list(dict.fromkeys(tokens))


def _score_employee(query: str, user: User) -> int:
    query_norm = _norm(query)
    if not query_norm:
        return 0
    full = _norm(f"{user.full_name} {user.email}")
    if query_norm in full:
        return 100 + len(query_norm)

    user_tokens = _employee_tokens(user)
    user_stems = {_stem(token) for token in user_tokens}
    score = 0
    for token in _tokens(query_norm):
        stem = _stem(token)
        if token in user_tokens or stem in user_stems:
            score += 35
        elif any(ut.startswith(stem) or stem.startswith(_stem(ut)) for ut in user_tokens):
            score += 18
    return score


def employee_summary(user: User, settings: Settings) -> dict:
    return {
        "userId": user.id,
        "fullName": user.full_name or user.email,
        "email": user.email,
        "role": user.role,
        "timezone": settings.app.timezone,
    }


def search_employees(
    db: Session,
    settings: Settings,
    actor: User,
    query: str = "",
    *,
    limit: int = 10,
    include_inaccessible: bool = False,
) -> list[User]:
    """Найти сотрудников по имени/email.

    Справочник команды (id/имя/email) доступен всем авторизованным — он нужен
    для приглашения участников (UX-05). Доступ к чужому КАЛЕНДАРЮ по-прежнему
    проверяется отдельно (``resolve_employee_query``/``employee_availability``).
    """
    source = users_service.list_active_users(db)
    scored: list[tuple[int, User]] = []
    for user in source:
        score = _score_employee(query, user) if query else 1
        if score > 0:
            scored.append((score, user))
    scored.sort(key=lambda item: (-item[0], item[1].full_name or item[1].email))
    return [user for _, user in scored][:limit]


def resolve_employee_query(db: Session, settings: Settings, actor: User, query: str) -> User:
    matches = search_employees(db, settings, actor, query, limit=5, include_inaccessible=True)
    if not matches:
        raise EmployeeNotFound(query)
    top_score = _score_employee(query, matches[0])
    close = [user for user in matches if _score_employee(query, user) >= max(1, top_score - 5)]
    if len(close) > 1 and top_score < 120:
        raise EmployeeAmbiguous(query, close[:5])
    target = matches[0]
    if target.id != actor.id and not actor.is_admin:
        raise CalendarAccessDenied(query)
    return target


def extract_employee_queries(text: str) -> list[str]:
    """Вытащить имена из запросов вроде «слоты Маши» или «у Петра и Анны»."""
    fragments: list[str] = []
    patterns = [
        r"\bу\s+(.+)$",
        r"\b(?:слоты|окна|окно|окошк\w*|время|занятость|расписание)\s+(.+)$",
        r"\bс\s+([А-ЯЁA-Z][^,.?!]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        fragment = _TEMPORAL_SPLIT_RE.split(match.group(1), maxsplit=1)[0]
        fragment = re.sub(r"\b(?:свободн\w*|занят\w*|календар\w*)\b", " ", fragment, flags=re.I)
        fragment = " ".join(fragment.strip(" ,.;:!?").split())
        if fragment:
            fragments.append(fragment)
            break

    queries: list[str] = []
    for fragment in fragments:
        parts = re.split(r"\s*(?:,| и )\s*", fragment, flags=re.I)
        for part in parts:
            part = part.strip(" ,.;:!?")
            if len(part) >= 2 and part.lower() not in {"меня", "мой", "моя", "мне"}:
                queries.append(part)
    return list(dict.fromkeys(queries))


def _today_in_timezone(tz_name: str, now: datetime) -> date:
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return now.date()
    if now.tzinfo:
        return now.astimezone(tz).date()
    return datetime.now(tz).date() if now.date() == datetime.now().date() else now.date()


def infer_date_range(text: str, settings: Settings, now: datetime | None = None) -> DateRange:
    """Определить диапазон для запросов ассистента о расписании."""
    from app.services.assistant import normalizer

    now = now or datetime.now()
    lower = _norm(text)
    today = _today_in_timezone(settings.app.timezone, now)
    parsed = normalizer.parse_date(text, now)

    if "следующ" in lower and "недел" in lower:
        current_monday = today - timedelta(days=today.weekday())
        start_day = current_monday + timedelta(days=7)
        start = datetime.combine(start_day, time.min)
        end = start + timedelta(days=7)
        return DateRange(start, end, "следующая неделя")

    if parsed is not None and ("недел" not in lower or "сегодня" in lower or "завтра" in lower):
        start = datetime.combine(parsed, time.min)
        end = start + timedelta(days=1)
        return DateRange(start, end, parsed.strftime("%d.%m.%Y"))

    if "недел" in lower:
        start = now.replace(second=0, microsecond=0)
        end = start + timedelta(days=7)
        return DateRange(start, end, "ближайшие 7 дней")

    start = now.replace(second=0, microsecond=0)
    end = start + timedelta(days=1)
    return DateRange(start, end, "сегодня")


def parse_api_range(
    settings: Settings,
    *,
    date_value: str | None = None,
    range_start: str | None = None,
    range_end: str | None = None,
) -> DateRange:
    try:
        if date_value:
            day = date.fromisoformat(date_value)
            start = datetime.combine(day, time.min)
            return DateRange(start, start + timedelta(days=1), day.strftime("%d.%m.%Y"))
        if range_start and range_end:
            start = datetime.fromisoformat(range_start)
            end = datetime.fromisoformat(range_end)
            if end <= start:
                raise ValueError
            return DateRange(start, end, f"{start:%d.%m.%Y}–{end:%d.%m.%Y}")
    except ValueError as exc:
        raise ValueError("Некорректная дата или диапазон") from exc
    return infer_date_range("неделя", settings)


# ARCH-03: единая реализация слияния интервалов живёт в scheduling.
_merge_busy = scheduling.merge_events_busy


def employee_availability(
    db: Session,
    settings: Settings,
    actor: User,
    target: User,
    requested_range: DateRange,
    *,
    requested_slot_duration: int | None = None,
    include_meeting_details: bool | None = None,
) -> dict:
    if target.id != actor.id and not actor.is_admin:
        raise CalendarAccessDenied(str(target.id))

    duration = requested_slot_duration or settings.scheduling.default_meeting_minutes
    work_start, work_end = availability.parse_working_hours(settings)
    events = availability.participant_events(db, [target.id], requested_range.start, requested_range.end)
    slots = availability.find_free_slots(
        db,
        settings,
        [target.id],
        requested_range.start,
        requested_range.end,
        duration_minutes=duration,
        meeting_format="online",
    )
    can_view_details = include_meeting_details if include_meeting_details is not None else (
        actor.is_admin or actor.id == target.id
    )
    busy_intervals = []
    for start, end in _merge_busy(events):
        interval = {"startAt": start.isoformat(), "endAt": end.isoformat()}
        if can_view_details:
            interval["events"] = [
                {
                    "id": event.id,
                    "title": event.title,
                    "startAt": event.start_at.isoformat(),
                    "endAt": event.end_at.isoformat(),
                    "ownerUserId": event.owner_id,
                    "status": event.status,
                }
                for event in events
                if event.start_at < end and event.end_at > start
            ]
        busy_intervals.append(interval)

    return {
        "employeeId": target.id,
        "name": target.full_name or target.email,
        "email": target.email,
        "timezone": settings.app.timezone,
        "workingHours": {"start": work_start.strftime("%H:%M"), "end": work_end.strftime("%H:%M")},
        "requestedRange": {
            "startAt": requested_range.start.isoformat(),
            "endAt": requested_range.end.isoformat(),
            "label": requested_range.label,
        },
        "requestedSlotDuration": duration,
        "busyIntervals": busy_intervals,
        "availableSlots": [slot.to_dict() for slot in slots],
    }
