from __future__ import annotations

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.cora_routes import cora_client
from app.main import app
from app.settings import settings
from app.system_state import get_system_state, merge_state_for_user, save_system_state


class CoraWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_db = f"{self.temp_dir.name}/state.db"
        self.webhook_db = f"{self.temp_dir.name}/cora.db"
        self.settings_patchers = [
            patch.object(settings, "system_state_db_path", self.state_db),
            patch.object(settings, "cora_webhook_db_path", self.webhook_db),
            patch.object(settings, "cora_webhook_token", "test-secret"),
        ]
        for patcher in self.settings_patchers:
            patcher.start()
        save_system_state(
            {
                "invoices": [
                    {
                        "id": 17,
                        "billingEntityId": 4,
                        "invoiceNumber": "COB-17",
                        "dueDate": "2026-08-20",
                        "status": "Enviada ao cliente",
                        "total": 123.45,
                        "paidAmount": 0,
                        "paymentDate": "",
                    }
                ],
                "billingEntities": [
                    {"id": 4, "cnpj": "12.345.678/0001-90"}
                ],
                "companies": [],
            },
            source="test",
            updated_by="test",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.settings_patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def paid_invoice() -> dict:
        return {
            "id": "inv_paid_123",
            "status": "PAID",
            "code": "external-code",
            "total_amount": 12345,
            "total_paid": 12345,
            "occurrence_date": "2026-08-11T10:20:00",
            "payment_terms": {"due_date": "2026-08-20"},
            "customer": {
                "document": {"identity": "12345678000190", "type": "CNPJ"}
            },
        }

    def test_webhook_is_public_but_requires_its_secret(self) -> None:
        with patch("app.main.user_from_session") as user_from_session:
            response = self.client.post(
                "/api/cora/webhook",
                headers={
                    "webhook-event-id": "evt_unauthorized",
                    "webhook-event-type": "invoice.paid",
                    "webhook-resource-id": "inv_paid_123",
                },
            )
        self.assertEqual(response.status_code, 401)
        user_from_session.assert_not_called()

    def test_paid_event_is_confirmed_and_updates_internal_invoice(self) -> None:
        headers = {
            "webhook-event-id": "evt_paid_123",
            "webhook-event-type": "invoice.paid",
            "webhook-resource-id": "inv_paid_123",
        }
        with patch.object(
            cora_client,
            "invoice",
            AsyncMock(return_value=self.paid_invoice()),
        ) as invoice_lookup:
            response = self.client.post(
                "/api/cora/webhook?token=test-secret",
                headers=headers,
            )
            duplicate = self.client.post(
                "/api/cora/webhook?token=test-secret",
                headers=headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["processed"])
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        invoice_lookup.assert_awaited_once_with("inv_paid_123")

        invoice = get_system_state()["data"]["invoices"][0]
        self.assertEqual(invoice["status"], "Paga")
        self.assertEqual(invoice["paidAmount"], 123.45)
        self.assertEqual(invoice["paymentDate"], "2026-08-11")
        self.assertEqual(invoice["paymentSource"], "Cora")
        self.assertEqual(invoice["coraInvoiceId"], "inv_paid_123")

    def test_spoofed_paid_event_cannot_override_cora_status(self) -> None:
        details = self.paid_invoice()
        details["status"] = "OPEN"
        with patch.object(
            cora_client,
            "invoice",
            AsyncMock(return_value=details),
        ):
            response = self.client.post(
                "/api/cora/webhook?token=test-secret",
                headers={
                    "webhook-event-id": "evt_spoofed",
                    "webhook-event-type": "invoice.paid",
                    "webhook-resource-id": "inv_paid_123",
                },
            )
        self.assertEqual(response.status_code, 503, response.text)
        invoice = get_system_state()["data"]["invoices"][0]
        self.assertNotEqual(invoice["status"], "Paga")

    def test_manual_sync_remains_protected_by_login(self) -> None:
        response = self.client.post("/api/cora/invoices/inv_paid_123/sync")
        self.assertEqual(response.status_code, 401, response.text)

    def test_stale_browser_cannot_revert_cora_payment(self) -> None:
        current = get_system_state()["data"]
        current_invoice = current["invoices"][0]
        current_invoice.update(
            {
                "status": "Paga",
                "paidAmount": 123.45,
                "paymentDate": "2026-08-11",
                "paymentSource": "Cora",
                "coraInvoiceId": "inv_paid_123",
            }
        )
        save_system_state(current, source="cora-webhook", updated_by="cora")
        stale = {
            **current,
            "invoices": [
                {
                    **current_invoice,
                    "status": "Enviada ao cliente",
                    "paidAmount": 0,
                    "paymentDate": "",
                    "paymentSource": "",
                }
            ],
        }
        merged = merge_state_for_user(stale, {"role_code": "director"})
        self.assertEqual(merged["invoices"][0]["status"], "Paga")
        self.assertEqual(merged["invoices"][0]["paymentSource"], "Cora")


if __name__ == "__main__":
    unittest.main()
