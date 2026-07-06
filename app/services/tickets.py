"""Подбор билетов (поезд/самолёт/автобус) — тонкая обёртка над travel_search.

Каноническая реализация провайдеров живёт в ``app/services/assistant/travel_search.py``
(``TravelProvider`` / ``MockTravelProvider`` / провайдер под реальные API).
Этот модуль сохраняет прежний контракт ``search(settings, origin, destination, date)``
для эндпоинта ``/api/tickets/search`` и обратной совместимости.

--------------------------------------------------------------------------------
Как получать цену/ссылку/время в дороге «по-настоящему» (режим ``provider``)
--------------------------------------------------------------------------------
1. Официальные API-агрегаторы (предпочтительно, стабильно, легально):
   * Авиа: Travelpayouts / Aviasales API — цена, авиакомпания, время вылета/прилёта
     и партнёрская deeplink-ссылка на покупку.
   * Ж/Д: API РЖД (pass.rzd.ru) или агрегаторы (Туту, Яндекс.Путешествия).
   * Расписания/маршруты: Яндекс.Расписания (rasp.yandex API) — станции,
     departure/arrival, duration.
   Время в дороге = (arrival - departure); цена и ссылка приходят в ответе.

2. Парсинг веб-страниц (если официального API нет):
   * HTTP-запрос страницы поиска (httpx), разбор HTML (BeautifulSoup/lxml) ИЛИ
     прямой вызов того же JSON-XHR, что дёргает фронтенд (DevTools → Network).
   * Вытаскиваем цену, ссылку, время отправления/прибытия; длительность строкой
     ("7 ч 30 мин") парсим регуляркой в минуты.
   * Учитывать robots.txt, rate-limit, антибот (часто нужен Playwright/Selenium).

3. Fallback без провайдера: distance_km / avg_speed_kmh * 60
   (см. ``location_service`` и ``tickets.avg_speed_kmh`` в YAML).
"""
from __future__ import annotations

from datetime import datetime

from app.core.config import Settings
from app.schemas.assistant import TicketOption
from app.services.assistant import travel_search


def search(
    settings: Settings,
    origin: str,
    destination: str,
    depart_date: datetime | None = None,
    transport_type: str = "any",
) -> list[TicketOption]:
    """Единая точка входа: список вариантов билетов (делегирует travel_search)."""
    return travel_search.search(settings, origin, destination, depart_date, transport_type)
