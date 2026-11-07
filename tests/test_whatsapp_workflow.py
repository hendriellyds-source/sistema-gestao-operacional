from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
import base64
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings
from app.whatsapp_client import WhatsAppSendResponse
from app.whatsapp_client import WhatsAppAPIError
from app.whatsapp_store import update_conversation
from app.whatsapp_store import (
    _initialized_databases,
    create_label,
    initialize_whatsapp_database,
    list_conversations,
    list_messages,
    mark_conversation_read_for_user,
    save_whatsapp_business_connection,
    set_contact_labels,
    whatsapp_sync_diagnostics,
)


class WhatsAppWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self.temporary_directory.name) / "whatsapp-test.db"
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_whatsapp_visual_interface_is_removed(self) -> None:
        admin = {
            "id": 1, "profile_id": "1", "display_name": "Administrador",
            "role_code": "admin", "areas": ["admin"],
        }
        with patch("app.main.user_from_session", return_value=admin):
            full = self.client.get("/whatsapp")
            workspace = self.client.get("/workspace")
            integration = self.client.get("/whatsapp/integracao")
            employee_app = self.client.get("/app")
        self.assertEqual(full.status_code, 404)
        self.assertEqual(integration.status_code, 404)
        self.assertNotIn('Atendimento WhatsApp', workspace.text)
        self.assertNotIn('whatsapp_floating.js', workspace.text)
        self.assertNotIn('whatsapp_floating.js', employee_app.text)
        with patch("app.main.user_from_session", return_value=None):
            unauthenticated_employee_app = self.client.get("/app")
        self.assertNotIn('whatsapp_floating.js', unauthenticated_employee_app.text)

    @staticmethod
    def _payload(message_id: str, text: str) -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "test-waba",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "contacts": [
                                    {
                                        "wa_id": "5548999999999",
                                        "profile": {"name": "Cliente Teste"},
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "5548999999999",
                                        "id": message_id,
                                        "timestamp": "1788200000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

    def _post(self, payload: dict):
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = "sha256=" + hmac.new(
            b"test-app-secret", raw_body, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            "/api/whatsapp/webhook",
            content=raw_body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": signature,
            },
        )

    @staticmethod
    def _echo_payload(message_id: str, text: str) -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "test-waba",
                "changes": [{
                    "field": "smb_message_echoes",
                    "value": {
                        "metadata": {"display_phone_number": "5500000000000"},
                        "message_echoes": [{
                            "from": "5500000000000",
                            "to": "5548999999999",
                            "id": message_id,
                            "timestamp": "1788200000",
                            "type": "text",
                            "text": {"body": text},
                        }],
                    },
                }],
            }],
        }

    @staticmethod
    def _history_payload() -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "test-waba",
                "changes": [{
                    "field": "history",
                    "value": {
                        "metadata": {"display_phone_number": "5500000000000"},
                        "history": [{
                            "metadata": {"phase": 1, "progress": 100},
                            "threads": [{
                                "id": "5548999999999",
                                "messages": [
                                    {
                                        "from": "5548999999999",
                                        "to": "5500000000000",
                                        "id": "wamid.history.in",
                                        "timestamp": "1788100000",
                                        "type": "text",
                                        "text": {"body": "Mensagem antiga"},
                                        "history_context": {"status": "read"},
                                    },
                                    {
                                        "from": "5500000000000",
                                        "to": "5548999999999",
                                        "id": "wamid.history.out",
                                        "timestamp": "1788100060",
                                        "type": "text",
                                        "text": {"body": "Resposta antiga"},
                                        "history_context": {"status": "read"},
                                    },
                                ],
                            }],
                        }],
                    },
                }],
            }],
        }

    def test_menu_creates_conversation_and_routes_finance(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
            patch.object(settings, "whatsapp_auto_menu_enabled", True),
        ):
            first_response = self._post(self._payload("wamid.1", "Olá"))
            self.assertEqual(first_response.status_code, 200, first_response.text)

            conversations = list_conversations()
            self.assertEqual(len(conversations), 1)
            self.assertEqual(conversations[0]["status"], "awaiting_menu")
            self.assertEqual(conversations[0]["profile_name"], "Cliente Teste")

            route_response = self._post(self._payload("wamid.2", "1"))
            self.assertEqual(route_response.status_code, 200, route_response.text)

            conversations = list_conversations()
            self.assertEqual(conversations[0]["queue_key"], "finance")
            self.assertEqual(conversations[0]["queue_label"], "Financeiro")
            self.assertEqual(conversations[0]["assignee"], "Financeiro")
            self.assertEqual(conversations[0]["status"], "open")
            self.assertEqual(len(list_messages(conversations[0]["id"])), 2)

    def test_greeting_is_sent_only_once_unless_menu_is_requested(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_auto_menu_enabled", True),
            patch("app.whatsapp_routes.send_and_record") as send_reply,
        ):
            for message_id, text in (
                ("wamid.hello", "Olá"),
                ("wamid.followup", "Tenho uma dúvida"),
                ("wamid.menu", "menu"),
                ("wamid.option", "2"),
                ("wamid.thanks", "Obrigado"),
            ):
                self.assertEqual(self._post(self._payload(message_id, text)).status_code, 200)
            self.assertEqual(send_reply.call_count, 3)
            self.assertIn("1 - Financeiro", send_reply.call_args_list[0].kwargs["message"])
            self.assertIn("1 - Financeiro", send_reply.call_args_list[1].kwargs["message"])
            self.assertIn("Levantamentos", send_reply.call_args_list[2].kwargs["message"])

    def test_existing_contact_is_not_greeted_when_automation_is_enabled(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch("app.whatsapp_routes.send_and_record") as send_reply,
        ):
            with patch.object(settings, "whatsapp_auto_menu_enabled", False):
                self.assertEqual(self._post(self._payload("wamid.before", "Oi")).status_code, 200)
            with patch.object(settings, "whatsapp_auto_menu_enabled", True):
                self.assertEqual(self._post(self._payload("wamid.after", "Outra pergunta")).status_code, 200)
            send_reply.assert_not_called()

    def test_duplicate_webhook_does_not_duplicate_message(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            payload = self._payload("wamid.duplicate", "Projetos")
            first_response = self._post(payload)
            duplicate_response = self._post(payload)

            self.assertEqual(first_response.json()["messages"], 1)
            self.assertEqual(duplicate_response.json()["messages"], 0)
            self.assertEqual(duplicate_response.json()["duplicates"], 1)
            conversation = list_conversations()[0]
            self.assertEqual(len(list_messages(conversation["id"])), 1)

    def test_delayed_inbound_webhook_keeps_chronological_order_and_preview(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_auto_menu_enabled", False),
        ):
            newer = self._payload("wamid.newer", "Mensagem mais recente")
            newer["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "1788200120"
            older = self._payload("wamid.older", "Mensagem anterior")
            self.assertEqual(self._post(newer).status_code, 200)
            self.assertEqual(self._post(older).status_code, 200)

            conversation = list_conversations()[0]
            messages = list_messages(conversation["id"])
            self.assertEqual([row["message_id"] for row in messages], ["wamid.older", "wamid.newer"])
            self.assertEqual(conversation["last_message_text"], "Mensagem mais recente")
            self.assertEqual(conversation["last_message_at"], messages[-1]["created_at"])

    def test_delayed_mobile_echo_keeps_chronological_order_and_preview(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
        ):
            newer = self._echo_payload("wamid.echo.newer", "Resposta mais recente")
            newer["entry"][0]["changes"][0]["value"]["message_echoes"][0]["timestamp"] = "1788200120"
            older = self._echo_payload("wamid.echo.older", "Resposta anterior")
            self.assertEqual(self._post(newer).status_code, 200)
            self.assertEqual(self._post(older).status_code, 200)

            conversation = list_conversations()[0]
            messages = list_messages(conversation["id"])
            self.assertEqual([row["message_id"] for row in messages], ["wamid.echo.older", "wamid.echo.newer"])
            self.assertEqual(conversation["last_message_text"], "Resposta mais recente")
            self.assertEqual(conversation["last_message_at"], messages[-1]["created_at"])

    def test_sync_diagnostics_counts_history_and_echo_without_content(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
        ):
            self.assertEqual(self._post(self._history_payload()).status_code, 200)
            self.assertEqual(self._post(self._echo_payload("wamid.echo", "Segredo")).status_code, 200)
            diagnostics = whatsapp_sync_diagnostics()
            self.assertEqual(diagnostics["events"]["history"]["total"], 1)
            self.assertEqual(diagnostics["events"]["smb_message_echoes"]["total"], 1)
            self.assertEqual(diagnostics["echo"]["messages_in_webhooks"], 1)
            self.assertEqual(diagnostics["echo"]["missing_recipient_or_id"], 0)
            self.assertEqual(diagnostics["history"]["messages_in_webhooks"], 2)
            self.assertEqual(diagnostics["messages"]["outbound:business_app"]["total"], 1)
            self.assertNotIn("Segredo", str(diagnostics))

    def test_existing_preview_is_repaired_from_message_event_time(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_auto_menu_enabled", False),
        ):
            self.assertEqual(
                self._post(self._payload("wamid.repair", "Mensagem correta")).status_code,
                200,
            )
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE whatsapp_conversations "
                        "SET last_message_text = 'Prévia incorreta', "
                        "last_message_at = '2030-01-01 00:00:00'"
                    )
                    connection.execute(
                        "DELETE FROM whatsapp_schema_migrations WHERE name = ?",
                        ("message_event_time_v1",),
                    )
            _initialized_databases.discard(Path(self.database_path).resolve())
            initialize_whatsapp_database()
            conversation = list_conversations()[0]
            message = list_messages(conversation["id"])[0]
            self.assertEqual(conversation["last_message_text"], "Mensagem correta")
            self.assertEqual(conversation["last_message_at"], message["created_at"])

    def test_webhook_does_not_mix_inactive_business_accounts(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_auto_menu_enabled", False),
        ):
            save_whatsapp_business_connection(waba_id="active-waba", activate=True)
            inactive = self._post(self._payload("wamid.inactive", "Número antigo"))
            self.assertEqual(inactive.status_code, 200)
            self.assertEqual(inactive.json()["status"], "ignored")
            self.assertEqual(list_conversations(), [])

            active_payload = self._payload("wamid.active", "Número atual")
            active_payload["entry"][0]["id"] = "active-waba"
            active = self._post(active_payload)
            self.assertEqual(active.json()["messages"], 1)
            self.assertEqual(list_conversations()[0]["last_message_text"], "Número atual")

    def test_business_app_echo_is_mirrored_as_daniel(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_mobile_sender_name", "Daniel"),
        ):
            response = self._post(self._echo_payload("wamid.echo.1", "Já respondi"))
            self.assertEqual(response.status_code, 200, response.text)
            conversation = list_conversations()[0]
            message = list_messages(conversation["id"])[0]
            self.assertEqual(message["direction"], "outbound")
            self.assertEqual(message["source"], "business_app")
            self.assertEqual(message["actor_name"], "Daniel")

    def test_history_is_imported_without_creating_unread_notifications(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
        ):
            response = self._post(self._history_payload())
            self.assertEqual(response.status_code, 200, response.text)
            conversation = list_conversations(viewer_key="helena")[0]
            messages = list_messages(conversation["id"])
            self.assertEqual(len(messages), 2)
            self.assertEqual(conversation["viewer_unread_count"], 0)
            self.assertTrue(all(message["source"] == "history" for message in messages))

    def test_read_state_and_labels_are_individual(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.unread", "Olá"))
            conversation = list_conversations(viewer_key="admin")[0]
            mark_conversation_read_for_user(conversation["id"], user_key="admin")
            self.assertEqual(
                list_conversations(viewer_key="admin")[0]["viewer_unread_count"], 0
            )
            self.assertEqual(
                list_conversations(viewer_key="helena")[0]["viewer_unread_count"], 1
            )
            label = create_label(name="Funcionário", color="#1976c9")
            labels = set_contact_labels(
                contact_phone=conversation["contact_phone"],
                label_ids=[label["id"]],
                primary_label_id=label["id"],
            )
            self.assertEqual(labels[0]["name"], "Funcionário")
            filtered = list_conversations(label_id=label["id"], viewer_key="admin")
            self.assertEqual(filtered[0]["primary_label_color"], "#1976c9")

    def test_admin_preview_preserves_agent_notification(self) -> None:
        admin = {
            "id": 1, "display_name": "Administrador", "role_code": "admin"
        }
        helena = {"id": 2, "display_name": "Helena", "role_code": "helena"}
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.agent.unread", "Preciso de um projeto"))
            conversation = list_conversations()[0]
            update_conversation(conversation["id"], assignee="Helena")
            with patch("app.main.user_from_session", return_value=admin):
                preview = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/read",
                    json={"propagate_to_whatsapp": True},
                )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertFalse(preview.json()["propagated_to_whatsapp"])
            self.assertEqual(
                list_conversations(viewer_key="2")[0]["viewer_unread_count"], 1
            )
            with (
                patch("app.main.user_from_session", return_value=helena),
                patch("app.whatsapp_routes.whatsapp_client.mark_message_read") as mark,
            ):
                read = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/read",
                    json={"propagate_to_whatsapp": True},
                )
            self.assertEqual(read.status_code, 200, read.text)
            self.assertTrue(read.json()["queued_for_whatsapp"])
            self.assertFalse(read.json()["propagated_to_whatsapp"])
            mark.assert_called_once_with("wamid.agent.unread")
            mark.assert_called_once_with("wamid.agent.unread")
            self.assertEqual(
                list_conversations(viewer_key="2")[0]["viewer_unread_count"], 0
            )

    def test_failed_meta_read_receipt_is_retried(self) -> None:
        helena = {"id": 2, "display_name": "Helena", "role_code": "helena"}
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.read.retry", "Preciso de ajuda"))
            conversation = list_conversations()[0]
            update_conversation(conversation["id"], assignee="Helena")
            with (
                patch("app.main.user_from_session", return_value=helena),
                patch(
                    "app.whatsapp_routes.whatsapp_client.mark_message_read",
                    side_effect=WhatsAppAPIError("falha temporária"),
                ) as mark,
            ):
                first = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/read",
                    json={"propagate_to_whatsapp": True},
                )
                self.assertEqual(first.status_code, 200)
                self.assertFalse(first.json()["propagated_to_whatsapp"])
                self.assertTrue(first.json()["queued_for_whatsapp"])
                self.assertEqual(mark.call_count, 1)
            with (
                patch("app.main.user_from_session", return_value=helena),
                patch("app.whatsapp_routes.whatsapp_client.mark_message_read") as mark,
            ):
                second = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/read",
                    json={"propagate_to_whatsapp": True},
                )
                self.assertTrue(second.json()["queued_for_whatsapp"])
                mark.assert_called_once_with("wamid.read.retry")
                third = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/read",
                    json={"propagate_to_whatsapp": True},
                )
                self.assertEqual(third.json()["read_messages"], 0)
                self.assertFalse(third.json()["queued_for_whatsapp"])

    def test_agent_cannot_open_another_persons_conversation(self) -> None:
        helena = {"id": 2, "display_name": "Helena", "role_code": "helena"}
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.private", "Olá"))
            conversation = list_conversations()[0]
            update_conversation(conversation["id"], assignee="Daniel")
            with patch("app.main.user_from_session", return_value=helena):
                response = self.client.get(
                    f"/api/whatsapp/conversations/{conversation['id']}"
                )
            self.assertEqual(response.status_code, 403)

    def test_profile_assignment_reaches_only_selected_account_and_stays_assigned(self) -> None:
        admin = {
            "profile_id": "11111111-1111-4111-8111-111111111111",
            "organization_id": "org-test",
            "display_name": "Administrador",
            "role_code": "admin",
        }
        helena = {
            "profile_id": "22222222-2222-4222-8222-222222222222",
            "organization_id": "org-test",
            "display_name": "Helena Projetos",
            "role_code": "helena",
        }
        other = {
            "profile_id": "33333333-3333-4333-8333-333333333333",
            "organization_id": "org-test",
            "display_name": "Helena Projetos",
            "role_code": "aline",
        }
        provider_response = WhatsAppSendResponse(
            status_code=200, payload={"messages": [{"id": "wamid.assigned.out"}]}
        )
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.assigned.in", "Preciso de projeto"))
            conversation_id = list_conversations()[0]["id"]
            with (
                patch("app.main.user_from_session", return_value=admin),
                patch(
                    "app.whatsapp_routes._assignable_profile",
                    return_value={"id": helena["profile_id"], "name": helena["display_name"]},
                ),
            ):
                assigned = self.client.patch(
                    f"/api/whatsapp/conversations/{conversation_id}",
                    json={"assignee_profile_id": helena["profile_id"], "status": "open"},
                )
                self.assertEqual(assigned.status_code, 200, assigned.text)
                self.assertEqual(
                    assigned.json()["conversation"]["assignee_profile_id"],
                    helena["profile_id"],
                )
                preview = self.client.post(
                    f"/api/whatsapp/conversations/{conversation_id}/read",
                    json={"propagate_to_whatsapp": True},
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                with patch(
                    "app.whatsapp_routes.whatsapp_client.send_text",
                    return_value=provider_response,
                ):
                    sent = self.client.post(
                        f"/api/whatsapp/conversations/{conversation_id}/messages",
                        json={"message": "Vou encaminhar"},
                    )
                self.assertEqual(sent.status_code, 200, sent.text)

            with patch("app.main.user_from_session", return_value=helena):
                inbox = self.client.get("/api/whatsapp/conversations?view=mine")
                detail = self.client.get(f"/api/whatsapp/conversations/{conversation_id}")
            self.assertEqual(inbox.status_code, 200, inbox.text)
            self.assertEqual(len(inbox.json()["items"]), 1)
            self.assertGreaterEqual(inbox.json()["items"][0]["viewer_unread_count"], 1)
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(
                detail.json()["conversation"]["assignee_profile_id"],
                helena["profile_id"],
            )

            with patch("app.main.user_from_session", return_value=other):
                other_inbox = self.client.get("/api/whatsapp/conversations?view=mine")
                other_detail = self.client.get(
                    f"/api/whatsapp/conversations/{conversation_id}"
                )
            self.assertEqual(other_inbox.json()["items"], [])
            self.assertEqual(other_detail.status_code, 403)

    def test_only_admin_can_list_assignable_profiles(self) -> None:
        admin = {"role_code": "admin", "organization_id": "org-test"}
        helena = {"role_code": "helena", "organization_id": "org-test"}
        profiles = [{"id": "profile-1", "name": "Helena", "role_code": "helena"}]
        with patch("app.main.user_from_session", return_value=admin), patch(
            "app.whatsapp_routes._assignable_profiles", return_value=profiles
        ):
            response = self.client.get("/api/whatsapp/assignees")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], profiles)
        with patch("app.main.user_from_session", return_value=helena):
            denied = self.client.get("/api/whatsapp/assignees")
        self.assertEqual(denied.status_code, 403)

    def test_director_can_switch_between_all_and_own_conversations(self) -> None:
        daniel = {
            "role_code": "director", "display_name": "Recrutador Demo",
            "profile_id": "0588a421-468e-496d-ae17-b2252e944784",
        }
        helena_id = "a22ab0b3-28ec-40fc-b9dd-079391012632"
        second = self._payload("wamid.second.view", "Projeto")
        change = second["entry"][0]["changes"][0]["value"]
        change["contacts"][0]["wa_id"] = "5548999888777"
        change["messages"][0]["from"] = "5548999888777"
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.first.view", "Olá"))
            self._post(second)
            conversations = list_conversations()
            self.assertEqual(len(conversations), 2)
            for conversation in conversations:
                if conversation["contact_phone"] == "5548999999999":
                    update_conversation(
                        conversation["id"], assignee="Recrutador Demo",
                        assignee_profile_id=daniel["profile_id"],
                    )
                else:
                    update_conversation(
                        conversation["id"], assignee="Helena",
                        assignee_profile_id=helena_id,
                    )
            with patch("app.main.user_from_session", return_value=daniel):
                all_items = self.client.get("/api/whatsapp/conversations?view=all")
                mine = self.client.get("/api/whatsapp/conversations?view=mine")
            self.assertEqual(all_items.status_code, 200, all_items.text)
            self.assertEqual(len(all_items.json()["items"]), 2)
            self.assertEqual(mine.status_code, 200, mine.text)
            self.assertEqual(len(mine.json()["items"]), 1)
            self.assertEqual(mine.json()["items"][0]["assignee_profile_id"], daniel["profile_id"])

    def test_assignable_profiles_are_active_internal_accounts_in_same_organization(self) -> None:
        from app.whatsapp_routes import _assignable_profiles

        query = Mock()
        query.select.return_value = query
        query.eq.return_value = query
        query.order.return_value = query
        query.execute.return_value.data = [
            {
                "id": "helena-id", "auth_user_id": "auth-helena",
                "full_name": "Helena", "email": "helena@example.com",
                "role_code": "helena", "status": "active",
            },
            {
                "id": "client-id", "auth_user_id": "auth-client",
                "full_name": "Cliente", "email": "client@example.com",
                "role_code": "client", "status": "active",
            },
            {
                "id": "no-login", "auth_user_id": None,
                "full_name": "Sem login", "email": "no-login@example.com",
                "role_code": "financial", "status": "active",
            },
        ]
        supabase = Mock()
        supabase.table.return_value = query
        with patch("app.whatsapp_routes.create_admin_supabase", return_value=supabase):
            profiles = _assignable_profiles({"organization_id": "org-test"})
        self.assertEqual([profile["id"] for profile in profiles], ["helena-id"])
        self.assertIn(("organization_id", "org-test"), [call.args for call in query.eq.call_args_list])
        self.assertIn(("status", "active"), [call.args for call in query.eq.call_args_list])

    def test_audio_upload_is_recorded_for_responsible_attendant(self) -> None:
        helena = {"id": 2, "display_name": "Helena", "role_code": "helena"}
        provider_response = WhatsAppSendResponse(
            status_code=200, payload={"messages": [{"id": "wamid.audio.out"}]}
        )
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.before.audio", "Olá"))
            conversation = list_conversations()[0]
            update_conversation(conversation["id"], assignee="Helena")
            with (
                patch("app.main.user_from_session", return_value=helena),
                patch(
                    "app.whatsapp_routes.whatsapp_client.upload_audio",
                    return_value="media-audio-id",
                ) as upload,
                patch(
                    "app.whatsapp_routes.whatsapp_client.send_audio",
                    return_value=provider_response,
                ) as send,
            ):
                response = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/messages/audio",
                    json={
                        "content_base64": base64.b64encode(
                            b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x00" * 40
                        ).decode("ascii"),
                        "filename": "teste.ogg",
                        "mime_type": "audio/ogg",
                    },
                )
            self.assertEqual(response.status_code, 200, response.text)
            upload.assert_called_once()
            send.assert_called_once_with(
                recipient=conversation["contact_phone"], media_id="media-audio-id"
            )
            message = list_messages(conversation["id"])[-1]
            self.assertEqual(message["message_type"], "audio")
            self.assertEqual(message["actor_name"], "Helena")
            self.assertTrue(message["media_path"])

    def test_document_received_can_be_downloaded_only_by_responsible_user(self) -> None:
        document = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
        payload = self._payload("wamid.inbound.pdf", "")
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message.pop("text")
        message.update({
            "type": "document",
            "document": {"id": "meta-pdf-id", "mime_type": "application/pdf", "filename": "contrato.pdf"},
        })
        helena = {"role_code": "helena", "display_name": "Helena", "profile_id": "a22ab0b3-28ec-40fc-b9dd-079391012632"}
        other = {"role_code": "aline", "display_name": "Aline", "profile_id": "38d6ac77-246a-4b77-a399-2731b510a18f"}
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
            patch("app.whatsapp_media.whatsapp_client.download_media", return_value=(document, "application/pdf")) as download,
        ):
            response = self._post(payload)
            self.assertEqual(response.status_code, 200, response.text)
            conversation = list_conversations()[0]
            update_conversation(conversation["id"], assignee="Helena", assignee_profile_id=helena["profile_id"])
            saved = list_messages(conversation["id"])[0]
            self.assertEqual(saved["message_type"], "document")
            self.assertTrue(saved["media_path"])
            with patch("app.main.user_from_session", return_value=helena):
                result = self.client.get(f"/api/whatsapp/messages/{saved['id']}/media?download=true")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.content, document)
            self.assertIn("attachment", result.headers["content-disposition"])
            self.assertEqual(result.headers["x-content-type-options"], "nosniff")
            with patch("app.main.user_from_session", return_value=other):
                denied = self.client.get(f"/api/whatsapp/messages/{saved['id']}/media")
            self.assertEqual(denied.status_code, 403)
            download.assert_called_once_with("meta-pdf-id")

    def test_document_upload_is_sent_and_recorded(self) -> None:
        user = {"role_code": "admin", "display_name": "Daniel", "profile_id": "daecab59-3abf-4fc7-bdc8-6ae85acc9201"}
        document = b"%PDF-1.4\n%%EOF\n"
        provider_response = WhatsAppSendResponse(
            status_code=200, payload={"messages": [{"id": "wamid.outbound.pdf"}]}
        )
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            self._post(self._payload("wamid.before.pdf", "Olá"))
            conversation = list_conversations()[0]
            with (
                patch("app.main.user_from_session", return_value=user),
                patch("app.whatsapp_routes.whatsapp_client.upload_file", return_value="meta-upload-id") as upload,
                patch("app.whatsapp_routes.whatsapp_client.send_file", return_value=provider_response) as send,
            ):
                response = self.client.post(
                    f"/api/whatsapp/conversations/{conversation['id']}/messages/file",
                    json={"content_base64": base64.b64encode(document).decode(), "filename": "planta.pdf", "mime_type": "application/pdf"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                sent = response.json()["message"]
                download = self.client.get(f"/api/whatsapp/messages/{sent['id']}/media?download=true")
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.content, document)
            upload.assert_called_once()
            send.assert_called_once_with(
                recipient=conversation["contact_phone"], media_id="meta-upload-id",
                message_type="document", filename="planta.pdf",
            )
            self.assertEqual(sent["message_type"], "document")
            self.assertEqual(sent["media_filename"], "planta.pdf")

    def test_file_upload_rejects_mismatched_content(self) -> None:
        from app.whatsapp_media import decode_file_upload
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            decode_file_upload(
                content_base64=base64.b64encode(b"not a pdf").decode(),
                filename="fraude.pdf", declared_mime="application/pdf",
            )

    def test_image_upload_uses_whatsapp_image_type(self) -> None:
        from app.whatsapp_media import decode_file_upload
        image = b"\x89PNG\r\n\x1a\n" + b"sample-png-content"
        decoded = decode_file_upload(
            content_base64=base64.b64encode(image).decode(),
            filename="pasta\\foto.png", declared_mime="image/png",
        )
        self.assertEqual(decoded, (image, "foto.png", "image/png", "image"))

    def test_meta_document_message_includes_filename(self) -> None:
        from app.whatsapp_client import WhatsAppClient
        client = WhatsAppClient()
        response = Mock(status_code=200)
        response.json.return_value = {"messages": [{"id": "wamid.document"}]}
        with (
            patch.object(client, "_operational_credentials", return_value=("phone-id", "test-token")),
            patch("app.whatsapp_client.requests.post", return_value=response) as request,
        ):
            result = client.send_file(
                recipient="+55 (48) 99999-9999", media_id="media-id",
                message_type="document", filename="planta.pdf",
            )
        self.assertEqual(result.status_code, 200)
        sent = request.call_args.kwargs["json"]
        self.assertEqual(sent["to"], "5548999999999")
        self.assertEqual(sent["document"], {"id": "media-id", "filename": "planta.pdf"})

    def test_invalid_signature_is_rejected(self) -> None:
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
        ):
            response = self.client.post(
                "/api/whatsapp/webhook",
                json=self._payload("wamid.invalid", "Olá"),
                headers={"x-hub-signature-256": "sha256=invalid"},
            )

        self.assertEqual(response.status_code, 401)

    def test_admin_disconnect_preserves_history_and_stops_webhook_processing(self) -> None:
        admin = {"role_code": "admin", "display_name": "Administrador"}
        helena = {"role_code": "helena", "display_name": "Helena"}
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
            patch.object(settings, "whatsapp_enabled", False),
        ):
            connected = save_whatsapp_business_connection(
                waba_id="123456", phone_number_id="987654",
                access_token="private-test-token", activate=True,
            )
            first = self._payload("wamid.before.disconnect", "Olá")
            first["entry"][0]["id"] = "123456"
            self.assertEqual(self._post(first).status_code, 200)
            conversation = list_conversations()[0]
            with patch("app.main.user_from_session", return_value=helena):
                forbidden = self.client.post(
                    f"/api/whatsapp/onboarding/connections/{connected['id']}/disconnect",
                    json={"waba_id": "123456", "confirmation": "DESCONECTAR"},
                )
            self.assertEqual(forbidden.status_code, 403)
            with patch("app.main.user_from_session", return_value=admin):
                mismatch = self.client.post(
                    f"/api/whatsapp/onboarding/connections/{connected['id']}/disconnect",
                    json={"waba_id": "999999", "confirmation": "DESCONECTAR"},
                )
                self.assertEqual(mismatch.status_code, 409)
                response = self.client.post(
                    f"/api/whatsapp/onboarding/connections/{connected['id']}/disconnect",
                    json={"waba_id": "123456", "confirmation": "DESCONECTAR"},
                )
                status_response = self.client.get("/api/whatsapp/onboarding/status")
                with (
                    patch.object(settings, "whatsapp_phone_number_id", "legacy-phone"),
                    patch.object(settings, "whatsapp_business_account_id", "legacy-waba"),
                    patch.object(settings, "whatsapp_access_token", "legacy-token"),
                ):
                    operational_status = self.client.get("/api/whatsapp/status")
            self.assertEqual(response.status_code, 200, response.text)
            disconnected = response.json()["connection"]
            self.assertEqual(disconnected["connection_status"], "disconnected")
            self.assertEqual(disconnected["is_active"], 0)
            self.assertEqual(disconnected["has_access_token"], 0)
            self.assertEqual(disconnected["disconnected_by"], "Administrador")
            self.assertEqual(status_response.json()["connections"][0]["id"], connected["id"])
            self.assertEqual(operational_status.status_code, 200)
            self.assertFalse(operational_status.json()["ready"])
            self.assertFalse(operational_status.json()["configured"]["access_token"])
            self.assertEqual(len(list_messages(conversation["id"])), 1)
            later = self._payload("wamid.after.disconnect", "Ainda está aí?")
            later["entry"][0]["id"] = "123456"
            ignored = self._post(later)
            self.assertEqual(ignored.status_code, 200)
            self.assertEqual(ignored.json()["status"], "ignored")
            self.assertEqual(len(list_messages(conversation["id"])), 1)

    def test_reauthorizing_disconnected_account_restores_operational_token(self) -> None:
        from app.whatsapp_store import disconnect_whatsapp_business_connection, get_active_whatsapp_credentials
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_app_secret", "test-app-secret"),
        ):
            connected = save_whatsapp_business_connection(
                waba_id="123456", phone_number_id="987654",
                access_token="first-token", activate=True,
            )
            disconnect_whatsapp_business_connection(
                connected["id"], waba_id="123456", disconnected_by="Administrador",
            )
            self.assertIsNone(get_active_whatsapp_credentials())
            restored = save_whatsapp_business_connection(
                waba_id="123456", phone_number_id="987654",
                access_token="new-token", activate=True,
            )
            self.assertEqual(restored["connection_status"], "connected")
            self.assertEqual(restored["is_active"], 1)
            self.assertIsNone(restored["disconnected_at"])
            self.assertEqual(get_active_whatsapp_credentials(), ("987654", "new-token"))

    def test_start_conversation_sends_template_and_records_message(self) -> None:
        user = {
            "id": 1,
            "display_name": "Recrutador Demo",
            "email": "demo@portfolio.local",
            "role_code": "director",
        }
        provider_response = WhatsAppSendResponse(
            status_code=200,
            payload={"messages": [{"id": "wamid.outbound.1"}]},
        )
        with (
            patch.object(settings, "whatsapp_db_path", self.database_path),
            patch.object(settings, "whatsapp_initial_template_name", "test_template"),
            patch.object(settings, "whatsapp_initial_template_language", "en_US"),
            patch("app.main.user_from_session", return_value=user),
            patch(
                "app.whatsapp_routes.whatsapp_client.send_template",
                return_value=provider_response,
            ) as send_template,
        ):
            response = self.client.post(
                "/api/whatsapp/conversations",
                json={
                    "phone": "+55 (47) 99709-8277",
                    "profile_name": "Daniel Teste",
                },
            )

            self.assertEqual(response.status_code, 200, response.text)
            conversation = response.json()["conversation"]
            self.assertEqual(conversation["contact_phone"], "5547997098277")
            self.assertEqual(conversation["profile_name"], "Daniel Teste")
            self.assertEqual(conversation["status"], "open")
            messages = list_messages(conversation["id"])
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["direction"], "outbound")
            self.assertEqual(messages[0]["message_id"], "wamid.outbound.1")
            send_template.assert_called_once()
            self.assertEqual(
                send_template.call_args.kwargs["recipient"],
                "5547997098277",
            )


if __name__ == "__main__":
    unittest.main()
