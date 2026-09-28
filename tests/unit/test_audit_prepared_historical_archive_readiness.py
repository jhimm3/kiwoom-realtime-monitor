"""The archive preflight must only read its input and reject incomplete work."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from scripts.audit_prepared_historical_archive_readiness import audit


class ArchiveReadinessAuditTests(unittest.TestCase):
    def test_incomplete_archive_is_blocked_without_changing_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "archive.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)")
                db.execute("CREATE TABLE archive_input_progress("
                           "scope TEXT,stock_code TEXT,identity TEXT,progress_state TEXT,"
                           "error TEXT,article_revision_id TEXT,body_revision_id TEXT,input_hash TEXT)")
                db.execute("INSERT INTO archive_build_manifest VALUES(?,?)",
                           ("report", json.dumps({"build_state": "building"})))
                db.execute("INSERT INTO archive_build_manifest VALUES(?,?)",
                           ("prepared_search_inputs", json.dumps({"state": "complete",
                                                                  "staged_rows": 3,
                                                                  "expected_rows": 3})))
                db.executemany("INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?)", [
                    ("historical_backfill", "000001", "a", "pending", None, None, None, "a"),
                    ("historical_backfill", "000001", "b", "assessment_done", None,
                     "article-b", "body-b", "b"),
                    ("historical_backfill", "000001", "c", "source_failed", "fetch failed",
                     None, None, "c"),
                ])
            before = hashlib.sha256(path.read_bytes()).digest()
            result = audit(path, full_integrity_check=True)
            self.assertFalse(result["ready_for_seal_review"])
            self.assertEqual("ok", result["integrity_check"])
            self.assertEqual(1, result["unverified_rule_inputs"])
            self.assertEqual(1, result["source_failure_reasons"]["fetch failed"])
            self.assertIn("search_pending_remaining", result["blockers"])
            self.assertIn("source_failures_require_explicit_review", result["blockers"])
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())

    def test_stale_rule_identity_blocks_review_even_when_counts_match(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "archive.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)")
                db.execute("CREATE TABLE archive_input_progress("
                           "scope TEXT,stock_code TEXT,identity TEXT,progress_state TEXT,"
                           "error TEXT,article_revision_id TEXT,body_revision_id TEXT,input_hash TEXT)")
                db.execute("CREATE TABLE archive_verified_rule_inputs("
                           "scope TEXT,stock_code TEXT,identity TEXT,outcome TEXT,"
                           "input_hash TEXT,article_revision_id TEXT,body_revision_id TEXT)")
                db.execute("INSERT INTO archive_build_manifest VALUES(?,?)",
                           ("report", json.dumps({"build_state": "building", "dataset_id": "dataset-1"})))
                db.execute("INSERT INTO archive_build_manifest VALUES(?,?)",
                           ("prepared_search_inputs", json.dumps({"state": "complete",
                                                                  "staged_rows": 1,
                                                                  "expected_rows": 1})))
                db.execute("INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?)",
                           ("historical_backfill", "000001", "a", "assessment_done", None,
                            "article-a", "body-a", "current"))
                db.execute("INSERT INTO archive_verified_rule_inputs VALUES(?,?,?,?,?,?,?)",
                           ("historical_backfill", "000001", "a", "verified", "stale",
                            "article-a", "body-a"))
            result = audit(path)
            self.assertEqual(1, result["verified_rule_rows"])
            self.assertEqual(1, result["unverified_rule_inputs"])
            self.assertIn("search_rule_verification_mismatch", result["blockers"])


if __name__ == "__main__":
    unittest.main()
