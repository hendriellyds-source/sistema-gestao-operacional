from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings


def admin_user() -> dict[str, object]:
    return {
        "role_code": "admin",
        "role": "adm",
        "areas": ["admin", "finance"],
        "auth_user_id": "00000000-0000-0000-0000-000000000001",
        "profile_id": "00000000-0000-0000-0000-000000000002",
        "organization_id": "00000000-0000-0000-0000-000000000003",
        "display_name": "Administrador",
        "username": "admin@example.invalid",
        "email": "admin@example.invalid",
        "avatar_url": None,
        "can_dispatch_demands": True,
        "dispatch_targets": [],
    }


class PublicSiteRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_institutional_and_legal_pages_are_public_without_a_session(self) -> None:
        with patch("app.main.user_from_session", return_value=None):
            home = self.client.get("/")
            privacy = self.client.get("/privacy")
            terms = self.client.get("/terms")
            data_deletion = self.client.get("/data-deletion")

        self.assertEqual(home.status_code, 200)
        self.assertIn("Sistema de Gestão Operacional", home.text)
        self.assertIn("Case técnico de portfólio", home.text)
        self.assertIn('href="/login"', home.text)
        self.assertIn('href="/docs"', home.text)
        self.assertNotIn("CURRENT_USER", home.text)

        for response in (privacy, terms, data_deletion):
            self.assertEqual(response.status_code, 200)
            self.assertIn("dados fictícios", response.text.lower())

    def test_home_does_not_publish_real_company_contacts(self) -> None:
        with patch("app.main.user_from_session", return_value=None):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("wa.me/", response.text)
        self.assertNotIn("instagram.com", response.text)
        self.assertIn("Nenhum dado real", response.text)

    def test_home_presents_portfolio_stack_and_demo_entrypoint(self) -> None:
        with patch("app.main.user_from_session", return_value=None):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("FastAPI", response.text)
        self.assertIn("SQLite", response.text)
        self.assertIn("RBAC", response.text)
        self.assertIn("Abrir demonstração", response.text)

    def test_login_page_and_login_endpoint_remain_functional(self) -> None:
        with patch("app.main.user_from_session", return_value=None):
            page = self.client.get("/login")

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="loginForm"', page.text)
        self.assertIn('location.href="/workspace"', page.text)

        user = admin_user()
        with (
            patch("app.auth.authenticate", return_value=user),
            patch("app.auth.create_session", return_value=("test-session", "2026-08-11T12:00:00+00:00")),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "admin@example.invalid", "password": "valid-password"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertIn("portfolio_session=test-session", response.headers["set-cookie"])

    def test_authenticated_login_and_workspace_use_the_new_internal_route(self) -> None:
        user = admin_user()
        with patch("app.main.user_from_session", return_value=user):
            redirected = self.client.get("/login", follow_redirects=False)

        self.assertEqual(redirected.status_code, 303)
        self.assertEqual(redirected.headers["location"], "/workspace")

        empty_state = {
            "has_state": False,
            "revision": 0,
            "updated_at": None,
            "data": None,
        }
        with (
            patch("app.main.user_from_session", return_value=user),
            patch("app.main.get_system_state", return_value=empty_state),
        ):
            workspace = self.client.get("/workspace")

        self.assertEqual(workspace.status_code, 200, workspace.text[:300])
        self.assertIn("CURRENT_USER", workspace.text)
        self.assertIn("Administrador", workspace.text)

    def test_workspace_still_enforces_role_authorization(self) -> None:
        unauthorized_user = {**admin_user(), "role_code": "client", "role": "client", "areas": []}
        with patch("app.main.user_from_session", return_value=unauthorized_user):
            response = self.client.get("/workspace", follow_redirects=False)

        self.assertEqual(response.status_code, 403)
        self.assertIn("workspace", response.json()["detail"])

    def test_internal_pages_and_apis_stay_protected_without_a_session(self) -> None:
        protected_pages = (
            "/workspace",
            "/rh/employees",
            "/finance/calendar",
            "/fleet",
            "/admin/users",
            "/docs",
        )
        protected_apis = (
            "/api/auth/me",
            "/api/vehicles",
            "/api/system-state",
            "/api/aline/config",
            "/api/financial-calendar",
            "/api/admin/profiles",
        )

        with patch("app.main.user_from_session", return_value=None):
            for path in protected_pages:
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303, (path, response.text))
                self.assertEqual(response.headers["location"], "/login")

            for path in protected_apis:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401, (path, response.text))
                self.assertEqual(response.json()["detail"], "Faça login para continuar.")


if __name__ == "__main__":
    unittest.main()
