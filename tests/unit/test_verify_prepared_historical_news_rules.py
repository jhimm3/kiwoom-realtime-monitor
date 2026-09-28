from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from scripts.finalize_prepared_historical_article_bodies import finalize as finalize_bodies
from scripts.finalize_prepared_historical_assessments import finalize as finalize_assessments
from scripts.preprocess_historical_news_to_nas import prepare_job
from scripts.stage_prepared_historical_news_archive import stage
from scripts.verify_prepared_historical_news_rules import verify
from tests.unit.test_finalize_prepared_historical_article_bodies import _sha


def _fixture(root: Path, *, mismatch: bool = True,
             same_event: bool = False) -> tuple[Path, Path, Path, str]:
    prepared, archive, raw = (root / name for name in
                              ("prepared.sqlite3", "archive.sqlite3", "raw.sqlite3"))
    with closing(sqlite3.connect(prepared)) as db:
        db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                   "identity TEXT,state TEXT,article_json TEXT,body_json TEXT,"
                   "rules_json TEXT,error TEXT,updated_at REAL,"
                   "PRIMARY KEY(scope,stock_code,identity))")
        for identity, title in (("match", "기업, 삼성전자와 500억원 공급계약 체결"),
                                ("mismatch", "기업, 삼성전자와 500억원 공급계약 체결"
                                 if same_event else "일반 기업 소식")):
            document = {"stock_code": "005930", "stock_name": "기업", "identity": identity,
                        "title": title, "description": "", "published_at": "2026-09-01T00:00:00+00:00"}
            article = {"stock_code": "005930", "identity": identity,
                       "document": document, "targets": [["005930", "기업"]]}
            body = {"body_status": "summary_only", "body_text": "기사 요약",
                    "fetched_at": 1790300000.0}
            computed = prepare_job(
                {"job_key": "fixture", "attempts": 1, "stage": "RULE", "target_id": "005930",
                 "payload": {"stock_code": "005930", "stock_name": "기업"}},
                article, {"body_text": body["body_text"], "status": "summary_only"},
                allow_network=False,
            )
            if identity == "mismatch" and mismatch:
                computed["assessment"]["reason"] = "stale prepared assessment"
            rules = [{"target_id": "005930", "stock_name": "기업",
                      "assessment": computed["assessment"],
                      "core_sentences": computed["core_sentences"],
                      "rule_result": computed["rule_result"]}]
            db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                       ("historical_backfill", "005930", identity, "ready",
                        json.dumps(article), json.dumps(body), json.dumps(rules), "", 1.0))
        db.commit()
    with closing(sqlite3.connect(raw)) as db:
        db.execute("CREATE TABLE news_article_body_snapshots ("
                   "provider TEXT,office_id TEXT,article_id TEXT,"
                   "extractor_version TEXT,body_sha256 TEXT,fetched_at TEXT)")
    SQLiteQueryStore(archive).initialize()
    with closing(sqlite3.connect(archive)) as db:
        db.execute("CREATE TABLE archive_build_manifest (key TEXT PRIMARY KEY,value_json TEXT)")
        db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",
                   (json.dumps({"build_state": "building"}),))
        db.commit()
    digest = _sha(prepared)
    stage(prepared, archive, digest)
    finalize_bodies(prepared, archive, raw, digest, 0)
    finalize_assessments(prepared, archive, digest)
    return prepared, archive, raw, digest


class VerifyPreparedHistoricalRulesTests(unittest.TestCase):
    def test_verified_and_mismatch_are_distinct_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = _fixture(Path(root))
            result = verify(prepared, archive, digest)
            self.assertEqual({"verified": 1, "mismatch": 1}, result["outcomes_now"])
            self.assertEqual(0, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual([("match", "verified"), ("mismatch", "mismatch")],
                                 db.execute("SELECT identity,outcome FROM archive_verified_rule_inputs "
                                            "ORDER BY identity").fetchall())
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])

    def test_verification_insert_failure_preserves_progress(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = _fixture(Path(root))
            verify(prepared, archive, digest, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TRIGGER reject_verified BEFORE INSERT ON "
                           "archive_verified_rule_inputs BEGIN "
                           "SELECT RAISE(ABORT,'injected verification failure'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected verification failure"):
                verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM archive_verified_rule_inputs").fetchone()[0])
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_input_progress "
                    "WHERE progress_state='assessment_done'").fetchone()[0])

    def test_later_completed_lower_key_is_verified_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = _fixture(Path(root), mismatch=False)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("UPDATE archive_input_progress SET progress_state='article_body_done' "
                           "WHERE identity='match'")
                db.commit()
            self.assertEqual(1, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                db.execute("UPDATE archive_input_progress SET progress_state='assessment_done' "
                           "WHERE identity='match'")
                db.commit()
            self.assertEqual(1, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_verified_rule_inputs "
                    "WHERE outcome='verified'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
