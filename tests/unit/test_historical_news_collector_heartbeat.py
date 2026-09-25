from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.probe_historical_backfill import _write_news_heartbeat  # noqa: E402


class HistoricalNewsHeartbeatTests(unittest.TestCase):
    def test_concurrent_writes_leave_valid_heartbeat_and_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news-job-heartbeat.json"
            job = SimpleNamespace(code="005930", target_date="2026-09-23", query_text="삼성전자")
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(
                    lambda page: _write_news_heartbeat(path, job, "search_page", page=page),
                    range(80),
                ))
            heartbeat = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["code"], "005930")
            self.assertEqual(heartbeat["phase"], "search_page")
            self.assertFalse(list(path.parent.glob(".news-job-heartbeat.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
