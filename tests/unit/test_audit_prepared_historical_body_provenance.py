from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.audit_prepared_historical_body_provenance import audit


class PreparedBodyProvenanceAuditTests(unittest.TestCase):
    def test_fulltext_evidence_and_missing_evidence_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared.sqlite3"
            raw = root / "raw.sqlite3"
            with closing(sqlite3.connect(raw)) as db:
                db.execute("CREATE TABLE news_article_body_snapshots ("
                           "provider TEXT, office_id TEXT, article_id TEXT, "
                           "extractor_version TEXT, body_sha256 TEXT, fetched_at TEXT)")
                fetched = datetime.fromtimestamp(1790300000, timezone.utc).isoformat()
                db.executemany("INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?)", [
                    ("naver", "1", "a", "v7", hashlib.sha256(b"match").hexdigest(), fetched),
                    ("naver", "1", "b", "v6", hashlib.sha256(b"other").hexdigest(), fetched),
                    ("naver", "1", "e", "v6", hashlib.sha256(b"same bytes").hexdigest(), fetched),
                ])
                db.commit()
            with closing(sqlite3.connect(prepared)) as db:
                db.execute("CREATE TABLE prepared_news (scope TEXT,stock_code TEXT,"
                           "identity TEXT,state TEXT,article_json TEXT,body_json TEXT)")
                for identity, status, body in [("a", "fulltext", "match"),
                                               ("b", "fulltext", "changed"),
                                               ("c", "fulltext", "unrecorded"),
                                               ("d", "summary_only", "summary"),
                                               ("e", "fulltext", "same bytes")]:
                    article = {"document": {"historical_source": {
                        "provider": "naver", "office_id": "1", "article_id": identity}}}
                    db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?)", (
                        "historical_backfill", "005930", identity, "ready",
                        json.dumps(article), json.dumps({"body_status": status,
                                                         "body_text": body,
                                                         "fetched_at": 1790300001 if identity == "e" else 1790300000}),
                    ))
                db.execute("INSERT INTO prepared_news VALUES(?,?,?,?,?,?)", (
                    "historical_backfill", "005930", "failed", "failed", "{}", "{}"))
                db.commit()
            result = audit(prepared, raw)
            self.assertEqual(5, result["ready_rows_total"])
            self.assertEqual(5, result["ready_rows_inspected"])
            self.assertEqual(2, result["counts"]["fulltext:raw_hash_match"])
            self.assertEqual(1, result["counts"]["fulltext:raw_hash_and_fetch_time_match"])
            self.assertEqual(1, result["counts"]["fulltext:raw_hash_only"])
            self.assertEqual(1, result["counts"]["fulltext:raw_hash_mismatch"])
            self.assertEqual(1, result["counts"]["fulltext:no_raw_snapshot"])
            self.assertEqual(1, result["counts"]["body_status:summary_only"])
            self.assertEqual({"v7": 1}, result["matched_extractor_versions"])
            limited = audit(prepared, raw, limit=2)
            self.assertTrue(limited["limited"])
            self.assertEqual(2, limited["ready_rows_inspected"])


if __name__ == "__main__":
    unittest.main()
