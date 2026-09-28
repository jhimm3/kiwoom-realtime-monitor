from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_monitor.central_server.database import SQLiteQueryStore, stable_document_hash
from scripts.finalize_prepared_historical_article_bodies import finalize
from scripts.stage_prepared_historical_news_archive import stage


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixtures(root: Path) -> tuple[Path, Path, Path, str]:
    prepared, archive, raw = (root / name for name in
                              ("prepared.sqlite3", "archive.sqlite3", "raw.sqlite3"))
    with closing(sqlite3.connect(prepared)) as db:
        db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                   "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                   "rules_json TEXT,error TEXT,updated_at REAL,"
                   "PRIMARY KEY(scope,stock_code,identity))")
        for identity, body_status, body_text in [
            ("a", "fulltext", "verified text"),
            ("b", "summary_only", "summary text"),
            ("c", "fulltext", "unverified text"),
            ("d", "fulltext", "hash only text"),
        ]:
            document = {"stock_code": "005930", "identity": identity,
                        "stock_name": "Samsung", "title": identity,
                        "published_at": "2026-09-25T09:00:00+09:00",
                        "historical_source": {"provider": "naver", "office_id": "001",
                                              "article_id": identity}}
            article = {"stock_code": "005930", "identity": identity,
                       "document": document, "targets": [["005930", "Samsung"]]}
            body = {"body_status": body_status, "body_text": body_text,
                    "fetched_at": 1790300000.0, "source_url": "https://example.test/" + identity}
            rules = [{"target_id": "005930", "stock_name": "Samsung",
                      "assessment": {"sentiment": "neutral"}, "core_sentences": [],
                      "rule_result": {"rule_version": "supply-contract-rule-v2"}
                      if identity == "b" else None}]
            db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)", (
                "historical_backfill", "005930", identity, "ready",
                json.dumps(article), json.dumps(body), json.dumps(rules), "", 1.0,
            ))
        db.commit()
    SQLiteQueryStore(archive).initialize()
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
        db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                   (json.dumps({"build_state": "building"}),))
        with closing(sqlite3.connect(prepared)) as source:
            article = json.loads(source.execute(
                "SELECT article_json FROM prepared_news WHERE identity='a'"
            ).fetchone()[0])
        document = article["document"]
        db.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("seed-article", "005930", "a", stable_document_hash(document),
             "naver_historical_web", document["published_at"], 1.0, 1.0,
             "historical_news_pc_backfill", None, json.dumps(document)),
        )
        db.execute(
            "INSERT INTO central_news_body_revisions("
            "body_revision_id,article_revision_id,content_hash,extractor_version,"
            "fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
            ("seed-body", "seed-article", stable_document_hash({"body": "verified text"}),
             "article-text-v7", 1.0, 1.0, "fulltext", "verified text", ""),
        )
        db.commit()
    with closing(sqlite3.connect(raw)) as db:
        db.execute("CREATE TABLE news_article_body_snapshots ("
                   "provider TEXT,office_id TEXT,article_id TEXT,"
                   "extractor_version TEXT,body_sha256 TEXT,fetched_at TEXT)")
        db.execute("INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?)",
                   ("naver", "001", "a", "article-text-v7",
                    hashlib.sha256(b"verified text").hexdigest(),
                    datetime.fromtimestamp(1790300000, timezone.utc).isoformat()))
        db.execute("INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?)",
                   ("naver", "001", "d", "article-text-v7",
                    hashlib.sha256(b"hash only text").hexdigest(),
                    datetime.fromtimestamp(1790300001, timezone.utc).isoformat()))
        db.commit()
    digest = _sha(prepared)
    stage(prepared, archive, digest)
    return prepared, archive, raw, digest


class PreparedArticleBodyFinalizerTests(unittest.TestCase):
    def test_reuses_seed_and_resumes_without_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = _fixtures(Path(directory))
            first = finalize(prepared, archive, raw, digest, 2, max_rows=1)
            self.assertEqual(1, first["processed_now"])
            self.assertEqual(3, first["remaining_pending"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(("seed-article", "seed-body"), db.execute(
                    "SELECT article_revision_id,body_revision_id FROM archive_input_progress "
                    "WHERE identity='a'").fetchone())
                self.assertEqual(("raw_hash_verified",), db.execute(
                    "SELECT source_status FROM archive_body_provenance WHERE identity='a'"
                ).fetchone())
            second = finalize(prepared, archive, raw, digest, 2)
            self.assertEqual(3, second["processed_now"])
            self.assertEqual(0, second["remaining_pending"])
            self.assertEqual(0, finalize(prepared, archive, raw, digest, 2)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM central_news_article_revisions").fetchone()[0])
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM central_news_body_revisions").fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])
                self.assertEqual([("a", "raw_hash_verified"),
                                  ("b", "summary_from_prepared"),
                                  ("c", "version_unverified"),
                                  ("d", "raw_hash_only")], db.execute(
                    "SELECT identity,source_status FROM archive_body_provenance "
                    "ORDER BY identity").fetchall())
                self.assertEqual(("prepared-body-version-unverified-v1",), db.execute(
                    "SELECT extractor_version FROM central_news_body_revisions "
                    "WHERE body_revision_id=(SELECT body_revision_id FROM archive_body_provenance "
                    "WHERE identity='d')").fetchone())

    def test_sql_failure_rolls_back_article_body_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = _fixtures(Path(directory))
            # Initialization creates the provenance table; the first call may
            # finish one row, then the injected failure must leave the next row untouched.
            finalize(prepared, archive, raw, digest, 1, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TRIGGER abort_provenance BEFORE INSERT ON archive_body_provenance "
                           "BEGIN SELECT RAISE(ABORT,'injected'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected"):
                finalize(prepared, archive, raw, digest, 1, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_article_revisions").fetchone()[0])
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_body_revisions").fetchone()[0])
                self.assertEqual(("pending", None, None), db.execute(
                    "SELECT progress_state,article_revision_id,body_revision_id "
                    "FROM archive_input_progress WHERE identity='b'").fetchone())


if __name__ == "__main__":
    unittest.main()
