"""Keep the source inventory honest as Kiwoom routes change."""

from __future__ import annotations

import runpy
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class KiwoomStorageAuditTests(unittest.TestCase):
    def test_inventory_distinguishes_calls_from_declared_routes(self) -> None:
        inventory = runpy.run_path(str(ROOT / "scripts" / "audit_kiwoom_storage_paths.py"))["inventory"](ROOT)
        called = {entry["api_id"] for entry in inventory["literal_rest_calls"]}
        declared = set(inventory["declared_broker_api_ids_not_proof_of_active_use"])
        self.assertIn("ka00198", called)
        self.assertIn("ka10080", called)
        self.assertIn("ka10016", declared)
        self.assertNotIn("ka10016", called)
        self.assertTrue(inventory["dynamic_request_calls_to_review"])

    def test_inventory_covers_realtime_registration_and_write_boundaries(self) -> None:
        inventory = runpy.run_path(str(ROOT / "scripts" / "audit_kiwoom_storage_paths.py"))["inventory"](ROOT)
        self.assertGreaterEqual(set(inventory["registered_realtime_types_from_literal_lists"]),
                                {"0B", "0w", "0g", "0s", "00", "04", "0J", "0U", "1h"})
        writers = {entry["method"]: entry for entry in inventory["postgres_methods_with_literal_write_sql"]}
        self.assertIn("central_minute_bars", writers["save_minute_bars"]["tables_in_literal_sql"])
        self.assertIn("central_execution_events", writers["append_execution_event"]["tables_in_literal_sql"])
        self.assertIn("journal_execution_event_projections", inventory["pc_sqlite_declared_tables"])


if __name__ == "__main__":
    unittest.main()
