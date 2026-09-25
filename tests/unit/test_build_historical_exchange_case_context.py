from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_historical_exchange_case_context import build_context  # noqa: E402


class ExchangeCaseContextTests(unittest.TestCase):
    def test_matches_effective_day_and_excludes_oos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = root / "selection.json"
            selection.write_text(json.dumps({"cases": [
                {"role": "TRAIN", "selection_date": "2026-09-08", "outcome_date": "2026-09-09",
                 "candidate_codes_after_exclusion": 1, "excluded_codes": []},
                {"role": "VALIDATION", "selection_date": "2026-09-10", "outcome_date": "2026-09-11",
                 "candidate_codes_after_exclusion": 1, "excluded_codes": []},
                {"role": "OOS", "selection_date": "2026-09-14", "outcome_date": "2026-09-15",
                 "candidate_codes_after_exclusion": 1, "excluded_codes": []},
            ]}), encoding="utf-8")
            candidates = root / "candidates.sqlite3"
            with closing(sqlite3.connect(candidates)) as db:
                db.executescript("""
                    CREATE TABLE stocks(code TEXT PRIMARY KEY,market_code TEXT);
                    CREATE TABLE candidate_days(code TEXT,dt TEXT);
                    INSERT INTO stocks VALUES('008290','10');
                    INSERT INTO candidate_days VALUES('008290','2026-09-08');
                    INSERT INTO candidate_days VALUES('008290','2026-09-10');
                    INSERT INTO candidate_days VALUES('008290','2026-09-14');
                """)
            context = root / "context.sqlite3"
            with closing(sqlite3.connect(context)) as db:
                db.executescript("""
                    CREATE TABLE historical_exchange_effective_events(
                        code TEXT,receipt_no TEXT,receipt_date TEXT,kind TEXT,
                        effective_date TEXT,effective_time TEXT,precision TEXT,
                        source_label TEXT,raw_sha256 TEXT);
                    CREATE TABLE historical_exchange_effective_daily_checks(
                        code TEXT,receipt_no TEXT,kind TEXT,effective_date TEXT,
                        effective_time TEXT,receipt_to_effective_days INTEGER,
                        daily_row_state TEXT,daily_volume INTEGER,
                        last_positive_before TEXT,first_positive_on_or_after TEXT,
                        review_status TEXT);
                    INSERT INTO historical_exchange_effective_events VALUES(
                        '008290','20260908900687','2026-09-08','halt',
                        '2026-09-09','','date','정지일시','hash');
                    INSERT INTO historical_exchange_effective_daily_checks VALUES(
                        '008290','20260908900687','halt','2026-09-09','',1,
                        'zero',0,'2026-09-08','','no_detected_contradiction');
                    INSERT INTO historical_exchange_effective_events VALUES(
                        '008290','later','2026-09-14','delist',
                        '2026-09-15','','date','상장폐지일','hash2');
                    INSERT INTO historical_exchange_effective_daily_checks VALUES(
                        '008290','later','delist','2026-09-15','',1,
                        'absent',NULL,'','','no_detected_contradiction');
                """)
            result = build_context(selection, candidates, context)
            self.assertEqual(result["development_case_count"], 2)
            self.assertEqual(result["event_kind_counts"], {"halt": 1})
            event = result["cases"][0]["exchange_events"][0]
            self.assertEqual((event["receipt_date"], event["effective_date"]),
                             ("2026-09-08", "2026-09-09"))
            self.assertFalse(result["policy"]["oos_included"])
            self.assertFalse(result["policy"]["strategy_signal_or_tradability_gate"])


if __name__ == "__main__":
    unittest.main()
