"""«Голос» секретаря: озвучивание ответа ассистентом smart_calendar_secretary."""
from __future__ import annotations

from app.core.config import Settings
from app.models.user import User
from app.services.assistant import dify_client
from app.services.assistant.orchestrator.common import logger
from app.services.assistant.schemas import AssistantResult


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
