from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import report_historical_collection_status as report
from kiwoom_monitor.infrastructure.naver_stock_market_news import initialize_database


class HistoricalCollectionStatusTests(unittest.TestCase):
    def test_market_news_status_counts_finished_source_days_and_current_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "naver_stock_market_news.sqlite3"
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
            state_path = root / "historical_collection" / "market-news-state.json"
            state_path.parent.mkdir()
            state_path.write_text(json.dumps({"status": "running", "pid": 123}), encoding="utf-8")
            with (
                patch.object(report, "MARKET_NEWS_START", "2019-01-01"),
                patch.object(report, "MARKET_NEWS_END", "2019-01-02"),
            ):
                status = report._market_news_status(database)
            self.assertEqual(status["total_source_days"], 4)
            self.assertEqual(status["finished_source_days"], 2)
            self.assertEqual(status["progress_percent"], 50.0)
            self.assertEqual(status["current"]["pages"], 4)
            self.assertEqual(status["collector"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
