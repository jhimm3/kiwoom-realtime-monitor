from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.local_storage_diagnostics import inspect_local_storage


class LocalStorageDiagnosticsTests(unittest.TestCase):
    def test_groups_database_sidecars_and_research_without_opening_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "monitor.sqlite3": 10,
                "monitor.sqlite3-wal": 11,
                "news.sqlite3": 20,
                "journal.sqlite3-shm": 30,
                "research/run/observations.jsonl": 40,
                "logs/app.log": 50,
                "api.env": 60,
            }
            for name, size in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * size)

            result = inspect_local_storage(root)

            self.assertTrue(result["complete"])
            self.assertEqual(sum(files.values()), result["total_bytes"])
            categories = {item["category"]: item for item in result["categories"]}
            self.assertEqual(21, categories["market"]["bytes"])
            self.assertEqual(20, categories["news"]["bytes"])
            self.assertEqual(30, categories["journal"]["bytes"])
            self.assertEqual(40, categories["research"]["bytes"])
            self.assertEqual(50, categories["logs"]["bytes"])
            self.assertEqual(60, categories["other"]["bytes"])
            self.assertIn("분봉: 30일", result["retention"][0])


if __name__ == "__main__":
    unittest.main()
