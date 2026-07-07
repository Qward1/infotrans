"""Склонение русских имён/фамилий по падежам — для «живых» реплик секретаря.

Оркестратор строит фразы вида «встречу с <участник>», где участник должен стоять
в творительном падеже («с Марией Кузнецовой»), а не в именительном («с Мария
Кузнецова»). Здесь — тонкая обёртка над ``pymorphy3``.

Зависимость опциональная: если ``pymorphy3`` не установлен (или падают словари),
модуль мягко возвращает исходную форму имени — по тому же принципу, что и mock-
режим остальных провайдеров. Так demo работает и без морфологии, просто имена
остаются в именительном.
"""
from __future__ import annotations

import functools
import logging
import re

logger = logging.getLogger("smartcal.morphology")

# Граммемы pymorphy для собственных имён (Name — имя, Surn — фамилия, Patr — отчество).
_NAME_TAGS = frozenset({"Name", "Surn", "Patr"})
# Падежи pymorphy: nomn именительный, gent родительный, datv дательный,
# accs винительный, ablt творительный («с кем?»), loct предложный.
INSTRUMENTAL = "ablt"

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


@functools.lru_cache(maxsize=1)
def _analyzer():
    """Ленивая (и кэшированная) инициализация анализатора. ``None`` — морфология недоступна."""
    try:
        import pymorphy3  # noqa: PLC0415 — тяжёлый и опциональный импорт держим локально

        return pymorphy3.MorphAnalyzer()
    except Exception as exc:  # noqa: BLE001 — нет пакета или словарей → работаем без склонения
        logger.info("pymorphy3 недоступен — склонение имён отключено (%s)", exc)
        return None


def _detect_gender(first_word: str, analyzer) -> str | None:
    """Определить род по имени (чтобы фамилия склонялась согласованно)."""
    parses = analyzer.parse(first_word)
    for parse in parses:
        if "Name" in parse.tag.grammemes and parse.tag.gender:
            return parse.tag.gender
    for parse in parses:
        if parse.tag.gender:
            return parse.tag.gender
    return None


def _best_parse(word: str, gender: str | None, analyzer):
    """Выбрать разбор: приоритет — имя/фамилия в именительном нужного рода.

    Без этого «Кузнецова» (ж., им.) распознаётся и как род. падеж «Кузнецов» (м.),
    и творительный уходит в «Кузнецовым» вместо «Кузнецовой».
    """
    def score(parse) -> int:
        grammemes = parse.tag.grammemes
        value = 0
        if _NAME_TAGS & set(grammemes):
            value += 4
        if "nomn" in grammemes:
            value += 2
        if gender and parse.tag.gender == gender:
            value += 2
        return value

    return max(analyzer.parse(word), key=score)


def _inflect_word(word: str, case: str, gender: str | None, analyzer) -> str:
    parse = _best_parse(word, gender, analyzer)
    grammemes = {case}
    if gender:
        grammemes.add(gender)
    inflected = parse.inflect(grammemes) or parse.inflect({case})
    return inflected.word if inflected else word


def _capitalize(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def inflect_full_name(name: str, case: str = INSTRUMENTAL) -> str:
    """Склонить полное имя («Мария Кузнецова») в указанный падеж.

    Возвращает исходную строку без изменений, если морфология недоступна, строка
    не похожа на русское имя (email, латиница) или разбор не удался.
    """
    if not name or "@" in name or not _CYRILLIC_RE.search(name):
        return name
    analyzer = _analyzer()
    if analyzer is None:
        return name
    try:
        words = name.split()
        if not words:
            return name
        gender = _detect_gender(words[0], analyzer)
        return " ".join(_capitalize(_inflect_word(w, case, gender, analyzer)) for w in words)
    except Exception as exc:  # noqa: BLE001 — склонение не должно ронять ответ ассистента
        logger.warning("Не удалось склонить имя %r: %s", name, exc)
        return name
