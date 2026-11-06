from __future__ import annotations

import base64
import gc
import io
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import calendar_service, document_storage, history, operations
from app.bulk_work_import import parse_bulk_company_work_text, parse_brazilian_currency
from app.demo_cleanup import cleanup_system_state_data
from app.main import app, attach_trip_route_details
from app.settings import Settings, settings
from tests.fakes import MemorySupabase
from tests.test_access_and_routes import user


class BulkCompanyWorkParserTests(unittest.TestCase):
    def test_tabular_preview_preserves_order_values_and_optional_half_daily(self) -> None:
        preview = parse_bulk_company_work_text(
            "ALEX\tALEX UNIC\t R$ 1.450,00 \t R$ 900,00\n"
            "ALMEIDA JUNIOR\tAJ - SHOPPING BLUMENAU\t R$ 1.450,00\t\n"
            "BPM PRE MOLDADOS\tBPM VENCEDOR\t R$ 1.300,00\t R$ 900,00"
        )
        self.assertEqual(preview["valid_rows"], 3)
        self.assertEqual(preview["invalid_rows"], 0)
        self.assertEqual(
            [(row["company"], row["work"]) for row in preview["rows"]],
            [
                ("ALEX", "ALEX UNIC"),
                ("ALMEIDA JUNIOR", "AJ - SHOPPING BLUMENAU"),
                ("BPM PRE MOLDADOS", "BPM VENCEDOR"),
            ],
        )
        self.assertEqual(preview["rows"][0]["daily_rate"], 1450.0)
        self.assertEqual(preview["rows"][0]["half_daily_rate"], 900.0)
        self.assertEqual(preview["rows"][1]["half_daily_rate"], 0.0)
        self.assertTrue(preview["rows"][1]["warnings"])

    def test_duplicate_and_invalid_currency_are_rejected_before_submission(self) -> None:
        preview = parse_bulk_company_work_text(
            "Empresa\tObra\tR$ 100,00\tR$ 50,00\n"
            " empresa \t obra \tR$ 100,00\tR$ 50,00\n"
            "Empresa\tOutra\tR$ inválido\tR$ 50,00"
        )
        self.assertEqual(preview["valid_rows"], 1)
        self.assertEqual(preview["invalid_rows"], 2)
        self.assertIn("linha 1", preview["rows"][1]["errors"][0])
        self.assertIn("inválido", preview["rows"][2]["errors"][0])
        self.assertEqual(str(parse_brazilian_currency("R$ 1.450,00")), "1450.00")


class DemoCleanupTests(unittest.TestCase):
    def test_known_demo_records_and_relations_are_removed_without_touching_production(self) -> None:
        original = {
            "vehicles": [
                {"id": 99, "identifier": "ABC-1D23", "description": "Veículo teste"},
                {"id": 7, "identifier": "PROD123", "description": "Veículo produtivo"},
            ],
            "maintenances": [
                {"id": 1, "vehicleId": 99, "description": "Teste"},
                {"id": 2, "vehicleId": "uuid-producao", "description": "Troca de óleo"},
            ],
            "events": [
                {"id": 3, "vehicleId": 99, "title": "Revisão demo"},
                {"id": 4, "vehicleId": 7, "title": "IPVA produtivo"},
            ],
            "companies": [{"id": 1, "name": "Cliente real"}],
        }
        cleaned, stats = cleanup_system_state_data(original)
        self.assertEqual([item["identifier"] for item in cleaned["vehicles"]], ["PROD123"])
        self.assertEqual([item["id"] for item in cleaned["maintenances"]], [2])
        self.assertEqual([item["id"] for item in cleaned["events"]], [4])
        self.assertEqual(cleaned["companies"], original["companies"])
        self.assertEqual(stats["vehicles"], 1)
        self.assertEqual(len(original["vehicles"]), 2, "a limpeza não deve alterar o objeto de entrada")

    def test_demo_mode_can_be_enabled_by_environment_configuration(self) -> None:
        configured = Settings(demo_mode=True, _env_file=None)
        self.assertTrue(configured.demo_mode)

    def test_financial_calendar_hides_demo_vehicle_but_keeps_orphan_production_record(self) -> None:
        state = {
            "vehicles": [
                {"id": 99, "identifier": "ABC1D23"},
                {"id": 7, "identifier": "PROD123"},
            ],
            "maintenances": [
                {"id": 1, "vehicleId": 99, "scheduledDate": "2026-08-10", "type": "Revisão demo"},
                {"id": 2, "vehicleId": 7, "scheduledDate": "2026-08-10", "type": "Revisão produtiva"},
                {"id": 3, "vehicleId": 500, "scheduledDate": "2026-08-10", "type": "Registro produtivo legado"},
            ],
            "employees": [],
            "exams": [],
            "fines": [],
            "fleetDocuments": [],
            "invoices": [],
        }
        events = calendar_service._system_state_events(
            state,
            calendar_service.date(2026, 8, 1),
            calendar_service.date(2026, 8, 31),
        )
        maintenance_ids = {
            event["source_id"] for event in events if event["source_type"] == "maintenance"
        }
        self.assertEqual(maintenance_ids, {"2", "3"})

    def test_demo_vehicle_document_cleanup_preserves_production_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_db = settings.documents_db_path
            previous_root = settings.documents_root_path
            settings.documents_db_path = str(Path(directory) / "documents.db")
            settings.documents_root_path = str(Path(directory) / "documents")
            try:
                document_storage.init_document_storage()
                demo = document_storage.save_document(
                    owner_type="vehicle",
                    owner_key="ABC1D23",
                    owner_name="Veículo teste",
                    document_type="LICENCIAMENTO",
                    filename="demo.txt",
                    content=b"demo",
                )
                production = document_storage.save_document(
                    owner_type="vehicle",
                    owner_key="PROD123",
                    owner_name="Veículo produtivo",
                    document_type="LICENCIAMENTO",
                    filename="producao.txt",
                    content=b"producao",
                )
                result = document_storage.purge_demo_vehicle_documents()
                self.assertEqual(result, {"records": 1, "files": 1})
                self.assertFalse((Path(settings.documents_root_path) / demo["relative_path"]).exists())
                self.assertTrue((Path(settings.documents_root_path) / production["relative_path"]).is_file())
                remaining = document_storage.list_documents(owner_type="vehicle")
                self.assertEqual([item["owner_key"] for item in remaining], ["PROD123"])
            finally:
                settings.documents_db_path = previous_db
                settings.documents_root_path = previous_root
                gc.collect()

    def test_employee_documents_are_grouped_and_exported_by_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_db = settings.documents_db_path
            previous_root = settings.documents_root_path
            settings.documents_db_path = str(Path(directory) / "documents.db")
            settings.documents_root_path = str(Path(directory) / "documents")
            try:
                document_storage.save_document(
                    owner_type="employee",
                    owner_key="7",
                    owner_name="Funcionário",
                    category="DOCUMENTO",
                    document_type="RG",
                    filename="rg.txt",
                    content=b"documento",
                )
                exam = document_storage.save_document(
                    owner_type="employee",
                    owner_key="7",
                    owner_name="Funcionário",
                    category="EXAME",
                    document_type="ASO",
                    issue_date="2026-09-02",
                    filename="aso.txt",
                    content=b"exame",
                )
                exams = document_storage.list_documents(
                    owner_type="employee",
                    owner_key="7",
                    category="EXAME",
                )
                self.assertEqual([item["id"] for item in exams], [exam["id"]])
                self.assertEqual(exams[0]["issue_date"], "2026-09-02")

                with (
                    patch("app.main.user_from_session", return_value=user("admin")),
                    TestClient(app) as client,
                ):
                    response = client.get(
                        "/api/stored-documents/export",
                        params={
                            "owner_type": "employee",
                            "owner_key": "7",
                            "category": "EXAME",
                        },
                    )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "application/zip")
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    names = archive.namelist()
                    self.assertEqual(len(names), 1)
                    self.assertTrue(names[0].startswith("Exame/"))
                    self.assertEqual(archive.read(names[0]), b"exame")
            finally:
                settings.documents_db_path = previous_db
                settings.documents_root_path = previous_root
                gc.collect()


class PositionQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_history_db = settings.history_db_path
        self.temp_directory = tempfile.TemporaryDirectory()
        settings.history_db_path = str(Path(self.temp_directory.name) / "history.db")
        history.init_history_db()

    def tearDown(self) -> None:
        settings.history_db_path = self.previous_history_db
        gc.collect()
        self.temp_directory.cleanup()

    @staticmethod
    def point(identifier: str, external_id: int, timestamp: str, lat: float, lon: float) -> dict:
        return {
            "id": external_id,
            "time": timestamp,
            "createDate": timestamp,
            "rastreavel": identifier,
            "latitude": lat,
            "longitude": lon,
            "speed": 20,
            "ignition": True,
        }

    def test_single_local_day_uses_complete_half_open_24_hour_window(self) -> None:
        points = [
            self.point("PROD1", 1, "2026-08-03T02:59:59+00:00", -27.0000, -48.0000),
            self.point("PROD1", 2, "2026-08-03T03:00:00+00:00", -27.0001, -48.0001),
            self.point("PROD1", 3, "2026-08-03T12:00:00+00:00", -27.0002, -48.0002),
            self.point("PROD1", 4, "2026-08-04T02:59:59+00:00", -27.0003, -48.0003),
            self.point("PROD1", 5, "2026-08-04T03:00:00+00:00", -27.0004, -48.0004),
        ]
        history.upsert_positions(points)
        result = history.query_history("PROD1", "2026-08-03", "2026-08-03")
        self.assertEqual(result["total_points"], 3)
        self.assertEqual(result["points"][0]["external_id"], 2)
        self.assertEqual(result["points"][-1]["external_id"], 4)

    def test_invalid_duplicate_and_impossible_jump_points_are_discarded(self) -> None:
        cleaned, quality = history.filter_position_points(
            [
                {"position_time": "2026-08-03T10:00:00+00:00", "latitude": -27, "longitude": -48, "speed": 20},
                {"position_time": "2026-08-03T10:00:01+00:00", "latitude": -26, "longitude": -47, "speed": 20},
                {"position_time": "2026-08-03T10:00:02+00:00", "latitude": -27.0001, "longitude": -48.0001, "speed": 20},
                {"position_time": "2026-08-03T10:00:02+00:00", "latitude": -27.0001, "longitude": -48.0001, "speed": 20},
                {"position_time": "2026-08-03T10:00:03+00:00", "latitude": 0, "longitude": 0, "speed": 0},
                {"position_time": "2026-08-03T10:00:04+00:00", "latitude": -27.0002, "longitude": -48.0002, "speed": 300},
            ]
        )
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(quality, {"invalid": 1, "duplicates": 1, "outliers": 2})

    def test_all_vehicle_processing_keeps_each_route_independent(self) -> None:
        history.upsert_positions(
            [
                self.point("PROD1", 10, "2026-08-03T12:00:00+00:00", -27.0, -48.0),
                self.point("PROD1", 11, "2026-08-03T12:01:00+00:00", -27.001, -48.001),
                self.point("PROD2", 12, "2026-08-03T12:00:00+00:00", -26.0, -49.0),
                self.point("PROD2", 13, "2026-08-03T12:01:00+00:00", -26.001, -49.001),
            ]
        )
        counts = Counter(
            point["rastreavel"]
            for point in operations.get_all_positions("2026-08-03", "2026-08-03")
        )
        self.assertEqual(counts, {"PROD1": 2, "PROD2": 2})


class TripRouteDetailTests(unittest.TestCase):
    def test_selected_trip_receives_only_its_points_stop_and_resume(self) -> None:
        points = [
            {"position_time": f"2026-08-03T10:0{minute}:00+00:00", "latitude": -27, "longitude": -48}
            for minute in range(6)
        ]
        trips = [
            {
                "trip_id": "trip-1",
                "start_time": "2026-08-03T10:00:00+00:00",
                "end_time": "2026-08-03T10:02:00+00:00",
                "start_latitude": -27,
                "start_longitude": -48,
                "end_latitude": -27.01,
                "end_longitude": -48.01,
                "start_location": {"name": "Início"},
                "end_location": {"name": "Parada"},
            },
            {
                "trip_id": "trip-2",
                "start_time": "2026-08-03T10:04:00+00:00",
                "end_time": "2026-08-03T10:05:00+00:00",
                "start_latitude": -27.01,
                "start_longitude": -48.01,
                "end_latitude": -27.02,
                "end_longitude": -48.02,
                "start_location": {"name": "Retomada"},
                "end_location": {"name": "Final"},
            },
        ]
        stops = [
            {
                "start_time": "2026-08-03T10:02:00+00:00",
                "end_time": "2026-08-03T10:04:00+00:00",
                "latitude": -27.01,
                "longitude": -48.01,
                "location": {"name": "Parada"},
            }
        ]
        attach_trip_route_details(points, trips, stops)
        self.assertEqual(len(trips[0]["route_points"]), 3)
        self.assertEqual(trips[0]["stop_after"]["location"]["name"], "Parada")
        self.assertEqual(trips[0]["resume"]["time"], trips[1]["start_time"])
        self.assertIsNone(trips[1]["resume"])


class ProfilePhotoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = user("admin")
        self.profile_id = "10000000-0000-0000-0000-000000000001"
        self.memory = MemorySupabase(
            {
                "profiles": [
                    {
                        "id": self.profile_id,
                        "auth_user_id": "10000000-0000-0000-0000-000000000002",
                        "organization_id": self.current_user["organization_id"],
                        "role_code": "financial",
                        "full_name": "Usuária Financeira",
                        "email": "financeiro@example.invalid",
                        "status": "active",
                        "avatar_bucket": None,
                        "avatar_path": None,
                        "avatar_mime_type": None,
                        "avatar_updated_at": None,
                    }
                ]
            }
        )
        self.patches = [
            patch("app.main.user_from_session", side_effect=lambda _token: self.current_user),
            patch("app.profile_photos.create_admin_supabase", return_value=self.memory),
            patch("app.profile_photos.upload_private_object"),
            patch("app.profile_photos.delete_private_object"),
            patch("app.profile_photos.write_audit"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()

    def test_admin_can_list_upload_and_remove_profile_photo(self) -> None:
        listed = self.client.get("/api/admin/profiles")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertFalse(listed.json()[0]["has_photo"])
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nprofile-photo").decode()
        uploaded = self.client.put(
            f"/api/admin/profiles/{self.profile_id}/photo",
            json={"filename": "perfil.png", "mime_type": "image/png", "content_base64": png},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertTrue(uploaded.json()["has_photo"])
        self.assertTrue(uploaded.json()["photo_url"].startswith("/api/profile-photos/"))
        removed = self.client.delete(f"/api/admin/profiles/{self.profile_id}/photo")
        self.assertEqual(removed.status_code, 200, removed.text)

    def test_financial_role_cannot_manage_profile_photos(self) -> None:
        self.current_user = user("financial")
        self.assertEqual(self.client.get("/api/admin/profiles").status_code, 403)

    def test_bulk_preview_endpoint_is_admin_only(self) -> None:
        response = self.client.post(
            "/api/works/bulk/preview",
            json={"text": "Empresa\tObra\tR$ 100,00\tR$ 50,00"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["valid_rows"], 1)
        self.current_user = user("financial")
        self.assertEqual(
            self.client.post(
                "/api/works/bulk/preview",
                json={"text": "Empresa\tObra\tR$ 100,00\tR$ 50,00"},
            ).status_code,
            403,
        )


class OperationalUiAndMigrationTests(unittest.TestCase):
    def test_operational_migration_and_ui_hooks_are_present(self) -> None:
        sql = Path("migrations/20260803_operational_fixes.sql").read_text(encoding="utf-8").lower()
        self.assertIn("add column if not exists avatar_path", sql)
        self.assertIn("user-profile-photos", sql)
        self.assertIn("deleted_at = coalesce(deleted_at, now())", sql)
        self.assertIn("abc1d23", sql)
        self.assertNotIn("drop table", sql)
        self.assertNotIn("truncate ", sql)

        html = Path("app/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("/assets/brand.svg", html)
        self.assertIn('id="fleetHistoryDate"', html)
        self.assertNotIn('id="fleetDateFrom"', html)
        self.assertIn("selectFleetTripRoute", html)
        self.assertIn('"funcionarios","exames","ferias","clinicas","veiculos"', html)
        self.assertIn('rhName.textContent="RH"', html)
        self.assertIn('financeName.textContent="Financeiro"', html)
        self.assertTrue(Path("assets/brand.svg").is_file())


if __name__ == "__main__":
    unittest.main()
