from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.stage_prepared_historical_news_archive import stage


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PreparedArchiveStagingTests(unittest.TestCase):
    def test_resume_keeps_seed_unchanged_and_failed_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = root / "prepared.sqlite3", root / "archive.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                           "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                           "rules_json TEXT,error TEXT,updated_at REAL,"
                           "PRIMARY KEY(scope,stock_code,identity))")
                db.executemany("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)", [
                    ("historical_backfill", "A", "one", "ready", "{}", "{}", "[]", "", 1.0),
                    ("historical_backfill", "A", "two", "failed", "{}", "{}", "[]", "fetch failed", 2.0),
                    ("historical_backfill", "B", "three", "ready", "{}", "{}", "[]", "", 3.0),
                ])
                db.commit()
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
                db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                           (json.dumps({"build_state": "building"}),))
                db.execute("CREATE TABLE central_news_article_revisions (id TEXT PRIMARY KEY)")
                db.execute("INSERT INTO central_news_article_revisions VALUES('nas-id')")
                db.commit()
            digest = _hash(source)
            sidecar = source.with_name(source.name + "-wal")
            sidecar.write_bytes(b"uncheckpointed")
            with self.assertRaisesRegex(ValueError, "uncheckpointed WAL content"):
                stage(source, archive, digest)
            sidecar.unlink()
            first = stage(source, archive, digest, max_rows=1)
            self.assertEqual(("building", 1), (first["state"], first["staged_rows"]))
            with self.assertRaisesRegex(ValueError, "hash differs"):
                stage(source, archive, "0" * 64)
            second = stage(source, archive, digest)
            self.assertEqual(("complete", 3, 2),
                             (second["state"], second["staged_rows"], second["inserted_now"]))
            self.assertEqual(0, stage(source, archive, digest)["inserted_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual([("A", "one", "pending", ""),
                                  ("A", "two", "source_failed", "fetch failed"),
                                  ("B", "three", "pending", "")], db.execute(
                    "SELECT stock_code,identity,progress_state,error "
                    "FROM archive_input_progress ORDER BY stock_code,identity"
                ).fetchall())
                self.assertEqual([("nas-id",)], db.execute(
                    "SELECT id FROM central_news_article_revisions").fetchall())

    def test_rejects_unexplained_failure_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = root / "prepared.sqlite3", root / "archive.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                           "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                           "rules_json TEXT,error TEXT,updated_at REAL)")
                db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                           ("historical_backfill", "A", "one", "failed", "{}", "{}", "[]", "", 1.0))
                db.commit()
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
                db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                           (json.dumps({"build_state": "building"}),))
                db.commit()
            with self.assertRaisesRegex(ValueError, "lacks an error reason"):
                stage(source, archive, _hash(source))
            with closing(sqlite3.connect(archive)) as db:
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master "
                                             "WHERE name='archive_input_progress'").fetchone())


if __name__ == "__main__":
    unittest.main()
