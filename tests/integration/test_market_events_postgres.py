"""VI service concurrency contracts on the dedicated PostgreSQL diagnostic DB."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_metrics import summarize_db_calls
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from tests.integration.test_storage_boundary_postgres import _writer_metrics_capture


class _StatementFailureCursor:
    """Fail an actual statement after an INSERT, so native exit must rollback it."""

    def __init__(self, cursor, gate):
        self.cursor, self.gate = cursor, gate

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *error):
        return self.cursor.__exit__(*error)

    def execute(self, *args, **kwargs):
        result = self.cursor.execute(*args, **kwargs)
        self.gate.wait()
        self.cursor.execute("SELECT 1 / 0")
        return result


class _HeldWriter:
    """Hold only the first real writer connection; all peers use native psycopg."""

    def __init__(self, connect, mode="commit"):
        self.connect, self.mode = connect, mode
        self.entered, self.release = threading.Event(), threading.Event()
        self.connections = []
        self.lock = threading.Lock()

    def wait(self):
        self.entered.set()
        if not self.release.wait(30):
            raise TimeoutError("test did not release the held VI transaction")

    def __call__(self):
        connection = self.connect()
        with self.lock:
            first = not self.connections
            self.connections.append(connection)
        if first and self.mode == "statement_error":
            cursor = connection.cursor
            connection.cursor = lambda *args, **kwargs: _StatementFailureCursor(
                cursor(*args, **kwargs), self,
            )
        elif first:
            commit = connection.commit

            def held_commit():
                self.wait()
                commit()
                if self.mode == "ack_loss":
                    import psycopg
                    connection.close()
                    raise psycopg.OperationalError("injected loss of VI COMMIT acknowledgement")

            connection.commit = held_commit
        return connection

    def close(self):
        self.release.set()
        for connection in self.connections:
            connection.close()


class MarketEventPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated PostgreSQL diagnostic DB not configured")
        if urlsplit(url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing VI concurrency tests outside the dedicated DB")
        cls.store = PostgresQueryStore(url)
        with cls.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), to_regclass('central_vi_event_revisions')")
            database, table = cursor.fetchone()
            if database != "kiwoom_monitor_diagnostic_test" or table is None:
                raise RuntimeError("dedicated PostgreSQL VI test schema is unavailable")

    def setUp(self):
        self.connect = self.store._connect
        self.codes, self.tasks, self.gates = [], [], []
        self.enterContext(_writer_metrics_capture())
        self.started = time.time()
        now = datetime(2026, 10, 1, 9, 15, tzinfo=timezone(timedelta(hours=9)))
        self.service = MarketEventService(SimpleNamespace(), RealtimeHub(), self.store,
                                          now_provider=lambda: now)

    def tearDown(self):
        # Release failed fixtures before cleanup can wait on their uncommitted rows.
        for gate in self.gates:
            gate.close()
        if self.codes:
            with self.connect() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM central_vi_event_revisions WHERE stock_code=ANY(%s)",
                               (self.codes,))
                cursor.execute("DELETE FROM central_upper_limit_fact_revisions WHERE stock_code=ANY(%s)",
                               (self.codes,))
                cursor.execute("DELETE FROM central_hot_cohort_current WHERE stock_code=ANY(%s)", (self.codes,))
                cursor.execute("DELETE FROM central_hot_cohort_revisions WHERE stock_code=ANY(%s)", (self.codes,))
            self.assertEqual([], self.rows())

    def held_writer(self, mode="commit"):
        gate = _HeldWriter(self.connect, mode)
        self.gates.append(gate)
        self.addCleanup(gate.close)
        return gate

    def code(self):
        code = "DIAG-" + uuid.uuid4().hex
        self.codes.append(code)
        return code

    async def send(self, *codes):
        message = {"trnm": "REAL", "data": [{"type": "1h", "values": {
            "9001": "A" + code, "9068": "1", "1225": "1", "1221": "+75000",
            "1223": "091501", "1490": "1", "9069": "+", "9081": "KRX",
        }} for code in codes]}
        before = set(self.service._background)
        self.assertTrue(await self.service.handle_ws_message(message, None))
        task, = self.service._background - before
        self.tasks.append(task)
        return task

    async def held(self, gate):
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(gate.entered.wait, 30), 35))

    async def finish(self, gate):
        gate.release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), 35)
        self.assertFalse(self.service._vi_inflight)
        return outcomes

    def rows(self):
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT event_id,event_key,stock_code,document_json "
                           "FROM central_vi_event_revisions WHERE stock_code=ANY(%s) "
                           "ORDER BY stock_code", (self.codes,))
            return cursor.fetchall()

    def calls(self):
        return [call for call in summarize_db_calls(self.started, time.time(), mode="raw")["calls"]
                if call["writer_kind"] == "market_event:vi" and call["access_mode"] == "write"]

    def test_duplicates_share_commit_peer_stays_independent_and_later_replay_reaches_db(self):
        owner_code, peer_code = self.code(), self.code()
        gate = self.held_writer()

        async def run():
            with patch.object(self.store, "_connect", side_effect=gate):
                try:
                    await self.send(owner_code, owner_code)
                    await self.held(gate)
                    await self.send(owner_code)
                    await asyncio.sleep(0)
                    self.assertEqual(1, len(gate.connections))
                    peer = await self.send(peer_code)
                    await asyncio.wait_for(asyncio.shield(peer), 30)
                    self.assertFalse(gate.release.is_set())
                    self.assertEqual([peer_code], [row[2] for row in self.rows()])
                    self.assertEqual(2, len(gate.connections))
                finally:
                    outcomes = await self.finish(gate)
                self.assertEqual([None, None, None], outcomes)
                before = self.rows()
                replay = await self.send(owner_code)
                await asyncio.wait_for(asyncio.shield(replay), 30)
                self.assertEqual(before, self.rows())
                self.assertEqual(3, len(gate.connections))
                await self.service.close()

        asyncio.run(run())
        calls = self.calls()
        self.assertEqual(3, len(calls))
        self.assertEqual(3, sum(call["commits"] for call in calls))
        self.assertTrue(all(call["transactions"] == 1 and call["sql_calls"] == 1 for call in calls))
        self.assertEqual(3, len({call["call_id"] for call in calls}))
        self.assertEqual(3, len({call["backend_pid"] for call in calls}))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))

    def test_failed_statement_rolls_back_batch_and_waiting_duplicate_retries_all_keys(self):
        codes = [self.code(), self.code()]
        gate = self.held_writer("statement_error")

        async def run():
            with patch.object(self.store, "_connect", side_effect=gate):
                try:
                    await self.send(*codes)
                    await self.held(gate)
                    self.assertEqual([], self.rows())
                    await self.send(*reversed(codes))
                    await asyncio.sleep(0)
                    self.assertEqual(1, len(gate.connections))
                finally:
                    outcomes = await self.finish(gate)
                import psycopg
                self.assertIsInstance(outcomes[0], psycopg.errors.DivisionByZero)
                self.assertIsNone(outcomes[1])
                self.assertEqual(2, len(gate.connections))

        asyncio.run(run())
        self.assertEqual(sorted(codes), [row[2] for row in self.rows()])
        calls = self.calls()
        self.assertEqual(2, len(calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertEqual(1, sum(call["commits"] for call in calls))
        self.assertTrue(all(call["rows_attempted"] == 2 for call in calls))

    def test_cancelled_owner_and_shutdown_wait_for_actual_commit(self):
        code = self.code()
        gate = self.held_writer()

        async def run():
            with patch.object(self.store, "_connect", side_effect=gate):
                closing = None
                try:
                    owner = await self.send(code)
                    await self.held(gate)
                    owner.cancel()
                    await asyncio.sleep(0)
                    self.assertTrue(self.service._vi_inflight)
                    await self.send(code)
                    closing = asyncio.create_task(self.service.close())
                    await asyncio.sleep(0)
                    self.assertFalse(closing.done())
                    self.assertEqual([], self.rows())
                finally:
                    outcomes = await self.finish(gate)
                    if closing is not None:
                        await asyncio.wait_for(closing, 30)
                self.assertIsInstance(outcomes[0], asyncio.CancelledError)
                self.assertIsNone(outcomes[1])
                self.assertEqual(1, len(gate.connections))

        asyncio.run(run())
        self.assertEqual([code], [row[2] for row in self.rows()])
        self.assertEqual(1, len(self.calls()))
        self.assertEqual(1, self.calls()[0]["commits"])

    def test_lost_commit_ack_retries_without_duplicate_history(self):
        code = self.code()
        gate = self.held_writer("ack_loss")

        async def run():
            with patch.object(self.store, "_connect", side_effect=gate):
                try:
                    await self.send(code)
                    await self.held(gate)
                    await self.send(code)
                    await asyncio.sleep(0)
                    self.assertEqual(1, len(gate.connections))
                finally:
                    outcomes = await self.finish(gate)
                import psycopg
                self.assertIsInstance(outcomes[0], psycopg.OperationalError)
                self.assertIsNone(outcomes[1])
                self.assertEqual(2, len(gate.connections))

        asyncio.run(run())
        self.assertEqual([code], [row[2] for row in self.rows()])
        calls = self.calls()
        self.assertEqual(2, len(calls))
        self.assertEqual({"unknown", "committed"}, {call["outcome"] for call in calls})
        self.assertEqual(1, sum(call["commits"] for call in calls))
        self.assertTrue(all(call["transactions"] == 1 for call in calls))

    def fact_rows(self):
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT fact_key,document_json FROM central_upper_limit_fact_revisions "
                           "WHERE stock_code=ANY(%s) ORDER BY fact_key", (self.codes,))
            return cursor.fetchall()

    def fact_calls(self):
        return [call for call in summarize_db_calls(self.started, time.time(), mode="raw")["calls"]
                if call["writer_kind"] == "market_event:upper_limit" and call["access_mode"] == "write"]

    def below_limit_tick(self, code):
        self.service._upper_limits[code] = 130000
        return TradeTick(code, 120000, 1, 1, 1, 120000, "091501", 29.97)

    def test_upper_fact_duplicate_ack_and_cancelled_close_keep_vi_peer_independent(self):
        code, peer = self.code(), self.code()
        tick = self.below_limit_tick(code)
        gate = self.held_writer()
        async def run():
            with patch.object(self.store, "_connect", side_effect=gate):
                waiter = asyncio.create_task(self.service.observe_trade(tick))
                closing = duplicate = None
                try:
                    await self.held(gate)
                    duplicate = asyncio.create_task(self.service.observe_trade(tick))
                    await asyncio.sleep(.01)
                    self.assertEqual(1, len(gate.connections))
                    self.assertEqual([], self.fact_rows())
                    vi = await self.send(peer)
                    await asyncio.wait_for(vi, 10)
                    self.assertFalse(waiter.done())
                    self.assertEqual([peer], [row[2] for row in self.rows()])
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    closing = asyncio.create_task(self.service.close())
                    await asyncio.sleep(.01)
                    closing.cancel()
                    await asyncio.sleep(.01)
                    closing.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(closing.done())
                    self.assertEqual(1, len(self.service._pending_facts))
                finally:
                    gate.release.set()
                    if duplicate is not None:
                        await asyncio.wait_for(duplicate, 30)
                    if closing is not None:
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(closing, 30)
                    await self.service.close()
        asyncio.run(run())
        self.assertEqual(1, len(self.fact_rows()))
        self.assertEqual(1, len(self.fact_calls()))
        self.assertEqual(1, self.fact_calls()[0]["commits"])
        self.assertEqual(2, len({call["backend_pid"] for call in self.fact_calls() + self.calls()}))

    def _upper_fact_retry(self, mode):
        code = self.code()
        tick = self.below_limit_tick(code)
        gate = self.held_writer(mode)
        documents = []
        append = self.store.append_upper_limit_facts
        def save(values):
            import copy
            documents.append(copy.deepcopy(values))
            return append(values)
        async def run():
            with patch.object(self.store, "_connect", side_effect=gate), \
                    patch.object(self.store, "append_upper_limit_facts", side_effect=save):
                waiter = asyncio.create_task(self.service.observe_trade(tick))
                try:
                    await self.held(gate)
                    self.assertEqual([], self.fact_rows())
                finally:
                    gate.release.set()
                    await asyncio.wait_for(waiter, 30)
                    await self.service.close()
        asyncio.run(run())
        self.assertEqual(2, len(documents))
        self.assertEqual(documents[0], documents[1])
        self.assertEqual(1, len(self.fact_rows()))
        self.assertEqual(2, len(self.fact_calls()))
        self.assertEqual(1, self.service._fact_failures)
        self.assertEqual(1, sum(call["commits"] for call in self.fact_calls()))
        self.assertEqual(2, len({call["backend_pid"] for call in self.fact_calls()}))
        return self.fact_calls()

    def test_upper_fact_native_statement_rollback_retries_frozen_input(self):
        calls = self._upper_fact_retry("statement_error")
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))

    def test_upper_fact_actual_commit_ack_loss_retries_without_duplicate_history(self):
        calls = self._upper_fact_retry("ack_loss")
        self.assertEqual({"unknown", "committed"}, {call["outcome"] for call in calls})

    def test_metadata_ack_and_later_signal_preserve_cohort_current_and_history(self):
        code = self.code()
        gate = self.held_writer()
        native_record = self.store.record_hot_cohort_revision
        async def request(_api, _path, _body):
            return SimpleNamespace(payload={"nxtEnable": "Y", "upl_pric": "+130000"})
        self.service._broker = SimpleNamespace(request=request)
        self.service._selected = ("7", "condition")
        signal_time = self.service._now()
        def record(revision, current):
            if revision["event_type"] == "ELIGIBILITY":
                with patch.object(self.store, "_connect", side_effect=gate):
                    return native_record(revision, current)
            return native_record(revision, current)
        async def run():
            await self.service.record_condition_signal(code, "I", source="INITIAL")
            metadata = deleting = None
            with patch.object(self.store, "record_hot_cohort_revision", side_effect=record):
                try:
                    metadata = asyncio.create_task(self.service._load_metadata(code))
                    await self.held(gate)
                    started = asyncio.Event()
                    async def delete():
                        started.set()
                        await self.service.record_condition_signal(code, "D", source="REAL")
                    deleting = asyncio.create_task(delete())
                    await asyncio.wait_for(started.wait(), 5)
                    self.assertFalse(deleting.done())
                    self.assertEqual("I", self.service._cohort[code]["last_signal"])
                    self.service._now = lambda: signal_time + timedelta(days=1)
                finally:
                    gate.release.set()
                    if metadata is not None:
                        await asyncio.wait_for(metadata, 30)
                    if deleting is not None:
                        await asyncio.wait_for(deleting, 30)
                    await self.service.close()
        asyncio.run(run())
        current = next(row for row in self.store.load_hot_cohort(active_only=True) if row["stock_code"] == code)
        self.assertEqual("D", current["last_signal"])
        self.assertEqual(signal_time.timestamp(), current["last_signal_at"])
        self.assertTrue(current["nxt_eligible"])
        self.assertEqual("D", self.service._cohort[code]["last_signal"])
        history = self.store.load_market_event_history("cohort", code=code, limit=10)
        self.assertEqual({"ENTERED", "ELIGIBILITY", "D"}, {row["event_type"] for row in history})
        self.assertEqual(signal_time.date().isoformat(),
                         next(row for row in history if row["event_type"] == "D")["session_id"])

    def test_upper_fact_capture_preserves_history_and_native_call_identity(self):
        import json
        from pathlib import Path
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from tests.integration.test_recorded_workload_capture_postgres import _capture
        from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom
        code = self.code()
        tick = TradeTick(code, 130000, 1, 1, 1, 130000, "091501", 29.97)
        reports = []
        def rss():
            return next(int(line.split()[1]) * 1024 for line in
                        Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:"))
        async def run(service):
            service._upper_limits[code] = 130000
            await service.observe_trade(tick)
            await service.observe_trade(tick)
            await service.close()
        before = rss()
        started, cpu = time.perf_counter(), time.process_time()
        asyncio.run(run(self.service))
        reports.append({"payload_capture": False, "write_shape": "insert", "elapsed_ms": (time.perf_counter()-started)*1000,
                        "cpu_ms": (time.process_time()-cpu)*1000, "rss_before": before, "rss_after": rss()})
        initial = self.fact_rows()
        replacement = MarketEventService(SimpleNamespace(), RealtimeHub(), self.store, now_provider=self.service._now)
        with recorder_storage_headroom(), _capture(inputs=True) as session:
            before = rss()
            started, cpu = time.perf_counter(), time.process_time()
            asyncio.run(run(replacement))
            reports.append({"payload_capture": True, "write_shape": "conflict_noop", "elapsed_ms": (time.perf_counter()-started)*1000,
                            "cpu_ms": (time.process_time()-cpu)*1000, "rss_before": before, "rss_after": rss()})
            final = trace.stop()
            self.assertEqual("complete", final["state"])
            self.assertEqual(0, final["known_dropped"])
            self.assertEqual(0, final["input_rejected"])
            events = [json.loads(line) for part in final["chunks"]
                      for line in trace.chunk_bytes(session["trace_id"], part["name"]).splitlines()]
            starts = [row for row in events if row["event_type"] == "operation_start"]
            calls = [row for row in events if row["event_type"] == "call_start"]
            self.assertEqual(2, len(starts))
            self.assertEqual(2, len(calls))
            self.assertEqual({row["operation_id"] for row in starts},
                             {row["input_operation_id"] for row in calls})
            self.assertTrue(all(row["workload_id"] == "market_events" for row in starts))
            self.assertTrue(all(not row["cause_input_id"] for row in starts))
        self.assertEqual(initial, self.fact_rows())
        print(json.dumps({"upper_limit_capture_cost": {"scope": "native history/capture compatibility; different insert/no-op states, not an overhead comparison",
            "performance_comparable": False,
            "source_state_equivalent": False, "samples": reports}}))


if __name__ == "__main__":
    unittest.main()
