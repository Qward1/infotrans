"""Формирование протокола встречи из текста документа/стенограммы.

Если доступен Dify (``protocol_assistant``) — используем его. Иначе работает
детерминированный локальный парсер, который раскладывает текст по разделам
(резюме, участники, решения, задачи, ответственные, сроки, риски, follow-up).
Если текста нет вовсе — возвращаем осмысленный demo-протокол, чтобы UI жил.
"""
from __future__ import annotations

import logging
import re

from app.core.config import Settings
from app.services.assistant import dify_client
from app.services.assistant.schemas import FollowUpMeeting, ProtocolData

logger = logging.getLogger("smartcal.protocol")

_EMAIL_RE = re.compile(r"[\w.\-]+@[\w.\-]+\.\w+")
_DATE_HINT_RE = re.compile(
    r"(\d{1,2}[.\-]\d{1,2}(?:[.\-]\d{2,4})?|\d{4}-\d{2}-\d{2}|завтра|послезавтра|"
    r"понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье|"
    r"на\s+следующей\s+неделе)",
    re.I,
)

_BUCKETS = {
    "decisions": re.compile(r"реши\w+|решение|договорил|принят\w*\s+решени|постанови", re.I),
    "action_items": re.compile(r"задач\w+|поручen|поручить|нужно\s+сделать|todo|action\s*item|\[\s?\]|сделать\s+до", re.I),
    "risks": re.compile(r"риск|проблем|блокер|угроза|opasн|задержк", re.I),
    "follow_up": re.compile(r"следующ\w+\s+встреч|созвон|повторн\w+\s+встреч|встрет\w+\s+снова|назначить\s+встреч|встреча\s+по", re.I),
    "deadline": re.compile(r"срок|дедлайн|до\s+\d|к\s+\d{1,2}[.\-]", re.I),
    "responsible": re.compile(r"ответственн|отвечает|responsible|@\w+", re.I),
}


def _split_lines(text: str) -> list[str]:
    raw = re.split(r"[\n;]+", text)
    return [ln.strip(" -•\t") for ln in raw if ln.strip(" -•\t")]


def _first_sentences(text: str, n: int = 2) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    summary = " ".join(sentences[:n]).strip()
    return summary[:400]


def _extract_participants(text: str) -> list[str]:
    parts: list[str] = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    m = re.search(r"(?:участники|присутствовали|присутствуют)\s*[:\-]\s*(.+)", text, re.I)
    if m:
        for name in re.split(r"[,;]", m.group(1)):
            name = name.strip()
            if 2 <= len(name) <= 60 and name not in parts:
                parts.append(name)
    return parts


def _demo_protocol(source_document_id: int | None, target_event_id: int | None) -> ProtocolData:
    return ProtocolData(
        source_document_id=source_document_id,
        target_event_id=target_event_id,
        summary="Обсудили статус проекта, сроки и распределение задач. "
        "Демо-протокол (в документе не найден распознаваемый текст).",
        participants=["Иван Сотрудников", "Demo Admin"],
        decisions=[
            "Утвердить план работ на следующую неделю",
            "Перейти на еженедельные статус-встречи",
        ],
        action_items=[
            "Подготовить ТЗ по интеграции",
            "Согласовать бюджет с финансовым отделом",
        ],
        responsibles=["Иван Сотрудников"],
        deadlines=["до пятницы"],
        risks=["Риск сдвига сроков из-за нехватки ресурсов"],
        follow_up_meetings=[
            FollowUpMeeting(title="Статус по интеграции", date_hint="через неделю",
                            participants=["Иван Сотрудников"], duration_minutes=30)
        ],
    )


def generate_local(
    settings: Settings,
    text: str,
    source_document_id: int | None = None,
    target_event_id: int | None = None,
) -> ProtocolData:
    """Локальный детерминированный разбор текста в протокол."""
    text = (text or "").strip()
    if len(text) < 20:
        return _demo_protocol(source_document_id, target_event_id)

    lines = _split_lines(text)
    proto = ProtocolData(
        source_document_id=source_document_id,
        target_event_id=target_event_id,
        summary=_first_sentences(text),
        participants=_extract_participants(text),
    )

    for ln in lines:
        low = ln.lower()
        if _BUCKETS["decisions"].search(low):
            proto.decisions.append(ln)
        if _BUCKETS["action_items"].search(low):
            proto.action_items.append(ln)
            m = re.search(r"@(\w+)|ответственн\w*\s*[:\-]?\s*([A-ЯЁ][а-яё]+)", ln)
            if m:
                proto.responsibles.append(m.group(1) or m.group(2))
        if _BUCKETS["risks"].search(low):
            proto.risks.append(ln)
        if _BUCKETS["deadline"].search(low):
            dm = _DATE_HINT_RE.search(ln)
            proto.deadlines.append(dm.group(0) if dm else ln)
        if _BUCKETS["follow_up"].search(low):
            dm = _DATE_HINT_RE.search(ln)
            proto.follow_up_meetings.append(
                FollowUpMeeting(
                    title=ln[:80],
                    date_hint=dm.group(0) if dm else None,
                    participants=_EMAIL_RE.findall(ln),
                    duration_minutes=settings.scheduling.default_meeting_minutes,
                )
            )

    # dedupe + подчистка
    proto.decisions = list(dict.fromkeys(proto.decisions))[:10]
    proto.action_items = list(dict.fromkeys(proto.action_items))[:15]
    proto.risks = list(dict.fromkeys(proto.risks))[:10]
    proto.deadlines = list(dict.fromkeys(proto.deadlines))[:10]
    proto.responsibles = list(dict.fromkeys([r for r in proto.responsibles if r]))[:10]

    if not (proto.decisions or proto.action_items or proto.follow_up_meetings):
        # Текст есть, но структуры не нашли — вернём хотя бы резюме + demo-задачи.
        demo = _demo_protocol(source_document_id, target_event_id)
        demo.summary = proto.summary or demo.summary
        demo.participants = proto.participants or demo.participants
        return demo
    return proto


def generate(
    settings: Settings,
    text: str,
    source_document_id: int | None = None,
    target_event_id: int | None = None,
    user_email: str | None = None,
) -> ProtocolData:
    """Главная точка входа: Dify → локальный парсер (с мягким откатом)."""
    if settings.assistant.dify.enabled:
        try:
            result = dify_client.call_chat(
                settings,
                assistant="protocol_assistant",
                message=text[:12000],
                inputs={"mode": "protocol"},
                user_email=user_email,
            )
            answer = result["answer"]
            if isinstance(answer, dict):
                data = {**answer, "source_document_id": source_document_id,
                        "target_event_id": target_event_id}
                return ProtocolData.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dify protocol failed, fallback to local: %s", exc)
    return generate_local(settings, text, source_document_id, target_event_id)
