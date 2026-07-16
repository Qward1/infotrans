"""Хендлер поиска билетов (mock/provider/sites через travel_search)."""
from __future__ import annotations

from datetime import datetime

from app.services import audit as audit_service
from app.services.assistant import travel_search
from app.services.assistant.schemas import AssistantCard
from app.services.assistant.travel_search import TicketSearchError


def handle_find_tickets(settings, db, user, nr, result, now):
    tr = nr.travel
    depart = datetime.combine(tr.departure_date, datetime.min.time()) if tr.departure_date else None
    ret = datetime.combine(tr.return_date, datetime.min.time()) if tr.return_date else None
    if settings.tickets.mode == "sites":
        try:
            params = travel_search.build_params(
                tr.origin_city or "",
                tr.destination_city or "",
                depart,
                tr.transport_type,
                return_date=ret,
                preferences=tr.preferences,
            )
            sources = [link.to_dict() for link in travel_search.external_search_links(params)]
        except TicketSearchError as exc:
            result.status = "error"
            result.reply = f"Не удалось подготовить поиск билетов: {exc}"
            return
        result.status = "done"
        result.reply = (
            f"Подготовил поиск {tr.origin_city} → {tr.destination_city}"
            + (f" на {tr.departure_date:%d.%m}" if tr.departure_date else "")
            + ". Откройте подходящий сайт — актуальные цены и места будут там."
        )
        result.cards.append(AssistantCard(kind="travel_sources", title="Поиск на сайтах", data={"sources": sources}))
        audit_service.record(db, actor_user_id=user.id, action="search_tickets", entity_type="travel",
                             payload={"origin": tr.origin_city, "destination": tr.destination_city,
                                      "source_mode": "sites", "count": len(sources)})
        return
    # FN-06/BUG-26: предпочтения управляют сортировкой, фильтры применяются к выдаче.
    prefs = set(tr.preferences or [])
    sort_by = "duration" if "fastest" in prefs else "price"
    try:
        options = travel_search.search(
            settings,
            tr.origin_city or "",
            tr.destination_city or "",
            depart,
            tr.transport_type,
            return_date=ret,
            preferences=tr.preferences,
            sort_by=sort_by,
        )
    except TicketSearchError as exc:
        result.status = "error"
        result.reply = f"Не удалось выполнить поиск билетов: {exc}"
        return
    applied: list[str] = []
    if "direct" in prefs:
        options = [o for o in options if o.transfers == 0]
        applied.append("без пересадок")
    if tr.budget:
        options = [o for o in options if o.price <= tr.budget]
        applied.append(f"до {tr.budget:.0f} {settings.tickets.currency}")
    options = options[:6]
    result.status = "done"
    result.travel_options = [o.model_dump(mode="json") for o in options]
    if not options:
        result.reply = "Не удалось подобрать варианты — уточните города и дату" + (
            f" (фильтры: {', '.join(applied)})." if applied else "."
        )
        return
    best = options[0]
    best_label = "Быстрее всего" if sort_by == "duration" else "Дешевле всего"
    result.reply = (
        f"Нашёл {len(options)} вариантов {tr.origin_city} → {tr.destination_city}"
        + (f" на {tr.departure_date:%d.%m}" if tr.departure_date else "")
        + (f" ({', '.join(applied)})" if applied else "")
        + f". {best_label}: {travel_search.explain_option(best)}."
    )
    result.cards.append(AssistantCard(kind="travel_options", title="Варианты поездки",
                                       data={"origin": tr.origin_city, "destination": tr.destination_city,
                                             "options": result.travel_options}))
    audit_service.record(db, actor_user_id=user.id, action="search_tickets", entity_type="travel",
                         payload={"origin": tr.origin_city, "destination": tr.destination_city,
                                  "count": len(options)})
