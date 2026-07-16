# 🗓️ Умный цифровой календарь встреч и поездок

Product-like MVP: FastAPI-приложение с ролями, недельным календарём, чат-ассистентом
(mock + готовая интеграция с Dify), поиском билетов, разбором документов в протоколы,
уведомлениями, админкой и статистикой команды. Интерфейс — в стиле **liquid glass**
(полупрозрачные blur-панели, синие оттенки, светлая/тёмная темы, адаптив под ноутбук и
большой монитор).

Всё запускается одной командой через `uvicorn`, конфигурация — только в YAML (без `.env`),
БД по умолчанию — SQLite (меняется одной строкой в конфиге).

---

## ⚡ Быстрый старт

```bash
# 1. Зависимости
pip install -r requirements.txt

# 2. Конфиг уже есть (config/config.yaml с demo-значениями).
#    При отсутствии — подхватится config/config.example.yaml или дефолты.

# 3. Запуск
uvicorn app.main:app --reload
```

Откройте <http://127.0.0.1:8000>. При первом старте автоматически создаются таблицы,
seed-админ и demo-данные (6 аккаунтов, встречи на 2 недели с конфликтами, уведомления,
загруженный протокол). Наполнение идемпотентно — повторный запуск ничего не дублирует.

### 🔑 Demo-доступ

| Роль | Email | Пароль |
| --- | --- | --- |
| **Администратор** | `admin@demo.local` | `admin12345` |
| Сотрудник | `user@demo.local` | `user12345` |
| Сотрудник | `anna@demo.local` | `user12345` |
| Сотрудник | `maria@demo.local` | `user12345` |
| Сотрудник | `petr@demo.local` | `user12345` |
| Сотрудник | `olga@demo.local` | `user12345` |

> Логин/пароль админа задаются в `config/config.yaml → seed_admin`.
> Если админ уже существует — пароль автоматически **не** перезаписывается.
> Блок demo-доступов на странице входа показывается только при `app.debug: true`
> и печатает статический demo-пароль, а не значение из конфига.

---

## 🖥️ Экраны (10)

| # | Экран | Путь | Права |
| --- | --- | --- | --- |
| 1 | Вход | `/login` | все |
| 2 | Дашборд | `/dashboard` | user |
| 3 | Календарь (неделя) | `/calendar` | user |
| 4 | Чат-ассистент | `/chat` | user |
| 5 | Документы / протоколы | `/documents` | user |
| 6 | Поиск билетов | `/travel` | user |
| 7 | Уведомления | `/notifications` | user |
| 8 | Пользователи | `/admin/users` | admin |
| 9 | Статистика команды | `/admin/stats` | admin |
| 10 | Настройки | `/settings` | user (правка — admin) |

- **Дашборд**: приветствие, KPI, быстрые действия (создать встречу, чат, свободный слот,
  протокол, билеты), встречи сегодня, нагрузка по дням недели, ближайшие встречи, конфликты.
- **Календарь**: недельный вид с навигацией, цвет по приоритету, бейджи online/offline/hybrid,
  индикатор конфликта, кнопки создать/редактировать/удалить/перенести и «найти свободный слот».
- **Чат**: карточки intent, созданной встречи, конфликта, альтернативных слотов, плана переноса,
  билетов, протокола, задач; кнопки подтверждения; demo-подсказки.
- **Документы**: загрузка PDF/DOCX/TXT (drag-and-drop), список документов, кнопка
  «Сформировать протокол», вывод summary/решений/задач/ответственных/сроков/follow-up,
  кнопка «Создать встречи из протокола».
- **Билеты**: форма (откуда/куда/даты/транспорт/бюджет/предпочтения), карточки вариантов
  (тип, время, длительность, пересадки, цена, provider, ссылка).
- **Статистика**: время на встречах за неделю по сотрудникам, количество встреч, средняя
  длительность, конфликты, переносы, топ загруженных дней, распределение форматов и
  приоритетов, статусы, журнал аудита. Графики — простые HTML/CSS-полосы (без внешнего BI).

---

## 🧩 Возможности

- **Аутентификация**: login/logout, сессии в подписанных cookie, пароли только хэшированные.
- **Роли** `user` / `admin` с проверкой прав на уровне зависимостей FastAPI (страницы и API).
- **Ассистент-оркестратор**: нормализация запроса → интент → сервис → карточки и действия
  с подтверждением. Работает без ключей (локальный нормализатор), готов к Dify.
- **Планирование**: свободные слоты в рабочих часах для одного/нескольких участников с
  учётом буферов на дорогу; конфликт-резолвинг по приоритету (альтернативы / перенос /
  подтверждение).
- **Билеты**: mock-подбор (самолёт/поезд/автобус) с ценой, ссылкой и расчётом времени в пути;
  задел под реальный provider-режим.
- **Документы → протоколы**: чтение PDF/DOCX/TXT, извлечение решений/задач/ответственных/
  сроков/follow-up, создание встреч из протокола.
- **Уведомления**: mock-канал (пишутся в БД, видны в топбаре и на странице), задел под
  мессенджер/email.
- **Админка**: CRUD пользователей, смена роли, активация/деактивация (с защитой последнего
  админа), статистика, аудит, безопасное редактирование настроек (с backup YAML).
- **UI**: liquid glass, адаптивный sidebar/topbar, тёмная/светлая тема (в `localStorage`),
  empty states, loading-индикаторы, тосты, модалки, бейджи.

---

## 🛠️ Стек

FastAPI · Uvicorn · SQLAlchemy 2.0 · Pydantic 2 · Jinja2 · Vanilla JS · CSS (без сборки) ·
Passlib (`pbkdf2_sha256`, переносимо; bcrypt поддерживается) · PyYAML · pypdf · python-docx · httpx.

---

## 📁 Структура проекта

```text
app/
  main.py              # сборка FastAPI: middleware, роутеры, обработчики ошибок, lifespan
  bootstrap.py         # инициализация: таблицы + seed-админ + demo-данные (идемпотентно)
  templating.py        # общий рендер Jinja2
  core/
    config.py          # загрузка/валидация YAML (Pydantic), get_settings()
    database.py        # engine/Session/Base, init_db()
    security.py        # хэш и проверка паролей
    permissions.py     # get_current_user / require_user / require_admin, сессии
  models/              # User, CalendarEvent, EventParticipant, Reminder, AuditLog,
                       #   AssistantAction, Document, Notification
  schemas/             # Pydantic-схемы (auth, user, calendar, assistant)
  services/
    users, auth, calendar, scheduling, availability, conflict_resolver,
    location_service, stats, audit, tickets, documents
    assistant/         # orchestrator, normalizer, dify_client, travel_search,
                       #   document_reader, protocol_generator, notification_service, schemas
  routers/             # auth, dashboard, calendar, chat, documents, travel,
                       #   notifications, admin, settings, api
  templates/           # base + 10 экранов (+ modals, macros)
  static/css/app.css   # liquid glass тема (светлая/тёмная)
  static/js/app.js     # тема, модалки, чат, документы, билеты, уведомления, API-хелпер
config/
  config.example.yaml  # пример (в репозитории)
  config.yaml          # локальный конфиг с секретами (в .gitignore); *.bak — backup правок
dify/
  README.md            # как подключить и импортировать Dify-ассистентов
  assistants/          # request_normalizer, smart_calendar_secretary, protocol_assistant,
                       #   travel_assistant (+ исходный пример)
tests/                 # config, auth, scheduling, orchestrator, assistant, smoke (40 тестов)
assistant.yml          # исходный пример Dify-ассистента (эталон формата — не удалять)
DEMO_SCRIPT.md         # пошаговый сценарий показа заказчику (5–10 минут)
ARCHITECTURE.md        # архитектура: backend/frontend/db/assistant/Dify/алгоритмы/адаптеры
```

---

## 🔐 Конфигурация (YAML, без `.env`)

Единственный источник настроек — `config/config.yaml`. Секции:
`app`, `database`, `security`, `seed_admin`, `assistant`, `scheduling`, `tickets`,
`notifications`, `demo`.

- **Сменить БД** — одна строка:

  ```yaml
  database:
    url: "postgresql+psycopg://user:pass@localhost:5432/smartcal"
  ```

- **Demo/mock-режим** (по умолчанию) — работает без внешних ключей:

  ```yaml
  assistant: { dify: { enabled: false } }
  tickets:   { mode: mock }
  notifications: { mode: mock }
  ```

- **Demo-данные при старте**:

  ```yaml
  demo:
    seed_on_startup: true   # false → пустая БД (только seed-админ)
  ```

- `config/config.yaml` — в `.gitignore`; `config/config.example.yaml` — в репозитории.
  Путь можно переопределить переменной окружения `SMARTCAL_CONFIG`.

- **За reverse-proxy под под-путём** (например, `https://host/jnserver/1120/application`):
  укажите префикс, чтобы ссылки/статика/редиректы строились правильно:

  ```yaml
  app:
    root_path: "/jnserver/1120/application"   # в корне — оставьте ""
  ```

  Заголовок `X-Forwarded-Prefix` от прокси имеет приоритет над этим значением.
  Работает и когда прокси срезает префикс, и когда пробрасывает полный путь.
- **Правка настроек через UI** (`/settings`, admin): безопасный whitelist скалярных полей;
  секреты не показываются и не редактируются; перед записью создаётся `config.yaml.bak`.

---

## 🤖 Интеграция с Dify

Каталог `dify/assistants/` содержит 4 импортируемых advanced-chat workflow:
`request_normalizer`, `smart_calendar_secretary`, `protocol_assistant`, `travel_assistant`
(+ `gospodderzhka-svo.example.yml` — исходный эталон формата).

Как **импортировать**: Dify → Studio → Import DSL → загрузите нужный `*.yml`, выберите
модель/провайдера, опубликуйте, получите `app-...` ключ. Пропишите в `config.yaml`:

```yaml
assistant:
  dify:
    enabled: true                       # включает режим Dify
    base_url: "https://api.dify.ai/v1"  # или self-hosted URL
    api_key: "app-COMMON-KEY"           # общий ключ (fallback)
    assistants:
      request_normalizer: "app-XXXX"
      smart_calendar_secretary: "app-YYYY"
      protocol_assistant: "app-ZZZZ"
      travel_assistant: "app-WWWW"
```

`dify.enabled: false` → локальный нормализатор (без ключей). `true` → вызовы Dify с
**мягким откатом** на локальный режим при любой ошибке (`source="dify-fallback"`).
Подробности — в [`dify/README.md`](dify/README.md).

---

## 🎬 Демо-сценарии (кратко)

1. Admin логинится → видит статистику команды (`/admin/stats`).
2. Admin создаёт пользователя и назначает его админом (`/admin/users`).
3. User логинится → видит календарь недели.
4. User пишет ассистенту: «Поставь встречу с Анной завтра в 15:00 на час» → встреча создаётся.
5. User пытается поставить встречу на занятый слот → ассистент показывает конфликт и
   альтернативы, предлагает варианты.
6. User загружает/выбирает документ → формируется протокол → создаются follow-up встречи.
7. User ищет билеты (форма или чат) → карточки авиа/ЖД/автобус.
8. Уведомления отображаются в топбаре и на `/notifications`.

Полный пошаговый сценарий с фразами — в [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).

---

## ✅ Проверка

```bash
python -m compileall app          # компиляция всех модулей
python -m pytest                  # 40 тестов: config, auth, scheduling, orchestrator, assistant, smoke
uvicorn app.main:app --reload     # запуск
```

Ручная проверка: открывается login → demo-admin → dashboard/calendar/chat; admin видит
users/stats/settings; user не видит admin-страницы (403); создание встречи; conflict-ответ;
загрузка документа и mock-протокол; mock-билеты.

---

## 🚧 Ограничения перед настоящим production

- **Ассистент** по умолчанию — детерминированный локальный нормализатор (regex/эвристики),
  не LLM. Dify/LLM подключаются флагом в YAML.
- **Билеты** — mock-провайдер (детерминированная генерация по расстоянию). Реальные API
  (Aviasales/РЖД/Яндекс.Расписания) подключаются provider-режимом (см. `tickets.py`).
- **Уведомления** — mock (в БД/лог/UI). Реальный мессенджер/email — новый провайдер.
- **БД** — SQLite без Alembic-миграций (`create_all`). Для прод — PostgreSQL + миграции.
- **Правка YAML через UI** пересобирает файл (комментарии не сохраняются; есть `.bak`).
- **Расстояния между городами** — грубая demo-таблица; заменяется геокодером/маршрутизатором.
- Пароли — `pbkdf2_sha256` (осознанно, вместо bcrypt — совместимость окружения).
