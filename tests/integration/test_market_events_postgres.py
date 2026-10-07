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


if __name__ == "__main__":
    unittest.main()
