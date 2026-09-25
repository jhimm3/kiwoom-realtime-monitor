from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.run_naver_stock_market_news import publish_snapshot, write_status
from kiwoom_monitor.infrastructure.naver_stock_market_news import initialize_database


class MarketNewsSnapshotTests(unittest.TestCase):
    def test_status_replace_retries_transient_windows_file_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / "market-news-state.json"
            status.write_text('{"status":"old"}', encoding="utf-8")
            original_replace = os.replace
            attempts = 0

            def replace_with_collision(source: Path, target: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    error = PermissionError(5, "destination in use")
                    error.winerror = 5
                    raise error
                original_replace(source, target)

            with (patch("scripts.run_naver_stock_market_news.os.replace", side_effect=replace_with_collision),
                  patch("scripts.run_naver_stock_market_news.time.sleep")):
                write_status(status, status="running")
            self.assertEqual(attempts, 2)
            self.assertEqual(json.loads(status.read_text(encoding="utf-8"))["status"], "running")
            self.assertFalse(list(status.parent.glob(".market-news-state.json.*.tmp")))

    def test_snapshot_is_consistent_and_published_under_dedicated_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "local.sqlite3"
            initialize_database(database)
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO market_news_days VALUES(?,?,?,?,?,?,?,?)",
                        ("flash", "2019-01-01", "complete", 2, 1, 3, "", "now"),
                    )
            nas = root / "nas"
            nas.mkdir()
            (nas / "AGENTS.md").write_text("sentinel", encoding="utf-8")
            published = publish_snapshot(database, nas)
            self.assertTrue(published.is_dir())
            with closing(sqlite3.connect(published / "naver_stock_market_news.sqlite3")) as copy:
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(copy.execute("SELECT articles FROM market_news_days").fetchone()[0], 3)
            manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"], [["flash", "complete", 1, 3]])
            pointer = published.parent.parent / "latest.json"
            self.assertEqual(json.loads(pointer.read_text(encoding="utf-8"))["run"], published.name)


if __name__ == "__main__":
    unittest.main()
