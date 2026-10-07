from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.snapshot_prepared_historical_news import snapshot


class PreparedHistoricalNewsSnapshotTests(unittest.TestCase):
    def test_frozen_copy_keeps_original_rows_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "active.sqlite3"
            output = Path(directory) / "frozen.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE prepared_news ("
                                   "scope TEXT,stock_code TEXT,identity TEXT,state TEXT,"
                                   "article_json TEXT,body_json TEXT,rules_json TEXT,"
                                   "error TEXT,updated_at REAL)")
                connection.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                                   ("historical_backfill", "005930", "article:1", "ready",
                                    "{}", "{}", "[]", "", 10.0))
                connection.commit()
            report = snapshot(source, output, "historical_backfill")
            self.assertEqual({"ready": 1}, report["rows_by_state"])
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(),
                             report["sha256"])
            saved = json.loads(Path(str(output) + ".manifest.json").read_text(
                encoding="utf-8"))
            self.assertEqual(report, saved)
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                                   ("historical_backfill", "005930", "article:2", "failed",
                                    "{}", "{}", "[]", "missing", 11.0))
                connection.commit()
            with closing(sqlite3.connect(f"file:{output.as_posix()}?mode=ro&immutable=1",
                                         uri=True)) as frozen:
                self.assertEqual(1, frozen.execute(
                    "SELECT COUNT(*) FROM prepared_news").fetchone()[0])
            with self.assertRaises(FileExistsError):
                snapshot(source, output, "historical_backfill")

    def test_scope_mismatch_does_not_publish_a_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "active.sqlite3"
            output = Path(directory) / "frozen.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE prepared_news ("
                                   "scope TEXT,stock_code TEXT,identity TEXT,state TEXT,"
                                   "article_json TEXT,body_json TEXT,rules_json TEXT,"
                                   "error TEXT,updated_at REAL)")
                connection.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                                   ("historical_market_backfill", "GLOBAL", "article:1",
                                    "ready", "{}", "{}", "[]", "", 10.0))
                connection.commit()
            with self.assertRaisesRegex(ValueError, "another scope"):
                snapshot(source, output, "historical_backfill")
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
