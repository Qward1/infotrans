# 🏛️ Архитектура — Умный цифровой календарь встреч и поездок

Документ описывает устройство системы: слои, поток данных, ключевые алгоритмы и точки
расширения. MVP намеренно построен так, чтобы demo/mock-режим работал без внешних ключей,
а переход на «боевые» интеграции сводился к флагам в YAML и реализации адаптеров.

```
Браузер (Jinja2 + vanilla JS + CSS)
        │  HTML-страницы + JSON API (fetch)
        ▼
FastAPI (routers)  ──►  Services (бизнес-логика)  ──►  SQLAlchemy ORM  ──►  SQLite/Postgres
        │                        │
        │                        ├─ assistant/ (оркестратор, нормализатор, адаптеры)
        │                        └─ scheduling / availability / conflict_resolver / stats
        ▼
Config (YAML → Pydantic Settings)   Dify / LLM / Travel / Notification провайдеры (mock↔real)
```

---

## 1. Backend

**Стек:** FastAPI, Starlette (сессии), SQLAlchemy 2.0 (typed ORM), Pydantic 2, Jinja2.

**Сборка приложения** (`app/main.py`):
- `SessionMiddleware` — сессия в подписанном cookie (секрет из YAML).
- `StaticFiles` — раздача `app/static`.
- `lifespan` → `bootstrap()` — таблицы, seed-админ, demo-данные.
- Обработчики `NotAuthenticated` (401/redirect на `/login`) и `NotAuthorized`
  (403-страница / JSON) — пользователю никогда не показывается raw traceback.
- Роутеры: `auth, dashboard, calendar, chat, documents, travel, notifications, admin,
  settings, api`.

**Слои:**
- `routers/` — тонкие: разбор запроса, вызов сервиса, рендер шаблона или JSON.
- `services/` — вся бизнес-логика (чистые функции + функции над `Session`), тестируется
  в изоляции.
- `models/` — ORM-модели; `schemas/` — Pydantic-контракты API.
- `core/` — конфиг, БД, безопасность, зависимости авторизации.

**Авторизация** (`core/permissions.py`): `require_user` / `require_admin` как FastAPI-
зависимости; `get_current_user_optional` для страниц со свободным входом. Проверка ролей —
и на страницах, и на API.

---

## 2. Frontend

Без сборщика: серверный Jinja2 + один `app.css` + модульные скрипты в `static/js/` (`core.js` — общий слой, далее `calendar/chat/documents/travel/admin.js`).

- **Стиль liquid glass** (`app.css`): CSS-переменные, класс `.glass` (blur + прозрачность +
  тень), синяя палитра, светлая/тёмная темы (через `:root[data-theme]`, тема в `localStorage`,
  применяется до отрисовки — без мигания). Адаптив: сайдбар сворачивается на узких экранах,
  сетки перестраиваются, на больших мониторах контент центрируется.
- **`static/js/*.js`** — модули, включающиеся по наличию якорного DOM-элемента: тема, мобильный
  сайдбар, тосты, `api()`-хелпер (обёртка над `fetch` с аккуратной обработкой ошибок),
  модалка события, чат-ассистент (рендер карточек и кнопок действий), модалка пользователя,
  уведомления (топбар), документы (upload + протокол), билеты (поиск + карточки).
- **Компоненты UI:** карточки, таблицы, бейджи (admin/user, online/offline/hybrid, приоритет,
  конфликт), empty states, loading-спиннеры, модалки, тосты, простые графики (полосы/столбики).

**Поток чата:** `POST /api/chat` → `AssistantResult` (reply + intent + cards[] +
suggested_actions[] + alternative_slots/travel_options/protocol). JS рендерит карточки по
`kind` и кнопки по `type` (подтверждение действия → `POST /api/assistant/actions/{id}/confirm`).

---

## 3. База данных

SQLAlchemy 2.0, декларативные модели. По умолчанию SQLite (`data/app.db`); URL меняется одной
строкой в YAML (Postgres/MySQL). «Миграции» для MVP — `Base.metadata.create_all` (без Alembic).

**Модели:**
- `User` — email, хэш пароля (`pbkdf2_sha256`), роль (`user`/`admin`), активность.
- `CalendarEvent` — время, timezone, формат (online/offline/hybrid), город/адрес/ссылка,
  важность, приоритет 0–10, статус (planned/completed/cancelled), источник.
- `EventParticipant` — участники встречи (для мультиучастниковой доступности).
- `Reminder` — напоминания (канал, время, статус).
- `AssistantAction` — черновик действия ассистента (pending → confirmed/rejected), payload в JSON.
- `Document` — загруженный документ (имя, тип, размер, извлечённый текст).
- `Notification` — уведомление (канал, текст, статус, meta с event_id).
- `AuditLog` — журнал действий (актор, действие, объект, payload, время).

---

## 4. Оркестрация ассистента

Единая точка входа чата — `services/assistant/orchestrator.run(settings, db, user, message)`:

1. **Нормализация** (`normalizer.normalize`) → `NormalizedRequest` (строгая Pydantic-схема:
   intent + event/travel/protocol/target_event + missing_fields + clarifying_question).
2. **Проверка достаточности** — если не хватает полей, возвращается уточняющий вопрос
   (действие не выполняется).
3. **Роутинг по интенту** — диспетч в хендлер (`_handle_create_event`, `_handle_find_slots`,
   `_handle_find_tickets`, `_handle_generate_protocol`, `_handle_target_action`, …).
4. **Сборка `AssistantResult`** — reply, карточки (`AssistantCard`), предложенные действия
   (`SuggestedAction`).

**Паттерн подтверждения:** действия, затрагивающие календарь/других участников, не выполняются
сразу — создаётся `AssistantAction` (черновик в БД), пользователь подтверждает через
`POST /api/assistant/actions/{id}/confirm|reject`. Личная встреча без конфликтов создаётся сразу.

Уровни нормализатора (выбор по YAML, единый контракт):
- **Локальный** (по умолчанию) — детерминированные regex/эвристики (даты, время, формат,
  города, участники, приоритет). Без ключей.
- **LLM** (`assistant.llm.enabled`) — хук под прямой вызов модели (сейчас → fallback на local).
- **Dify** (`assistant.dify.enabled`) — вызов ассистента `request_normalizer`.

---

## 5. Dify

Каталог `dify/assistants/` — 4 импортируемых advanced-chat workflow (start → LLM со
`structured_output` → answer):

| Ассистент | Роль |
| --- | --- |
| `request_normalizer` | текст → строгий JSON-интент (`NormalizedRequest`) |
| `smart_calendar_secretary` | секретарь: выбор сервиса, деловой ответ |
| `protocol_assistant` | текст встречи → протокол (решения/задачи/…/follow-up) |
| `travel_assistant` | запрос поездки → параметры поиска билетов |

Клиент — `assistant/dify_client.py` (`POST {base_url}/chat-messages`, ключ ассистента из
YAML, `response_mode: blocking`). Поле `answer` (строгий JSON) мапится в Pydantic-схемы.
**Любая ошибка Dify → мягкий откат на локальный режим** (`source="dify-fallback"`) — demo
не падает. Импорт и ключи — см. `dify/README.md`.

---

## 6. Алгоритм планирования (свободные слоты)

`services/scheduling.py` (чистые функции над интервалами) + `services/availability.py`
(поверх, с БД и правилами):

1. Собрать занятость всех участников (владелец + `EventParticipant`), исключая отменённые.
2. Слить пересекающиеся занятые интервалы (`_merge`).
3. Для каждого дня диапазона взять рабочие часы из YAML (`working_hours`), вычесть занятость,
   вернуть окна ≥ нужной длительности.
4. Для каждого окна проверить **буферы на дорогу** до/после соседних офлайн-встреч
   (`location_service`) и добавить предупреждения.

Рабочие часы, длительность по умолчанию, число альтернатив, буферы — всё из
`config.yaml → scheduling`.

---

## 7. Разрешение конфликтов

`services/conflict_resolver.resolve_conflicts(db, settings, proposed, participant_ids)`:

- **Нет пересечений** → `schedule_as_is` (можно ставить; предупреждения о дороге отдельно).
- **Конфликтует более приоритетная / «высокоприоритетная» (≥ порога YAML)** →
  `suggest_alternatives` (её нельзя двигать автоматически, предлагаем другие слоты).
- **Приоритеты равны** → `ask_user_confirmation` (авто-перенос запрещён; выбор за пользователем).
- **Новая приоритетнее** → `propose_reschedule_lower_priority` (план переноса менее важной
  встречи, но только после подтверждения).

Плюс расчёт **альтернативных слотов** (тем же алгоритмом доступности) и **буферов на дорогу**.
Порог высокого приоритета — `scheduling.high_priority_threshold`.

---

## 8. Provider-адаптеры (билеты)

`services/assistant/travel_search.py` — интерфейс `TravelProvider` + реализации:
- `MockTravelProvider` (по умолчанию) — детерминированная генерация вариантов (самолёт/поезд/
  автобус): цена, число пересадок и время в пути оцениваются по расстоянию между городами
  (`location_service.city_distance_km`) и параметрам YAML.
- `GenericApiTravelProvider` — заглушка боевого провайдера (следующий этап), с мягким откатом
  на mock.

Выбор — по `tickets.mode` (`mock`/`provider`). Как подключить реальные API (Aviasales/РЖД/
Яндекс.Расписания) или парсинг — в docstring `services/tickets.py`.
`services/tickets.py` — тонкий shim над `travel_search` (совместимость `/api/tickets/search`).

---

## 9. Notification-адаптеры

`services/assistant/notification_service.py` — интерфейс `NotificationProvider` +
`MockNotificationProvider` (пишет `Notification` в БД и `AuditLog`; видно в топбаре и на
`/notifications`). Единый вызов `notify(db, settings, user, text, title, channel, meta)`.
Реальный мессенджер/email (MAX/Telegram/SMTP) подключается новым провайдером без изменения
вызовов; переключение — по `notifications.mode` и `notifications.messenger`.

Аналогично `document_reader.py` — извлечение текста из PDF (`pypdf`) / DOCX (`python-docx`) /
TXT/MD, с понятными предупреждениями при отсутствии библиотеки или текстового слоя (скан).
`protocol_generator.py` — Dify → локальный детерминированный парсер (решения/задачи/
ответственные/сроки/риски/follow-up), с demo-протоколом при пустом тексте.

---

## 10. Конфигурация (YAML)

Единственный источник — `config/config.yaml` (без `.env`), валидируется Pydantic-схемами
(`core/config.py`). Отсутствующие секции получают безопасные дефолты; путь переопределяется
`SMARTCAL_CONFIG`. Секции: `app`, `database`, `security`, `seed_admin`, `assistant`
(`llm`, `dify`), `scheduling`, `tickets`, `notifications`, `demo`.

- **Флаги режимов:** `assistant.dify.enabled`, `assistant.llm.enabled`, `tickets.mode`,
  `notifications.mode`, `demo.seed_on_startup`.
- **Правка через UI** (`/settings`, admin) — безопасный whitelist скалярных полей: секреты
  не показываются/не редактируются, перед записью создаётся `config.yaml.bak`, кэш настроек
  сбрасывается (изменения без перезапуска).
- **Кэш:** `get_settings()` — `lru_cache`; при сохранении из UI вызывается `cache_clear()`.

---

## Точки расширения (сводно)

| Хочу | Где |
| --- | --- |
| LLM-нормализация | `assistant/normalizer.py` (ветка `llm.enabled`) |
| Dify workflows | `dify/assistants/*`, `assistant/dify_client.py` |
| Реальные билеты | новый `TravelProvider` в `assistant/travel_search.py`, `tickets.mode: provider` |
| Мессенджер/email | новый `NotificationProvider` в `assistant/notification_service.py` |
| Другая БД | `config.yaml → database.url` (без изменений кода) |
| Напоминания по расписанию | модель `Reminder` готова; нужен планировщик (cron/APScheduler) |
| Миграции | заменить `create_all` на Alembic |
