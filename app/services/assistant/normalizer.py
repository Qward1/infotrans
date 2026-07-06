"""Нормализация пользовательского запроса → строгий ``NormalizedRequest``.

Три уровня (выбор по YAML), у всех — один контракт:
* Dify (``assistant.dify.enabled``): запрос идёт в ассистента ``request_normalizer``;
* LLM (``assistant.llm.enabled``): прямой вызов модели (хук, пока → fallback);
* локальный детерминированный парсер (по умолчанию, без ключей).

Любая ошибка внешнего сервиса → мягкий откат на локальный парсер
(``source="dify-fallback"``), чтобы demo не падало.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta

from app.core.config import Settings
from app.services.assistant import dify_client
from app.services.assistant.schemas import (
    EventData,
    NormalizedRequest,
    ReminderData,
    TargetEvent,
    TravelData,
)

logger = logging.getLogger("smartcal.normalizer")

# --------------------------------------------------------------------------- #
# Словари для разбора дат/времени                                             #
# --------------------------------------------------------------------------- #
_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6, "воскресенье ": 6,
}
_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# intent → компилированный паттерн (порядок важен: специфичные выше общих).
_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("create_events_from_protocol", re.compile(r"созда\w*.*из\s+протокол|встреч\w*\s+из\s+протокол|по\s+протокол\w*\s+созда", re.I)),
    ("generate_meeting_protocol", re.compile(r"протокол|резюме\s+встреч|итоги\s+встреч|саммари\s+встреч|стенограмм", re.I)),
    ("find_tickets", re.compile(r"билет|рейс|поезд|самол[её]т|авиа|ж/?д|перел[её]т|поездк|командировк|доехать|долететь|добраться", re.I)),
    ("find_free_slots", re.compile(r"свободн|когда\s+я\s+свобод|найди\s+(?:мне\s+)?врем|окно|слот|когда\s+удобн|подбери\s+врем", re.I)),
    ("move_event", re.compile(r"перенеси|перенести|сдвинь|сдвинуть|передвинь", re.I)),
    ("delete_event", re.compile(r"удали|отмени|убери|отменить|удалить\s+встреч", re.I)),
    ("update_event", re.compile(r"измени|обнови|поменяй|редактир", re.I)),
    ("create_reminder", re.compile(r"напомн|напоминани|reminder", re.I)),
    ("summarize_schedule", re.compile(r"что\s+у\s+меня|расписани|обзор\s+дня|мой\s+день|итоги\s+недел|сводк", re.I)),
    ("show_calendar", re.compile(r"покажи\s+календар|мой\s+календар|календар\w*\s+на|встречи\s+на\s+недел", re.I)),
    ("create_event", re.compile(r"встреч|созвон|запланир|назнач|поставь|добав.*событ|митинг|созвонимся|совещани|планёрк|планерк", re.I)),
]

_EMAIL_RE = re.compile(r"[\w.\-]+@[\w.\-]+\.\w+")
_CITY_PAIR_RE = re.compile(r"из\s+([A-Za-zА-Яа-яЁё\-]+)\s+(?:в|до)\s+([A-Za-zА-Яа-яЁё\-]+)", re.I)
_CITY_IN_RE = re.compile(r"\bв\s+([А-ЯЁ][а-яё\-]+(?:е|у|ом|и)?)\b")

# Хвост запроса (дата/время/формат), который нужно отрезать из названия встречи.
_TEMPORAL_CUT = re.compile(
    r"\s+(?:завтра|послезавтра|сегодня|онлайн|офлайн|очно|гибрид|"
    r"в\s+\d|с\s+\d|на\s+\d|к\s+\d|\d{1,2}[:.]\d{2}|\d{1,2}\.\d{1,2}|"
    r"понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье).*$",
    re.I,
)
# Стоп-слова, которые могли прилипнуть к названию города.
_CITY_STOPWORDS = {"на", "в", "до", "поездом", "самолетом", "самолётом", "завтра", "сегодня"}


def detect_language(text: str) -> str:
    return "ru" if re.search(r"[А-Яа-яЁё]", text) else "en"


def detect_intent(text: str) -> str:
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            return intent
    return "unknown"


# --------------------------------------------------------------------------- #
# Парсинг дат/времени                                                          #
# --------------------------------------------------------------------------- #
def parse_date(text: str, now: datetime) -> date | None:
    t = text.lower()
    if "послезавтра" in t:
        return (now + timedelta(days=2)).date()
    if "завтра" in t:
        return (now + timedelta(days=1)).date()
    if "сегодня" in t or "сейчас" in t:
        return now.date()

    # день недели
    for name, wd in _WEEKDAYS.items():
        if name in t:
            days_ahead = (wd - now.weekday()) % 7
            days_ahead = days_ahead or 7  # ближайший будущий
            return (now + timedelta(days=days_ahead)).date()

    # ISO YYYY-MM-DD
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # DD.MM(.YYYY)
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        try:
            d = date(year, month, day)
            if not m.group(3) and d < now.date():
                d = date(year + 1, month, day)
            return d
        except ValueError:
            pass

    # DD <месяц словом>
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)", t)
    if m:
        day = int(m.group(1))
        for stem, month in _MONTHS.items():
            if m.group(2).startswith(stem):
                year = now.year
                try:
                    d = date(year, month, day)
                    if d < now.date():
                        d = date(year + 1, month, day)
                    return d
                except ValueError:
                    pass
    return None


def parse_time_and_duration(text: str) -> tuple[time | None, time | None, int | None]:
    """Вернуть (start_time, end_time, duration_minutes)."""
    t = text.lower()
    start = end = None
    duration = None

    # «с 14 до 16» / «с 14:00 до 16:30» / «14-16»
    m = re.search(r"(?:с\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:до|-|–|—)\s*(\d{1,2})(?::(\d{2}))?", t)
    if m:
        try:
            start = time(int(m.group(1)), int(m.group(2) or 0))
            end = time(int(m.group(3)), int(m.group(4) or 0))
        except ValueError:
            start = end = None

    if start is None:
        # однозначное «HH:MM» — берём в первую очередь (не спутать с датой DD.MM)
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
        if m:
            try:
                start = time(int(m.group(1)), int(m.group(2)))
            except ValueError:
                start = None

    if start is None:
        # «в 15:00» / «в 15» / «на 9» — но не «на 10.07» (это дата)
        m = re.search(r"(?:в|во|на|к)\s+(\d{1,2})(?!\s*[.:]\d)(?::(\d{2}))?\s*(?:час(?:ов|а)?)?", t)
        if m:
            try:
                start = time(int(m.group(1)), int(m.group(2) or 0))
            except ValueError:
                start = None

    # длительность
    if "полтора часа" in t:
        duration = 90
    elif "полчаса" in t:
        duration = 30
    else:
        m = re.search(r"на\s+(\d{1,3})\s*(мин|час)", t)
        if m:
            val = int(m.group(1))
            if m.group(2).startswith("мин"):
                duration = val
            elif val <= 4:
                # «на 2 часа» — длительность; «на 16 часов» — это время (16:00), не длительность.
                duration = val * 60
        elif re.search(r"\bна\s+час\b|\bчас(?:ок)?\b", t):
            duration = 60

    if start and end and duration is None:
        s = start.hour * 60 + start.minute
        e = end.hour * 60 + end.minute
        if e > s:
            duration = e - s
    return start, end, duration


def parse_format(text: str) -> str | None:
    t = text.lower()
    if re.search(r"онлайн|zoom|zoom|видеосвяз|по\s+видео|дистанционн|созвон|google\s*meet|teams|ссылк", t):
        return "online"
    if re.search(r"очно|офис|лично|адрес|переговорн|в\s+кабинет|офлайн", t):
        return "offline"
    if re.search(r"гибрид", t):
        return "hybrid"
    return None


def parse_priority(text: str) -> tuple[int | None, str | None]:
    t = text.lower()
    if re.search(r"критичн|горит|очень\s+срочн", t):
        return 10, "critical"
    if re.search(r"важн|срочн|критич|приоритетн", t):
        return 8, "high"
    if re.search(r"неважн|не\s+срочн|низк\w+\s+приоритет|при\s+случае", t):
        return 2, "low"
    return None, None


def _extract_cities(text: str) -> tuple[str | None, str | None]:
    m = _CITY_PAIR_RE.search(text)
    if m:
        return _clean_city(m.group(1)), _clean_city(m.group(2))
    return None, None


# Нормализация падежных форм городов к именительному (грубо, для demo).
_CITY_CANON = {
    "москвы": "Москва", "москву": "Москва", "москве": "Москва",
    "казани": "Казань", "казань": "Казань",
    "сочи": "Сочи",
    "питера": "Санкт-Петербург", "петербург": "Санкт-Петербург",
    "петербурга": "Санкт-Петербург", "спб": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург", "санкт-петербурга": "Санкт-Петербург",
    "санкт-петербурге": "Санкт-Петербург",
    "екатеринбурга": "Екатеринбург", "екатеринбург": "Екатеринбург",
    "новосибирска": "Новосибирск", "новосибирск": "Новосибирск",
}


def _clean_city(raw: str) -> str:
    key = raw.strip().lower()
    if key in _CITY_STOPWORDS:
        return ""
    return _CITY_CANON.get(key, raw.strip().capitalize())


# --------------------------------------------------------------------------- #
# Достаточность данных                                                         #
# --------------------------------------------------------------------------- #
def compute_missing(nr: NormalizedRequest) -> list[str]:
    """Каких полей не хватает для выполнения действия."""
    missing: list[str] = []
    ev = nr.event
    if nr.intent == "create_event":
        if not (ev.title and ev.title.strip()):
            missing.append("title")
        if ev.date is None:
            missing.append("date")
        if ev.start_time is None and ev.duration_minutes is None:
            missing.append("start_time")
        # format не обязателен: если не указан — по умолчанию офлайн
        # (для онлайна достаточно сказать «онлайн»/дать ссылку).
    elif nr.intent == "find_tickets":
        tr = nr.travel
        if not tr.origin_city:
            missing.append("origin_city")
        if not tr.destination_city:
            missing.append("destination_city")
        if tr.departure_date is None:
            missing.append("departure_date")
        # transport_type имеет дефолт "any" — не требуем.
    elif nr.intent in {"delete_event", "update_event", "move_event"}:
        te = nr.target_event
        if te.event_id is None and not (te.title or te.date_hint):
            missing.append("target_event")
        if nr.intent == "move_event" and ev.date is None and ev.start_time is None:
            missing.append("new_time")
    elif nr.intent == "create_reminder":
        te = nr.target_event
        if te.event_id is None and not (te.title or te.date_hint):
            missing.append("target_event")
        if ev.reminder is None or (
            ev.reminder.minutes_before is None and ev.reminder.remind_at is None
        ):
            missing.append("reminder_time")
    elif nr.intent == "generate_meeting_protocol":
        if nr.protocol.source_document_id is None and nr.protocol.target_event_id is None:
            missing.append("source_document")
    elif nr.intent == "find_free_slots":
        # duration и диапазон имеют дефолты — данных достаточно.
        pass
    return missing


_FIELD_QUESTIONS = {
    "title": "Как назвать встречу / какая тема?",
    "date": "На какую дату планируем?",
    "start_time": "В какое время начать (или сколько минут займёт)?",
    "format": "Встреча будет онлайн или очно?",
    "origin_city": "Из какого города выезжаем?",
    "destination_city": "В какой город едем?",
    "departure_date": "На какую дату нужны билеты?",
    "target_event": "Какую встречу вы имеете в виду (название или дата)?",
    "new_time": "На какое время/дату перенести?",
    "reminder_time": "За сколько до встречи напомнить?",
    "source_document": "Прикрепите документ/стенограмму встречи — по нему соберу протокол.",
}


def build_clarifying_question(missing: list[str]) -> str | None:
    if not missing:
        return None
    parts = [_FIELD_QUESTIONS.get(m, m) for m in missing[:2]]
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Локальный нормализатор                                                       #
# --------------------------------------------------------------------------- #
def normalize_local(settings: Settings, message: str, now: datetime | None = None) -> NormalizedRequest:
    now = now or datetime.now()
    text = message.strip()
    intent = detect_intent(text)
    lang = detect_language(text)

    nr = NormalizedRequest(
        intent=intent,
        confidence=0.9 if intent != "unknown" else 0.3,
        original_text=text,
        language=lang,
        source="local",
    )

    tz = settings.app.timezone

    if intent in {"create_event", "update_event", "move_event", "create_reminder",
                  "show_calendar", "find_free_slots"}:
        ev = nr.event
        ev.timezone = tz
        ev.date = parse_date(text, now)
        ev.start_time, ev.end_time, ev.duration_minutes = parse_time_and_duration(text)
        ev.format = parse_format(text)
        prio, imp = parse_priority(text)
        ev.priority = prio
        ev.importance = imp
        ev.participants = _EMAIL_RE.findall(text)
        ev.meeting_url = _extract_url(text)
        if ev.format is None and ev.meeting_url:
            ev.format = "online"
        # город/адрес для очных встреч
        city = _first_city(text)
        if city:
            ev.city = city
        if intent != "find_free_slots":
            ev.title = _guess_title(text, intent)

        # напоминание
        rem = _parse_reminder(text)
        if rem:
            ev.reminder = rem

    if intent in {"delete_event", "update_event", "move_event", "create_reminder"}:
        nr.target_event = _guess_target_event(text, now)
        if intent == "move_event":
            nr.event.date = parse_date(text, now)
            nr.event.start_time, nr.event.end_time, nr.event.duration_minutes = parse_time_and_duration(text)

    if intent == "find_tickets":
        origin, dest = _extract_cities(text)
        tr = TravelData(
            origin_city=origin,
            destination_city=dest,
            departure_date=parse_date(text, now),
            transport_type=_transport_type(text),
        )
        prefs = []
        if re.search(r"деш[её]в|недорог|эконом", text, re.I):
            prefs.append("cheapest")
        if re.search(r"быстр|скор", text, re.I):
            prefs.append("fastest")
        if re.search(r"без\s+пересад|прям", text, re.I):
            prefs.append("direct")
        tr.preferences = prefs
        m = re.search(r"до\s+(\d[\d\s]{2,})\s*(?:р|руб|₽)", text, re.I)
        if m:
            tr.budget = float(m.group(1).replace(" ", ""))
        nr.travel = tr

    if intent == "generate_meeting_protocol":
        nr.protocol.target_event_id = _guess_target_event(text, now).event_id

    nr.missing_fields = compute_missing(nr)
    nr.clarifying_question = build_clarifying_question(nr.missing_fields)
    return nr


_URL_RE = re.compile(r"https?://[^\s]+")


def _extract_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def _transport_type(text: str) -> str:
    t = text.lower()
    if re.search(r"самол[её]т|авиа|перел[её]т|рейс", t):
        return "flight"
    if re.search(r"поезд|ж/?д|жд\b", t):
        return "train"
    return "any"


def _first_city(text: str) -> str | None:
    m = _CITY_IN_RE.search(text)
    if m:
        return _clean_city(m.group(1))
    return None


def _parse_reminder(text: str) -> ReminderData | None:
    t = text.lower()
    m = re.search(r"напомн\w*\s+за\s+(\d{1,3})\s*(мин|час|дн)", t)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        minutes = val if unit.startswith("мин") else val * 60 if unit.startswith("час") else val * 1440
        return ReminderData(minutes_before=minutes)
    if "напомн" in t:
        return ReminderData(minutes_before=60)
    return None


def _guess_target_event(text: str, now: datetime) -> TargetEvent:
    te = TargetEvent()
    m = re.search(r"(?:встреч\w*|событи\w*|созвон\w*)\s+[«\"]?([^»\"\n]{3,60})[»\"]?", text, re.I)
    if m:
        te.title = m.group(1).strip().strip(".,")
    d = parse_date(text, now)
    if d:
        te.date_hint = d.isoformat()
    m = re.search(r"#(\d+)", text)
    if m:
        te.event_id = int(m.group(1))
    return te


def _capitalize(s: str) -> str:
    s = s.strip(" .,")
    return (s[0].upper() + s[1:]) if s else s


def _guess_title(text: str, intent: str) -> str | None:
    # В кавычках — берём как есть.
    m = re.search(r"[«\"]([^»\"\n]{3,80})[»\"]", text)
    if m:
        return m.group(1).strip()

    # Убираем email-адреса участников (они не часть названия).
    text = _EMAIL_RE.sub("", text)
    # Убираем командный глагол в начале.
    t = re.sub(
        r"^\s*(?:запланируй|запланировать|назначь|назначить|поставь|поставить|"
        r"добавь|добавить|создай|создать|организуй|организовать)\s+",
        "", text.strip(), flags=re.I,
    )
    # Отрезаем хвост с датой/временем/форматом.
    t = _TEMPORAL_CUT.sub("", t).strip(" .,")
    # Нормализуем «встречу с/по …» → «Встреча с/по …».
    t = re.sub(r"^(?:встречу|встреча|созвон|совещание|митинг)\s+(с|по|о|об|про)\s+",
               lambda mm: f"Встреча {mm.group(1)} ", t, flags=re.I)
    # Убираем «висящие» предлоги в конце (напр. «Встреча с» после вырезанного email).
    t = re.sub(r"\s+(?:с|со|по|о|об|про|и|в|на)\s*$", "", t, flags=re.I).strip(" .,")
    if re.search(r"планёрк|планерк", t, re.I):
        return "Планёрка"
    # Осталось только слово «встреча/созвон…» без сути — используем общее имя.
    if t.lower().strip(" .,") in {"встреча", "встречу", "встречи", "созвон", "совещание", "митинг"}:
        return "Встреча"
    if 3 <= len(t) <= 80:
        return _capitalize(t)
    return None


# --------------------------------------------------------------------------- #
# Публичная точка входа                                                        #
# --------------------------------------------------------------------------- #
def normalize(
    settings: Settings,
    message: str,
    user_email: str | None = None,
    conversation_id: str | None = None,
    now: datetime | None = None,
) -> NormalizedRequest:
    """Нормализовать запрос: Dify → LLM → локальный парсер (с fallback)."""
    if settings.assistant.dify.enabled:
        try:
            raw = dify_client.normalize_via_dify(settings, message, user_email, conversation_id)
            nr = _merge_dify_result(settings, message, raw, now)
            nr.source = "dify"
            return nr
        except Exception as exc:  # noqa: BLE001 — намеренно широкий: любой сбой → fallback
            logger.warning("Dify normalize failed, fallback to local: %s", exc)
            nr = normalize_local(settings, message, now)
            nr.source = "dify-fallback"
            return nr

    if settings.assistant.llm.enabled:
        # Хук под прямой LLM-вызов. Пока не реализован → локальный парсер.
        logger.info("LLM normalize hook not implemented — using local parser")
        nr = normalize_local(settings, message, now)
        nr.source = "llm-fallback"
        return nr

    return normalize_local(settings, message, now)


def _merge_dify_result(settings: Settings, message: str, raw: dict, now: datetime | None) -> NormalizedRequest:
    """Собрать NormalizedRequest из JSON, вернутого Dify; добить недостающее локально."""
    # Локальный разбор как основа/страховка.
    base = normalize_local(settings, message, now)
    if not isinstance(raw, dict):
        return base
    try:
        merged = base.model_dump()
        for key in ("intent", "confidence", "language"):
            if raw.get(key):
                merged[key] = raw[key]
        for section in ("event", "travel", "protocol", "target_event"):
            if isinstance(raw.get(section), dict):
                merged[section] = {**merged.get(section, {}), **{
                    k: v for k, v in raw[section].items() if v not in (None, "", [])
                }}
        nr = NormalizedRequest.model_validate(merged)
    except Exception:  # noqa: BLE001
        nr = base
    nr.original_text = message
    nr.missing_fields = compute_missing(nr)
    nr.clarifying_question = build_clarifying_question(nr.missing_fields)
    return nr
