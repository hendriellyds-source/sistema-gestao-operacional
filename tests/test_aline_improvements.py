from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import aline_projects
from app.settings import settings


class AlineImprovementsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.previous_db = settings.aline_db_path
        self.previous_documents = settings.aline_documents_root_path
        self.previous_imports = settings.aline_imports_root_path
        settings.aline_db_path = str(Path(self.directory.name) / "aline.db")
        settings.aline_documents_root_path = str(Path(self.directory.name) / "documents")
        settings.aline_imports_root_path = str(Path(self.directory.name) / "imports")
        aline_projects.init_aline_projects()

    def tearDown(self) -> None:
        settings.aline_db_path = self.previous_db
        settings.aline_documents_root_path = self.previous_documents
        settings.aline_imports_root_path = self.previous_imports
        gc.collect()
        self.directory.cleanup()

    def _create_process(self) -> dict:
        client = aline_projects.create_client(aline_projects.DataPayload(data={
            "person_type": "PF", "name": "Cliente Pesquisa", "city": "Joinville",
        }), x_portfolio_profile="Aline")
        return aline_projects.create_process(aline_projects.DataPayload(data={
            "client_id": client["id"], "title": "Regularização", "process_type": "Prefeitura",
            "service_type": "Retificação de área", "city": "Joinville",
            "entry_code": "INICIAL-123", "secondary_code": "SEGUNDO-456",
            "third_code": "TERCEIRO-789",
            "document_submission_due": "2026-08-10", "next_deadline": "2026-08-20",
            "status_manual": "OK",
        }), x_portfolio_profile="Aline")

    def test_registry_seed_is_expanded_and_idempotent(self) -> None:
        aline_projects.init_aline_projects()
        with sqlite3.connect(settings.aline_db_path) as connection:
            cities = {row[0] for row in connection.execute("SELECT city FROM aline_registries")}
            duplicates = connection.execute(
                "SELECT LOWER(TRIM(name)), COUNT(*) FROM aline_registries GROUP BY LOWER(TRIM(name)) HAVING COUNT(*) > 1"
            ).fetchall()
        self.assertTrue({"Biguaçu", "Joinville", "Blumenau", "Chapecó", "Itajaí"}.issubset(cities))
        self.assertEqual(duplicates, [])

    def test_search_finds_initial_secondary_code_and_client(self) -> None:
        self._create_process()
        for query in ("INICIAL-123", "SEGUNDO-456", "TERCEIRO-789", "Cliente Pesquisa"):
            rows = aline_projects.list_processes(
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
        with sqlite3.connect(settings.aline_db_path) as connection:
            before = connection.execute("SELECT COUNT(*) FROM aline_events").fetchone()[0]
        with patch.object(aline_projects, "_today", return_value=date(2026, 8, 1)):
            first = aline_projects.aline_calendar(
                date_from="2026-08-01", date_to="2026-08-31", city="Joinville"
            )
            second = aline_projects.aline_calendar(
                date_from="2026-08-01", date_to="2026-08-31", city="Joinville"
            )
        self.assertEqual(len(first["events"]), 2)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(len({event["id"] for event in first["events"]}), 2)
        with sqlite3.connect(settings.aline_db_path) as connection:
            after = connection.execute("SELECT COUNT(*) FROM aline_events").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
