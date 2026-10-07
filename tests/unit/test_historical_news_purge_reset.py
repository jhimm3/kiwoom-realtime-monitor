from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.reset_purged_historical_news_imports import reset_market, reset_search


class HistoricalNewsPurgeResetTests(unittest.TestCase):
    def test_only_manifested_imports_are_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "purged.jsonl"
            manifest.write_text("\n".join(json.dumps(value) for value in (
                {"scope": "historical_backfill", "stock_code": "005930", "identity": "old-search"},
                {"scope": "historical_market_backfill", "stock_code": "GLOBAL", "identity": "old-market"},
            )) + "\n", encoding="utf-8")
            search_db, market_db = root / "search.db", root / "market.db"
            with closing(sqlite3.connect(search_db)) as connection, connection:
                connection.execute("CREATE TABLE nas_news_imports(code TEXT,identity TEXT,state TEXT,"
                                   "imported_at TEXT,last_error TEXT)")
                connection.executemany("INSERT INTO nas_news_imports VALUES(?,?,?,?,?)", [
                    ("005930", "old-search", "imported", "today", ""),
                    ("005930", "finished-search", "imported", "today", ""),
                ])
            with closing(sqlite3.connect(market_db)) as connection, connection:
                connection.execute("CREATE TABLE market_news_articles(source TEXT,office_id TEXT,"
                                   "article_id TEXT,article_url TEXT)")
                connection.execute("CREATE TABLE nas_market_news_imports(source TEXT,office_id TEXT,"
                                   "article_id TEXT,state TEXT,imported_at TEXT,last_error TEXT)")
                connection.executemany("INSERT INTO market_news_articles VALUES(?,?,?,?)", [
                    ("FLASH", "1", "1", "old-market"),
                    ("FLASH", "1", "2", "finished-market"),
                ])
                connection.executemany("INSERT INTO nas_market_news_imports VALUES(?,?,?,?,?,?)", [
                    ("FLASH", "1", "1", "imported", "today", ""),
                    ("FLASH", "1", "2", "imported", "today", ""),
                ])

            self.assertEqual(1, reset_search(search_db, manifest, False)["search_requeued"])
            self.assertEqual(1, reset_market(market_db, manifest, False))
            with closing(sqlite3.connect(search_db)) as connection, connection:
                self.assertEqual(2, connection.execute(
                    "SELECT COUNT(*) FROM nas_news_imports WHERE state='imported'").fetchone()[0])

            reset_search(search_db, manifest, True)
            reset_market(market_db, manifest, True)
            with closing(sqlite3.connect(search_db)) as connection, connection:
                self.assertEqual([("finished-search", "imported"), ("old-search", "pending")],
                                 connection.execute("SELECT identity,state FROM nas_news_imports "
                                                    "ORDER BY identity").fetchall())
            with closing(sqlite3.connect(market_db)) as connection, connection:
                self.assertEqual([("1", "pending"), ("2", "imported")],
                                 connection.execute("SELECT article_id,state FROM nas_market_news_imports "
                                                    "ORDER BY article_id").fetchall())


if __name__ == "__main__":
    unittest.main()
