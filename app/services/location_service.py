"""Гео-логика: расстояния между городами, оценка времени в дороге,
буферы между встречами и проверка реалистичности очной встречи.

Единый источник географических оценок для планировщика (``availability`` /
``conflict_resolver``) и поиска билетов (``assistant.travel_search``).
Данные грубые (demo), но детерминированные; на следующем этапе заменяются
геокодером/маршрутизатором.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings

# Очень грубые расстояния между популярными городами (км).
_APPROX_DISTANCE_KM: dict[tuple[str, str], float] = {
    ("москва", "санкт-петербург"): 650,
    ("москва", "казань"): 720,
    ("москва", "нижний новгород"): 410,
    ("москва", "сочи"): 1360,
    ("москва", "екатеринбург"): 1420,
    ("москва", "новосибирск"): 3300,
    ("москва", "краснодар"): 1200,
    ("санкт-петербург", "казань"): 1150,
    ("санкт-петербург", "сочи"): 2000,
    ("екатеринбург", "казань"): 730,
}

# Средняя «дверь-в-дверь» скорость берётся из tickets.avg_speed_kmh (ARCH-06);
# fallback на 90 км/ч, если в конфиге ноль/мусор.
_DEFAULT_INTERCITY_KMH = 90.0


def _effective_speed_kmh(settings: Settings) -> float:
    speed = getattr(settings.tickets, "avg_speed_kmh", 0) or 0
    return float(speed) if speed > 0 else _DEFAULT_INTERCITY_KMH


def normalize_city(city: str | None) -> str:
    return (city or "").strip().lower()


def same_city(a: str | None, b: str | None) -> bool:
    na, nb = normalize_city(a), normalize_city(b)
    return bool(na) and na == nb


def city_distance_km(origin: str | None, destination: str | None, default: float = 700.0) -> float:
    """Грубая оценка расстояния между городами (км)."""
    a, b = normalize_city(origin), normalize_city(destination)
    if not a or not b:
        return 0.0
    if a == b:
        return 0.0
    return _APPROX_DISTANCE_KM.get((a, b)) or _APPROX_DISTANCE_KM.get((b, a)) or default


def intercity_travel_minutes(origin: str | None, destination: str | None, settings: Settings) -> int:
    """Оценка времени в пути между городами (минуты), дверь-в-дверь."""
    dist = city_distance_km(origin, destination)
    if dist <= 0:
        return 0
    return int(round(dist / _effective_speed_kmh(settings) * 60))


@dataclass(frozen=True)
class Place:
    """Локация встречи для расчёта буфера."""

    format: str = "offline"   # online | offline | hybrid
    city: str = ""
    address: str = ""

    @property
    def is_physical(self) -> bool:
        return self.format in ("offline", "hybrid")


def travel_buffer_minutes(a: Place, b: Place, settings: Settings) -> int:
    """Сколько минут нужно заложить на дорогу между двумя встречами.

    * онлайн ↔ что угодно → online_buffer (обычно 0);
    * тот же адрес → same_address_buffer;
    * тот же город, другой адрес → same_city_travel_buffer;
    * разные города → оценка по расстоянию (не меньше default_travel_buffer);
    * адрес/город неизвестен → default_travel_buffer.
    """
    sc = settings.scheduling
    if not a.is_physical or not b.is_physical:
        return sc.online_buffer_minutes

    # Разные города — самый долгий переезд.
    if a.city and b.city and normalize_city(a.city) != normalize_city(b.city):
        return max(sc.default_travel_buffer_minutes,
                   intercity_travel_minutes(a.city, b.city, settings))

    # Один и тот же город.
    if same_city(a.city, b.city):
        if a.address and b.address and a.address.strip().lower() == b.address.strip().lower():
            return sc.same_address_buffer_minutes
        return sc.same_city_travel_buffer_minutes

    # Город/адрес не заданы — берём безопасный буфер по умолчанию.
    return sc.default_travel_buffer_minutes


def is_offline_realistic(origin_city: str | None, meeting_city: str | None, settings: Settings) -> bool:
    """Реалистична ли очная встреча (успеет ли участник доехать в тот же день).

    Если города разные и дорога дольше realistic_offline_max_travel_minutes —
    очный формат нереалистичен (предлагаем онлайн).
    """
    if not origin_city or not meeting_city:
        return True
    if same_city(origin_city, meeting_city):
        return True
    travel = intercity_travel_minutes(origin_city, meeting_city, settings)
    return travel <= settings.scheduling.realistic_offline_max_travel_minutes


def describe_buffer(minutes: int) -> str:
    if minutes <= 0:
        return "без буфера на дорогу"
    if minutes < 60:
        return f"{minutes} мин на дорогу"
    h, m = divmod(minutes, 60)
    return f"{h}ч {m:02d}м на дорогу" if m else f"{h}ч на дорогу"
