from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.materialize_historical_news_seed import materialize


def _add_row(db: sqlite3.Connection, table: str, key: str, payload: dict, role: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    db.execute("INSERT INTO seed_rows VALUES(?,?,?,?,?)",
               (table, json.dumps([key]), encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(), role))


class MaterializeHistoricalNewsSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.seed = self.root / "seed.sqlite3"
        self.archive = self.root / "archive.sqlite3"
        with closing(sqlite3.connect(self.seed)) as db, db:
            db.execute("CREATE TABLE seed_rows(table_name TEXT,row_key TEXT,payload_json TEXT,"
                       "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
            db.execute("CREATE TABLE seed_manifest(key TEXT PRIMARY KEY,value_json TEXT)")
            self._rows(db)

    def _rows(self, db: sqlite3.Connection) -> None:
        article = {
            "accepted_sequence": 42, "article_revision_id": "old-a",
            "stock_code": "005930", "identity": "https://example.test/a",
            "content_hash": "article-hash", "collector_id": "historical",
            "published_at": "2024-01-01T09:00:00+09:00", "received_at": 1.0,
            "available_at": 2.0, "collection_scope": "historical_backfill",
            "revision_of": None, "document_json": {"title": "원본 제목"},
        }
        body = {
            "accepted_sequence": 84, "body_revision_id": "old-b",
            "article_revision_id": "old-a", "content_hash": "body-hash",
            "extractor_version": "v1", "fetched_at": 3.0,
            "available_at": 4.0, "status": "available",
            "body_text": "원본 본문", "error": "",
        }
        _add_row(db, "central_news_article_revisions", "old-a", article, "historical")
        _add_row(db, "central_news_body_revisions", "old-b", body, "historical")
        report = {"schema": "historical-news-seed/v1",
                  "snapshot_started_at": "2026-09-26 19:09:31+00",
                  "tables": {"central_news_article_revisions": 1,
                             "central_news_body_revisions": 1},
                  "roles": {"historical": 2}}
        db.execute("INSERT INTO seed_manifest VALUES('report',?)", (json.dumps(report),))

    def test_preserves_ids_sequences_and_json(self) -> None:
        result = materialize(self.seed, self.archive)
        self.assertEqual("building", result["build_state"])
        self.assertEqual(2, result["seed_rows"])
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual((42, "old-a", '{"title":"원본 제목"}'), db.execute(
                "SELECT accepted_sequence,article_revision_id,document_json "
                "FROM central_news_article_revisions").fetchone())
            self.assertEqual((84, "old-b", "old-a"), db.execute(
                "SELECT accepted_sequence,body_revision_id,article_revision_id "
                "FROM central_news_body_revisions").fetchone())
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM archive_seed_roles").fetchone()[0])
            self.assertEqual("building", json.loads(db.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()[0]
                                                     )["build_state"])

    def test_schema_mismatch_does_not_publish_archive(self) -> None:
        with closing(sqlite3.connect(self.seed)) as db, db:
            db.execute("UPDATE seed_rows SET payload_json=json_set(payload_json,"
                       "'$.unknown_field','x') WHERE table_name='central_news_body_revisions'")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            materialize(self.seed, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.archive.with_name(self.archive.name + ".partial").exists())


if __name__ == "__main__":
    unittest.main()
