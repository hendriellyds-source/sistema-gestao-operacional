from __future__ import annotations

import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.access_control import FLEET_PREFIXES, path_is_allowed
from app.auth import ROLE_AREAS, ROLE_TO_LEGACY
from app.main import app
from app.system_state import filter_state_for_user
from tests.fakes import MemorySupabase


def user(role_code: str) -> dict[str, object]:
    legacy = {
        "admin": "adm",
        "director": "diretor",
        "financial": "financeiro",
        "helena": "helena",
        "aline": "aline",
    }.get(role_code, role_code)
    areas = {
        "admin": ["admin", "finance"],
        "director": ["admin", "finance"],
        "financial": ["finance", "admin"],
        "helena": ["projectist"],
        "aline": ["aline"],
    }.get(role_code, [])
    return {
        "role_code": role_code,
        "role": legacy,
        "areas": areas,
        "auth_user_id": "00000000-0000-0000-0000-000000000001",
        "profile_id": "00000000-0000-0000-0000-000000000002",
        "organization_id": "00000000-0000-0000-0000-000000000003",
        "display_name": role_code,
        "username": f"{role_code}@example.invalid",
        "can_dispatch_demands": False,
        "dispatch_targets": [],
    }


class AccessRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = user("admin")
        self.session_patch = patch(
            "app.main.user_from_session", side_effect=lambda _token: self.current_user
        )
        self.state_patch = patch(
            "app.main.get_system_state",
            return_value={"has_state": False, "revision": 0, "updated_at": None, "data": None},
        )
        self.session_patch.start()
        self.state_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.state_patch.stop()
        self.session_patch.stop()

    def test_admin_and_director_access_certificates(self) -> None:
        memory = MemorySupabase({"employee_certificates": []})
        with patch("app.employee_certificates.create_admin_supabase", return_value=memory):
            for role in ("admin", "director"):
                self.current_user = user(role)
                response = self.client.get("/api/employee-certificates")
                self.assertEqual(response.status_code, 200, (role, response.text))

    def test_canonical_profile_roles_and_existing_areas_are_preserved(self) -> None:
        self.assertEqual(
            set(ROLE_TO_LEGACY),
            {
                "admin",
                "director",
                "financial",
                "helena",
                "aline",
                "employee",
                "client",
                "pending",
            },
        )
        self.assertEqual(ROLE_AREAS["helena"], ["projectist"])
        self.assertEqual(ROLE_AREAS["aline"], ["aline"])

    def test_financial_accesses_hr_fleet_and_calendar_pages(self) -> None:
        self.current_user = user("financial")
        for path in (
            "/rh/employees",
            "/rh/exams",
            "/rh/vacations",
            "/rh/certificates",
            "/rh/clinics",
            "/fleet",
            "/finance/calendar",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.text[:200]))
        for prefix in FLEET_PREFIXES:
            self.assertTrue(path_is_allowed(self.current_user, prefix))

    def test_financial_cannot_access_user_management(self) -> None:
        self.current_user = user("financial")
        self.assertEqual(self.client.get("/admin/users").status_code, 403)

    def test_helena_and_aline_cannot_access_finance(self) -> None:
        for role in ("helena", "aline"):
            self.current_user = user(role)
            self.assertEqual(self.client.get("/finance/calendar").status_code, 403)
            self.assertEqual(self.client.get("/rh/employees").status_code, 403)
            self.assertEqual(self.client.get("/fleet").status_code, 403)
            self.assertEqual(
                self.client.get(
                    "/api/financial-calendar?date_from=2026-08-01&date_to=2026-08-31"
                ).status_code,
                403,
            )

    def test_filtered_state_does_not_expose_finance_to_helena_or_aline(self) -> None:
        state = {
            "has_state": True,
            "data": {
                "projectClients": [{"id": 1}],
                "alineProcesses": [{"id": 2}],
                "invoices": [{"id": 3, "total": 999}],
                "billingEntities": [{"id": 4}],
            },
        }
        for role in ("helena", "aline"):
            filtered = filter_state_for_user(state, user(role))["data"]
            self.assertNotIn("invoices", filtered)
            self.assertNotIn("billingEntities", filtered)

    def test_unauthorized_role_gets_403_on_page_and_api(self) -> None:
        self.current_user = user("client")
        self.assertEqual(self.client.get("/rh/employees").status_code, 403)
        self.assertEqual(self.client.get("/api/employee-certificates").status_code, 403)

    def test_valid_upload_invalid_file_and_unauthorized_download(self) -> None:
        memory = MemorySupabase({"employee_certificates": []})
        valid_pdf = base64.b64encode(b"%PDF-1.7\nvalid-test").decode()
        payload = {
            "employee_id": 10,
            "data": {
                "course_name": "NR-10",
                "expiration_date": "2027-08-03",
                "no_expiration": False,
            },
            "file": {
                "filename": "nr10.pdf",
                "mime_type": "application/pdf",
                "content_base64": valid_pdf,
            },
        }
        with (
            patch("app.employee_certificates.create_admin_supabase", return_value=memory),
            patch("app.employee_certificates._employee_exists", return_value=True),
            patch("app.employee_certificates.upload_private_object"),
            patch("app.employee_certificates.write_audit"),
            patch(
                "app.employee_certificates.signed_private_url",
                return_value="https://signed.invalid/certificate",
            ) as signed_url,
        ):
            self.current_user = user("financial")
            created = self.client.post("/api/employee-certificates", json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            certificate_id = created.json()["id"]
            invalid = {**payload, "file": {**payload["file"], "content_base64": base64.b64encode(b"not a pdf").decode()}}
            refused = self.client.post("/api/employee-certificates", json=invalid)
            self.assertEqual(refused.status_code, 422)
            download = self.client.get(
                f"/api/employee-certificates/{certificate_id}/download"
            )
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.json()["url"], "https://signed.invalid/certificate")
            self.assertGreater(download.json()["expires_in"], 0)
            signed_url.assert_called_once()
            self.current_user = user("helena")
            self.assertEqual(
                self.client.get(f"/api/employee-certificates/{certificate_id}/download").status_code,
                403,
            )
            self.assertEqual(
                self.client.delete(f"/api/employee-certificates/{certificate_id}").status_code,
                403,
            )

    def test_frontend_does_not_reference_server_credentials(self) -> None:
        forbidden = (
            "SUPABASE_SECRET_KEY",
            "SELSYN_API_KEY",
            "ADMIN_TOKEN",
            "supabase_secret_key",
            "selsyn_api_key",
            "admin_token",
        )
        for path in (
            "app/templates/index.html",
            "assets/rh_financial_v3.js",
        ):
            content = Path(path).read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, content, (path, marker))


if __name__ == "__main__":
    unittest.main()
