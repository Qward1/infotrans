"""Слой оркестрации ассистента (нормализация → интент → сервисы → действия).

Экспортируем ключевые точки входа для роутеров и обратной совместимости.
"""
from app.services.assistant import (  # noqa: F401
    dify_client,
    document_reader,
    normalizer,
    notification_service,
    orchestrator,
    protocol_generator,
    travel_search,
)
from app.services.assistant.orchestrator import (  # noqa: F401
    confirm_action,
    reject_action,
    run,
)

__all__ = [
    "orchestrator",
    "normalizer",
    "dify_client",
    "travel_search",
    "document_reader",
    "protocol_generator",
    "notification_service",
    "run",
    "confirm_action",
    "reject_action",
]
