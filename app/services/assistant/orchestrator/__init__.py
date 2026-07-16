"""Оркестратор ассистента-секретаря (пакет, ARCH-01).

Единая точка входа чата: нормализует запрос → проверяет достаточность данных →
роутит по интенту → вызывает нужные сервисы (планирование, конфликты, билеты,
протоколы) → собирает ``AssistantResult`` с карточками и предложенными действиями.

Действия, затрагивающие календарь/других участников, не выполняются сразу:
создаётся черновик ``AssistantAction``, который пользователь подтверждает через
``confirm_action`` (эндпоинты /api/assistant/actions/{id}/confirm|reject).

Публичный контракт (не менять): ``run``, ``confirm_action``, ``reject_action``,
``create_action``, ``build_protocol_from_document``, ``expire_stale_actions``.
Модули пакета: ``core`` (run/dispatch), ``voice`` (голос секретаря),
``handlers_*`` (интенты), ``actions`` (confirm/reject), ``serializers``,
``common`` (черновики/утилиты).
"""
from app.services.assistant.orchestrator.actions import (  # noqa: F401
    confirm_action,
    expire_stale_actions,
    reject_action,
)
from app.services.assistant.orchestrator.common import create_action  # noqa: F401
from app.services.assistant.orchestrator.core import run  # noqa: F401
from app.services.assistant.orchestrator.handlers_protocol import (  # noqa: F401
    build_protocol_from_document,
)
from app.services.assistant.orchestrator.voice import (  # noqa: F401
    _apply_secretary_voice,
    _secretary_context,
)

__all__ = [
    "run",
    "confirm_action",
    "reject_action",
    "create_action",
    "build_protocol_from_document",
    "expire_stale_actions",
]
