"""Smoke-тест приложения через TestClient: старт, вход, ключевые страницы и API."""
from __future__ import annotations

import uuid

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

    r = client.get("/api/calendar/range?view=month&date=2026-07-01")
    assert r.status_code == 200
    data = r.json()
    assert data["view"] == "month"
    assert len(data["days"]) >= 28

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


def test_admin_calendar_access_and_user_forbidden(client):
    settings = get_settings()
    password = "pass12345"
    suffix = uuid.uuid4().hex[:8]
    owner_email = f"cal-owner-{suffix}@test.local"
    other_email = f"cal-other-{suffix}@test.local"

    def login(email: str, password_value: str):
        r = client.post(
            "/login",
            data={"email": email, "password": password_value},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

    client.get("/logout")
    login(settings.seed_admin.email, settings.seed_admin.password)
    created_users = []
    for email in (owner_email, other_email):
        r = client.post(
            "/api/admin/users",
            json={
                "email": email,
                "full_name": email.split("@")[0],
                "password": password,
                "role": "user",
                "is_active": True,
            },
        )
        assert r.status_code == 201, r.text
        created_users.append(r.json())
    owner_id = created_users[0]["id"]

    payload = {
        "owner_id": owner_id,
        "title": "Admin owned event",
        "start_at": "2026-07-15T10:00:00",
        "end_at": "2026-07-15T11:00:00",
        "location_type": "online",
        "priority": 5,
    }
    r = client.post("/api/events", json=payload)
    assert r.status_code == 201, r.text
    event = r.json()
    assert event["owner_id"] == owner_id
    assert event["created_by_id"] != owner_id

    r = client.get(f"/api/calendar/range?view=week&date=2026-07-15&user_id={owner_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["owner"]["id"] == owner_id
    assert any(item["id"] == event["id"] for item in data["events"])

    r = client.patch(f"/api/events/{event['id']}", json={"title": "Edited by admin"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Edited by admin"

    client.get("/logout")
    login(other_email, password)
    r = client.get(f"/api/calendar/range?view=week&date=2026-07-15&user_id={owner_id}")
    assert r.status_code == 403
    r = client.patch(f"/api/events/{event['id']}", json={"title": "Forbidden edit"})
    assert r.status_code == 403


def test_adaptive_chat_ui_hooks_render(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"email": settings.seed_admin.email, "password": settings.seed_admin.password},
    )
    r = client.get("/chat")
    assert r.status_code == 200
    html = r.text
    assert 'id="sidebar-toggle"' in html
    assert 'id="assistant-shell"' in html
    assert 'id="chat-history-panel"' in html
    assert 'id="chat-side-toggle"' in html

    js = client.get("/static/js/app.js").text
    css = client.get("/static/css/app.css").text
    assert "smartcal-sidebar-collapsed" in js
    assert "smartcal-chat-history-collapsed" in js
    assert "smartcal-chat-side-collapsed" in js
    assert ".chat-shell.chat-side-collapsed" in css


def test_travel_page_uses_dedicated_layout(client):
    settings = get_settings()
    client.post(
        "/login",
        data={"email": settings.seed_admin.email, "password": settings.seed_admin.password},
    )
    r = client.get("/travel")
    assert r.status_code == 200
    html = r.text
    assert 'class="travel-shell"' in html
    assert 'class="travel-main"' in html
    assert 'class="travel-side"' in html
    assert 'class="chat-shell"' not in html
    assert 'class="chat-side"' not in html

    css = client.get("/static/css/app.css").text
    assert ".travel-shell" in css
    assert ".travel-side" in css


def test_assistant_chat_history_api_scopes_users(client):
    settings = get_settings()
    password = "pass12345"
    suffix = uuid.uuid4().hex[:8]
    owner_email = f"chat-owner-{suffix}@test.local"
    other_email = f"chat-other-{suffix}@test.local"

    def login(email: str, password_value: str):
        r = client.post(
            "/login",
            data={"email": email, "password": password_value},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

    client.get("/logout")
    login(settings.seed_admin.email, settings.seed_admin.password)
    for email in (owner_email, other_email):
        r = client.post(
            "/api/admin/users",
            json={
                "email": email,
                "full_name": email.split("@")[0],
                "password": password,
                "role": "user",
                "is_active": True,
            },
        )
        assert r.status_code == 201, r.text

    client.get("/logout")
    login(owner_email, password)
    r = client.post("/api/chat", json={"message": "Покажи мой календарь на неделю"})
    assert r.status_code == 200, r.text
    chat_id = r.json()["conversation_id"]
    assert chat_id

    r = client.get("/api/assistant/chats")
    assert r.status_code == 200
    assert any(item["id"] == chat_id for item in r.json()["items"])

    r = client.get(f"/api/assistant/chats/{chat_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["userId"]
    assert detail["title"].startswith("Покажи мой календарь")
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    r = client.patch(f"/api/assistant/chats/{chat_id}", json={"title": "План недели"})
    assert r.status_code == 200
    assert r.json()["title"] == "План недели"

    client.get("/logout")
    login(other_email, password)
    r = client.get(f"/api/assistant/chats/{chat_id}")
    assert r.status_code == 404

    r = client.get("/api/assistant/chats")
    assert r.status_code == 200
    assert all(item["id"] != chat_id for item in r.json()["items"])

    client.get("/logout")
    login(settings.seed_admin.email, settings.seed_admin.password)
    r = client.get(f"/api/assistant/chats?user_id={detail['userId']}")
    assert r.status_code == 200
    assert any(item["id"] == chat_id for item in r.json()["items"])

    r = client.get(f"/api/assistant/chats/{chat_id}")
    assert r.status_code == 200
    assert r.json()["userId"] == detail["userId"]

    r = client.post(
        f"/api/assistant/chats/{chat_id}/messages",
        json={"role": "assistant", "content": "admin write attempt", "payload": {}},
    )
    assert r.status_code == 403

    r = client.delete(f"/api/assistant/chats/{chat_id}")
    assert r.status_code == 403
