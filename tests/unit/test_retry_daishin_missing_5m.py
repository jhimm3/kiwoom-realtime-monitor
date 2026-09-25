from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import retry_daishin_missing_5m as recovery
from scripts.run_daishin_candidate_collection import _initialize_jobs
from kiwoom_monitor.infrastructure.historical_backfill import store_daishin_probe_payload


class DaishinMissingFiveMinuteRetryTests(unittest.TestCase):
    def test_imports_only_bars_older_than_one_minute_and_preserves_job_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference, database = root / "reference.sqlite3", root / "history.sqlite3"
            with closing(sqlite3.connect(reference)) as connection, connection:
                connection.execute("CREATE TABLE candidate_days(code TEXT)")
                connection.execute("INSERT INTO candidate_days VALUES('005930')")
            _initialize_jobs(reference, database)
            store_daishin_probe_payload(database, {
                "provider": "daishin_creon", "code": "005930", "venue": "K",
                "session_scope": "regular", "interval_seconds": 60,
                "adjustment_mode": "raw", "observed_at": "2026-09-23T00:00:00+00:00",
                "bars": [self._bar("2024-08-29T09:01:00+09:00")],
            })
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "UPDATE market_backfill_jobs SET state='complete',one_minute_bars=1,"
                    "one_minute_oldest='2024-08-29T09:01:00+09:00' WHERE code='005930'"
                )

            def write_raw(code: str, interval: int, output: Path, *, to_date: str = "") -> None:
                self.assertEqual((code, interval, to_date), ("005930", 5, "20240828"))
                values = [
                    {"record_type": "page", "provider": "daishin_creon", "code": code,
                     "interval_seconds": 300, "venue": "K", "session_scope": "regular",
                     "adjustment_mode": "raw", "observed_at": "2026-09-23T00:00:00+00:00",
                     "bars": [self._bar("2024-08-29T09:05:00+09:00"),
                              self._bar("2024-08-28T09:05:00+09:00")]},
                    {"record_type": "summary", "code": code, "interval_seconds": 300,
                     "total_bars": 2, "provider_has_more": False},
                ]
                output.write_text("\n".join(json.dumps(v) for v in values) + "\n", encoding="utf-8")

            with patch.object(recovery, "_run_backfill", side_effect=write_raw):
                result = recovery.retry("005930", database, root / "raw")

            self.assertEqual(result["status"], "imported")
            self.assertEqual(result["selected_bars"], 1)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT state,one_minute_bars,five_minute_bars FROM market_backfill_jobs "
                    "WHERE code='005930'"
                ).fetchone(), ("complete", 1, 1))

    @staticmethod
    def _bar(at: str) -> dict[str, object]:
        return {"bar_time": at, "raw_date": int(at[:10].replace("-", "")),
                "raw_time": 905 if at.endswith("09:05:00+09:00") else 901,
                "open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 10, "trading_value": 1000}


if __name__ == "__main__":
    unittest.main()
