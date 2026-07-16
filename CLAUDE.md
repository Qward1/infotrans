# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

«Умный цифровой календарь встреч и поездок» — product-like MVP на FastAPI: недельный
календарь, чат-ассистент (локальный нормализатор + задел под Dify/LLM), планирование
слотов с конфликт-резолвингом, поиск билетов, разбор документов в протоколы, уведомления,
админка. Подробная архитектура — в [ARCHITECTURE.md](ARCHITECTURE.md); обзор и demo-доступы —
в [README.md](README.md); сценарий показа — в [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

## Команды

```bash
uvicorn app.main:app --reload          # запуск (создаёт таблицы + seed-админ + demo-данные)
python3 -m pytest                      # весь набор тестов (~40)
python3 -m pytest tests/test_smoke.py  # один файл
python3 -m pytest tests/test_scheduling.py::test_find_free_slots  # один тест
python3 -m compileall app              # быстрая проверка компиляции всех модулей
```

В этом окружении интерпретатор вызывается как `python3`, не `python`.

## Правила, которые нельзя нарушать

- **Пароли — `pbkdf2_sha256` (passlib), НЕ bcrypt.** В окружении стоит bcrypt 4.x,
  несовместимый с passlib 1.7.4 (падает на инициализации CryptContext). pbkdf2 переносим,
  bcrypt-хэши всё равно проверяются. Не «чинить» обратно на bcrypt.
- **Конфигурация только в YAML, `.env` отсутствует намеренно.** Единственный источник —
  `config/config.yaml` (в `.gitignore`; в репозитории только `config/config.example.yaml`).
  Путь переопределяется переменной `SMARTCAL_CONFIG`. Схемы и дефолты — в
  [app/core/config.py](app/core/config.py); отсутствующие секции получают безопасные значения.
- **`get_settings()` кэширован (`lru_cache`).** После любой правки конфигурации в рантайме
  (например, из UI `/settings`) обязательно `get_settings.cache_clear()`, иначе изменения
  не подхватятся.
- **Режимом ассистента управляет `assistant.dify.enabled`, а НЕ `assistant.mode`.**
  Поле `assistant.mode` оставлено справочно. `dify.enabled: false` → локальный нормализатор;
  `true` → вызовы Dify с обязательным мягким откатом на локальный режим (`source="dify-fallback"`).
- **`assistant.yml` в корне — эталон формата Dify-ассистента, не удалять.**
- **Миграций нет** — схема поднимается через `Base.metadata.create_all` в
  [app/bootstrap.py](app/bootstrap.py). При изменении моделей это учитывать (для прод — Alembic).
- **Reverse-proxy: `app.root_path` ≠ `app.base_path`.** Прокси `t1v.scibox.tech` СРЕЗАЕТ
  под-путь `/jnserver/1120/application` и форвардит чистые пути. Поэтому ASGI `root_path`
  (передаётся в `FastAPI(root_path=...)`) держим `""` — иначе ломается монтирование
  `StaticFiles` (mount дописывает префикс к пути → `/static/...` даёт 404). А внешний
  префикс для ссылок/статики/редиректов в HTML задаётся отдельным `app.base_path`
  (`base_path()` в [app/core/urls.py](app/core/urls.py): X-Forwarded-Prefix → scope root_path
  → `base_path` → `root_path`). `root_path` в FastAPI выставлять только если прокси СОХРАНЯЕТ
  префикс в пути (стандартный ASGI).

## Архитектура (big picture)

Слои строго разделены — правки бизнес-логики идут в `services/`, роутеры остаются тонкими:

- `routers/` — разбор запроса, вызов сервиса, рендер Jinja2 или JSON. Ничего «умного».
- `services/` — вся логика чистыми функциями и функциями над `Session` (тестируется изолированно).
- `models/` (ORM) и `schemas/` (Pydantic-контракты API) — строго раздельны.
- `core/` — `config` (YAML→Pydantic), `database` (engine/Session/Base), `security` (хэши),
  `permissions` (`require_user`/`require_admin` как FastAPI-зависимости), `urls` (base path для proxy).

**Ассистент** — единая точка входа `services/assistant/orchestrator` (пакет:
`core`/`voice`/`handlers_*`/`actions`/`serializers`/`common`), `orchestrator.run(settings, db, user, message)`
→ `AssistantResult` (reply, intent, cards[], suggested_actions[], alternative_slots,
travel_options, protocol). Именно это возвращает `POST /api/chat`. Поток:
нормализация (`normalizer` → строгая `NormalizedRequest`) → проверка достаточности полей
(иначе уточняющий вопрос) → роутинг по интенту → сборка карточек и действий. `run()` —
тонкая обёртка над `_run_core()`; общий выход — `_apply_secretary_voice()`.

**Два ассистента Dify (при `dify.enabled`).** `request_normalizer` превращает текст в
`NormalizedRequest`. Оркестратор остаётся источником истины (делает всю работу, считает
интент/карточки/действия/статусы и детерминированный `reply`), а финальный ТЕКСТ ответа
«озвучивает» `smart_calendar_secretary`: ему отдаются факты бэкенда (draft `reply` + сводка в
`inputs`), он только перефразирует их живо и грамотно, не выдумывая. Оба вызова — с мягким
откатом на детерминированный текст (`mode="dify-fallback"`). Склонение имён участников в
репликах («с Марией Кузнецовой») — `services/assistant/morphology.py` (pymorphy3, опционально;
без пакета — именительный падеж).

**Паттерн подтверждения** (важно при добавлении действий): всё, что затрагивает календарь или
других участников, НЕ выполняется сразу — создаётся черновик `AssistantAction` (pending) в БД,
исполняется через `POST /api/assistant/actions/{id}/confirm|reject`. Личная встреча без
конфликтов создаётся сразу.

**Планирование и конфликты** — `services/scheduling.py` (чистые операции над интервалами) +
`services/availability.py` (мультиучастник, рабочие часы и буферы на дорогу из YAML) +
`services/conflict_resolver.py` (стратегии `schedule_as_is` / `suggest_alternatives` /
`propose_reschedule_lower_priority` / `ask_user_confirmation` по приоритетам и порогу
`scheduling.high_priority_threshold`).

**Provider-адаптеры (mock ↔ real через флаги YAML)** — общий приём во всём проекте:
`travel_search.py` (`tickets.mode: mock|provider`), `notification_service.py`
(`notifications.mode`), `document_reader.py`/`protocol_generator.py`. Mock-режим работает без
внешних ключей; реальная интеграция добавляется новым провайдером без изменения вызовов.
`tickets.py`/`documents.py` — тонкие shim'ы над `assistant/*` для обратной совместимости URL.

**Frontend** без сборки: серверный Jinja2 + один `app/static/css/app.css` (тема liquid glass,
светлая/тёмная через `:root[data-theme]`, системная по умолчанию) + `app/static/js/`:
`core.js` (общий слой: BASE, тема, тосты, api, esc/fmt в `window.smartcal`, модальный
хелпер) подключается первым, затем `calendar.js`/`chat.js`/`documents.js`/`travel.js`/
`admin.js` — модули включаются по наличию якорного DOM-элемента; чат рендерит карточки
по `kind` и кнопки по `type`.

## Заметки по тестам

`tests/test_smoke.py` поднимает приложение через `TestClient`, что запускает `bootstrap()`
(создание demo-данных) и работает с БД из активной конфигурации, а не с in-memory.
`conftest.py` лишь гарантирует корень проекта в `sys.path`. Тесты рассчитывают на дефолтный
`root_path=""`, поэтому не завязаны на proxy-конфиг.

При изменении поведения нормализатора синхронно правь ожидания в `tests/test_assistant.py`
(например, набор `missing_fields` для create_event). Reverse-proxy — все шаблоны используют
`{{ base }}/...`, `core.js` префиксует запросы через `window.APP_BASE`, редиректы в роутерах —
через `local_redirect` из [app/core/urls.py](app/core/urls.py).
