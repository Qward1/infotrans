"""Smoke-тесты загрузчика конфигурации (YAML)."""
from __future__ import annotations

import textwrap
from pathlib import Path

from app.core.config import Settings, load_settings


def test_defaults_when_missing(tmp_path: Path):
    """Отсутствующий файл → безопасные значения по умолчанию (пример подхватится или дефолты)."""
    settings = load_settings(tmp_path / "nope.yaml")
    assert isinstance(settings, Settings)
    assert settings.app.name
    assert settings.assistant.mode in {"mock", "dify"}
    assert settings.database.url.startswith("sqlite") or "://" in settings.database.url


def test_loads_custom_yaml(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            app:
              name: "Тестовый календарь"
              secret_key: "abc"
              timezone: "UTC"
            database:
              url: "sqlite:///./data/test.db"
            assistant:
              mode: "dify"
            tickets:
              mode: "mock"
              avg_speed_kmh: 80
            seed_admin:
              email: "root@test.local"
              password: "secret123"
            """
        ),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.app.name == "Тестовый календарь"
    assert settings.app.timezone == "UTC"
    assert settings.assistant.mode == "dify"
    assert settings.tickets.avg_speed_kmh == 80
    assert settings.seed_admin.email == "root@test.local"


def test_partial_yaml_fills_defaults(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("app:\n  name: Only Name\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.app.name == "Only Name"
    # Незаданные секции получают дефолты.
    assert settings.security.session_cookie == "smartcal_session"
    assert settings.tickets.currency == "RUB"
