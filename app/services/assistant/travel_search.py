"""Поиск авиа/жд/автобусных билетов.

Provider-интерфейс + mock-провайдер. Архитектура позволяет позже подключить
реальные API-агрегаторы (Travelpayouts/Aviasales, РЖД, Яндекс.Расписания) —
достаточно реализовать новый ``TravelProvider`` и выбрать его по ``tickets.mode``.

Как получать цену/ссылку/время в пути «по-настоящему» — см. docstring
``app/services/tickets.py`` (официальные API либо парсинг с расчётом
``arrive_at - depart_at``).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from app.core.config import Settings
from app.schemas.assistant import TicketOption
from app.services import location_service

_MODE_SPEED_KMH = {"plane": 750, "train": 90, "bus": 65}
_MODE_BASE_PRICE = {"plane": 4500, "train": 2200, "bus": 1400}
_MODE_OVERHEAD_H = {"plane": 2.0, "train": 0.5, "bus": 0.3}
_MODE_PER_KM = {"plane": 3.0, "train": 1.2, "bus": 0.8}
_MODE_RU = {"plane": "самолёт", "train": "поезд", "bus": "автобус"}


def _jitter(seed: str, spread: float) -> float:
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return (h % 1000) / 1000.0 * spread


# --------------------------------------------------------------------------- #
# Интерфейс провайдера                                                         #
# --------------------------------------------------------------------------- #
class TravelProvider(ABC):
    name = "abstract"

    @abstractmethod
    def search_flights(
        self, origin: str, destination: str, depart_date: datetime, settings: Settings
    ) -> list[TicketOption]: ...

    @abstractmethod
    def search_trains(
        self, origin: str, destination: str, depart_date: datetime, settings: Settings
    ) -> list[TicketOption]: ...

    def search_buses(
        self, origin: str, destination: str, depart_date: datetime, settings: Settings
    ) -> list[TicketOption]:
        return []


class MockTravelProvider(TravelProvider):
    """Локальный детерминированный провайдер: правдоподобные варианты без API."""

    name = "mock"

    def _options_for_mode(
        self, mode: str, origin: str, destination: str, depart_date: datetime, settings: Settings
    ) -> list[TicketOption]:
        distance = location_service.city_distance_km(origin, destination, default=700.0)
        speed = _MODE_SPEED_KMH[mode]
        hours = distance / speed + _MODE_OVERHEAD_H[mode]
        duration_minutes = int(round(hours * 60))
        currency = settings.tickets.currency

        out: list[TicketOption] = []
        for idx, hour in enumerate((8, 14, 19)):
            seed = f"{origin}|{destination}|{mode}|{idx}|{depart_date:%Y-%m-%d}"
            depart_at = depart_date.replace(hour=hour, minute=0, second=0, microsecond=0)
            arrive_at = depart_at + timedelta(minutes=duration_minutes)
            # число пересадок растёт с расстоянием для поезда/автобуса
            transfers = 0
            if mode != "plane" and distance > 1500:
                transfers = 1 + int(_jitter(seed, 1.5))
            elif mode == "plane" and distance > 3000 and idx == 2:
                transfers = 1
            base = _MODE_BASE_PRICE[mode] + distance * _MODE_PER_KM[mode]
            price = round(base * (0.85 + _jitter(seed, 0.4)) * (1 + 0.1 * transfers), 2)
            out.append(
                TicketOption(
                    provider=f"mock-{mode}",
                    mode=mode,
                    origin=origin,
                    destination=destination,
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    duration_minutes=duration_minutes,
                    transfers=transfers,
                    price=price,
                    currency=currency,
                    url=(
                        f"https://example-tickets.local/search?from={origin}"
                        f"&to={destination}&date={depart_date:%Y-%m-%d}&mode={mode}"
                    ),
                )
            )
        return out

    def search_flights(self, origin, destination, depart_date, settings):
        return self._options_for_mode("plane", origin, destination, depart_date, settings)

    def search_trains(self, origin, destination, depart_date, settings):
        return self._options_for_mode("train", origin, destination, depart_date, settings)

    def search_buses(self, origin, destination, depart_date, settings):
        return self._options_for_mode("bus", origin, destination, depart_date, settings)


class GenericApiTravelProvider(TravelProvider):
    """Заглушка боевого провайдера (следующий этап). Пока откатывается на mock."""

    name = "generic-api"

    def search_flights(self, origin, destination, depart_date, settings):
        raise NotImplementedError("Реальный travel-провайдер ещё не подключён")

    def search_trains(self, origin, destination, depart_date, settings):
        raise NotImplementedError("Реальный travel-провайдер ещё не подключён")


def get_provider(settings: Settings) -> TravelProvider:
    if settings.tickets.mode == "provider":
        return GenericApiTravelProvider()
    return MockTravelProvider()


def search(
    settings: Settings,
    origin: str,
    destination: str,
    depart_date: datetime | None = None,
    transport_type: str = "any",
) -> list[TicketOption]:
    """Единая точка входа. transport_type: flight | train | any."""
    depart_date = depart_date or (datetime.now() + timedelta(days=1))
    provider = get_provider(settings)

    def _collect(p: TravelProvider) -> list[TicketOption]:
        options: list[TicketOption] = []
        try:
            if transport_type in ("flight", "any"):
                options += p.search_flights(origin, destination, depart_date, settings)
            if transport_type in ("train", "any"):
                options += p.search_trains(origin, destination, depart_date, settings)
            if transport_type == "any":
                options += p.search_buses(origin, destination, depart_date, settings)
        except NotImplementedError:
            raise
        return options

    try:
        options = _collect(provider)
    except NotImplementedError:
        # Мягкий откат на mock, чтобы UI не падал.
        options = _collect(MockTravelProvider())

    options.sort(key=lambda o: o.price)
    return options


def explain_option(opt: TicketOption) -> str:
    """Человекочитаемое объяснение варианта (для travel_assistant)."""
    hrs, mins = divmod(opt.duration_minutes, 60)
    transfers = "без пересадок" if opt.transfers == 0 else f"пересадок: {opt.transfers}"
    return (
        f"{_MODE_RU.get(opt.mode, opt.mode)}: {opt.depart_at:%d.%m %H:%M} → "
        f"{opt.arrive_at:%d.%m %H:%M} ({hrs}ч {mins:02d}м, {transfers}), "
        f"{opt.price:.0f} {opt.currency}"
    )
