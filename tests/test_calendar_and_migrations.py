from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
import gc
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import audit, calendar_service, operations, system_state
from app.main import app
from app.private_storage import (
    EMPLOYEE_CERTIFICATES_BUCKET,
    PrivateStorageError,
    decode_private_file,
    random_object_path,
    validate_private_object_reference,
)
from app.settings import settings
from tests.fakes import MemorySupabase
from tests.test_access_and_routes import user


class CalendarAggregationTests(unittest.TestCase):
    def test_expired_certificate_upcoming_exam_and_vehicle_debt_are_aggregated(self) -> None:
        state = {
            "employees": [{"id": 1, "name": "Pessoa Teste"}],
            "exams": [{"id": 2, "employeeId": 1, "due": "2026-08-12", "status": "Pendente"}],
            "maintenances": [],
            "fines": [],
            "fleetDocuments": [],
            "invoices": [],
        }
        supabase = MemorySupabase(
            {
                "employee_certificates": [
                    {
                        "id": "cert-1",
                        "organization_id": "org",
                        "employee_id": 1,
                        "course_name": "NR-35",
                        "expiration_date": "2026-08-02",
                        "no_expiration": False,
                        "deleted_at": None,
                    }
                ],
                "financial_calendar_entries": [],
            }
        )
        debt = {
            "id": 3,
            "identifier": "PROD1",
            "description": "IPVA",
            "due_date": "2026-08-15",
            "status": "PENDENTE",
            "amount": 500,
        }
        with (
            patch.object(calendar_service, "get_system_state", return_value={"data": state}),
            patch.object(calendar_service, "list_vehicle_debts", return_value=[debt]),
            patch.object(calendar_service, "list_vehicle_documents", return_value=[]),
            patch.object(calendar_service, "list_stored_documents", return_value=[]),
            patch.object(calendar_service, "list_calendar_maintenance", return_value=[]),
            patch.object(calendar_service, "create_admin_supabase", return_value=supabase),
        ):
            result = calendar_service.aggregate_calendar(
                organization_id="org",
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
            )
        source_types = {item["source_type"] for item in result["events"]}
        self.assertIn("employee_certificate", source_types)
        self.assertIn("employee_exam", source_types)
        self.assertIn("vehicle_debt", source_types)

    def test_deadline_status_uses_supplied_local_day_boundary(self) -> None:
        self.assertEqual(
            calendar_service._deadline_status(
                "2026-08-02", "pending", today=date(2026, 8, 3)
            ),
            "overdue",
        )
        self.assertEqual(
            calendar_service._deadline_status(
                "2026-08-03", "pending", today=date(2026, 8, 3)
            ),
            "pending",
        )


class PrivateStorageValidationTests(unittest.TestCase):
    def test_file_size_extension_and_mime_are_enforced(self) -> None:
        pdf = base64.b64encode(b"%PDF-1.7\nreview").decode()
        decoded, name, extension, mime_type = decode_private_file(
            filename="folder\\certificate.pdf",
            content_base64=pdf,
            declared_mime="application/pdf",
            max_bytes=1024,
        )
        self.assertTrue(decoded.startswith(b"%PDF-"))
        self.assertEqual(name, "certificate.pdf")
        self.assertEqual(extension, ".pdf")
        self.assertEqual(mime_type, "application/pdf")
        with self.assertRaises(ValueError):
            decode_private_file(
                filename="certificate.png",
                content_base64=pdf,
                declared_mime="image/png",
                max_bytes=1024,
            )
        with self.assertRaises(ValueError):
            decode_private_file(
                filename="certificate.pdf",
                content_base64=pdf,
                declared_mime="image/png",
                max_bytes=1024,
            )
        with self.assertRaises(ValueError):
            decode_private_file(
                filename="certificate.pdf",
                content_base64=pdf,
                declared_mime="application/pdf",
                max_bytes=4,
            )

    def test_private_reference_is_bound_to_bucket_and_owner_prefix(self) -> None:
        path = random_object_path(
            organization_id="org-1",
            namespace="employees",
            owner_id="10",
            extension=".pdf",
        )
        bucket, validated_path = validate_private_object_reference(
            bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            object_path=path,
            expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            organization_id="org-1",
            namespace="employees",
            owner_id="10",
        )
        self.assertEqual(bucket, EMPLOYEE_CERTIFICATES_BUCKET)
        self.assertEqual(validated_path, path)
        with self.assertRaises(PrivateStorageError):
            validate_private_object_reference(
                bucket="financial-calendar-attachments",
                object_path=path,
                expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                organization_id="org-1",
                namespace="employees",
                owner_id="10",
            )
        with self.assertRaises(PrivateStorageError):
            validate_private_object_reference(
                bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                object_path=path,
                expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                organization_id="org-1",
                namespace="employees",
                owner_id="11",
            )


class ManualEntryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = user("financial")
        self.memory = MemorySupabase({"financial_calendar_entries": []})
        self.patches = [
            patch("app.main.user_from_session", side_effect=lambda _token: self.current_user),
            patch("app.financial_calendar.create_admin_supabase", return_value=self.memory),
            patch("app.financial_calendar.write_audit"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()

    def test_manual_entry_can_be_created_and_marked_paid(self) -> None:
        response = self.client.post(
            "/api/financial-calendar/entries",
            json={
                "data": {
                    "title": "Folha mensal",
                    "category": "payroll",
                    "due_date": "2026-08-10",
                    "amount": 1250,
                    "status": "scheduled",
                }
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        entry_id = response.json()["entries"][0]["id"]
        paid = self.client.post(
            f"/api/financial-calendar/entries/{entry_id}/status",
            json={"status": "paid"},
        )
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(paid.json()["status"], "paid")
        self.assertIsNotNone(paid.json()["paid_at"])
        self.assertIsNone(paid.json()["cancelled_at"])
        cancelled = self.client.post(
            f"/api/financial-calendar/entries/{entry_id}/status",
            json={"status": "cancelled"},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertIsNone(cancelled.json()["paid_at"])
        self.assertIsNotNone(cancelled.json()["cancelled_at"])

    def test_recurrence_is_finite_and_bounded(self) -> None:
        response = self.client.post(
            "/api/financial-calendar/entries",
            json={
                "data": {
                    "title": "Imposto parcelado",
                    "category": "tax",
                    "due_date": "2026-08-10",
                },
                "recurrence": {"frequency": "monthly", "interval": 1, "count": 37},
            },
        )
        self.assertEqual(response.status_code, 422)


class MigrationIdempotenceTests(unittest.TestCase):
    def test_sqlite_initializers_can_run_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = {
                "audit": settings.audit_db_path,
                "history": settings.history_db_path,
                "state": settings.system_state_db_path,
            }
            settings.audit_db_path = str(Path(directory) / "audit.db")
            settings.history_db_path = str(Path(directory) / "history.db")
            settings.system_state_db_path = str(Path(directory) / "system_state.db")
            try:
                audit.init_audit_db(); audit.init_audit_db()
                operations.init_operations_db(); operations.init_operations_db()
                system_state.init_system_state(); system_state.init_system_state()
                connection = sqlite3.connect(settings.audit_db_path)
                try:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_log)")}
                finally:
                    connection.close()
                self.assertIn("actor_user_id", columns)
                self.assertIn("actor_role_code", columns)
            finally:
                settings.audit_db_path = previous["audit"]
                settings.history_db_path = previous["history"]
                settings.system_state_db_path = previous["state"]
                gc.collect()

    def test_supabase_migration_is_additive_and_idempotent(self) -> None:
        sql = Path("migrations/20260803_first_deploy.sql").read_text(encoding="utf-8").lower()
        self.assertGreaterEqual(sql.count("create table if not exists"), 2)
        self.assertIn("drop policy if exists", sql)
        self.assertIn("employee_certificates_storage_path_ck", sql)
        self.assertIn("financial_calendar_attachment_path_ck", sql)
        self.assertIn("p.role_code in ('admin', 'director', 'financial')", sql)
        self.assertNotRegex(
            sql,
            r"\b(?:insert\s+into|update|delete\s+from)\s+storage\.buckets\b",
        )
        for operation in ("select", "insert", "update", "delete"):
            self.assertIn(
                f"create policy private_hr_finance_objects_{operation}",
                sql,
            )
        self.assertGreaterEqual(sql.count("on storage.objects"), 4)
        self.assertIn("employee-certificates", sql)
        self.assertIn("financial-calendar-attachments", sql)
        self.assertNotIn("drop table", sql)
        self.assertNotIn("truncate ", sql)


if __name__ == "__main__":
    unittest.main()
