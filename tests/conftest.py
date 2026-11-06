"""Isolate every test from the user's local databases."""
import pytest
from app.settings import settings

@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, request):
    for field in type(settings).model_fields:
        if field.endswith(("_db_path", "_root_path", "_key_path")):
            target = tmp_path / field
            if field.endswith("_db_path"):
                target = target.with_suffix(".db")
            monkeypatch.setattr(settings, field, str(target))
    if request.module.__name__.split(".")[-1] in {
        "test_whatsapp_routes", "test_whatsapp_workflow", "test_cora_webhook",
        "test_whatsapp_onboarding", "test_historical_vision"
    }:
        # Adapters and HTTP transports are mocked in these operational tests.
        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "integrations_enabled", True)
