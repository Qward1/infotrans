# Dify-ассистенты

Каталог с Dify-воркфлоу для умного календаря и подготовленной интеграцией.

## Что здесь лежит

| Ассистент | Файл | Назначение |
| --- | --- | --- |
| **request_normalizer** | `assistants/request_normalizer/request_normalizer.yml` | Текст пользователя → строгий JSON-интент (`NormalizedRequest`): intent + event/travel/protocol/target_event + missing_fields. Это основной ассистент, который дергает backend при `dify.enabled=true`. |
| **smart_calendar_secretary** | `assistants/smart_calendar_secretary/smart_calendar_secretary.yml` | Главный ассистент-секретарь: понимает запрос, выбирает сервис (`needs_service`), отвечает в деловом стиле. |
| **protocol_assistant** | `assistants/protocol_assistant/protocol_assistant.yml` | Текст встречи/стенограммы → протокол (решения, задачи, ответственные, сроки, риски, follow-up встречи). |
| **travel_assistant** | `assistants/travel_assistant/travel_assistant.yml` | Запрос поездки → параметры поиска билетов (`TravelData`) + пояснение. |
| _пример_ | `assistants/gospodderzhka-svo.example.yml` | Копия исходного примера (`../assistant.yml`) — эталон формата Dify (роутер, knowledge-retrieval, code, HTTP, DOCX). Не удаляйте. |

> Оригинальный пример также остаётся в корне проекта: `../assistant.yml`.

Каждый ассистент — самостоятельный advanced-chat workflow (start → llm со
`structured_output` → answer). JSON из узла `answer` backend парсит и мапит в свои
Pydantic-схемы (`app/services/assistant/schemas.py`).

## Формат Dify (кратко)

Файл ассистента — это YAML с ключами:

- `app` — метаданные (`name`, `icon`, `mode: advanced-chat`).
- `dependencies` — плагины (модель-провайдер, генераторы документов).
- `workflow.features` — загрузка файлов, подсказки, опции.
- `workflow.graph.nodes` / `workflow.graph.edges` — граф узлов и связи.

Ключевые типы узлов:

- `start` — входные переменные (в т.ч. `sys.query`, `sys.files`).
- `if-else` — детерминированный роутер по условиям.
- `knowledge-retrieval` — поиск в базе знаний.
- `llm` — вызов модели; при `structured_output_enabled: true` возвращает строгий JSON по схеме.
- `code` — упаковка/трансформация данных.
- `http-request` — вызов внешнего микросервиса.
- `answer` — финальный ответ (может отдавать JSON).

Модель подключается через провайдер
`langgenius/openai_api_compatible/openai_api_compatible` (OpenAI-совместимый API),
что удобно для self-hosted моделей (в примере — `qwen3-32b-fp8-v2`).

## Как импортировать в Dify

1. В Dify → **Studio → Import DSL** загрузите нужный `*.yml`
   (по одному ассистенту: `request_normalizer`, `smart_calendar_secretary`,
   `protocol_assistant`, `travel_assistant`).
2. В узле `... (LLM)` выберите провайдера/модель и укажите ключ модели
   (провайдер `openai_api_compatible`, можно self-hosted).
3. Опубликуйте приложение и получите **App API Key** (`app-...`).
4. Пропишите ключи в `config/config.yaml` (уже реализовано в backend):

   ```yaml
   assistant:
     dify:
       enabled: true                       # включает режим Dify
       base_url: "https://api.dify.ai/v1"  # или self-hosted URL
       api_key: "app-COMMON-KEY"           # общий ключ (fallback)
       assistants:
         request_normalizer: "app-XXXX"    # ключ конкретного ассистента
         smart_calendar_secretary: "app-YYYY"
         protocol_assistant: "app-ZZZZ"
         travel_assistant: "app-WWWW"
   ```

   Если ключ ассистента пуст — используется общий `api_key`.

## Как это уже использует backend

Клиент реализован в `app/services/assistant/dify_client.py`, вызывается из
`normalizer.py` (нормализация) и `protocol_generator.py` (протоколы). Запрос:

```http
POST {base_url}/chat-messages
Authorization: Bearer {api_key ассистента}
Content-Type: application/json

{
  "inputs": {"user_tz": "Europe/Moscow"},
  "query": "<текст пользователя>",
  "user": "<email>",
  "response_mode": "blocking"
}
```

Поле `answer` (строгий JSON) парсится и мапится в Pydantic-схемы
(`NormalizedRequest` / `ProtocolData`). **Любая ошибка Dify → мягкий откат на
локальный режим** (`source="dify-fallback"`), demo не падает.

- `dify.enabled: false` → работает локальный нормализатор (mock), ключи не нужны.
- `dify.enabled: true` → вызовы Dify с fallback.

## Контракт JSON (request_normalizer)

```json
{
  "intent": "create_event | find_tickets | find_free_slots | generate_meeting_protocol | ...",
  "confidence": 0.0,
  "language": "ru",
  "missing_fields": [],
  "clarifying_question": "",
  "event":  { "title": "", "date": "YYYY-MM-DD", "start_time": "HH:MM", "format": "online|offline|hybrid", "priority": 5, "participants": [] },
  "travel": { "origin_city": "", "destination_city": "", "departure_date": "YYYY-MM-DD", "transport_type": "flight|train|any" },
  "protocol": { "source_document_id": null, "target_event_id": null },
  "target_event": { "event_id": null, "title": "", "date_hint": "" }
}
```

Backend по `intent` вызывает соответствующий сервис (`availability`,
`conflict_resolver`, `travel_search`, `protocol_generator`, `calendar`).
