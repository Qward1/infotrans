"""Поиск авиа/жд/автобусных билетов через provider-адаптеры.

Production-flow не откатывается на синтетические варианты: если внешний provider
не настроен или недоступен, вызывающий API получает явную ошибку. ``mock`` остаётся
только явным demo/test-режимом.
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode, urljoin

import httpx

from app.core.config import Settings
from app.schemas.assistant import TicketOption
from app.services import location_service

_MODE_SPEED_KMH = {"plane": 750, "train": 90, "bus": 65}
_MODE_BASE_PRICE = {"plane": 4500, "train": 2200, "bus": 1400}
_MODE_OVERHEAD_H = {"plane": 2.0, "train": 0.5, "bus": 0.3}
_MODE_PER_KM = {"plane": 3.0, "train": 1.2, "bus": 0.8}
_MODE_RU = {"plane": "самолёт", "train": "поезд", "bus": "автобус"}
_TRANSPORT_TO_MODES = {
    "any": ("plane", "train", "bus"),
    "flight": ("plane",),
    "plane": ("plane",),
    "train": ("train",),
    "bus": ("bus",),
}
_SORT_KEYS = {"price", "departure", "duration"}

# Небольшой справочник для Aviasales/Travelpayouts, где API ждёт IATA-коды.
# Это не результаты поиска, а только преобразование часто вводимых русских городов.
_CITY_IATA = {
    "москва": "MOW",
    "санкт-петербург": "LED",
    "санкт петербург": "LED",
    "спб": "LED",
    "казань": "KZN",
    "сочи": "AER",
    "екатеринбург": "SVX",
    "новосибирск": "OVB",
    "нижний новгород": "GOJ",
    "самара": "KUF",
    "уфа": "UFA",
    "краснодар": "KRR",
    "ростов-на-дону": "ROV",
    "ростов на дону": "ROV",
    "пермь": "PEE",
    "красноярск": "KJA",
}
_CITY_SLUGS = {
    "москва": "Moskva",
    "санкт-петербург": "Sankt-Peterburg",
    "санкт петербург": "Sankt-Peterburg",
    "спб": "Sankt-Peterburg",
    "казань": "Kazan",
    "сочи": "Sochi",
    "екатеринбург": "Ekaterinburg",
    "новосибирск": "Novosibirsk",
    "нижний новгород": "Nizhniy-Novgorod",
    "самара": "Samara",
    "уфа": "Ufa",
    "краснодар": "Krasnodar",
    "ростов-на-дону": "Rostov-Na-Donu",
    "ростов на дону": "Rostov-Na-Donu",
    "пермь": "Perm",
    "красноярск": "Krasnoyarsk",
}
_RZD_STATION_IDS = {
    # UUID-идентификаторы публичного поиска ticket.rzd.ru для городов/вокзалов.
    # Карта намеренно небольшая: если города нет, даём fallback на официальный сайт.
    "москва": "5a26dbfb340c743bb49213f5",
    "санкт-петербург": "5a8ac90f340c7425a3d36780",
    "санкт петербург": "5a8ac90f340c7425a3d36780",
    "спб": "5a8ac90f340c7425a3d36780",
}


class TicketSearchError(Exception):
    """Базовая ошибка поиска билетов."""


class TicketValidationError(TicketSearchError):
    """Некорректные пользовательские параметры."""


class TicketProviderNotConfigured(TicketSearchError):
    """Внешний provider не настроен."""


class TicketProviderError(TicketSearchError):
    """Внешний provider вернул ошибку или недоступен."""


@dataclass(frozen=True)
class TicketSearchParams:
    origin: str
    destination: str
    depart_date: datetime
    return_date: datetime | None = None
    transport_type: str = "any"
    passengers: int = 1
    preferences: tuple[str, ...] = field(default_factory=tuple)
    sort_by: str = "price"


@dataclass(frozen=True)
class ExternalSearchLink:
    provider: str
    title: str
    mode: str
    url: str
    origin: str
    destination: str
    depart_date: str
    return_date: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "title": self.title,
            "mode": self.mode,
            "url": self.url,
            "origin": self.origin,
            "destination": self.destination,
            "depart_date": self.depart_date,
            "return_date": self.return_date,
            "note": self.note,
        }


def _jitter(seed: str, spread: float) -> float:
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    return (h % 1000) / 1000.0 * spread


def _norm_city(value: str) -> str:
    return " ".join(value.strip().lower().replace("ё", "е").split())


def _mode_for_transport(transport_type: str) -> tuple[str, ...]:
    key = (transport_type or "any").strip().lower()
    if key not in _TRANSPORT_TO_MODES:
        raise TicketValidationError("transport должен быть одним из: any, flight, train, bus")
    return _TRANSPORT_TO_MODES[key]


def _sort_options(options: list[TicketOption], sort_by: str) -> list[TicketOption]:
    sort_by = sort_by if sort_by in _SORT_KEYS else "price"
    if sort_by == "departure":
        return sorted(options, key=lambda o: o.depart_at)
    if sort_by == "duration":
        return sorted(options, key=lambda o: o.duration_minutes)
    return sorted(options, key=lambda o: o.price)


def _api_key(settings: Settings) -> str:
    provider = settings.tickets.provider
    env_name = getattr(provider, "api_key_env", "") or ""
    return (os.environ.get(env_name) if env_name else "") or provider.api_key


# --------------------------------------------------------------------------- #
# Интерфейс провайдера                                                         #
# --------------------------------------------------------------------------- #
class TravelProvider(ABC):
    name = "abstract"

    @abstractmethod
    def search(self, params: TicketSearchParams, settings: Settings) -> list[TicketOption]: ...


class MockTravelProvider(TravelProvider):
    """Локальный детерминированный provider для demo/test-режима."""

    name = "mock"

    def _options_for_mode(
        self,
        mode: str,
        params: TicketSearchParams,
        settings: Settings,
    ) -> list[TicketOption]:
        origin = params.origin
        destination = params.destination
        depart_date = params.depart_date
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
            price = round(base * (0.85 + _jitter(seed, 0.4)) * (1 + 0.1 * transfers) * params.passengers, 2)
            out.append(
                TicketOption(
                    provider=f"mock-{mode}",
                    carrier=f"Demo {_MODE_RU[mode]}",
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

    def search(self, params: TicketSearchParams, settings: Settings) -> list[TicketOption]:
        options: list[TicketOption] = []
        for mode in _mode_for_transport(params.transport_type):
            options += self._options_for_mode(mode, params, settings)
        return options


class GenericHttpTravelProvider(TravelProvider):
    """HTTP-adapter для внешнего API, возвращающего нормализованные билеты.

    Ожидаемый ответ: список или объект с ``items``/``results``/``data``. Поля строк
    мапятся гибко: ``carrier/provider``, ``mode/transport``, ``depart_at/departure_at``,
    ``arrive_at/arrival_at``, ``duration_minutes/duration``, ``price/value``.
    """

    name = "generic-http"

    def _request(self, params: TicketSearchParams, settings: Settings) -> object:
        provider = settings.tickets.provider
        key = _api_key(settings)
        if not provider.base_url or not key:
            raise TicketProviderNotConfigured(
                "Не настроен внешний API билетов: задайте tickets.provider.base_url "
                "и API-ключ в config.yaml или переменной окружения."
            )
        query = {
            "origin": params.origin,
            "destination": params.destination,
            "date": params.depart_date.strftime("%Y-%m-%d"),
            "transport": params.transport_type,
            "passengers": params.passengers,
            "currency": settings.tickets.currency,
        }
        if params.return_date:
            query["return_date"] = params.return_date.strftime("%Y-%m-%d")
        try:
            with httpx.Client(timeout=provider.timeout) as client:
                response = client.get(
                    provider.base_url,
                    params=query,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "X-API-Key": key,
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise TicketProviderError(f"API билетов вернул HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TicketProviderError(f"API билетов недоступен: {exc}") from exc

    def search(self, params: TicketSearchParams, settings: Settings) -> list[TicketOption]:
        return _parse_provider_items(self._request(params, settings), params, settings, self.name)


class TravelpayoutsProvider(TravelProvider):
    """Aviasales/Travelpayouts Data API adapter для авиабилетов."""

    name = "travelpayouts"

    def search(self, params: TicketSearchParams, settings: Settings) -> list[TicketOption]:
        requested_modes = _mode_for_transport(params.transport_type)
        if "plane" not in requested_modes:
            raise TicketProviderNotConfigured(
                "Текущий provider Travelpayouts поддерживает только авиабилеты. "
                "Для ЖД/автобусов настройте tickets.provider.name: generic и внешний HTTP endpoint."
            )
        provider = settings.tickets.provider
        key = _api_key(settings)
        if not key:
            raise TicketProviderNotConfigured(
                "Не настроен токен Travelpayouts: задайте SMARTCAL_TICKETS_API_KEY "
                "или tickets.provider.api_key."
            )
        origin = _iata(params.origin)
        destination = _iata(params.destination)
        if not origin or not destination:
            raise TicketValidationError(
                "Для Travelpayouts укажите город из поддерживаемого справочника или IATA-код аэропорта/города."
            )
        base_url = provider.base_url or "https://api.travelpayouts.com"
        url = urljoin(base_url.rstrip("/") + "/", "aviasales/v3/get_latest_prices")
        query = {
            "currency": settings.tickets.currency.lower(),
            "origin": origin,
            "destination": destination,
            "beginning_of_period": params.depart_date.strftime("%Y-%m-%d"),
            "period_type": "day",
            "one_way": "false" if params.return_date else "true",
            "page": 1,
            "sorting": "price",
            "limit": 30,
            "show_to_affiliates": "true",
        }
        try:
            with httpx.Client(timeout=provider.timeout) as client:
                response = client.get(
                    url,
                    params=query,
                    headers={"X-Access-Token": key, "Accept-Encoding": "gzip, deflate"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise TicketProviderError(f"Travelpayouts вернул HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TicketProviderError(f"Travelpayouts недоступен: {exc}") from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            raise TicketProviderError(str(payload.get("error") or "Travelpayouts вернул ошибку"))
        options = _parse_provider_items(payload, params, settings, self.name, forced_mode="plane")
        if params.passengers > 1:
            for option in options:
                option.price *= params.passengers
        return options


def get_provider(settings: Settings) -> TravelProvider:
    if settings.tickets.mode == "mock":
        return MockTravelProvider()
    provider_name = (settings.tickets.provider.name or "generic").strip().lower()
    if provider_name in {"travelpayouts", "aviasales"}:
        return TravelpayoutsProvider()
    if provider_name in {"generic", "http", "generic-http"}:
        return GenericHttpTravelProvider()
    return GenericHttpTravelProvider()


def _iata(city_or_code: str) -> str | None:
    value = city_or_code.strip()
    if len(value) == 3 and value.isascii() and value.isalpha():
        return value.upper()
    return _CITY_IATA.get(_norm_city(value))


def _slug(city: str) -> str:
    normalized = _norm_city(city)
    if normalized in _CITY_SLUGS:
        return _CITY_SLUGS[normalized]
    # Fallback для ссылок Туту/Busfor: не идеально, но лучше, чем терять маршрут.
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
        "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    raw = "".join(translit.get(ch, ch) for ch in normalized)
    parts = [p.capitalize() for p in raw.replace("-", " ").split() if p]
    return "-".join(parts) or quote(city.strip())


def external_search_links(params: TicketSearchParams) -> list[ExternalSearchLink]:
    """Ссылки на реальные сайты поиска без API-ключей.

    Эти ссылки не являются результатами билетов и не содержат сгенерированных цен.
    Они ведут пользователя на сайт-источник, где выполняется актуальный поиск,
    показываются места и оформляется покупка.
    """
    modes = _mode_for_transport(params.transport_type)
    date_iso = params.depart_date.strftime("%Y-%m-%d")
    ddmm = params.depart_date.strftime("%d%m")
    ret_ddmm = params.return_date.strftime("%d%m") if params.return_date else ""
    ret_iso = params.return_date.strftime("%Y-%m-%d") if params.return_date else None
    links: list[ExternalSearchLink] = []

    if "plane" in modes:
        origin_iata = _iata(params.origin)
        destination_iata = _iata(params.destination)
        if origin_iata and destination_iata:
            suffix = f"{origin_iata}{ddmm}{destination_iata}{ret_ddmm}{params.passengers}"
            avia_url = f"https://www.aviasales.ru/search/{suffix}"
            note = "Открывает готовый поиск Aviasales по маршруту и дате."
        else:
            avia_url = "https://www.aviasales.ru/"
            note = "Город не найден в локальном IATA-справочнике; маршрут нужно подтвердить на сайте."
        links.append(
            ExternalSearchLink(
                provider="Aviasales",
                title="Авиабилеты на Aviasales",
                mode="plane",
                url=avia_url,
                origin=params.origin,
                destination=params.destination,
                depart_date=date_iso,
                return_date=ret_iso,
                note=note,
            )
        )

    if "train" in modes:
        origin_id = _RZD_STATION_IDS.get(_norm_city(params.origin))
        destination_id = _RZD_STATION_IDS.get(_norm_city(params.destination))
        if origin_id and destination_id:
            rzd_url = f"https://ticket.rzd.ru/searchresults/v/1/{origin_id}/{destination_id}/{date_iso}"
            rzd_note = "Официальный поиск РЖД по маршруту и дате."
        else:
            rzd_url = "https://ticket.rzd.ru/"
            rzd_note = "Официальный сайт РЖД; маршрут нужно выбрать на сайте."
        links.append(
            ExternalSearchLink(
                provider="РЖД",
                title="ЖД билеты на РЖД",
                mode="train",
                url=rzd_url,
                origin=params.origin,
                destination=params.destination,
                depart_date=date_iso,
                return_date=ret_iso,
                note=rzd_note,
            )
        )
        train_url = f"https://www.tutu.ru/poezda/{_slug(params.origin)}/{_slug(params.destination)}/"
        links.append(
            ExternalSearchLink(
                provider="Туту",
                title="ЖД билеты на Туту",
                mode="train",
                url=train_url,
                origin=params.origin,
                destination=params.destination,
                depart_date=date_iso,
                return_date=ret_iso,
                note="Страница маршрута Туту с расписанием, ценами и покупкой билетов.",
            )
        )

    if "bus" in modes:
        bus_url = f"https://bus.tutu.ru/bilety_na_avtobus/{_slug(params.origin)}/{_slug(params.destination)}/"
        links.append(
            ExternalSearchLink(
                provider="Туту Автобусы",
                title="Автобусы на Туту",
                mode="bus",
                url=bus_url,
                origin=params.origin,
                destination=params.destination,
                depart_date=date_iso,
                return_date=ret_iso,
                note="Страница маршрута Туту с расписанием автобусов и покупкой билетов.",
            )
        )
        busfor_url = (
            "https://busfor.ru/автобусы/"
            f"{quote(params.origin.strip())}/{quote(params.destination.strip())}"
            "?"
            + urlencode({"date": date_iso})
        )
        links.append(
            ExternalSearchLink(
                provider="Busfor",
                title="Автобусы на Busfor",
                mode="bus",
                url=busfor_url,
                origin=params.origin,
                destination=params.destination,
                depart_date=date_iso,
                return_date=ret_iso,
                note="Поиск автобусных билетов на внешнем сайте.",
            )
        )

    return links


def _items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("items") or payload.get("results") or payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("items") or data.get("results") or []
    return [x for x in data if isinstance(x, dict)]


def _parse_dt(value: object, fallback_date: datetime, hour: int = 0) -> tuple[datetime, str]:
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            if "T" in raw:
                return datetime.fromisoformat(raw), "datetime"
            parsed_date = datetime.strptime(raw[:10], "%Y-%m-%d")
            return parsed_date.replace(hour=hour), "date"
        except ValueError:
            pass
    return fallback_date.replace(hour=hour, minute=0, second=0, microsecond=0), "date"


def _first(item: dict, *names: str, default=None):
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return default


def _parse_provider_items(
    payload: object,
    params: TicketSearchParams,
    settings: Settings,
    provider_name: str,
    forced_mode: str | None = None,
) -> list[TicketOption]:
    options: list[TicketOption] = []
    allowed_modes = set(_mode_for_transport(params.transport_type))
    for item in _items(payload):
        mode = forced_mode or str(_first(item, "mode", "transport", "transport_type", default="")).lower()
        if mode == "flight":
            mode = "plane"
        if mode not in _MODE_RU:
            mode = forced_mode or "plane"
        if mode not in allowed_modes:
            continue

        depart_value = _first(item, "depart_at", "departure_at", "departure_time", "depart_date")
        depart_at, precision = _parse_dt(depart_value, params.depart_date, hour=0)
        duration = _first(item, "duration_minutes", "duration_to", "duration", default=0)
        try:
            duration_minutes = int(round(float(duration or 0)))
        except (TypeError, ValueError):
            duration_minutes = 0
        arrive_value = _first(item, "arrive_at", "arrival_at", "arrival_time")
        if arrive_value:
            arrive_at, _ = _parse_dt(arrive_value, depart_at)
        else:
            arrive_at = depart_at + timedelta(minutes=max(duration_minutes, 0))

        price = _first(item, "price", "value", "amount")
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            continue

        transfers = _first(item, "transfers", "number_of_changes", "stops", default=0)
        try:
            transfers_value = int(transfers or 0)
        except (TypeError, ValueError):
            transfers_value = 0

        link = str(_first(item, "url", "link", "deep_link", default="") or "")
        if link.startswith("/"):
            link = "https://www.aviasales.com" + link
        seats = _first(item, "available_seats", "seats_left", "availability")
        try:
            seats_value = int(seats) if seats not in (None, "") else None
        except (TypeError, ValueError):
            seats_value = None

        options.append(
            TicketOption(
                provider=str(_first(item, "provider", default=provider_name)),
                carrier=str(_first(item, "carrier", "airline", "company", default=provider_name)),
                mode=mode,
                origin=str(_first(item, "origin_name", "origin", default=params.origin)),
                destination=str(_first(item, "destination_name", "destination", default=params.destination)),
                depart_at=depart_at,
                arrive_at=arrive_at,
                duration_minutes=duration_minutes,
                transfers=transfers_value,
                price=price_value,
                currency=str(_first(item, "currency", default=settings.tickets.currency)).upper(),
                url=link,
                available_seats=seats_value,
                time_precision=precision,
            )
        )
    return options


def build_params(
    origin: str,
    destination: str,
    depart_date: datetime | None,
    transport_type: str = "any",
    return_date: datetime | None = None,
    passengers: int = 1,
    preferences: tuple[str, ...] | list[str] | None = None,
    sort_by: str = "price",
) -> TicketSearchParams:
    origin = origin.strip()
    destination = destination.strip()
    if not origin:
        raise TicketValidationError("Укажите город отправления")
    if not destination:
        raise TicketValidationError("Укажите город прибытия")
    if _norm_city(origin) == _norm_city(destination):
        raise TicketValidationError("Город отправления и прибытия не должны совпадать")
    if depart_date is None:
        raise TicketValidationError("Укажите дату отправления")
    if depart_date.date() < datetime.now().date():
        raise TicketValidationError("Дата отправления не может быть в прошлом")
    if return_date and return_date.date() < depart_date.date():
        raise TicketValidationError("Дата возвращения не может быть раньше даты отправления")
    if passengers < 1 or passengers > 9:
        raise TicketValidationError("Количество пассажиров должно быть от 1 до 9")
    _mode_for_transport(transport_type)
    sort_by = sort_by if sort_by in _SORT_KEYS else "price"
    return TicketSearchParams(
        origin=origin,
        destination=destination,
        depart_date=depart_date,
        return_date=return_date,
        transport_type=transport_type,
        passengers=passengers,
        preferences=tuple(preferences or ()),
        sort_by=sort_by,
    )


def search(
    settings: Settings,
    origin: str,
    destination: str,
    depart_date: datetime | None = None,
    transport_type: str = "any",
    return_date: datetime | None = None,
    passengers: int = 1,
    preferences: tuple[str, ...] | list[str] | None = None,
    sort_by: str = "price",
) -> list[TicketOption]:
    """Единая точка входа. transport_type: any | flight | train | bus."""
    params = build_params(
        origin,
        destination,
        depart_date,
        transport_type,
        return_date=return_date,
        passengers=passengers,
        preferences=preferences,
        sort_by=sort_by,
    )
    provider = get_provider(settings)
    return _sort_options(provider.search(params, settings), params.sort_by)


def explain_option(opt: TicketOption) -> str:
    """Человекочитаемое объяснение варианта (для travel_assistant)."""
    hrs, mins = divmod(opt.duration_minutes, 60)
    transfers = "без пересадок" if opt.transfers == 0 else f"пересадок: {opt.transfers}"
    return (
        f"{_MODE_RU.get(opt.mode, opt.mode)}: {opt.depart_at:%d.%m %H:%M} → "
        f"{opt.arrive_at:%d.%m %H:%M} ({hrs}ч {mins:02d}м, {transfers}), "
        f"{opt.price:.0f} {opt.currency}"
    )
