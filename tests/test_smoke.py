"""Smoke-тест приложения через TestClient: старт, вход, ключевые страницы и API."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Контекст-менеджер запускает lifespan → bootstrap (таблицы, seed-админ, demo).
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_requires_auth_redirects(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].endswith("/login")


def test_api_requires_auth(client):
    r = client.get("/api/calendar/week")
    assert r.status_code == 401


def test_login_and_dashboard_flow(client):
    settings = get_settings()
    r = client.post(
        "/login",
        data={"email": settings.seed_admin.email, "password": settings.seed_admin.password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/dashboard")

    # Сессия сохраняется в клиенте — дальше ходим авторизованно.
    r = client.get("/dashboard")
    assert r.status_code == 200

    r = client.get("/api/calendar/week")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # Админ видит статистику.
    r = client.get("/api/admin/stats")
    assert r.status_code == 200
    assert "overview" in r.json()


def test_create_and_delete_event(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"email": settings.seed_admin.email, "password": settings.seed_admin.password},
    )
    payload = {
        "title": "Тестовая встреча",
        "start_at": "2026-07-10T10:00:00",
        "end_at": "2026-07-10T11:00:00",
        "location_type": "online",
        "priority": 6,
    }
    r = client.post("/api/events", json=payload)
    assert r.status_code == 201, r.text
    event_id = r.json()["id"]

    r = client.delete(f"/api/events/{event_id}")
    assert r.status_code == 204
