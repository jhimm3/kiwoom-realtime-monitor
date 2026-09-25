from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import historical_collection_monitor as monitor
from kiwoom_monitor.infrastructure.naver_stock_market_news import initialize_database


class HistoricalCollectionMonitorTests(unittest.TestCase):
    def test_excluded_products_do_not_inflate_collection_denominator(self) -> None:
        text, percent = monitor._counts_text({"complete": 2, "failed": 1, "excluded": 2})
        self.assertIn("완료 2 / 3", text)
        self.assertIn("excluded 2", text)
        self.assertAlmostEqual(percent, 200 / 3)

    def test_stale_running_state_does_not_treat_recent_database_row_as_live_process(self) -> None:
        with (
            patch.object(monitor, "_process_alive", return_value=False),
            patch.object(monitor, "_age_seconds", return_value=1),
        ):
            health = monitor._collector_health(
                {"status": "running", "pid": 1234}, {}, {"updated_at": "recent"},
            )
        self.assertEqual(health[0], "응답 없음")

    def test_stale_heartbeat_with_live_collector_is_labeled_as_delayed(self) -> None:
        with (
            patch.object(monitor, "_process_alive", return_value=True),
            patch.object(monitor, "_age_seconds", side_effect=lambda value: 300 if value else None),
        ):
            health = monitor._collector_health(
                {"status": "running", "pid": 1234, "updated_at": "old"},
                {"pid": 5678, "updated_at": "old"}, {},
            )
        self.assertEqual(health[0], "응답 지연 · 프로세스 실행 중")
        self.assertEqual(monitor.PROCESSING_REFRESH_SECONDS, 60)

    def test_completed_collector_is_labeled_complete(self) -> None:
        self.assertEqual(monitor._collector_health({"status": "complete"}, {}, {})[0], "완료")

    def test_prepared_snapshot_separates_fulltext_and_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "prepared.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute(
                        "CREATE TABLE prepared_news(state TEXT,body_json TEXT,updated_at REAL)"
                    )
                    connection.executemany("INSERT INTO prepared_news VALUES(?,?,?)", [
                        ("ready", '{"body_status":"fulltext"}', 10),
                        ("ready", '{"body_status":"summary_only"}', 11),
                        ("failed", "{}", 12),
                    ])
            snapshot = monitor._prepared_snapshot(database)
            self.assertEqual(snapshot["counts"], {"ready": 2, "failed": 1})
            self.assertEqual(snapshot["body_counts"], {"fulltext": 1, "summary_only": 1})
            self.assertEqual(snapshot["updated_at"], 12)

    def test_market_news_snapshot_includes_uncollected_source_days_as_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "market-news.sqlite3"
            initialize_database(database)
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.executemany(
                        "INSERT INTO market_news_days VALUES(?,?,?,?,?,?,?,?)",
                        [
                            ("flash", "2019-01-01", "complete_boundary", 11, 10, 135, "", "2026-09-23T04:00:00+00:00"),
                            ("world", "2019-01-01", "empty", 2, 1, 0, "", "2026-09-23T04:00:01+00:00"),
                            ("flash", "2019-01-02", "running", 5, 4, 60, "", "2026-09-23T04:00:02+00:00"),
                        ],
                    )
            with (
                patch.object(monitor, "MARKET_NEWS_DATABASE", database),
                patch.object(monitor, "MARKET_NEWS_START", monitor.date(2019, 1, 1)),
                patch.object(monitor, "MARKET_NEWS_END", monitor.date(2019, 1, 2)),
            ):
                snapshot = monitor._market_news_snapshot()
            self.assertEqual(snapshot["counts"], {
                "complete_boundary": 1, "empty": 1, "running": 1, "pending": 1,
            })
            self.assertEqual(snapshot["current"]["target_date"], "2019-01-02")
            text, percent = monitor._counts_text(snapshot["counts"])
            self.assertIn("완료 2 / 4", text)
            self.assertEqual(percent, 50.0)


if __name__ == "__main__":
    unittest.main()
