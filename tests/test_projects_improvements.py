from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import projects_projects
from app.settings import settings


class ProjetosImprovementsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.previous_db = settings.projects_db_path
        self.previous_documents = settings.projects_documents_root_path
        self.previous_imports = settings.projects_imports_root_path
        settings.projects_db_path = str(Path(self.directory.name) / "projects.db")
        settings.projects_documents_root_path = str(Path(self.directory.name) / "documents")
        settings.projects_imports_root_path = str(Path(self.directory.name) / "imports")
        projects_projects.init_projects_projects()

    def tearDown(self) -> None:
        settings.projects_db_path = self.previous_db
        settings.projects_documents_root_path = self.previous_documents
        settings.projects_imports_root_path = self.previous_imports
        gc.collect()
        self.directory.cleanup()

    def _create_process(self) -> dict:
        client = projects_projects.create_client(projects_projects.DataPayload(data={
            "person_type": "PF", "name": "Cliente Pesquisa", "city": "Joinville",
        }), x_operational_profile="Projetos")
        return projects_projects.create_process(projects_projects.DataPayload(data={
            "client_id": client["id"], "title": "Regularização", "process_type": "Prefeitura",
            "service_type": "Retificação de área", "city": "Joinville",
            "entry_code": "INICIAL-123", "secondary_code": "SEGUNDO-456",
            "third_code": "TERCEIRO-789",
            "document_submission_due": "2026-08-10", "next_deadline": "2026-08-20",
            "status_manual": "OK",
        }), x_operational_profile="Projetos")

    def test_registry_seed_is_expanded_and_idempotent(self) -> None:
        projects_projects.init_projects_projects()
        with sqlite3.connect(settings.projects_db_path) as connection:
            cities = {row[0] for row in connection.execute("SELECT city FROM projects_registries")}
            duplicates = connection.execute(
                "SELECT LOWER(TRIM(name)), COUNT(*) FROM projects_registries GROUP BY LOWER(TRIM(name)) HAVING COUNT(*) > 1"
            ).fetchall()
        self.assertTrue({"Cidade Demo", "Joinville", "Blumenau", "Chapecó", "Itajaí"}.issubset(cities))
        self.assertEqual(duplicates, [])

    def test_search_finds_initial_secondary_code_and_client(self) -> None:
        self._create_process()
        for query in ("INICIAL-123", "SEGUNDO-456", "TERCEIRO-789", "Cliente Pesquisa"):
            rows = projects_projects.list_processes(
                archived=False, search=query, city="", service_type="",
                process_type="", status="", registry_id=None,
            )
            self.assertEqual(len(rows), 1, query)
        self.assertEqual(
            [item["date"] for item in rows[0]["deadline_dates"]],
            ["2026-08-10", "2026-08-20"],
        )

    def test_calendar_is_computed_and_does_not_duplicate_events(self) -> None:
        self._create_process()
        with sqlite3.connect(settings.projects_db_path) as connection:
            before = connection.execute("SELECT COUNT(*) FROM projects_events").fetchone()[0]
        with patch.object(projects_projects, "_today", return_value=date(2026, 8, 1)):
            first = projects_projects.projects_calendar(
                date_from="2026-08-01", date_to="2026-08-31", city="Joinville"
            )
            second = projects_projects.projects_calendar(
                date_from="2026-08-01", date_to="2026-08-31", city="Joinville"
            )
        self.assertEqual(len(first["events"]), 2)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(len({event["id"] for event in first["events"]}), 2)
        with sqlite3.connect(settings.projects_db_path) as connection:
            after = connection.execute("SELECT COUNT(*) FROM projects_events").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
