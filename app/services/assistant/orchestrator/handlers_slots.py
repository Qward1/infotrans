"""Хендлер поиска свободных слотов (свои/участники/сотрудники)."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services import availability
from app.services.assistant import calendar_context
from app.services.assistant.orchestrator.common import resolve_participant_ids
from app.services.assistant.orchestrator.handlers_events import resolve_employee_targets
from app.services.assistant.orchestrator.serializers import prefill_from_nr
from app.services.assistant.schemas import AssistantCard, SuggestedAction


def handle_find_slots(settings, db, user, nr, result, now):
    ev = nr.event
    duration = ev.duration_minutes or settings.scheduling.default_meeting_minutes
    requested_targets = resolve_employee_targets(settings, db, user, nr, result)
    if requested_targets is None:
        return
    if requested_targets:
        requested_range = calendar_context.infer_date_range(nr.original_text, settings, now)
        availability_items = [
            calendar_context.employee_availability(
                db,
                settings,
                user,
                target,
                requested_range,
                requested_slot_duration=duration,
            )
            for target in requested_targets
        ]
        result.status = "done"
        result.cards.append(
            AssistantCard(
                kind="employee_availability",
                title="Занятость сотрудников",
                data={"items": availability_items},
            )
        )
        if len(availability_items) == 1:
            item = availability_items[0]
            result.alternative_slots = item["availableSlots"]
            slots_count = len(item["availableSlots"])
            busy_count = len(item["busyIntervals"])
            if slots_count:
                result.reply = (
                    f"{item['name']}: нашёл {slots_count} свободных окон на {item['requestedRange']['label']} "
                    f"длительностью от {duration} мин. Занятых интервалов: {busy_count}."
                )
                for slot in item["availableSlots"][:3]:
                    result.suggested_actions.append(
                        SuggestedAction(
                            type="create_event",
                            label=f"Занять {datetime.fromisoformat(slot['start_at']):%d.%m %H:%M}",
                            style="ghost",
                            payload={
                                "owner_id": item["employeeId"],
                                "start_at": slot["start_at"],
                                "end_at": slot["end_at"],
                                "source": "assistant",
                            },
                        )
                    )
            else:
                result.reply = (
                    f"{item['name']}: свободных слотов на {item['requestedRange']['label']} "
                    f"длительностью {duration} мин не найдено."
                )
            return

        lines = []
        any_slots = False
        for item in availability_items:
            count = len(item["availableSlots"])
            any_slots = any_slots or count > 0
            lines.append(f"• {item['name']}: свободных окон {count}, занятых интервалов {len(item['busyIntervals'])}")
        result.reply = (
            f"Проверил занятость на {availability_items[0]['requestedRange']['label']} "
            f"(слот от {duration} мин):\n" + "\n".join(lines)
        )
        if not any_slots:
            result.reply += "\nСвободных слотов не найдено."
        return

    participant_ids, unresolved = resolve_participant_ids(db, ev.participants)
    all_ids = list(dict.fromkeys([user.id, *participant_ids]))
    if unresolved:
        result.warnings.append("Не нашёл участников: " + ", ".join(unresolved))

    if ev.date:
        range_start = datetime.combine(ev.date, availability.parse_working_hours(settings)[0])
        range_end = datetime.combine(ev.date, availability.parse_working_hours(settings)[1])
    else:
        range_start = now
        range_end = now + timedelta(days=7)

    slots = availability.find_free_slots(
        db, settings, all_ids, range_start, range_end, duration_minutes=duration,
        city=ev.city or "", address=ev.address or "", meeting_format=ev.format or "offline",
        not_before=now)

    result.status = "done"
    result.alternative_slots = [s.to_dict() for s in slots]
    if not slots:
        result.reply = "Свободных окон в рабочих часах не нашлось. Попробуйте другой диапазон или короче встречу."
        return
    who = "у вас" if len(all_ids) <= 1 else f"у всех {len(all_ids)} участников"
    result.reply = f"Нашёл {len(slots)} свободных окон ({who}), длительность {duration} мин."
    # UX-06: контекст диалога (тема/участники/формат) едет вместе со слотами,
    # чтобы «Занять» открывал полностью заполненную форму.
    prefill = {k: v for k, v in prefill_from_nr(nr).items() if k not in {"start_at", "end_at"}}
    result.cards.append(AssistantCard(kind="alternative_slots", title="Свободные окна",
                                       data={"slots": result.alternative_slots, "prefill": prefill}))
    for s in slots[:3]:
        result.suggested_actions.append(SuggestedAction(
            type="create_event", label=f"Занять {s.start:%d.%m %H:%M}", style="ghost",
            payload={**prefill, "start_at": s.start.isoformat(timespec="minutes"),
                     "end_at": s.end.isoformat(timespec="minutes"), "source": "assistant"}))
