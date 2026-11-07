from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings
from app.whatsapp_client import whatsapp_client
from app.whatsapp_store import save_whatsapp_business_connection


class WhatsAppRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_openapi_exposes_whatsapp_webhook(self) -> None:
        schema = app.openapi()
        webhook = schema["paths"]["/api/whatsapp/webhook"]

        self.assertIn("get", webhook)
        self.assertIn("post", webhook)
        self.assertIn("WhatsApp", webhook["get"]["tags"])

    def test_get_webhook_is_public_and_returns_challenge(self) -> None:
        with (
            patch.object(settings, "whatsapp_verify_token", "local-test-token"),
            patch("app.main.user_from_session") as user_from_session,
        ):
            response = self.client.get(
                "/api/whatsapp/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "local-test-token",
                    "hub.challenge": "123456",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, "123456")
        user_from_session.assert_not_called()

    def test_inbox_requires_login(self) -> None:
        with patch("app.main.user_from_session", return_value=None):
            response = self.client.get("/whatsapp", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_visual_whatsapp_pages_are_unavailable(self) -> None:
        user = {
            "id": 1,
            "display_name": "Recrutador Demo",
            "email": "demo@portfolio.local",
            "role_code": "director",
        }
        with patch("app.main.user_from_session", return_value=user):
            inbox = self.client.get("/whatsapp")
            integration = self.client.get("/whatsapp/integracao")

        self.assertEqual(inbox.status_code, 404)
        self.assertEqual(integration.status_code, 404)

    def test_embedded_signup_activates_encrypted_operational_connection(self) -> None:
        user = {
            "id": 1,
            "display_name": "Recrutador Demo",
            "email": "demo@portfolio.local",
            "role_code": "director",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "whatsapp.db"
            inspected = SimpleNamespace(
                business_id="101",
                waba_id="202",
                phone_number_id="303",
                business_name="Empresa de teste",
                display_phone_number="+55 47 99709-8277",
                verified_name="Número de teste",
                access_token="new-operational-token",
            )
            with (
                patch("app.main.user_from_session", return_value=user),
                patch.object(settings, "whatsapp_db_path", str(database_path)),
                patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
                patch(
                    "app.whatsapp_routes.inspect_authorized_business",
                    return_value=inspected,
                ) as inspect_business,
            ):
                response = self.client.post(
                    "/api/whatsapp/onboarding/complete",
                    headers={"Origin": "http://testserver"},
                    json={
                        "code": "temporary-authorization-code",
                        "business_id": "101",
                        "waba_id": "202",
                        "phone_number_id": "303",
                    },
                )
                status_response = self.client.get(
                    "/api/whatsapp/onboarding/status"
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["operational_configuration_changed"])
        self.assertEqual(response.json()["connection"]["waba_id"], "202")
        self.assertTrue(response.json()["connection"]["is_active"])
        self.assertNotIn("access_token", response.json()["connection"])
        self.assertNotIn("access_token_enc", response.json()["connection"])
        self.assertNotIn("new-operational-token", response.text)
        self.assertEqual(status_response.status_code, 200, status_response.text)
        self.assertEqual(len(status_response.json()["connections"]), 1)
        self.assertTrue(status_response.json()["connections"][0]["has_access_token"])
        inspect_business.assert_called_once()

    def test_client_prefers_latest_authorized_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "whatsapp.db"
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "display_phone_number": "+55 47 9709-8277",
                "verified_name": "Hendrielly De Souza",
                "status": "CONNECTED",
            }
            with (
                patch.object(settings, "whatsapp_db_path", str(database_path)),
                patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
                patch.object(settings, "whatsapp_enabled", True),
                patch.object(settings, "whatsapp_api_version", "v26.0"),
                patch.object(settings, "whatsapp_phone_number_id", "old-phone"),
                patch.object(settings, "whatsapp_access_token", "old-token"),
                patch("app.whatsapp_client.requests.get", return_value=response) as get,
            ):
                save_whatsapp_business_connection(
                    waba_id="new-waba",
                    phone_number_id="new-phone",
                    access_token="new-token",
                    activate=True,
                )
                provider = whatsapp_client.get_phone_number_status()

        self.assertEqual(provider["verified_name"], "Hendrielly De Souza")
        self.assertEqual(
            get.call_args.args[0],
            "https://graph.facebook.com/v26.0/new-phone",
        )
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Bearer new-token",
        )

    def test_client_does_not_fall_back_to_test_number_after_onboarding(self) -> None:
        from app.whatsapp_client import WhatsAppConfigurationError

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "whatsapp.db"
            with (
                patch.object(settings, "whatsapp_db_path", str(database_path)),
                patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
                patch.object(settings, "whatsapp_enabled", True),
                patch.object(settings, "whatsapp_phone_number_id", "test-phone"),
                patch.object(settings, "whatsapp_access_token", "test-token"),
            ):
                save_whatsapp_business_connection(
                    waba_id="authorized-waba",
                    phone_number_id="authorized-phone",
                    activate=False,
                )
                with self.assertRaises(WhatsAppConfigurationError):
                    whatsapp_client.validate_configuration()


if __name__ == "__main__":
    unittest.main()
