"""HTTP-клиент Dify.

Все параметры (URL, ключи, режим) берутся только из YAML. Модуль ничего не
делает при ``dify.enabled=false`` — вызовы просто не производятся вызывающей
стороной. При ошибках поднимает исключение, которое нормализатор/оркестратор
ловит и мягко откатывается на локальный режим.

Формат вызова — Dify Chat App API:
    POST {base_url}/chat-messages
    Authorization: Bearer {api_key}
    {"inputs": {...}, "query": message, "user": email,
     "conversation_id": ..., "response_mode": "blocking"}
Ответ содержит поле ``answer`` — для наших ассистентов это строка со строгим JSON.
"""
from __future__ import annotations

import json
import logging

from app.core.config import Settings

logger = logging.getLogger("smartcal.dify")


class DifyError(RuntimeError):
    """Ошибка обращения к Dify (сеть, статус, разбор ответа)."""


def api_key_for(settings: Settings, assistant: str) -> str:
    """Ключ конкретного ассистента (или общий api_key, если персональный не задан)."""
    per_assistant = getattr(settings.assistant.dify.assistants, assistant, "") or ""
    return per_assistant or settings.assistant.dify.api_key


def call_chat(
    settings: Settings,
    assistant: str,
    message: str,
    inputs: dict | None = None,
    user_email: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Вызвать Dify chat-messages и вернуть распарсенный ответ.

    Возвращает dict вида ``{"answer": <str|dict>, "raw": <full response>}``.
    Бросает :class:`DifyError` при любой проблеме.
    """
    try:
        import httpx  # локальный импорт: без dify.enabled зависимость не нужна
    except ImportError as exc:  # pragma: no cover
        raise DifyError("httpx не установлен — включите его для режима dify") from exc

    dcfg = settings.assistant.dify
    api_key = api_key_for(settings, assistant)
    if not api_key or api_key.startswith("app-REPLACE"):
        raise DifyError(f"Не задан api_key для ассистента '{assistant}'")

    url = dcfg.base_url.rstrip("/") + "/chat-messages"
    payload = {
        "inputs": inputs or {},
        "query": message,
        "user": user_email or "smartcal-user",
        "response_mode": "blocking",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        with httpx.Client(timeout=dcfg.timeout) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise DifyError(f"Ошибка запроса к Dify: {exc}") from exc

    answer = body.get("answer", "")
    parsed = _try_parse_json(answer)
    return {"answer": parsed if parsed is not None else answer, "raw": body}


def normalize_via_dify(
    settings: Settings,
    message: str,
    user_email: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Прогнать сообщение через ассистента ``request_normalizer`` и вернуть JSON-интент.

    ``conversation_id`` здесь — это ID нашей сессии в приложении, а не ID диалога
    Dify (мы не сохраняем conversation_id, который возвращает сам Dify). Пересылать
    свой ID в Dify нельзя: Dify примет только ID, который сам ранее выдал, а для
    незнакомого ID отвечает 404 "Conversation Not Found". Поэтому каждый вызов
    нормализатора идёт как новый (stateless) диалог Dify.
    """
    result = call_chat(
        settings,
        assistant="request_normalizer",
        message=message,
        inputs={"user_tz": settings.app.timezone},
        user_email=user_email,
    )
    answer = result["answer"]
    if isinstance(answer, dict):
        return answer
    parsed = _try_parse_json(answer)
    if parsed is None:
        raise DifyError("Dify вернул не-JSON ответ нормализатора")
    return parsed


def _try_parse_json(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    # Иногда модель оборачивает JSON в ```json ... ```
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
