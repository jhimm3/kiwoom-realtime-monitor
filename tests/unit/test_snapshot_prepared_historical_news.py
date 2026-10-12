from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.snapshot_prepared_historical_news import snapshot
from scripts.stage_prepared_historical_news_archive import _input_hash


class PreparedHistoricalNewsSnapshotTests(unittest.TestCase):
    def test_explicit_market_alias_preserves_rows_original_scope_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'active.sqlite3'; output=Path(directory)/'frozen.sqlite3'
            rows=[('historical_market_backfill','GLOBAL','a','ready','{}','{}','[]','',10.),
                  ('historical_market','GLOBAL','b','ready','{}','{}','[]','',11.),
                  ('historical_market','GLOBAL','c','failed','{}','{}','[]','timeout',12.)]
            with closing(sqlite3.connect(source)) as db, db:
                db.execute('CREATE TABLE prepared_news(scope TEXT,stock_code TEXT,identity TEXT,state TEXT,article_json TEXT,body_json TEXT,rules_json TEXT,error TEXT,updated_at REAL,PRIMARY KEY(scope,stock_code,identity))')
                db.executemany('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)',rows)
            original=source.read_bytes()
            with self.assertRaisesRegex(ValueError,'another scope'):
                snapshot(source,output,'historical_market_backfill')
            report=snapshot(source,output,'historical_market_backfill',scope_aliases=('historical_market',))
            self.assertEqual({'historical_market':2},report['normalized_scope_aliases'])
            self.assertEqual({'ready':2,'failed':1},report['rows_by_state'])
            self.assertEqual(original,source.read_bytes())
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual([('historical_market_backfill',3)],db.execute('SELECT scope,COUNT(*) FROM prepared_news GROUP BY scope').fetchall())
                self.assertEqual([('b','historical_market',_input_hash(rows[1][1:])),('c','historical_market',_input_hash(rows[2][1:]))],db.execute('SELECT identity,original_scope,input_hash FROM prepared_scope_origins ORDER BY identity').fetchall())

    def test_alias_overlap_refuses_publication_and_unknown_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'active.sqlite3'; output=Path(directory)/'frozen.sqlite3'
            with closing(sqlite3.connect(source)) as db, db:
                db.execute('CREATE TABLE prepared_news(scope TEXT,stock_code TEXT,identity TEXT,state TEXT,article_json TEXT,body_json TEXT,rules_json TEXT,error TEXT,updated_at REAL)')
                for scope in ('historical_market','historical_market_backfill'):
                    db.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)',(scope,'GLOBAL','same','ready','{}','{}','[]','',1.))
            original=source.read_bytes()
            with self.assertRaisesRegex(ValueError,'verified'):
                snapshot(source,output,'historical_market_backfill',scope_aliases=('unknown',))
            with self.assertRaisesRegex(ValueError,'overlaps'):
                snapshot(source,output,'historical_market_backfill',scope_aliases=('historical_market',))
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output)+'.manifest.json').exists())
            self.assertEqual(original,source.read_bytes())

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
