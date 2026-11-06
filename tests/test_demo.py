import socket
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.settings import Settings, settings
from app.auth import authenticate
from app.demo_seed import seed_demo_state
from app.system_state import get_system_state


@pytest.fixture
def demo_client(tmp_path):
    with patch.multiple(settings, demo_mode=True, integrations_enabled=False, auth_cookie_secure=False):
        patches = []
        for field in type(settings).model_fields:
            if field.endswith(("_db_path", "_root_path", "_key_path")):
                target = tmp_path / field
                if field.endswith("_db_path"):
                    target = target.with_suffix(".db")
                item = patch.object(settings, field, str(target))
                item.start()
                patches.append(item)
        try:
            original_connect = socket.socket.connect
            def local_connect(sock, address):
                if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                    return original_connect(sock, address)
                raise AssertionError("Demo attempted external network I/O")
            with patch("socket.socket.connect", local_connect):
                with TestClient(app) as client:
                    yield client
        finally:
            for item in reversed(patches):
                item.stop()


def login(client):
    return client.post("/api/auth/login", json={"email": settings.demo_email, "password": settings.demo_password})


def test_demo_is_configurable_and_strips_real_credentials():
    demo = Settings(_env_file=None, demo_mode=True, integrations_enabled=True,
                    selsyn_api_key="must-not-be-used", supabase_url="https://example.invalid",
                    cora_enabled=True, whatsapp_enabled=True)
    assert demo.demo_mode
    assert not demo.selsyn_api_key and not demo.supabase_url
    assert not demo.cora_enabled and not demo.whatsapp_enabled
    assert demo.system_state_db_path.startswith("data/demo/")
    assert not Settings(_env_file=None, demo_mode=False).demo_mode


def test_demo_end_to_end_without_network(demo_client):
    client = demo_client
    assert client.get("/api/system-state").status_code == 401
    assert client.post("/api/auth/login", json={"email": settings.demo_email, "password": "wrong"}).status_code == 401
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/auth/me").json()["role_code"] == "admin"
    assert client.get("/workspace").status_code == 200
    state = client.get("/api/system-state").json()
    assert len(state["data"]["employees"]) == 3
    assert state["data"]["works"][0]["companyId"] == state["data"]["companies"][0]["id"]
    assert len(state["data"]["vehicles"]) == 2
    assert client.get("/api/vehicles/live").status_code == 200
    for path in ["/api/cora/webhook", "/api/whatsapp/webhook", "/api/projects/email/status"]:
        assert client.get(path).status_code == 503
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_seed_does_not_overwrite_changes(demo_client):
    before = get_system_state()
    seed_demo_state()
    assert get_system_state() == before
    assert login(demo_client).status_code == 200
    state = demo_client.get("/api/system-state").json()
    state["data"]["works"][0]["notes"] = "Edited during evaluation"
    result = demo_client.put("/api/system-state", json={"data": state["data"], "client_revision": state["revision"]})
    assert result.status_code == 200
    seed_demo_state()
    assert get_system_state()["data"]["works"][0]["notes"] == "Edited during evaluation"


def test_demo_password_is_rejected_when_demo_disabled():
    with patch.object(settings, "demo_mode", False), patch("app.auth.create_public_supabase", side_effect=RuntimeError("unconfigured")):
        assert authenticate(settings.demo_email, settings.demo_password) is None
