import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from scripts.seed_news_jobs_from_nas_rankings import ranking_jobs, seed_day


def snapshot(name="삼성전자", key="2026-10-09T09:00:00+09:00"):
    return {"snapshot_key": key, "payload": {"codes": ["005930"], "items": [
        {"stk_cd": "A005930", "stk_nm": name}]}}


class NasRankingNewsSeedTests(unittest.TestCase):
    def test_repeated_codes_dedupe_but_changed_name_keeps_search_and_provenance(self):
        rows = [snapshot(), snapshot(key="2026-10-09T10:00:00+09:00"),
                snapshot("새이름", "2026-10-09T11:00:00+09:00")]
        jobs = ranking_jobs("2026-10-09", rows)
        self.assertEqual(2, len(jobs))
        self.assertIn(("005930", "삼성전자", rows[0]["snapshot_key"]), jobs)

    def test_timezone_day_and_missing_names_and_cap_are_rejected(self):
        self.assertEqual(1, len(ranking_jobs("2026-10-09", [snapshot(key="2026-10-09T00:00:00+00:00")])))
        for rows in ([snapshot(key="2026-10-08T09:00:00+09:00")], [snapshot("")], [snapshot()] * 5000):
            with self.assertRaises(ValueError):
                ranking_jobs("2026-10-09", rows)

    def test_empty_day_is_not_invented(self):
        self.assertEqual([], ranking_jobs("2026-10-09", []))

    def test_replay_preserves_completed_work_and_adds_only_missing_day(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "news.sqlite3"
            jobs = ranking_jobs("2026-10-09", [snapshot()])
            self.assertEqual(1, seed_day(db, "2026-10-09", jobs))
            with closing(sqlite3.connect(db)) as c, c:
                c.execute("UPDATE news_backfill_jobs SET state='complete',attempts=2,pages_observed=5")
            self.assertEqual(0, seed_day(db, "2026-10-09", jobs))
            self.assertEqual(1, seed_day(db, "2026-10-10", jobs))
            with closing(sqlite3.connect(db)) as c:
                self.assertEqual(("complete", 2, 5, "nas.top20_membership"), c.execute(
                    "SELECT state,attempts,pages_observed,name_source FROM news_backfill_jobs "
                    "WHERE target_date='2026-10-09'").fetchone())
                self.assertEqual(("pending",), c.execute(
                    "SELECT state FROM news_backfill_jobs WHERE target_date='2026-10-10'").fetchone())


if __name__ == "__main__":
    unittest.main()
