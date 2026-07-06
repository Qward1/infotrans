"""Обработка документов — совместимость.

Реальная реализация чтения PDF/DOCX/TXT живёт в
``app/services/assistant/document_reader.py``. Модуль оставлен как тонкий
слой совместимости.
"""
from __future__ import annotations

from app.services.assistant.document_reader import (  # noqa: F401
    SUPPORTED_EXTENSIONS,
    ExtractedDocument,
    extract,
)


def extract_text(filename: str, content_type: str, raw: bytes) -> ExtractedDocument:
    """Синоним ``document_reader.extract`` (историческое имя)."""
    return extract(filename, content_type, raw)
