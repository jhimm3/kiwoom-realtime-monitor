from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_historical_monthly_gap_causes import audit  # noqa: E402


class MonthlyGapEvidenceTests(unittest.TestCase):
    def test_compares_raw_minute_volume_and_independent_minute_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gaps = root / "gaps.json"
            gaps.write_text(json.dumps({"gaps": [{
                "code": "123456", "outcome_date": "2025-12-02",
                "selection_date": "2025-12-01",
                "from_bar": "2025-12-02T09:01:00+09:00",
                "to_bar": "2025-12-02T09:13:00+09:00",
                "gap_seconds": 720, "overlapping_vi_count": 0,
                "vi_event_ids": [],
            }]}), encoding="utf-8")
            market = root / "market.sqlite3"
            with closing(sqlite3.connect(market)) as db:
                db.executescript("""
                    CREATE TABLE market_bars(provider TEXT,code TEXT,venue TEXT,
                        session_scope TEXT,interval_seconds INTEGER,adjustment_mode TEXT,
                        bar_time TEXT,volume INTEGER);
                    INSERT INTO market_bars VALUES
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:01:00+09:00',40),
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:13:00+09:00',60);
                """)
            context = root / "context.sqlite3"
            with closing(sqlite3.connect(context)) as db:
                db.executescript("""
                    CREATE TABLE historical_stock_fundamentals(provider TEXT,code TEXT,
                        trade_date TEXT,volume INTEGER);
                    CREATE TABLE historical_exchange_effective_events(code TEXT,
                        effective_date TEXT,receipt_no TEXT,receipt_date TEXT,
                        kind TEXT,effective_time TEXT,precision TEXT);
                    INSERT INTO historical_stock_fundamentals VALUES
                        ('daishin_creon','123456','2025-12-02',100);
                """)
            kiwoom = root / "kiwoom.sqlite3"
            with closing(sqlite3.connect(kiwoom)) as db:
                db.executescript("""
                    CREATE TABLE daily_bars(code TEXT,dt TEXT,volume INTEGER);
                    CREATE TABLE minute_bars(code TEXT,dt TEXT,ts TEXT);
                    INSERT INTO daily_bars VALUES('123456','2025-12-02',100);
                    INSERT INTO minute_bars VALUES
                        ('123456','2025-12-02','2025-12-02 09:01:00'),
                        ('123456','2025-12-02','2025-12-02 09:13:00');
                """)

            report = audit(gaps, market, context, kiwoom)

            self.assertEqual(1, report["counts"]["stock_days_exact_creon_volume"])
            self.assertEqual(1, report["counts"]["gaps_with_kiwoom_minute_reference"])
            self.assertEqual(0, report["counts"]["gaps_with_kiwoom_minute_inside"])
            self.assertEqual(0, report["gaps"][0]["kiwoom_minutes_strictly_inside_gap"])


if __name__ == "__main__":
    unittest.main()
