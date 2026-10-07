from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_historical_monthly_gap_raw import audit  # noqa: E402


class MonthlyGapRawTests(unittest.TestCase):
    def test_archived_gap_equals_saved_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gaps = root / "gaps.json"
            gaps.write_text(json.dumps({"gaps": [{
                "code": "123456", "outcome_date": "2025-12-02",
                "from_bar": "2025-12-02T09:01:00+09:00",
                "to_bar": "2025-12-02T09:13:00+09:00",
                "gap_seconds": 720,
            }]}), encoding="utf-8")
            raw_dir = root / "historical_collection" / "daishin" / "123456" / "attempt1"
            raw_dir.mkdir(parents=True)
            raw = raw_dir / "1m.ndjson"
            raw.write_text(json.dumps({"record_type": "page", "interval_seconds": 60,
                                       "bars": [
                                           {"bar_time": "2025-12-02T09:01:00+09:00", "volume": 40},
                                           {"bar_time": "2025-12-02T09:13:00+09:00", "volume": 60},
                                       ]}) + "\n", encoding="utf-8")
            market = root / "market.sqlite3"
            with closing(sqlite3.connect(market)) as db:
                db.executescript("""
                    CREATE TABLE market_backfill_jobs(code TEXT,state TEXT,raw_directory TEXT);
                    CREATE TABLE market_bars(provider TEXT,code TEXT,venue TEXT,
                        session_scope TEXT,interval_seconds INTEGER,adjustment_mode TEXT,
                        bar_time TEXT,volume INTEGER);
                    INSERT INTO market_bars VALUES
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:01:00+09:00',40),
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:13:00+09:00',60);
                """)
                db.execute("INSERT INTO market_backfill_jobs VALUES(?,?,?)",
                           ("123456", "complete", str(raw_dir)))
                db.commit()

            report = audit(gaps, market)

            self.assertEqual(1, report["counts"]["stock_days_raw_equals_saved"])
            self.assertEqual(0, report["counts"]["gaps_with_raw_bar_inside"])
            self.assertEqual(0, report["gaps"][0]["raw_bars_strictly_inside_gap"])

            raw.write_text(json.dumps({"record_type": "page", "interval_seconds": 60,
                                       "bars": [
                                           {"bar_time": "2025-12-02T09:01:00+09:00", "volume": 40},
                                           {"bar_time": "2025-12-02T09:07:00+09:00", "volume": 5},
                                           {"bar_time": "2025-12-02T09:13:00+09:00", "volume": 60},
                                       ]}) + "\n", encoding="utf-8")
            omitted = audit(gaps, market)
            self.assertEqual(0, omitted["counts"]["stock_days_raw_equals_saved"])
            self.assertEqual(1, omitted["counts"]["gaps_with_raw_bar_inside"])
            self.assertEqual(1, omitted["stock_days"][0]["raw_bars_missing_in_saved"])


if __name__ == "__main__":
    unittest.main()
