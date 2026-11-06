"""Historical metadata must survive the existing central JSON persistence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import settings
from app.system_state import save_system_state, get_system_state, filter_state_for_user


class HistoricalScheduleStateTests(unittest.TestCase):
    def test_history_and_partial_rows_round_trip_in_existing_state(self):
        entries = [
            dict(id=1, date="2026-08-04", teamId=1, companyId=1, workId=1,
                 type="Obra", dailyType="Inteira", value=1500, status="Confirmado",
                 historical=True, historicalRowKey="page-row-1", historicalPending=False,
                 reviewed=True, generateFinancial=False, originalText="MDM Brilhante"),
            dict(id=2, date="2026-08-04", teamId="", companyId="", workId="",
                 type="Obra", dailyType="Inteira", value=0, status="Confirmado",
                 historical=True, historicalRowKey="page-row-2", historicalPending=True,
                 reviewed=False, generateFinancial=False, originalText="Rock Pinheiro?"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(settings, "system_state_db_path", str(Path(folder) / "state.db")):
                save_system_state({"workScheduleEntries": entries}, source="sistema-web", updated_by="test")
                state = get_system_state()
                self.assertEqual(state["data"]["workScheduleEntries"], entries)
                for role in ("admin", "director", "financial", "projectist"):
                    self.assertEqual(filter_state_for_user(state, {"role_code": role})["data"]["workScheduleEntries"], entries)


if __name__ == "__main__":
    unittest.main()
