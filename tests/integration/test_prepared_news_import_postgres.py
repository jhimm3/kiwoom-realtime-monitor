"""Prepared historical article import checks for a disposable PostgreSQL DB.

KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL must name kiwoom_monitor_diagnostic_test.
The test refuses to run against any other database.
"""

from __future__ import annotations

import os
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_metrics import refresh_capture_state, summarize_db_calls
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from scripts.import_prepared_historical_news_to_nas import _complete_batch, _prepare_articles


class PreparedNewsImportPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated PostgreSQL test database not configured")
        if urlsplit(url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing prepared-news test outside the dedicated database")
        cls.store = PostgresQueryStore(url)
        with cls.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != "kiwoom_monitor_diagnostic_test":
                raise RuntimeError("connected database is not the dedicated test database")
        cls.store.initialize()

    def _record(self, code: str, identity: str, title: str) -> dict:
        document = {"stock_code": code, "identity": identity, "title": title,
                    "published_at": "2099-01-02T10:00:00+09:00"}
        return {"scope": "historical_backfill",
                "article": {"stock_code": code, "identity": identity, "document": document}}

    def _cleanup(self, code: str) -> None:
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT article_revision_id FROM central_news_article_revisions "
                           "WHERE stock_code=%s", (code,))
            article_ids = [str(row[0]) for row in cursor.fetchall()]
            if article_ids:
                cursor.execute("DELETE FROM central_news_jobs WHERE article_revision_id=ANY(%s)",
                               (article_ids,))
                cursor.execute("DELETE FROM central_news_body_revisions WHERE article_revision_id=ANY(%s)",
                               (article_ids,))
                cursor.execute("DELETE FROM central_documents WHERE collection='news_assessment' "
                               "AND document_key=ANY(%s)", (article_ids,))
                cursor.execute("DELETE FROM central_documents WHERE collection='news_original_publication' "
                               "AND owner=ANY(%s)", (article_ids,))
            cursor.execute("DELETE FROM central_news_article_revisions WHERE stock_code=%s", (code,))
            cursor.execute("DELETE FROM central_documents WHERE collection='news_article' AND owner=%s",
                           (code,))

    def test_batched_articles_replay_and_revision_chain(self) -> None:
        code = f"T{uuid.uuid4().hex[:12]}"
        records = [self._record(code, f"https://example.invalid/{code}/{index}", f"title-{index}")
                   for index in range(3)]
        try:
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, records)
            self.assertEqual([], failed)
            self.assertEqual(records, prepared)
            first_ids = [record["article_id"] for record in records]
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT article_revision_id,accepted_sequence FROM "
                               "central_news_article_revisions WHERE stock_code=%s "
                               "ORDER BY accepted_sequence", (code,))
                rows = cursor.fetchall()
                self.assertEqual(first_ids, [str(row[0]) for row in rows])
                self.assertEqual(sorted(row[1] for row in rows), [row[1] for row in rows])
                cursor.execute("SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=ANY(%s) "
                               "AND stage='BODY' AND state='PENDING'", (first_ids,))
                self.assertEqual(3, cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM central_documents WHERE collection='news_article' "
                               "AND owner=%s", (code,))
                self.assertEqual(3, cursor.fetchone()[0])
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, records)
            self.assertEqual([], failed)
            self.assertEqual(first_ids, [record["article_id"] for record in prepared])
            records[1]["article"]["document"]["title"] = "corrected"
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, records)
            self.assertEqual([], failed)
            self.assertNotEqual(first_ids[1], prepared[1]["article_id"])
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT revision_of FROM central_news_article_revisions "
                               "WHERE article_revision_id=%s", (prepared[1]["article_id"],))
                self.assertEqual(first_ids[1], cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM central_news_article_revisions WHERE stock_code=%s", (code,))
                self.assertEqual(4, cursor.fetchone()[0])
        finally:
            self._cleanup(code)

    def test_prepared_body_rule_completion_and_replay(self) -> None:
        code = f"T{uuid.uuid4().hex[:12]}"
        record = self._record(code, f"https://example.invalid/{code}/complete", "prepared")
        record["body"] = {"body_text": "검증용 기사 본문입니다.", "body_status": "fulltext"}
        record["rules"] = [{"target_id": code, "stock_name": code,
                            "assessment": {"relevance": "relevant"},
                            "core_sentences": ["검증용 기사 본문입니다."], "rule_result": None}]
        try:
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, [record])
            self.assertEqual([], failed)
            with self.store._connect() as connection:
                completed, deferred, failed = _complete_batch(connection, prepared)
            self.assertEqual([record], completed)
            self.assertEqual(([], []), (deferred, failed))
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT stage,state FROM central_news_jobs WHERE article_revision_id=%s "
                               "ORDER BY stage", (record["article_id"],))
                self.assertEqual([("BODY", "COMPLETED"), ("RULE", "COMPLETED")], cursor.fetchall())
                cursor.execute("SELECT COUNT(*) FROM central_news_body_revisions WHERE article_revision_id=%s",
                               (record["article_id"],))
                self.assertEqual(1, cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM central_documents WHERE collection='news_assessment' "
                               "AND owner=%s AND document_key=%s", (code, record["article_id"]))
                self.assertEqual(1, cursor.fetchone()[0])
            with self.store._connect() as connection:
                completed, deferred, failed = _complete_batch(connection, prepared)
            self.assertEqual([record], completed)
            self.assertEqual(([], []), (deferred, failed))
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM central_news_body_revisions WHERE article_revision_id=%s",
                               (record["article_id"],))
                self.assertEqual(1, cursor.fetchone()[0])
        finally:
            self._cleanup(code)

    def test_invalid_member_rolls_back_batch_then_isolates_failure(self) -> None:
        code = f"T{uuid.uuid4().hex[:12]}"
        good = self._record(code, f"https://example.invalid/{code}/good", "valid")
        bad = self._record(code, f"https://example.invalid/{code}/bad", "invalid")
        bad["article"]["document"] = []
        try:
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, [good, bad])
            self.assertEqual([good], prepared)
            self.assertEqual([bad], [record for record, _ in failed])
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM central_news_article_revisions WHERE stock_code=%s", (code,))
                self.assertEqual(1, cursor.fetchone()[0])
                cursor.execute("SELECT document_key FROM central_documents "
                               "WHERE collection='news_article' AND owner=%s", (code,))
                self.assertEqual([(good["article"]["identity"],)], cursor.fetchall())
        finally:
            self._cleanup(code)

    def test_completion_batches_keep_savepoints_and_separate_call_ids_on_one_connection(self) -> None:
        code = f"T{uuid.uuid4().hex[:12]}"
        good = self._record(code, f"https://example.invalid/{code}/good", "good")
        bad = self._record(code, f"https://example.invalid/{code}/deferred", "deferred")
        for record in (good, bad):
            record["body"] = {"body_text": "검증 본문", "body_status": "fulltext"}
            record["rules"] = []
        try:
            with self.store._connect() as connection:
                prepared, failed = _prepare_articles(self.store, connection, [good, bad])
            self.assertEqual([], failed)
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE central_news_jobs SET state='RUNNING' "
                               "WHERE article_revision_id=%s AND stage='BODY'",
                               (bad["article_id"],))

            with tempfile.TemporaryDirectory() as directory:
                control = Path(directory) / "capture.json"
                control.write_text(json.dumps({
                    "schema": 1, "instance_id": instance_id(),
                    "diagnostic_tool": {"expires_at": time.time() + 60,
                                        "session_id": uuid.uuid4().hex},
                    "capture": {"expires_at": time.time() + 60},
                }), encoding="utf-8")
                with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}):
                    refresh_capture_state(force=True)
                    try:
                        with self.store._connect() as connection:
                            pid = connection.info.backend_pid
                            self.assertEqual("IDLE", connection.info.transaction_status.name)
                            first = _complete_batch(connection, prepared)
                            self.assertEqual("IDLE", connection.info.transaction_status.name)
                            second = _complete_batch(connection, [good])
                            self.assertFalse(connection.closed)
                        calls = [call for call in summarize_db_calls(
                            time.time() - 30, time.time() + 1, mode="raw")["calls"]
                            if call["writer_kind"] == "prepared_news_completion_batch"]
                    finally:
                        control.unlink(missing_ok=True)
                        refresh_capture_state(force=True)

            self.assertEqual([good], first[0])
            self.assertEqual([bad], [record for record, _ in first[1]])
            self.assertEqual([], first[2])
            self.assertEqual(([good], [], []), second)
            self.assertEqual(2, len(calls))
            self.assertEqual(2, len({call["call_id"] for call in calls}))
            self.assertEqual([2, 1], [call["rows_attempted"] for call in calls])
            self.assertTrue(all(call["backend_pid"] == pid and call["commits"] == 1
                                and call["transactions"] == 1
                                and call["connection_acquire_ms"] is None
                                and call["outcome"] == "committed" for call in calls))
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT stage,state FROM central_news_jobs "
                               "WHERE article_revision_id=ANY(%s) AND stage='BODY' "
                               "ORDER BY article_revision_id",
                               ([good["article_id"], bad["article_id"]],))
                self.assertEqual(["COMPLETED", "RUNNING"], sorted(
                    [row[1] for row in cursor.fetchall()]))
                cursor.execute("SELECT article_revision_id FROM central_news_body_revisions "
                               "WHERE article_revision_id=ANY(%s)",
                               ([good["article_id"], bad["article_id"]],))
                self.assertEqual([(good["article_id"],)], cursor.fetchall())
        finally:
            self._cleanup(code)


if __name__ == "__main__":
    unittest.main()
