"""Public portfolio demo must not mutate shared state."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.settings import Settings, settings


def test_read_only_setting_is_opt_in_locally():
    configured = Settings(_env_file=None, demo_mode=True, public_demo_read_only=True)
    assert configured.public_demo_read_only
    assert configured.demo_mode
    assert not configured.integrations_enabled


def test_public_demo_blocks_writes_but_preserves_login_and_reads():
    with patch.object(settings, "demo_mode", True), patch.object(settings, "public_demo_read_only", True), patch.object(settings, "auth_cookie_secure", True):
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200
            assert client.post("/api/fuel", json={}).status_code == 403
            assert client.put("/api/vehicles/fake/profile", json={}).status_code == 403
            assert client.delete("/api/vehicles/fake").status_code == 403
            assert client.post("/api/whatsapp/webhook", json={}).status_code == 403
            login = client.post("/api/auth/login", json={
                "email": settings.demo_email,
                "password": settings.demo_password,
            })
            assert login.status_code == 200
            assert "secure" in login.headers["set-cookie"].lower()
            assert client.post("/api/auth/logout").status_code == 200


def test_regular_demo_still_supports_mutating_requests():
    with patch.object(settings, "demo_mode", True), patch.object(settings, "public_demo_read_only", False):
        with TestClient(app) as client:
            response = client.post("/api/fuel", json={})
            assert response.status_code != 403
