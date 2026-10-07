from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import collect_candidate_exchange_effective_dates as collector  # noqa: E402
import reconcile_candidate_exchange_effective_dates as reconciliation  # noqa: E402


class CandidateExchangeEffectiveDateTests(unittest.TestCase):
    def test_selects_exchange_suspensions_but_not_issuer_delisting_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = sqlite3.connect(Path(temporary) / "context.sqlite3")
            db.execute("CREATE TABLE historical_candidate_exchange_disclosures "
                       "(code TEXT, receipt_no TEXT, receipt_date TEXT, report_name TEXT, filer_name TEXT)")
            db.executemany("INSERT INTO historical_candidate_exchange_disclosures VALUES(?,?,?,?,?)", [
                ("000001", "1", "2024-01-03", "주권매매거래정지", "코스닥시장본부"),
                ("000001", "2", "2024-01-03", "상장폐지결정", "발행회사"),
                ("000001", "3", "2024-01-03", "기타시장안내(상장폐지 일정)", "코스닥시장본부"),
            ])
            self.assertEqual({"1", "3"}, set(collector.selected_filings(db)))
            db.close()

    def test_daily_rows_cross_check_without_changing_official_event_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "context.sqlite3"
            candidates = Path(temporary) / "candidates.sqlite3"
            with closing(sqlite3.connect(context)) as db:
                collector.initialize(db)
                db.executemany(
                    "INSERT INTO historical_exchange_effective_events VALUES(?,?,?,?,?,?,?,?,?,?)", [
                        ("000001", "1", "2024-01-02", "halt", "2024-01-03", "09:30",
                         "minute", "정지일시", "hash", "2026-09-24T00:00:00+00:00"),
                        ("000001", "2", "2024-01-05", "resume", "2024-01-08", "",
                         "date", "해제일시", "hash", "2026-09-24T00:00:00+00:00"),
                        ("000001", "3", "2024-01-09", "delist", "2024-01-10", "",
                         "date", "상장폐지일", "hash", "2026-09-24T00:00:00+00:00"),
                    ],
                )
                db.commit()
            with closing(sqlite3.connect(candidates)) as db:
                db.execute("CREATE TABLE daily_bars(code TEXT,dt TEXT,volume INTEGER)")
                db.execute("INSERT INTO daily_bars VALUES('000001','2024-01-03',100)")
                db.execute("INSERT INTO daily_bars VALUES('000001','2024-01-08',200)")
                db.execute("INSERT INTO daily_bars VALUES('000001','2024-01-11',300)")
                db.commit()

            report = reconciliation.reconcile(context, candidates)

            self.assertEqual(3, report["event_count"])
            self.assertEqual(3, report["receipt_date_differs_from_effective_date"])
            self.assertEqual({"halt:positive": 1, "resume:positive": 1, "delist:absent": 1},
                             report["daily_states"])
            self.assertEqual(1, report["review_status_counts"]["delist_with_later_trade_requires_review"])
            with closing(sqlite3.connect(context)) as db:
                self.assertEqual(("2024-01-03", 100), db.execute(
                    "SELECT effective_date,daily_volume FROM "
                    "historical_exchange_effective_daily_checks WHERE kind='halt'"
                ).fetchone())


if __name__ == "__main__":
    unittest.main()
