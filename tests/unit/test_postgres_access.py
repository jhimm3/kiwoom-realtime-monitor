from __future__ import annotations

import json
import asyncio
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from kiwoom_monitor.central_server.database import (
    PostgresQueryStore, StoredQuery, _NEWS_JOB_CLAIM_SELECT_SQL,
    _NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL,
)
from kiwoom_monitor.central_server.diagnostic_metrics import (
    refresh_capture_state, summarize_db_calls, summarize_market_bar_saves,
)
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, db_call_source, observe_existing_transaction, open_observed_connection,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, parameters: object = ()) -> FakeCursor:
        self.connection.statements.append((sql.split()[0], parameters))
        if self.connection.fail_execute and len(self.connection.statements) == self.connection.fail_execute:
            raise ValueError("statement failed")
        return self

    def executemany(self, sql: str, parameters: object) -> FakeCursor:
        return self.execute(sql, parameters)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.connection.rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.connection.rows[0] if self.connection.rows else None


class FakeConnection:
    def __init__(self, *, fail_execute: int = 0, fail_commit: bool = False,
                 fail_rollback: bool = False) -> None:
        self.statements: list[tuple[str, object]] = []
        self.rows: list[tuple[object, ...]] = []
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.info = type("Info", (), {"backend_pid": 321})()

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise ConnectionError("commit response lost")

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.fail_rollback:
            raise ConnectionError("rollback response lost")

    def close(self) -> None:
        self.closes += 1


class PostgresAccessTests(unittest.TestCase):
    def test_realtime_minute_phases_keep_one_native_transaction(self) -> None:
        raw = self._native_context_connection()
        store = PostgresQueryStore("unused")
        value = {
            "trading_date": "2099-01-09", "minute": "10:00", "code": "DIAG",
            "market": "KRX", "open": 100, "high": 100, "low": 100,
            "close": 100, "volume": 3, "trade_value_million_won": 3,
            "updated_at": 1790000000.0, "operation_id": "minute-phase-test",
        }
        with patch.object(store, "_connect", return_value=raw):
            store.save_minute_bars([value])

        call = self._calls()[0]
        self.assertEqual((1, 0, 1, 5),
                         (raw.commits, raw.rollbacks, raw.closes, call["sql_calls"]))
        sample = summarize_market_bar_saves(time.time() - 30, time.time() + 1)[
            "writer_transactions"]["realtime_minute"]["call_samples"][0]
        self.assertEqual(call["call_id"], sample["db_call_id"])
        self.assertEqual({"replayed": 0, "query_complete": 0, "bar_upserts": 1,
                          "metadata_upserts": 0, "revision_inserts": 0,
                          "operation_inserts": 1}, sample["domain_counts"])
        self.assertEqual({"day_locks", "operation_lookup", "query_authority",
                          "bar_upsert", "operation_insert"},
                         set(sample["domain_phase_ms"]))

    def test_read_source_survives_thread_boundary_and_is_summarized(self) -> None:
        async def read() -> None:
            with db_call_source("top20.entry_minutes"):
                await asyncio.to_thread(run_read)

        def run_read() -> None:
            connection = open_observed_connection(
                FakeConnection,
                DBWriterContext("read.document_collection", "document:market_data_coverage_intraday",
                                "load_documents", access_mode="read"),
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            connection.commit()
            connection.close()

        asyncio.run(read())
        self.assertEqual("", DBWriterContext("read.test", "plain", "read").source)
        self.assertEqual("top20.entry_minutes", self._calls()[0]["source"])
        summary = summarize_db_calls(time.time() - 30, time.time() + 1)
        self.assertEqual(
            {"top20.entry_minutes": 1},
            summary["readers"]["read.document_collection/document:market_data_coverage_intraday"]["sources"],
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = Path(self.directory.name) / "controls.json"
        self.environment = patch.dict("os.environ", {
            "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.control),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self._capture("first")

    def tearDown(self) -> None:
        self.control.unlink(missing_ok=True)
        refresh_capture_state(force=True)

    def _capture(self, session: str) -> None:
        self.control.write_text(json.dumps({
            "schema": 1, "instance_id": instance_id(),
            "diagnostic_tool": {"expires_at": time.time() + 60, "session_id": session},
            "capture": {"expires_at": time.time() + 60},
        }), encoding="utf-8")
        refresh_capture_state(force=True)

    def _calls(self) -> list[dict[str, object]]:
        return summarize_db_calls(time.time() - 30, time.time() + 1,
                                  mode="raw")["calls"]

    def _native_context_connection(self, *, fail_commit: bool = False,
                                   fail_rollback: bool = False) -> FakeConnection:
        try:
            import psycopg
        except ImportError:
            self.skipTest("Psycopg is required for native context behavior")

        class NativeContextConnection(FakeConnection):
            __exit__ = psycopg.Connection.__exit__
            _pool = None

            def __enter__(self) -> NativeContextConnection:
                return self

            @property
            def closed(self) -> bool:
                return self.closes > 0

        return NativeContextConnection(fail_commit=fail_commit,
                                       fail_rollback=fail_rollback)

    def test_caller_owned_transactions_keep_connection_and_nested_savepoint_independent(self) -> None:
        class TransactionConnection(FakeConnection):
            def __init__(self) -> None:
                super().__init__()
                self.depth = 0
                self.savepoint_rollbacks = 0

            def transaction(self):
                owner = self

                class Transaction:
                    def __enter__(self):
                        owner.depth += 1
                        self.nested = owner.depth > 1
                        return self

                    def __exit__(self, exc_type, exc, traceback):
                        owner.depth -= 1
                        if self.nested:
                            owner.savepoint_rollbacks += int(exc is not None)
                        elif exc is None:
                            owner.commit()
                        else:
                            owner.rollback()
                        return False

                return Transaction()

        raw = TransactionConnection()
        for index in range(2):
            with observe_existing_transaction(raw, DBWriterContext(
                "maintenance.prepared_news", "prepared_news_completion_batch",
                "complete_batch", rows_attempted=index + 1,
            )) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                try:
                    with connection.transaction():
                        raise ValueError("article deferred")
                except ValueError:
                    pass
        self.assertEqual((2, 0, 0, 2, 0),
                         (raw.commits, raw.rollbacks, raw.closes,
                          raw.savepoint_rollbacks, raw.depth))
        calls = [call for call in self._calls()
                 if call["writer_kind"] == "prepared_news_completion_batch"]
        self.assertEqual(2, len(calls))
        self.assertEqual(2, len({call["call_id"] for call in calls}))
        self.assertEqual([1, 2], [call["rows_attempted"] for call in calls])
        self.assertTrue(all(call["transactions"] == 1 and call["commits"] == 1
                            and call["connection_acquire_ms"] is None
                            and call["sql_calls"] == 1 for call in calls))

        with self.assertRaisesRegex(RuntimeError, "batch failed"):
            with observe_existing_transaction(raw, DBWriterContext(
                "maintenance.prepared_news", "prepared_news_completion_batch",
                "complete_batch", rows_attempted=1,
            )) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 2")
                raise RuntimeError("batch failed")
        self.assertEqual((2, 1, 0, 0),
                         (raw.commits, raw.rollbacks, raw.closes, raw.depth))
        rollback_call = self._calls()[-1]
        self.assertEqual(("rolled_back", 0, 1, "RuntimeError"),
                         (rollback_call["outcome"], rollback_call["commits"],
                          rollback_call["rollbacks"],
                          rollback_call["errors"][0]["exception_type"]))

    def test_cache_pilot_preserves_sql_commit_and_close_with_correlated_legacy_sample(self) -> None:
        raw = FakeConnection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            store.save_query("cache-key", "ka10081", time.time() + 60,
                             StoredQuery({"rows": [1]}, False, ""))

        self.assertEqual(["INSERT", "DELETE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual("committed", call["outcome"])
        self.assertEqual("rest.query_cache", call["writer_family"])
        self.assertEqual((1, 1, 2, 321), (call["transactions"], call["commits"],
                                           call["sql_calls"], call["backend_pid"]))
        legacy = summarize_market_bar_saves(time.time() - 30, time.time() + 1)
        self.assertEqual(1, legacy["writer_transactions"]["query_cache"]["commits"])
        self.assertEqual(call["call_id"], legacy["writer_transactions"]["query_cache"]
                         ["call_samples"][0]["db_call_id"])
        self.assertEqual(1, summarize_db_calls(time.time() - 30, time.time() + 1)
                         ["writers"]["rest.query_cache/query_cache"]["commits"])

    def test_query_cache_reader_keeps_native_context_and_separates_metrics(self) -> None:
        raw_hit = self._native_context_connection()
        raw_miss = self._native_context_connection()
        raw_failure = self._native_context_connection()
        raw_hit.rows = [(json.dumps({"rows": [1]}), False, "")]
        raw_failure.fail_execute = 1
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", side_effect=[raw_hit, raw_miss, raw_failure]):
            self.assertEqual(StoredQuery({"rows": [1]}, False, ""), store.load_query("hit"))
            self.assertIsNone(store.load_query("miss"))
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.load_query("failure")

        self.assertEqual((1, 0, 1), (raw_hit.commits, raw_hit.rollbacks, raw_hit.closes))
        self.assertEqual((1, 0, 1), (raw_miss.commits, raw_miss.rollbacks, raw_miss.closes))
        self.assertEqual((0, 1, 1), (raw_failure.commits, raw_failure.rollbacks,
                                    raw_failure.closes))
        summary = summarize_db_calls(time.time() - 30, time.time() + 1, mode="raw")
        calls = summary["calls"]
        self.assertEqual(3, len(calls))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["writer_family"] == "read.query_cache"
                            and call["writer_kind"] == "query_cache"
                            and call["sql_calls"] == 1 for call in calls))
        self.assertEqual(["committed", "committed", "rolled_back"],
                         [call["outcome"] for call in calls])
        self.assertEqual({}, summary["writers"])
        reader = summary["readers"]["read.query_cache/query_cache"]
        self.assertEqual((3, 2, 1), (reader["calls"], reader["commits"], reader["rollbacks"]))

    def test_document_collection_reader_keeps_native_context_and_kind_metrics(self) -> None:
        raw_hit = self._native_context_connection()
        raw_miss = self._native_context_connection()
        raw_hit.rows = [("owner", "key", 123.0, json.dumps({"value": 1}))]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", side_effect=[raw_hit, raw_miss]):
            self.assertEqual([{"owner": "owner", "key": "key", "updated_at": 123.0,
                               "document": {"value": 1}}],
                             store.load_documents("fixture_collection", "owner", 10))
            self.assertEqual([], store.load_documents("fixture_collection", "missing", 10))

        self.assertEqual((1, 0, 1), (raw_hit.commits, raw_hit.rollbacks, raw_hit.closes))
        self.assertEqual((1, 0, 1), (raw_miss.commits, raw_miss.rollbacks, raw_miss.closes))
        summary = summarize_db_calls(time.time() - 30, time.time() + 1, mode="raw")
        calls = summary["calls"]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["writer_family"] == "read.document_collection"
                            and call["writer_kind"] == "document:fixture_collection"
                            and call["operation"] == "load_documents"
                            and call["outcome"] == "committed"
                            and call["commits"] == 1
                            and call["sql_calls"] == 1 for call in calls))
        self.assertEqual({}, summary["writers"])
        self.assertEqual(2, summary["readers"]["read.document_collection/document:fixture_collection"]
                         ["calls"])

    def test_market_bar_readers_keep_native_context_and_separate_kinds(self) -> None:
        raw_minute = self._native_context_connection()
        raw_daily = self._native_context_connection()
        raw_missing_minute = self._native_context_connection()
        raw_missing_daily = self._native_context_connection()
        raw_minute.rows = [("2099-01-09", "10:00", "DIAG", "KRX", 100, 110, 90,
                            105, 10, 1, 1790000000.0)]
        raw_daily.rows = [("2099-01-09", "DIAG", "KRX", 100, 110, 90, 105,
                           10, 1, 1790000000.0)]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", side_effect=[raw_minute, raw_daily,
                                                             raw_missing_minute,
                                                             raw_missing_daily]):
            self.assertEqual("10:00", store.load_minute_bars("DIAG", "2099-01-09", "KRX")[0]["minute"])
            self.assertEqual(105, store.load_daily_bars("DIAG", "KRX", 10)[0]["close"])
            self.assertEqual([], store.load_minute_bars("MISSING", "2099-01-09", "KRX"))
            self.assertEqual([], store.load_daily_bars("MISSING", "KRX", 10))

        self.assertTrue(all((connection.commits, connection.rollbacks, connection.closes) == (1, 0, 1)
                            for connection in (raw_minute, raw_daily,
                                               raw_missing_minute, raw_missing_daily)))
        summary = summarize_db_calls(time.time() - 30, time.time() + 1, mode="raw")
        calls = summary["calls"]
        self.assertEqual(4, len(calls))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["writer_family"] == "read.market_bars"
                            and call["outcome"] == "committed"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["sql_calls"] == 1 for call in calls))
        self.assertEqual({}, summary["writers"])
        readers = summary["readers"]
        self.assertEqual(2, readers["read.market_bars/minute_bar"]["calls"])
        self.assertEqual(2, readers["read.market_bars/daily_bar"]["calls"])

    def test_execute_error_preserves_original_exception_and_implicit_discard(self) -> None:
        raw = FakeConnection(fail_execute=2)
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.save_query("cache-key", "ka10081", time.time() + 60,
                                 StoredQuery({}, False, ""))
        self.assertEqual((0, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual("closed_uncommitted", call["outcome"])
        self.assertEqual(0, call["rollbacks"])
        self.assertEqual([{"stage": "execute", "exception_type": "ValueError"}], call["errors"])

    def test_lost_commit_ack_remains_unknown_after_close(self) -> None:
        raw = FakeConnection(fail_commit=True)
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ConnectionError, "commit response lost"):
                store.save_query("cache-key", "ka10081", time.time() + 60,
                                 StoredQuery({}, False, ""))
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual("unknown", self._calls()[0]["outcome"])
        self.assertEqual(0, self._calls()[0]["commits"])

    def test_first_statement_failure_does_not_invent_started_transaction(self) -> None:
        raw = FakeConnection(fail_execute=1)
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.save_query("cache-key", "ka10081", time.time() + 60,
                                 StoredQuery({}, False, ""))
        call = self._calls()[0]
        self.assertIsNone(call["transactions"])
        self.assertEqual("closed_transaction_unknown", call["outcome"])
        writer = summarize_db_calls(time.time() - 30, time.time() + 1)
        self.assertEqual(1, writer["writers"]["rest.query_cache/query_cache"]
                         ["transactions_unavailable_calls"])

    def test_connect_failure_records_no_transaction_without_hiding_error(self) -> None:
        context = DBWriterContext("rest.query_cache", "query_cache", "save_query")
        with self.assertRaisesRegex(OSError, "unavailable"):
            open_observed_connection(lambda: (_ for _ in ()).throw(OSError("unavailable")), context)
        call = self._calls()[0]
        self.assertEqual(("connect_error", 0, None),
                         (call["outcome"], call["transactions"], call["backend_pid"]))

    def test_unregistered_and_session_replacement_are_explicit(self) -> None:
        raw = FakeConnection()
        connection = open_observed_connection(lambda: raw, None)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO fixture VALUES (1)")
        connection.commit()
        connection.close()
        self.assertEqual("UNREGISTERED", self._calls()[0]["writer_family"])
        self.assertEqual(1, summarize_db_calls(time.time() - 30, time.time() + 1)
                         ["unregistered_calls"])

        late = open_observed_connection(lambda: FakeConnection(),
                                        DBWriterContext("rest.query_cache", "query_cache", "late"))
        self._capture("second")
        late.cursor().execute("INSERT INTO fixture VALUES (1)")
        late.commit()
        late.close()
        self.assertEqual([], self._calls())

    def test_observer_failure_does_not_change_commit_and_explicit_rollback_is_counted(self) -> None:
        class BrokenHook:
            def stage_started(self, *args: object) -> None:
                raise RuntimeError("observer failed")

            def stage_finished(self, *args: object) -> None:
                raise RuntimeError("observer failed")

            def call_finished(self, *args: object) -> None:
                raise RuntimeError("observer failed")

        raw = FakeConnection()
        connection = open_observed_connection(
            lambda: raw,
            DBWriterContext("rest.query_cache", "query_cache", "rollback_probe"),
            hook=BrokenHook(),
        )
        with connection.cursor() as cursor:
            self.assertIs(cursor, cursor.execute("SELECT 1"))
        connection.rollback()
        connection.close()
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual("rolled_back", call["outcome"])
        self.assertEqual(1, call["rollbacks"])
        self.assertTrue(all(error["stage"] == "observer" for error in call["errors"]))

    def test_capture_off_keeps_storage_semantics_and_reports_no_db_calls(self) -> None:
        self.control.unlink()
        refresh_capture_state(force=True)
        raw = FakeConnection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            store.save_query("cache-key", "ka10081", time.time() + 60,
                             StoredQuery({}, False, ""))
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual([], self._calls())

    def test_broken_capture_control_does_not_prevent_cache_write(self) -> None:
        raw = FakeConnection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw), \
                patch("kiwoom_monitor.central_server.diagnostic_metrics.capture_session_token",
                      side_effect=OSError("diagnostic unavailable")):
            store.save_query("cache-key", "ka10081", time.time() + 60,
                             StoredQuery({}, False, ""))
        self.assertEqual(["INSERT", "DELETE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))

    def test_broken_legacy_metric_does_not_report_committed_cache_write_as_failed(self) -> None:
        raw = FakeConnection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw), \
                patch("kiwoom_monitor.central_server.diagnostic_metrics.record_writer_transaction",
                      side_effect=OSError("diagnostic unavailable")):
            store.save_query("cache-key", "ka10081", time.time() + 60,
                             StoredQuery({}, False, ""))
        self.assertEqual(["INSERT", "DELETE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))

    def test_native_context_success_measures_driver_commit_and_close(self) -> None:
        raw = self._native_context_connection()
        with open_observed_connection(
            lambda: raw, DBWriterContext("test", "native_context", "success"),
        ) as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO fixture VALUES (1)")
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("committed", 1, 0),
                         (call["outcome"], call["commits"], call["rollbacks"]))
        self.assertIsNotNone(call["commit_ms"])
        self.assertIsNotNone(call["close_ms"])

    def test_native_context_body_failure_measures_driver_rollback(self) -> None:
        raw = self._native_context_connection()
        with self.assertRaisesRegex(ValueError, "body failed"):
            with open_observed_connection(
                lambda: raw, DBWriterContext("test", "native_context", "body_failure"),
            ) as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO fixture VALUES (1)")
                raise ValueError("body failed")
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("rolled_back", 0, 1),
                         (call["outcome"], call["commits"], call["rollbacks"]))
        self.assertIn({"stage": "body", "exception_type": "ValueError"}, call["errors"])

    def test_native_context_rollback_failure_preserves_body_error(self) -> None:
        raw = self._native_context_connection(fail_rollback=True)
        with self.assertLogs("psycopg", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "body failed"):
                with open_observed_connection(
                    lambda: raw, DBWriterContext("test", "native_context", "rollback_failure"),
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("INSERT INTO fixture VALUES (1)")
                    raise ValueError("body failed")
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("unknown", 0, 0),
                         (call["outcome"], call["commits"], call["rollbacks"]))
        self.assertEqual("ConnectionError", call["errors"][0]["exception_type"])
        self.assertIsNotNone(call["rollback_ms"])
        self.assertIsNotNone(call["close_ms"])

    def test_native_context_commit_failure_keeps_driver_connection_lifetime(self) -> None:
        raw = self._native_context_connection(fail_commit=True)
        with self.assertRaisesRegex(ConnectionError, "commit response lost"):
            with open_observed_connection(
                lambda: raw, DBWriterContext("test", "native_context", "commit_failure"),
            ) as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO fixture VALUES (1)")
        self.assertEqual((1, 0, 0), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("unknown", 0, 0),
                         (call["outcome"], call["commits"], call["rollbacks"]))
        self.assertIsNone(call["close_ms"])

    def test_news_job_finish_uses_native_context_without_changing_commit_boundary(self) -> None:
        raw = self._native_context_connection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            store.finish_news_job("job-1", "body-ref")
        self.assertEqual(["UPDATE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.job_finish", "news_job_finish", "committed", 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["commits"]))

    def test_shadow_evaluation_and_checkpoint_keep_two_native_connections(self) -> None:
        evaluation_connection = self._native_context_connection()
        checkpoint_connection = self._native_context_connection()
        store = PostgresQueryStore("unused")
        decision = {"decision_id": "decision-1", "decided_at": "2099-01-09T00:00:00+00:00"}
        candidate = {"event_id": "event-1", "available_at": "2099-01-09T00:00:00+00:00"}
        started = time.time() - 1
        with patch.object(store, "_connect", side_effect=[evaluation_connection, checkpoint_connection]):
            store.save_shadow_evaluation("monitor", decision, candidate, "2099-01-09T00:01:00+00:00")
            store.save_shadow_monitor_state("monitor", {"cursor": 1})

        self.assertEqual(["SELECT", "INSERT", "SELECT", "INSERT"],
                         [statement[0] for statement in evaluation_connection.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in checkpoint_connection.statements])
        for connection in (evaluation_connection, checkpoint_connection):
            self.assertEqual((1, 0, 1), (connection.commits, connection.rollbacks, connection.closes))
        calls = self._calls()
        self.assertEqual(2, len(calls))
        self.assertEqual(2, len({call["call_id"] for call in calls}))
        self.assertEqual([("candidate.shadow_evaluation", "shadow_evaluation", 2, 4),
                          ("candidate.shadow_checkpoint", "shadow_monitor_state", 1, 1)], [
            (call["writer_family"], call["writer_kind"], call["rows_attempted"], call["sql_calls"])
            for call in calls
        ])
        writer = summarize_market_bar_saves(
            started, time.time() + 1,
        )["writer_transactions"]["shadow_monitor_state"]
        self.assertEqual(len(json.dumps({"cursor": 1}).encode("utf-8")),
                         writer["payload_bytes_estimated"])
        self.assertGreaterEqual(writer["encode_ms"]["median"], 0)
        self.assertEqual(calls[1]["call_id"], writer["call_samples"][0]["db_call_id"])

    def test_news_ai_job_enqueue_keeps_revision_reads_and_insert_in_one_commit(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("revision-1", "content-hash")]
        store = PostgresQueryStore("unused")
        value = {"stock_code": "005930", "identity": "article-1",
                  "processing_version": "ai-v1", "payload": {"event": {}}}
        wakes_after_commits = []
        store.set_news_job_wakeup(lambda: wakes_after_commits.append(raw.commits))
        with patch.object(store, "_connect", return_value=raw):
            self.assertEqual(1, store.enqueue_news_ai_jobs([value]))
        self.assertEqual([1], wakes_after_commits)
        self.assertEqual(["SELECT", "SELECT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.job_enqueue", "news_ai_job_enqueue", "committed",
                          3, 1, 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["rows_attempted"], call["commits"]))

    def test_news_ai_job_enqueue_second_candidate_failure_rolls_back_first(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("revision-1", "content-hash")]
        raw.fail_execute = 4
        store = PostgresQueryStore("unused")
        value = {"stock_code": "005930", "identity": "article-1",
                  "processing_version": "ai-v1", "payload": {"event": {}}}
        wakes = []
        store.set_news_job_wakeup(lambda: wakes.append(True))
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.enqueue_news_ai_jobs([value, value])
        self.assertEqual([], wakes)
        self.assertEqual(["SELECT", "SELECT", "INSERT", "SELECT"],
                         [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("rolled_back", 4, 2, 0, 1),
                         (call["outcome"], call["sql_calls"], call["rows_attempted"],
                          call["commits"], call["rollbacks"]))

    def test_empty_news_source_page_still_commits_run_and_cursor(self) -> None:
        raw = self._native_context_connection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            self.assertEqual({"raw_count": 0, "unique_count": 0, "duplicate_count": 0},
                             store.save_news_source_page({
                                 "source_id": "naver-query:empty", "query_text": "증권",
                                 "scope": "query_set", "items": [],
                             }))
        self.assertEqual(["LOCK", "INSERT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.source_page", "news_source:query_set", "committed",
                          3, 0, 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["rows_attempted"], call["commits"]))

    def test_news_source_page_run_failure_preserves_native_rollback(self) -> None:
        raw = self._native_context_connection()
        raw.fail_execute = 2
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.save_news_source_page({
                    "source_id": "naver-stock:flash:empty", "query_text": "flash",
                    "scope": "naver_stock_market", "items": [],
                })
        self.assertEqual(["LOCK", "INSERT"], [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news_source:naver_stock_market", "rolled_back", 0, 1),
                         (call["writer_kind"], call["outcome"], call["rows_attempted"],
                          call["rollbacks"]))

    def test_news_job_finish_execute_failure_still_rolls_back_once(self) -> None:
        raw = self._native_context_connection()
        raw.fail_execute = 1
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.finish_news_job("job-1", "body-ref")
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.job_finish", "rolled_back", 1),
                         (call["writer_family"], call["outcome"], call["rollbacks"]))

    def test_news_job_retry_preserves_queries_state_rule_and_commit_boundary(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [(2,)]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            store.retry_news_job("job-1", "temporary", 123.0, "body-ref")
        self.assertEqual(["SELECT", "UPDATE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("PENDING", "temporary", 123.0, "body-ref"),
                         raw.statements[1][1][:4])
        call = self._calls()[0]
        self.assertEqual(("news.job_retry", "news_job_retry", "retry_news_job",
                          "committed", 2, 1, 1),
                         (call["writer_family"], call["writer_kind"], call["operation"],
                          call["outcome"], call["sql_calls"], call["commits"],
                          call["rows_attempted"]))

    def test_news_job_retry_update_failure_preserves_native_rollback(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [(3,)]
        raw.fail_execute = 2
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.retry_news_job("job-1", "temporary", 123.0)
        self.assertEqual(["SELECT", "UPDATE"], [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.job_retry", "rolled_back", 0, 1),
                         (call["writer_family"], call["outcome"],
                          call["commits"], call["rollbacks"]))

    def test_news_job_claim_empty_result_still_commits_recovery_statement(self) -> None:
        raw = self._native_context_connection()
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            self.assertEqual([], store.claim_news_jobs(now=300.0))
        self.assertEqual(["UPDATE", "SELECT"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.job_claim", "news_job_claim", "committed", 2, 1, None),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["commits"], call["rows_attempted"]))
        legacy = summarize_market_bar_saves(time.time() - 30, time.time() + 1)
        samples = legacy["writer_transactions"]["news_job_claim"]["call_samples"]
        self.assertTrue(any(sample["db_call_id"] == call["call_id"] for sample in samples))

    def test_news_job_claim_updates_selected_row_in_same_context(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("job-1", "revision-1", "005930", "005930", "BODY",
                     "hash", "version", 0, {}, 100.0)]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            jobs = store.claim_news_jobs(now=300.0)
        self.assertEqual(["job-1"], [job["job_key"] for job in jobs])
        self.assertEqual(1, jobs[0]["attempts"])
        self.assertEqual(["UPDATE", "SELECT", "UPDATE"],
                         [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("committed", 3, 1),
                         (self._calls()[0]["outcome"], self._calls()[0]["sql_calls"],
                          self._calls()[0]["commits"]))

    def test_news_job_claim_select_failure_preserves_native_rollback(self) -> None:
        raw = self._native_context_connection()
        raw.fail_execute = 2
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.claim_news_jobs(now=300.0)
        self.assertEqual(["UPDATE", "SELECT"], [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("rolled_back", 0, 1),
                         (self._calls()[0]["outcome"], self._calls()[0]["commits"],
                          self._calls()[0]["rollbacks"]))

    def test_news_job_claim_plan_is_read_only_and_uses_claim_sql(self) -> None:
        raw = self._native_context_connection()
        raw.sql_statements = []

        class DiagnosticCursor(FakeCursor):
            def execute(self, sql: str, parameters: object = ()) -> DiagnosticCursor:
                raw.sql_statements.append(sql)
                result = super().execute(sql, parameters)
                if sql.startswith("SELECT current_database"):
                    self.connection.rows = [("diagnostic_test", "PostgreSQL 16")]
                elif sql.startswith("EXPLAIN"):
                    self.connection.rows = [([{"Plan": {
                        "Node Type": "Index Scan", "Relation Name": "central_news_jobs",
                        "Index Name": "idx_news_jobs_ready", "Plan Rows": 7,
                        "Plan Width": 120, "Filter": "fixed safe diagnostic predicate",
                        "Plans": [{"Node Type": "Seq Scan", "Parent Relationship": "SubPlan",
                                   "Subplan Name": "SubPlan 1",
                                   "Relation Name": "central_news_article_revisions",
                                   "Filter": "scope predicate", "Plan Rows": 9,
                                   "Plan Width": 32}],
                    }}],)]
                return result

        class DiagnosticConnection(type(raw)):
            def cursor(self) -> DiagnosticCursor:
                return DiagnosticCursor(self)

        # Retain psycopg's native context-manager boundary while replacing only
        # the cursor with deterministic planner responses.
        diagnostic_connection = DiagnosticConnection()
        diagnostic_connection.sql_statements = raw.sql_statements
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=diagnostic_connection):
            result = store.explain_news_job_claim_plan()

        self.assertEqual("read_only_explain_without_analyze", result["mode"])
        self.assertEqual("diagnostic_test", result["database"])
        self.assertEqual(3, len(result["plans"]))
        self.assertEqual(2, len(result["candidate_plans"]))
        self.assertEqual("Index Scan", result["plans"][0]["nodes"][0]["Node Type"])
        self.assertEqual("fixed safe diagnostic predicate",
                         result["plans"][0]["nodes"][0]["Filter"])
        subplan = result["plans"][0]["nodes"][1]
        self.assertEqual((0, 1, "SubPlan 1", "central_news_article_revisions"),
                         (subplan["parent_node"], subplan["depth"], subplan["Subplan Name"],
                          subplan["Relation Name"]))
        self.assertEqual("SET", diagnostic_connection.sql_statements[0].split()[0])
        explain_sql = [sql for sql in diagnostic_connection.sql_statements
                       if sql.startswith("EXPLAIN")]
        self.assertEqual(5, len(explain_sql))
        self.assertTrue(all("ANALYZE" not in sql.upper() for sql in explain_sql))
        self.assertTrue(any(sql.startswith("EXPLAIN (FORMAT JSON) "
                                    "SELECT job_key,article_revision_id") for sql in explain_sql))
        self.assertEqual("BODY", result["candidate_plans"][0]["preferred_stage"])
        self.assertEqual("RULE", result["candidate_plans"][1]["preferred_stage"])
        self.assertIn("AND central_news_jobs.stage IN ('BODY','RULE')", explain_sql[2])
        self.assertIn("AND (stage NOT IN ('BODY','RULE')", explain_sql[1])

    def test_news_job_claim_read_only_analyze_reports_actual_work_without_locking(self) -> None:
        raw = self._native_context_connection()
        statements: list[str] = []

        class DiagnosticCursor(FakeCursor):
            def execute(self, sql: str, parameters: object = ()) -> DiagnosticCursor:
                statements.append(sql)
                super().execute(sql, parameters)
                if sql.startswith("EXPLAIN"):
                    self.connection.rows = [([{
                        "Execution Time": 12.5, "Planning Time": 0.4,
                        "Plan": {
                            "Node Type": "Limit", "Actual Rows": 1,
                            "Shared Hit Blocks": 20,
                            "Plans": [{"Node Type": "Seq Scan",
                                       "Relation Name": "central_news_article_revisions",
                                       "Actual Rows": 42, "Actual Loops": 1,
                                       "Shared Read Blocks": 5}],
                    }}],)]
                return self

        class DiagnosticConnection(type(raw)):
            def cursor(self) -> DiagnosticCursor:
                return DiagnosticCursor(self)

        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=DiagnosticConnection()):
            result = store.analyze_news_job_claim_read_only("BODY")
        self.assertEqual("read_only_analyze_without_row_lock", result["mode"])
        self.assertEqual(12.5, result["execution_ms"])
        self.assertEqual(42, result["nodes"][1]["Actual Rows"])
        self.assertEqual(5, result["nodes"][1]["Shared Read Blocks"])
        self.assertEqual("SET TRANSACTION READ ONLY", statements[0])
        self.assertEqual("SET LOCAL statement_timeout TO '2000ms'", statements[1])
        self.assertIn("EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)", statements[2])
        self.assertNotIn("FOR UPDATE", statements[2])
        self.assertIn("LIMIT %s", statements[2])
        with self.assertRaises(ValueError):
            store.analyze_news_job_claim_read_only("AI")

    def test_news_job_claim_diagnostic_candidate_preserves_exclusion_and_priority(self) -> None:
        jobs = (
            ("current-body", "current", "BODY", 90.0),
            ("current-rule", "current", "RULE", 80.0),
            ("historical-body", "historical", "BODY", 100.0),
            ("historical-rule", "historical", "RULE", 100.0),
            ("historical-ai", "historical", "AI", 60.0),
            ("missing-article-body", "missing", "BODY", 70.0),
        )
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(
                "CREATE TABLE central_news_jobs (job_key TEXT,article_revision_id TEXT,"
                "stock_code TEXT,target_id TEXT,stage TEXT NOT NULL,input_hash TEXT,"
                "processing_version TEXT,attempts INTEGER,payload_json TEXT,updated_at REAL,"
                "state TEXT,next_retry_at REAL)"
            )
            connection.execute(
                "CREATE TABLE central_news_article_revisions "
                "(article_revision_id TEXT UNIQUE,collection_scope TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO central_news_article_revisions VALUES(?,?)",
                (("current", "query_set"), ("historical", "historical_backfill")),
            )
            connection.executemany(
                "INSERT INTO central_news_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ((key, article, "005930", "005930", stage, "hash", "version", 0,
                  "{}", updated, "PENDING", 0.0)
                 for key, article, stage, updated in jobs),
            )
            for preferred_stage in ("BODY", "RULE"):
                parameters = (200.0, preferred_stage, preferred_stage, "", "", "", "", "", "", 10)
                results = []
                for statement in (_NEWS_JOB_CLAIM_SELECT_SQL,
                                  _NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL):
                    read_only_sql = statement.replace(
                        "FOR UPDATE SKIP LOCKED LIMIT %s", "LIMIT %s"
                    ).replace("%s", "?")
                    results.append([row[0] for row in connection.execute(read_only_sql, parameters)])
                self.assertEqual(results[0], results[1])
                self.assertEqual({"current-body", "current-rule", "historical-ai",
                                  "missing-article-body"}, set(results[0]))
                self.assertEqual(f"current-{preferred_stage.lower()}", results[0][0])

    def test_external_news_claim_empty_result_commits_read_transaction(self) -> None:
        raw = self._native_context_connection()
        raw.info.backend_pid = 0
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_search"))
        self.assertEqual(["SELECT"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.external_claim", "news_external_claim", "pc_search",
                          "committed", 1, 1, None),
                         (call["writer_family"], call["writer_kind"], call["source"],
                          call["outcome"], call["sql_calls"], call["commits"],
                          call["rows_attempted"]))

    def test_external_news_claim_updates_selected_row_in_same_transaction(self) -> None:
        raw = self._native_context_connection()
        raw.info.backend_pid = 0
        raw.rows = [("job-1", "revision-1", "005930", "005930", "BODY",
                     "hash", "version", 0, {}, 100.0)]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            claimed = store.claim_external_historical_news_job("BODY", scope="pc_search")
        self.assertEqual(("job-1", 1), (claimed["job_key"], claimed["attempts"]))
        self.assertEqual(["SELECT", "UPDATE"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("committed", 2, 1),
                         (self._calls()[0]["outcome"], self._calls()[0]["sql_calls"],
                          self._calls()[0]["commits"]))

    def test_external_news_claim_update_error_rolls_back_once(self) -> None:
        raw = self._native_context_connection()
        raw.info.backend_pid = 0
        raw.rows = [("job-1", "revision-1", "005930", "005930", "BODY",
                     "hash", "version", 0, {}, 100.0)]
        raw.fail_execute = 2
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.claim_external_historical_news_job("BODY", scope="pc_search")
        self.assertEqual(["SELECT", "UPDATE"], [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("rolled_back", 0, 1),
                         (self._calls()[0]["outcome"], self._calls()[0]["commits"],
                          self._calls()[0]["rollbacks"]))

    def test_external_news_finish_uses_one_native_transaction_per_stage(self) -> None:
        store = PostgresQueryStore("unused")

        def complete(cursor: FakeCursor, value: dict[str, object], *, postgres: bool) -> dict[str, str]:
            self.assertTrue(postgres)
            cursor.execute("UPDATE central_news_jobs SET state='COMPLETED' WHERE job_key=%s",
                           (value["job_key"],))
            return {"state": "completed", "output_ref": "revision-1"}

        for stage in ("BODY", "RULE"):
            with self.subTest(stage=stage):
                raw = self._native_context_connection()
                with patch.object(store, "_connect", return_value=raw), patch(
                    "kiwoom_monitor.central_server.database._complete_external_news_job",
                    side_effect=complete,
                ):
                    result = store.complete_external_historical_news_job(
                        {"job_key": "job-1", "stage": stage})
                self.assertEqual("completed", result["state"])
                self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
                call = self._calls()[-1]
                self.assertEqual(
                    ("news.external_finish", f"news_external_finish:{stage}",
                     "committed", 1, 1, 1),
                    (call["writer_family"], call["writer_kind"], call["outcome"],
                     call["sql_calls"], call["commits"], call["rows_attempted"]),
                )

        raw = self._native_context_connection()
        raw.fail_execute = 1
        with patch.object(store, "_connect", return_value=raw), patch(
            "kiwoom_monitor.central_server.database._complete_external_news_job",
            side_effect=complete,
        ):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.complete_external_historical_news_job(
                    {"job_key": "job-1", "stage": "RULE"})
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[-1]
        self.assertEqual(("news_external_finish:RULE", "rolled_back", 0, 1),
                         (call["writer_kind"], call["outcome"], call["commits"],
                          call["rollbacks"]))

    def test_real_account_writers_keep_independent_native_transactions(self) -> None:
        store = PostgresQueryStore("unused")
        for name, helper in (("event", "_save_real_account_event"),
                             ("recovery", "_save_real_account_recovery")):
            with self.subTest(name=name):
                raw = self._native_context_connection()
                with patch.object(store, "_connect", return_value=raw), patch(
                    f"kiwoom_monitor.central_server.database.{helper}", return_value={"stored": True},
                ):
                    if name == "event":
                        result = store.save_real_account_event("binding", "order_execution", "event", "time",
                                                               settings_revision=1)
                    else:
                        result = store.save_real_account_recovery("binding", "recovery", "time",
                                                                  settings_revision=1)
                self.assertEqual({"stored": True}, result)
                self.assertEqual([("SELECT", ("credential-activation",))], raw.statements)
                self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
                call = self._calls()[-1]
                self.assertEqual(
                    ("account.real_monitor", f"real_account_{name}",
                     f"save_real_account_{name}", "committed", 1, 1),
                    (call["writer_family"], call["writer_kind"], call["operation"],
                     call["outcome"], call["commits"], call["rows_attempted"]),
                )

        raw = self._native_context_connection()
        with patch.object(store, "_connect", return_value=raw), patch(
            "kiwoom_monitor.central_server.database._save_real_account_event",
            side_effect=ValueError("stale binding"),
        ):
            with self.assertRaisesRegex(ValueError, "stale binding"):
                store.save_real_account_event("binding", "order_execution", "event", "time",
                                              settings_revision=1)
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("real_account_event", "rolled_back", 0, 1),
                         tuple(self._calls()[-1][key] for key in
                               ("writer_kind", "outcome", "commits", "rollbacks")))

    def test_account_settings_writers_keep_native_lock_and_independent_commits(self) -> None:
        store = PostgresQueryStore("unused")
        for kind, method, helper in (
            ("account_settings_save", "save_account_settings", "_save_account_settings"),
            ("market_profile_settings_save", "save_market_profile_settings",
             "_save_market_profile_settings"),
        ):
            with self.subTest(kind=kind):
                raw = self._native_context_connection()
                with patch.object(store, "_connect", return_value=raw), patch(
                    f"kiwoom_monitor.central_server.database.{helper}", return_value={"revision": 1},
                ) as saved:
                    self.assertEqual({"revision": 1}, getattr(store, method)(
                        {"some": "value"}, expected_revision=0))
                saved.assert_called_once()
                self.assertEqual([("SELECT", ("credential-activation",))], raw.statements)
                self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
                call = self._calls()[-1]
                self.assertEqual(("account.settings", kind, method, "committed", 1, 1),
                                 tuple(call[key] for key in
                                       ("writer_family", "writer_kind", "operation",
                                        "outcome", "commits", "rows_attempted")))

        raw = self._native_context_connection()
        with patch.object(store, "_connect", return_value=raw), patch(
            "kiwoom_monitor.central_server.database._save_market_profile_settings",
            side_effect=ValueError("MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"),
        ):
            with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"):
                store.save_market_profile_settings({"some": "value"}, expected_revision=0)
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("market_profile_settings_save", "rolled_back", 0, 1),
                         tuple(self._calls()[-1][key] for key in
                               ("writer_kind", "outcome", "commits", "rollbacks")))

    def test_news_body_revision_and_rule_job_share_native_transaction(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("005930", {"stock_name": "삼성전자"}, "news_article")]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            revision_id = store.save_news_body_revision({
                "article_revision_id": "article-1", "status": "fulltext",
                "body_text": "본문", "fetched_at": 100.0,
            })
        self.assertEqual(["INSERT", "SELECT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual(revision_id, raw.statements[0][1][0])
        self.assertEqual(revision_id,
                         json.loads(raw.statements[2][1][12])["body_revision_id"])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.body", "news_body", "committed", 3, 1, 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["commits"], call["rows_attempted"]))
        legacy = summarize_market_bar_saves(time.time() - 30, time.time() + 1)
        samples = legacy["writer_transactions"]["news_body"]["call_samples"]
        self.assertTrue(any(sample["db_call_id"] == call["call_id"] for sample in samples))

    def test_news_body_rule_job_insert_error_rolls_back_revision(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("005930", {"stock_name": "삼성전자"}, "news_article")]
        raw.fail_execute = 3
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.save_news_body_revision({
                    "article_revision_id": "article-1", "status": "fulltext",
                    "body_text": "본문", "fetched_at": 100.0,
                })
        self.assertEqual(["INSERT", "SELECT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(("rolled_back", 0, 1),
                         (self._calls()[0]["outcome"], self._calls()[0]["commits"],
                          self._calls()[0]["rollbacks"]))

    def test_news_ai_projection_revision_and_usage_share_native_transaction(self) -> None:
        raw = self._native_context_connection()
        store = PostgresQueryStore("unused")
        documents = [{"owner": "005930", "key": "article-1",
                      "document": {"summary": "요약"}}]
        revisions = [{"analysis_revision_id": "analysis-1", "target_id": "005930",
                      "article_revision_id": "article-1", "body_revision_id": "body-1",
                      "provider": "gemini", "model": "model", "prompt_version": "prompt-v1",
                      "input_hash": "hash", "output": {"summary": "요약"},
                      "usage": {"total_tokens": 5}}]
        usage = [{"owner": "2026-09-27", "key": "request-1",
                  "document": {"total_tokens": 5}}]
        with patch.object(store, "_connect", return_value=raw):
            store.save_news_ai_results(documents, revisions, usage)
        self.assertEqual(["INSERT", "INSERT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual("news_ai", raw.statements[0][1][0][0])
        self.assertEqual("analysis-1", raw.statements[1][1][0])
        self.assertEqual("news_request_usage", raw.statements[2][1][0][0])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.ai_results", "news_ai_results", "committed", 3, 3, 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["rows_attempted"], call["commits"]))

    def test_news_ai_usage_insert_error_rolls_back_all_three_stages(self) -> None:
        raw = self._native_context_connection()
        raw.fail_execute = 3
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.save_news_ai_results(
                    [{"owner": "005930", "key": "article-1", "document": {"summary": "요약"}}],
                    [{"target_id": "005930", "article_revision_id": "article-1",
                      "provider": "gemini", "model": "model", "prompt_version": "prompt-v1",
                      "input_hash": "hash", "output": {"summary": "요약"}}],
                    [{"owner": "2026-09-27", "key": "request-1",
                      "document": {"total_tokens": 5}}],
                )
        self.assertEqual(["INSERT", "INSERT", "INSERT"],
                         [item[0] for item in raw.statements])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("rolled_back", 3, 0, 1),
                         (call["outcome"], call["sql_calls"], call["commits"],
                          call["rollbacks"]))

    def test_news_event_replay_keeps_table_lock_and_single_native_commit(self) -> None:
        raw = self._native_context_connection()
        raw.rows = [("existing-event-revision",)]
        store = PostgresQueryStore("unused")
        with patch.object(store, "_connect", return_value=raw):
            revision_id = store.save_news_event_revision({
                "stock_code": "005930", "article_revision_id": "article-1",
                "body_revision_id": "body-1", "rule_version": "rule-v1",
                "input_hash": "hash",
            })
        self.assertEqual("existing-event-revision", revision_id)
        self.assertEqual(["LOCK", "SELECT"], [item[0] for item in raw.statements])
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = self._calls()[0]
        self.assertEqual(("news.event", "news_event", "committed", 2, 1, 1),
                         (call["writer_family"], call["writer_kind"], call["outcome"],
                          call["sql_calls"], call["rows_attempted"], call["commits"]))

    def test_registered_document_kinds_use_observed_context_only(self) -> None:
        store = PostgresQueryStore("unused")
        observed = self._native_context_connection()
        observed_krx = self._native_context_connection()
        observed_roll = self._native_context_connection()
        observed_catalog = self._native_context_connection()
        observed_comparison = self._native_context_connection()
        observed_nxt = self._native_context_connection()
        observed_fundamentals = self._native_context_connection()
        raw = self._native_context_connection()
        with patch.object(store, "_connect",
                          side_effect=[observed, observed_krx, observed_roll,
                                       observed_catalog, observed_comparison,
                                       observed_nxt, observed_fundamentals, raw]):
            store.upsert_documents("news_sync", [{
                "owner": "005930", "key": "latest", "document": {"checked_at": "2026-09-27"},
            }])
            store.upsert_documents("krx_trading_day_observations", [{
                "owner": "2026-09-28", "key": "latest",
                "document": {"trading_date": "2026-09-28", "source": "kiwoom_websocket_0s"},
            }])
            store.upsert_documents("external_market_roll_state", [{
                "owner": "WTI_FUTURES", "key": "current",
                "document": {"active_contract": "CLV26.NYM", "confirmation_count": 1},
            }])
            store.upsert_documents("stock_catalog", [{
                "owner": "krx", "key": "005930", "document": {"name": "fixture"},
            }])
            store.upsert_documents("external_market_collection_status", [{
                "owner": "diagnostic", "key": "current", "document": {"status": "ok"},
            }])
            store.upsert_documents("minute_trade_value_comparisons", [{
                "owner": "2026-09-27:005930", "key": "10:01",
                "document": {"query_scope": "KRX+NXT", "difference_million_won": 10},
            }])
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": True, "observed_at": "2026-09-27T10:00:00+09:00"},
            }])
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {"observed_at": "2026-09-27T10:00:00+09:00", "payload": {}},
            }])
        self.assertEqual((1, 0, 1), (observed.commits, observed.rollbacks, observed.closes))
        self.assertEqual((1, 0, 1),
                         (observed_krx.commits, observed_krx.rollbacks, observed_krx.closes))
        self.assertEqual((1, 0, 1),
                         (observed_roll.commits, observed_roll.rollbacks, observed_roll.closes))
        self.assertEqual((1, 0, 1),
                         (observed_catalog.commits, observed_catalog.rollbacks, observed_catalog.closes))
        self.assertEqual((1, 0, 1),
                         (observed_comparison.commits,
                          observed_comparison.rollbacks, observed_comparison.closes))
        self.assertEqual((1, 0, 1),
                         (observed_nxt.commits, observed_nxt.rollbacks, observed_nxt.closes))
        self.assertEqual((1, 0, 1),
                         (observed_fundamentals.commits,
                          observed_fundamentals.rollbacks, observed_fundamentals.closes))
        self.assertEqual((1, 0, 1), (raw.commits, raw.rollbacks, raw.closes))
        self.assertEqual(["INSERT"], [statement[0] for statement in observed.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in observed_krx.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in observed_roll.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in observed_catalog.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in observed_comparison.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in observed_nxt.statements])
        self.assertEqual(["INSERT"],
                         [statement[0] for statement in observed_fundamentals.statements])
        self.assertEqual(["INSERT"], [statement[0] for statement in raw.statements])
        calls = {call["writer_kind"]: call for call in self._calls()
                 if call["writer_kind"] in {
                     "document:news_sync", "document:krx_trading_day_observations",
                     "document:external_market_roll_state", "document:stock_catalog",
                     "document:minute_trade_value_comparisons",
                     "document:stock_nxt_eligibility",
                     "document:stock_fundamentals",
                 }}
        self.assertEqual({"document:news_sync", "document:krx_trading_day_observations",
                          "document:external_market_roll_state", "document:stock_catalog",
                          "document:minute_trade_value_comparisons",
                          "document:stock_nxt_eligibility",
                          "document:stock_fundamentals"}, set(calls))
        for call in calls.values():
            self.assertEqual(("document.collection", "committed", 1, 1),
                             (call["writer_family"], call["outcome"],
                              call["sql_calls"], call["commits"]))
        legacy = summarize_market_bar_saves(time.time() - 30, time.time() + 1)
        for kind, call in calls.items():
            samples = legacy["writer_transactions"][kind]["call_samples"]
            self.assertTrue(any(sample["db_call_id"] == call["call_id"] for sample in samples))

    def test_news_sync_document_execute_failure_rolls_back(self) -> None:
        store = PostgresQueryStore("unused")
        raw = self._native_context_connection()
        raw.fail_execute = 1
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.upsert_documents("news_sync", [{
                    "owner": "005930", "key": "latest", "document": {"checked_at": "2026-09-27"},
                }])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = next(call for call in self._calls() if call["writer_kind"] == "document:news_sync")
        self.assertEqual(("rolled_back", 1), (call["outcome"], call["rollbacks"]))

    def test_krx_trading_day_document_execute_failure_rolls_back(self) -> None:
        store = PostgresQueryStore("unused")
        raw = self._native_context_connection()
        raw.fail_execute = 1
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.upsert_documents("krx_trading_day_observations", [{
                    "owner": "2026-09-28", "key": "latest",
                    "document": {"trading_date": "2026-09-28"},
                }])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = next(
            call for call in self._calls()
            if call["writer_kind"] == "document:krx_trading_day_observations"
        )
        self.assertEqual(("rolled_back", 1), (call["outcome"], call["rollbacks"]))

    def test_external_market_roll_state_execute_failure_rolls_back(self) -> None:
        store = PostgresQueryStore("unused")
        raw = self._native_context_connection()
        raw.fail_execute = 1
        with patch.object(store, "_connect", return_value=raw):
            with self.assertRaisesRegex(ValueError, "statement failed"):
                store.upsert_documents("external_market_roll_state", [{
                    "owner": "WTI_FUTURES", "key": "current",
                    "document": {"active_contract": "CLV26.NYM"},
                }])
        self.assertEqual((0, 1, 1), (raw.commits, raw.rollbacks, raw.closes))
        call = next(
            call for call in self._calls()
            if call["writer_kind"] == "document:external_market_roll_state"
        )
        self.assertEqual(("rolled_back", 1), (call["outcome"], call["rollbacks"]))

    def test_two_observed_calls_do_not_share_connection_or_call_id(self) -> None:
        barrier = Barrier(2, timeout=5)

        def write() -> tuple[FakeConnection, str]:
            raw = FakeConnection()
            connection = open_observed_connection(
                lambda: raw, DBWriterContext("rest.query_cache", "query_cache", "parallel_probe"),
            )
            barrier.wait()
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO fixture VALUES (1)")
            connection.commit()
            connection.close()
            return raw, connection.call_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=10) for future in
                       (executor.submit(write), executor.submit(write))]
        self.assertIsNot(results[0][0], results[1][0])
        self.assertEqual(2, len({result[1] for result in results}))
        self.assertEqual(2, len(self._calls()))


if __name__ == "__main__":
    unittest.main()
