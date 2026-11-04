"""Reported research result contracts against an explicitly owned diagnostic PG."""
from __future__ import annotations

import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.domain.research_contract import (
    REPORTED_RESEARCH_RESULT_COLLECTION, ReportedResearchResult, ResearchResultConflict,
)
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentHttpError
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from tests.integration.postgres_test_support import isolated_observation_schema
from tests.unit.test_query_authority import wire_server
from tests.unit.test_research_reported_results import NOW, RESULT_TOKEN, OTHER_TOKEN, result_document, result_settings


class PostgresReportedResearchResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not selected:
            raise RuntimeError("explicit dedicated diagnostic PostgreSQL URL is required; no fallback")
        cls.test_url = explicit_dedicated_url(selected)

    def setUp(self):
        self.dsn = self.enterContext(isolated_observation_schema(self.test_url))
        self.store = self.new_store()
        self.store.initialize()

    def new_store(self, now=NOW):
        store = PostgresQueryStore(self.dsn, now_provider=lambda: now)
        self.addCleanup(store.close)
        return store

    def app(self, store):
        with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
            return create_app(result_settings(self.test_url))

    def assert_storage_only(self, accepted, source):
        self.assertEqual(source, accepted["result"])
        receipt = accepted["receipt"]
        self.assertEqual(ReportedResearchResult.from_document(source).content_hash, receipt["content_hash"])
        self.assertEqual(NOW.isoformat(), receipt["accepted_at"])
        self.assertEqual(9 * 3600, datetime.fromisoformat(receipt["accepted_at"]).utcoffset().total_seconds())
        self.assertIs(type(receipt["accepted_at"]), str)
        self.assertIs(type(accepted["result"]["payload"]["research_report"]["fold_reports"][0]["net_pnl_won"]), int)
        self.assertTrue(receipt["storage_only"])
        self.assertFalse(receipt["scientific_verified"])
        self.assertFalse(receipt["order_authorized"])

    def test_wire_first_receipt_retry_owner_conflict_restart(self):
        import psycopg

        calls, connects, commits = [], [], []
        class Cursor(psycopg.Cursor):
            def execute(cursor, query, *args, **kwargs):
                calls.append(str(query))
                return super().execute(query, *args, **kwargs)
        class Connection(psycopg.Connection):
            def commit(connection):
                commits.append(True)
                return super().commit()
        def connect():
            connects.append(True)
            return Connection.connect(self.dsn, cursor_factory=Cursor)
        source = result_document()
        with wire_server(self.app(self.store)) as (base, _):
            client = CentralContentClient(base, RESULT_TOKEN)
            with patch.object(self.store, "_connect", connect):
                accepted = client.accept_research_result(source, expected_owner="processor_a")
            self.assertEqual((1, 1), (len(connects), len(commits)))
            self.assertEqual(1, sum(query.startswith("INSERT INTO central_documents") for query in calls))
            self.assertEqual(1, sum(query.startswith("SELECT document_json FROM central_documents") for query in calls))
            self.assertIn("SET TRANSACTION ISOLATION LEVEL READ COMMITTED", calls)
            self.assert_storage_only(accepted, source)
            self.assertEqual("processor_a", accepted["receipt"]["owner"])
            self.assertEqual(accepted, client.accept_research_result(source, expected_owner="processor_a"))
            self.assertEqual(accepted, client.load_research_result(source["result_id"], expected_owner="processor_a"))
            with self.assertRaises(CentralContentHttpError) as error:
                CentralContentClient(base, OTHER_TOKEN).load_research_result(source["result_id"], expected_owner="processor_b")
            self.assertEqual(404, error.exception.status_code)
            other = CentralContentClient(base, OTHER_TOKEN).accept_research_result(source, expected_owner="processor_b")
            self.assertEqual("processor_b", other["receipt"]["owner"])
            changed = deepcopy(source); changed["payload"]["performance"]["status"] = "FAILED"
            with self.assertRaises(CentralContentHttpError) as error:
                client.accept_research_result(changed, expected_owner="processor_a")
            self.assertEqual(409, error.exception.status_code)
        restarted = self.new_store(NOW.replace(hour=14))
        with wire_server(self.app(restarted)) as (base, _):
            client = CentralContentClient(base, RESULT_TOKEN)
            self.assertEqual(accepted, client.load_research_result(source["result_id"], expected_owner="processor_a"))
            self.assertEqual(accepted, client.accept_research_result(source, expected_owner="processor_a"))

    def test_read_committed_parallel_noop_and_conflicting_winner(self):
        import psycopg

        for changed in (False, True):
            with self.subTest(conflicting_loser=changed):
                ready, release, insert_started, insert_finished = (threading.Event() for _ in range(4))
                loser_pid, isolation = [], []
                class PausedCommit(psycopg.Connection):
                    def commit(connection):
                        ready.set()
                        if not release.wait(10):
                            raise AssertionError("winner commit release was lost")
                        return super().commit()
                class LoserCursor(psycopg.Cursor):
                    def execute(cursor, query, *args, **kwargs):
                        text = str(query)
                        if text.startswith("INSERT INTO central_documents"):
                            insert_started.set()
                        result = super().execute(query, *args, **kwargs)
                        if text == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED":
                            isolation.append(cursor.connection.execute("SHOW transaction_isolation").fetchone()[0])
                        if text.startswith("INSERT INTO central_documents"):
                            insert_finished.set()
                        return result
                def loser_connect():
                    connection = psycopg.connect(self.dsn, cursor_factory=LoserCursor)
                    loser_pid.append(connection.info.backend_pid)
                    return connection
                peer = self.new_store(NOW.replace(hour=14))
                source = result_document("parallel-conflict" if changed else "parallel-noop")
                losing_source = deepcopy(source)
                if changed:
                    losing_source["payload"]["performance"]["status"] = "FAILED"
                with patch.object(self.store, "_connect", lambda: PausedCommit.connect(self.dsn)), \
                     patch.object(peer, "_connect", loser_connect), ThreadPoolExecutor(max_workers=2) as workers:
                    winner = workers.submit(self.store.accept_research_result, "processor_a", source)
                    loser = None
                    try:
                        self.assertTrue(ready.wait(5))
                        loser = workers.submit(peer.accept_research_result, "processor_a", losing_source)
                        self.assertTrue(insert_started.wait(5))
                        deadline = time.monotonic() + 5
                        blocked = False
                        with psycopg.connect(self.test_url, autocommit=True) as observer:
                            while time.monotonic() < deadline:
                                row = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (loser_pid[0],)).fetchone()
                                if row and row[0] == "Lock":
                                    blocked = True
                                    break
                                time.sleep(0.01)
                        self.assertTrue(blocked, "loser INSERT did not actually wait for the native winner")
                        self.assertFalse(insert_finished.is_set())
                        self.assertEqual(["read committed"], isolation)
                    finally:
                        release.set()
                    accepted = winner.result(timeout=10)
                    if changed:
                        with self.assertRaises(ResearchResultConflict):
                            loser.result(timeout=10)
                    else:
                        self.assertEqual(accepted, loser.result(timeout=10))
                self.assert_storage_only(accepted, source)
                self.assertEqual(accepted, self.store.load_document(REPORTED_RESEARCH_RESULT_COLLECTION, "processor_a", source["result_id"])["document"])

    def test_wire_commit_ack_loss_returns_same_receipt_after_reconnect(self):
        import psycopg

        class LostCommitAck(psycopg.Connection):
            def commit(connection):
                super().commit()
                connection.close()
                raise OSError("fixture commit ACK lost after durable commit")
        source = result_document("commit-ack-loss")
        with wire_server(self.app(self.store)) as (base, _):
            client = CentralContentClient(base, RESULT_TOKEN)
            with patch.object(self.store, "_connect", lambda: LostCommitAck.connect(self.dsn)):
                with self.assertRaises(CentralContentHttpError) as error:
                    client.accept_research_result(source, expected_owner="processor_a")
            self.assertEqual(500, error.exception.status_code)
            original = client.load_research_result(source["result_id"], expected_owner="processor_a")
            self.assert_storage_only(original, source)
        restarted = self.new_store(NOW.replace(hour=14))
        with wire_server(self.app(restarted)) as (base, _):
            client = CentralContentClient(base, RESULT_TOKEN)
            self.assertEqual(original, client.load_research_result(source["result_id"], expected_owner="processor_a"))
            self.assertEqual(original, client.accept_research_result(source, expected_owner="processor_a"))
            changed = deepcopy(source); changed["payload"]["performance"]["status"] = "FAILED"
            with self.assertRaises(CentralContentHttpError) as error:
                client.accept_research_result(changed, expected_owner="processor_a")
            self.assertEqual(409, error.exception.status_code)

    def test_native_precommit_failure_rolls_back_and_retry_preserves_bytes(self):
        import psycopg

        failure = OSError("fixture commit failed before durable commit")
        class BeforeCommitFailure(psycopg.Connection):
            def commit(connection):
                raise failure
        source = result_document("precommit-failure")
        with patch.object(self.store, "_connect", lambda: BeforeCommitFailure.connect(self.dsn)):
            with self.assertRaises(OSError) as error:
                self.store.accept_research_result("processor_a", source)
        self.assertIs(failure, error.exception)
        self.assertIsNone(self.store.load_document(REPORTED_RESEARCH_RESULT_COLLECTION, "processor_a", source["result_id"]))
        accepted = self.store.accept_research_result("processor_a", source)
        self.assert_storage_only(accepted, source)
        self.assertEqual(accepted, self.store.accept_research_result("processor_a", source))
