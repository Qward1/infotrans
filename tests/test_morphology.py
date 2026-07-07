"""Склонение имён участников для «живых» реплик секретаря."""
from __future__ import annotations

import pytest

from app.services.assistant import morphology

pymorphy3 = pytest.importorskip("pymorphy3")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Мария Кузнецова", "Марией Кузнецовой"),
        ("Иван Петров", "Иваном Петровым"),
        ("Анна Сергеевна Иванова", "Анной Сергеевной Ивановой"),
        ("Пётр Ильич Чайковский", "Петром Ильичом Чайковским"),
    ],
)
def test_instrumental_declension(name, expected):
    assert morphology.inflect_full_name(name, morphology.INSTRUMENTAL) == expected


def test_non_russian_and_email_pass_through():
    assert morphology.inflect_full_name("guest@test.local") == "guest@test.local"
    assert morphology.inflect_full_name("John Smith") == "John Smith"
    assert morphology.inflect_full_name("") == ""


def test_fallback_when_analyzer_unavailable(monkeypatch):
    """Без pymorphy имя возвращается без изменений (mock-принцип)."""
    monkeypatch.setattr(morphology, "_analyzer", lambda: None)
    assert morphology.inflect_full_name("Мария Кузнецова") == "Мария Кузнецова"
