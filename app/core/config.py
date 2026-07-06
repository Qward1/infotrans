"""Загрузка и валидация конфигурации из YAML.

Единственный источник настроек приложения — ``config/config.yaml``.
Файла ``.env`` в проекте нет намеренно: все ключи, URL, параметры
интеграций и seed-админ описываются в YAML. См. ``config/config.example.yaml``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Корень проекта (…/Умный календарь).
BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
CONFIG_PATH = Path(os.environ.get("SMARTCAL_CONFIG", CONFIG_DIR / "config.yaml"))
EXAMPLE_PATH = CONFIG_DIR / "config.example.yaml"


# --------------------------------------------------------------------------- #
# Схемы конфигурации (Pydantic). Значения по умолчанию делают конфиг гибким:  #
# отсутствующие секции просто получают безопасные demo-настройки.             #
# --------------------------------------------------------------------------- #
class AppConfig(BaseModel):
    name: str = "Умный календарь"
    secret_key: str = "insecure-dev-secret-change-me"
    timezone: str = "Europe/Moscow"
    debug: bool = True
    # ASGI root_path, передаётся в FastAPI(root_path=...). Задавать ТОЛЬКО если
    # прокси сохраняет префикс в пути и выставляет scope["root_path"] (стандартный
    # ASGI). Если прокси СРЕЗАЕТ префикс и форвардит чистые пути (наш случай) —
    # оставить "", иначе ломается монтирование StaticFiles (пути вида /static/...).
    root_path: str = ""
    # Внешний префикс пути для построения ссылок/редиректов/статики в HTML
    # (например, "/jnserver/1120/application"). Нужен, когда прокси срезает
    # префикс: приложение внутри работает на чистых путях, но в браузер ссылки
    # должны уходить С префиксом. Приоритет ниже, чем X-Forwarded-Prefix и
    # scope["root_path"]. См. app/core/urls.py.
    base_path: str = ""


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/app.db"
    echo: bool = False


class SecurityConfig(BaseModel):
    session_cookie: str = "smartcal_session"
    session_max_age: int = 1_209_600  # 14 дней


class SeedAdminConfig(BaseModel):
    email: str = "admin@demo.local"
    password: str = "admin12345"
    full_name: str = "Demo Admin"


class LLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai_api_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = "qwen3-32b-fp8-v2"
    temperature: float = 0.2
    timeout: int = 30


class DifyAssistantsConfig(BaseModel):
    """API-ключи Dify для каждого ассистента (пусто → берётся общий ``api_key``)."""

    request_normalizer: str = ""
    smart_calendar_secretary: str = ""
    protocol_assistant: str = ""
    travel_assistant: str = ""


class DifyConfig(BaseModel):
    # Главный тумблер: false → работает локальный нормализатор (mock),
    # true → вызовы Dify API с мягким откатом на mock при ошибке.
    enabled: bool = False
    base_url: str = "https://api.dify.ai/v1"
    api_key: str = ""
    app_id: str = ""
    timeout: int = 30
    default_assistant: str = "smart_calendar_secretary"
    assistants: DifyAssistantsConfig = Field(default_factory=DifyAssistantsConfig)


class AssistantConfig(BaseModel):
    mode: str = "mock"  # mock | dify (справочно; фактически рулит dify.enabled)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    dify: DifyConfig = Field(default_factory=DifyConfig)


class WorkingHoursConfig(BaseModel):
    start: str = "09:00"
    end: str = "19:00"


class SchedulingConfig(BaseModel):
    working_hours: WorkingHoursConfig = Field(default_factory=WorkingHoursConfig)
    slot_granularity_minutes: int = 30
    default_meeting_minutes: int = 60
    max_alternatives: int = 5
    # Буферы на дорогу между офлайн/гибрид встречами.
    default_travel_buffer_minutes: int = 60   # адрес неизвестен
    same_city_travel_buffer_minutes: int = 40  # тот же город, другой адрес
    same_address_buffer_minutes: int = 0
    online_buffer_minutes: int = 0
    # Реалистичность очной встречи: если дорога между городами дольше — предлагаем онлайн.
    realistic_offline_max_travel_minutes: int = 300
    high_priority_threshold: int = 8  # выше — нельзя автоматически сдвигать


class TicketProviderConfig(BaseModel):
    name: str = "generic"
    base_url: str = ""
    api_key: str = ""
    timeout: int = 30


class TicketsConfig(BaseModel):
    mode: str = "mock"  # mock | provider
    avg_speed_kmh: float = 65.0
    currency: str = "RUB"
    # Если очная поездка «туда-обратно за день» дольше — считаем нереалистичной.
    same_day_return_max_hours: float = 10.0
    provider: TicketProviderConfig = Field(default_factory=TicketProviderConfig)


class MessengerConfig(BaseModel):
    enabled: bool = False
    provider: str = "max"          # max | telegram | email | mock
    base_url: str = ""
    api_key: str = ""


class NotificationsConfig(BaseModel):
    # mode: mock — уведомления пишутся в БД/лог и показываются в UI.
    mode: str = "mock"             # mock | provider
    default_channel: str = "messenger"
    # Каналы доставки, включённые для demo (отображаются в настройках).
    channels: list[str] = Field(default_factory=lambda: ["web", "messenger", "email"])
    messenger: MessengerConfig = Field(default_factory=MessengerConfig)


class DemoConfig(BaseModel):
    """Управление demo-наполнением БД при старте."""

    # true → при пустой БД создаются demo-пользователи, встречи, уведомления и документ.
    # Идемпотентно: повторный запуск не дублирует данные.
    seed_on_startup: bool = True


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    seed_admin: SeedAdminConfig = Field(default_factory=SeedAdminConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    tickets: TicketsConfig = Field(default_factory=TicketsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)


def load_settings(path: Path | str | None = None) -> Settings:
    """Прочитать YAML и вернуть провалидированные настройки.

    Если файла нет — пробуем пример, иначе поднимаем настройки по умолчанию.
    Функция чистая (без кэша), удобна для тестов.
    """
    candidate = Path(path) if path else CONFIG_PATH
    source: Path | None = None
    if candidate.exists():
        source = candidate
    elif EXAMPLE_PATH.exists():
        source = EXAMPLE_PATH

    data: dict = {}
    if source is not None:
        with source.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (используется в приложении)."""
    return load_settings()
