from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import settings
from app.system_state import (
    get_system_state,
    list_system_state_versions,
    restore_system_state_revision,
    save_system_state,
)


class SystemStateRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temporary_directory.name) / "state.db")
        self.path_patch = patch.object(settings, "system_state_db_path", self.database_path)
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary_directory.cleanup()

    def test_previous_revision_can_be_restored_without_losing_current_backup(self) -> None:
        original = {"employees": [{"id": 1}, {"id": 2}], "companies": [{"id": 8}]}
        reset = {"employees": [{"id": 99}], "companies": []}
        save_system_state(original, source="sistema-web", updated_by="director")
        save_system_state(reset, source="sistema-web", updated_by="director")

        versions = list_system_state_versions()
        self.assertEqual(versions["current"]["revision"], 2)
        self.assertEqual(versions["current"]["summary"]["employees"], 1)
        self.assertEqual(versions["history"][0]["revision"], 1)
        self.assertEqual(versions["history"][0]["summary"]["employees"], 2)

        restored = restore_system_state_revision(
            revision=1,
            expected_current_revision=2,
            updated_by="director",
        )

        self.assertEqual(restored["revision"], 3)
        self.assertEqual(restored["restored_from_revision"], 1)
        self.assertEqual(get_system_state()["data"], original)
        history_revisions = [item["revision"] for item in list_system_state_versions()["history"]]
        self.assertIn(2, history_revisions)

    def test_restore_stops_if_current_revision_changed(self) -> None:
        save_system_state({"employees": [{"id": 1}]}, source="web", updated_by="one")
        save_system_state({"employees": [{"id": 2}]}, source="web", updated_by="two")

        with self.assertRaises(RuntimeError):
            restore_system_state_revision(
                revision=1,
                expected_current_revision=1,
                updated_by="director",
            )

        self.assertEqual(get_system_state()["revision"], 2)


if __name__ == "__main__":
    unittest.main()
