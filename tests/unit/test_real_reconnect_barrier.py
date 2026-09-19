from __future__ import annotations

import asyncio
import json
import threading
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from kiwoom_monitor.central_server.account_query import AccountQuerySessionManager
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from test_central_account_query import Broker, binding


class BlockingStore:
    def __init__(self):
        self.entered, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.values = []

    def save_realtime_snapshots(self, values):
        self.entered.set()
        try:
            if not self.release.wait(4):
                raise RuntimeError("fake gate timeout")
            self.values.extend(values)
        finally:
            self.finished.set()


class ReconnectSaveBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def until(self, predicate):
        for _ in range(400):
            if predicate(): return
            await asyncio.sleep(.005)
        self.fail("fake task did not reach expected boundary")

    async def test_cancelled_snapshot_waiter_close_waits_for_actual_store_completion(self):
        store = BlockingStore()
        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 10), store)
        collector._pending_snapshots[("trade", "005930")] = {"event_type": "trade", "item_key": "005930"}
        writing = asyncio.create_task(collector._flush_snapshots())
        closing = None
        try:
            await self.until(store.entered.is_set)
            writing.cancel()
            with self.assertRaises(asyncio.CancelledError): await writing
            closing = asyncio.create_task(collector.close())
            await asyncio.sleep(.025)
            self.assertFalse(closing.done(), "close must wait for the actual write, not only its cancelled waiter")
            store.release.set()
            await closing
            self.assertTrue(store.finished.is_set())
            self.assertEqual(1, len(store.values))
        finally:
            store.release.set()
            await self.until(store.finished.is_set)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)

    async def test_close_flushes_latest_arrival_after_older_owned_write(self):
        store = BlockingStore()
        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 10), store)
        collector._pending_snapshots[("trade", "005930")] = {"event_type": "trade", "item_key": "005930", "revision": 1}
        writing = asyncio.create_task(collector._flush_snapshots())
        try:
            await self.until(store.entered.is_set)
            collector._pending_snapshots[("trade", "005930")] = {"event_type": "trade", "item_key": "005930", "revision": 2}
            closing = asyncio.create_task(collector.close())
            store.release.set()
            await asyncio.gather(writing, closing)
            self.assertEqual([1, 2], [value["revision"] for value in store.values])
            self.assertEqual({}, collector._pending_snapshots)
        finally:
            store.release.set()
            await collector.close()

    async def test_cancelled_drain_waiter_still_waits_for_socket_exit(self):
        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 10))
        entered, release = asyncio.Event(), asyncio.Event()

        async def network():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                entered.set()
                await release.wait()

        old = collector._task = asyncio.create_task(network())
        await asyncio.sleep(0)
        pausing = asyncio.create_task(collector.begin_credential_change())
        try:
            await entered.wait()
            pausing.cancel()
            with self.assertRaises(asyncio.CancelledError): await pausing
            self.assertFalse(collector._credential_drain_task.done())
            await collector.start()
            self.assertIs(old, collector._task)
            release.set()
            await collector.begin_credential_change()
            self.assertIsNone(collector._task)
            self.assertEqual("PAUSED", collector.credential_connection_status()["phase"])
        finally:
            release.set()
            await collector.close()

    async def test_cancelled_resume_waiter_does_not_reuse_receipt_for_next_rotation(self):
        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 10))
        entered, release = asyncio.Event(), asyncio.Event()

        async def start():
            entered.set()
            await release.wait()

        with patch.object(collector, "start", new=AsyncMock(side_effect=start)) as restart:
            await collector.begin_credential_change()
            resuming = asyncio.create_task(collector.end_credential_change())
            try:
                await entered.wait()
                resuming.cancel()
                with self.assertRaises(asyncio.CancelledError): await resuming
                release.set()
                await self.until(lambda: collector._credential_resume_task is None)
                await collector.begin_credential_change()
                await collector.end_credential_change()
                self.assertEqual(2, restart.await_count)
                self.assertEqual(2, collector._connection_generation)
            finally:
                release.set()
                await collector.close()

    async def test_drain_waits_for_actual_token_thread_without_new_connection(self):
        entered, release = threading.Event(), threading.Event()

        def token():
            entered.set()
            if not release.wait(4): raise RuntimeError("fake token timeout")
            return "fake"

        hub = RealtimeHub()
        subscriber = hub.connect()
        hub.update_subscription(subscriber, ["005930"], [])
        collector = CentralRealtimeCollector(token, "real", hub, lambda: datetime(2026, 9, 15, 10))
        with patch("kiwoom_monitor.central_server.realtime_collector.connect") as connect:
            await collector.start()
            pausing = None
            try:
                await self.until(entered.is_set)
                pausing = asyncio.create_task(collector.begin_credential_change())
                await asyncio.sleep(.025)
                self.assertFalse(pausing.done())
                self.assertFalse(connect.called)
                release.set()
                await pausing
                self.assertEqual(set(), collector._token_tasks)
            finally:
                release.set()
                await collector.close()

    async def test_late_old_frame_is_discarded_and_pause_waits_for_socket_close(self):
        hub = RealtimeHub()
        subscriber = hub.connect()
        hub.update_subscription(subscriber, ["005930"], [])
        waiting, closing, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
        late = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {"20": "100030", "10": "100", "15": "3"}}]}

        class Socket:
            reads = 0
            async def send(self, raw): pass
            async def recv(self):
                self.reads += 1
                if self.reads == 1: return json.dumps({"return_code": 0})
                if self.reads == 2: return json.dumps({"trnm": "REG", "return_code": 0})
                waiting.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError: return json.dumps(late)
            async def __aenter__(self): return self
            async def __aexit__(self, *args):
                closing.set()
                await release_close.wait()

        collector = CentralRealtimeCollector(lambda: "fake", "real", hub, lambda: datetime(2026, 9, 15, 10))
        groups = {"1000": [{"item": ["005930"], "type": ["0B"]}]}
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=Socket()) as connect, \
                patch.object(collector, "_send_subscription", new=AsyncMock(return_value=groups)), \
                patch.object(collector, "_publish_parsed", wraps=collector._publish_parsed) as publish:
            await collector.start()
            try:
                await asyncio.wait_for(waiting.wait(), 2)
                pausing = asyncio.create_task(collector.begin_credential_change())
                await asyncio.wait_for(closing.wait(), 2)
                self.assertFalse(pausing.done())
                self.assertFalse(publish.called)
                release_close.set()
                await pausing
                self.assertEqual(1, connect.call_count)
                self.assertEqual(0, collector._abnormal_disconnects)
                self.assertEqual("PAUSED", collector._credential_phase)
            finally:
                release_close.set()
                await collector.close()

    async def test_same_source_first_registration_resets_gap_cumulative_baseline(self):
        hub = RealtimeHub()
        subscriber = hub.connect()
        hub.update_subscription(subscriber, ["005930"], [])
        current = datetime(2026, 9, 15, 10, 0, 30)
        collector = CentralRealtimeCollector(lambda: "fake", "real", hub, lambda: current)
        old = TradeTick("005930", 100, 100, None, 2, None, "100029", market="KRX")
        collector._minute_bars.add(old, current, 1)
        collector._second_trades.add(old, current, 1)
        collector._minute_bars.mark_capture_gap()
        collector._approved_trade_sources = {("005930", "KRX")}
        finished = False

        class Socket:
            reads = 0
            async def send(self, raw): pass
            async def recv(self):
                nonlocal finished
                self.reads += 1
                if self.reads == 1: return json.dumps({"return_code": 0})
                if self.reads == 2: return json.dumps({"trnm": "REG", "return_code": 0})
                finished = True
                return json.dumps({"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {"20": "100030", "10": "100", "13": "10000", "15": "3"}}]})
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        groups = {"1000": [{"item": ["005930"], "type": ["0B"]}]}
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=Socket()), \
                patch.object(collector, "_send_subscription", new=AsyncMock(return_value=groups)), \
                patch.object(collector, "_market_session", side_effect=lambda: None if finished else "KRX"):
            await collector._receive("KRX")
        self.assertEqual(5, collector._minute_bars.drain_dirty()[0]["volume"])
        self.assertEqual(5, sum(value["volume"] for value in collector._second_trades.drain_dirty()))
        self.assertEqual(current, collector._continuous_from[("005930", "KRX")])
        await collector.close()

    async def test_storage_failure_during_drain_preserves_retry_and_can_resume(self):
        class Store:
            def save_realtime_snapshots(self, values):
                raise OSError("fake storage failure")

        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 10), Store())
        saved = {"event_type": "trade", "item_key": "005930"}
        collector._pending_snapshots[("trade", "005930")] = saved
        with self.assertLogs("kiwoom_monitor.central_server.realtime_collector", level="ERROR"):
            await collector.begin_credential_change()
        self.assertEqual(saved, collector._pending_snapshots[("trade", "005930")])
        with patch.object(collector, "start", new=AsyncMock()):
            await collector.end_credential_change()
        self.assertFalse(collector._credential_paused)
        self.assertEqual(saved, collector._pending_snapshots[("trade", "005930")])
        collector._store = None
        await collector.close()

    async def test_drain_waits_for_accepted_market_close_action(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Events:
            async def close_krx_regular_session(self, session):
                entered.set()
                await release.wait()
                finished.set()

        collector = CentralRealtimeCollector(lambda: "fake", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 15, 15, 30), market_events=Events())
        await collector.start()
        pausing = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            pausing = asyncio.create_task(collector.begin_credential_change())
            await asyncio.sleep(.025)
            self.assertFalse(pausing.done())
            release.set()
            await pausing
            self.assertTrue(finished.is_set())
            self.assertIn("2026-09-15", collector._regular_close_notified)
        finally:
            release.set()
            await collector.close()


class AccountReconnectBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_old_query_drains_busy_rejects_and_commit_invalidates_cursor(self):
        current = binding("11111111-1111-1111-1111-111111111111")
        broker = Broker()
        entered, release = asyncio.Event(), asyncio.Event()
        original = broker.request

        async def request(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        broker.request = request
        manager = AccountQuerySessionManager(broker, lambda: current)
        old = asyncio.create_task(manager.query(api_id="kt00007", path="/api/dostk/acnt", body={}))
        await entered.wait()
        pausing = asyncio.create_task(manager.begin_credential_change())
        try:
            await asyncio.sleep(0)
            with self.assertRaisesRegex(RuntimeError, "ACCOUNT_QUERY_BUSY"):
                await manager.query(api_id="kt00007", path="/api/dostk/acnt", body={})
            self.assertFalse(pausing.done())
            with self.assertRaisesRegex(RuntimeError, "DRAIN_NOT_COMPLETE"):
                manager.end_credential_change(invalidate_cursors=True)
            release.set()
            page = await old
            await pausing
            manager.end_credential_change(invalidate_cursors=True)
            with self.assertRaisesRegex(ValueError, "CURSOR_EXPIRED"):
                await manager.query(api_id="kt00007", path="/api/dostk/acnt", body={},
                    batch_id=page["batch_id"], page_index=1, next_key=page["next_key"])
            self.assertEqual(1, len(broker.calls))
        finally:
            release.set()
            await manager.close()

    async def test_precommit_resume_preserves_old_cursor_and_shutdown_forbids_resume(self):
        broker = Broker()
        manager = AccountQuerySessionManager(broker,
            lambda: binding("11111111-1111-1111-1111-111111111111"))
        page = await manager.query(api_id="kt00007", path="/api/dostk/acnt", body={})
        await manager.begin_credential_change()
        manager.end_credential_change(invalidate_cursors=False)
        result = await manager.query(api_id="kt00007", path="/api/dostk/acnt", body={},
            batch_id=page["batch_id"], page_index=1, next_key=page["next_key"])
        self.assertTrue(result["complete"])
        await manager.begin_credential_change()
        await manager.close()
        with self.assertRaisesRegex(RuntimeError, "ACCOUNT_QUERY_CLOSED"):
            manager.end_credential_change(invalidate_cursors=False)
        with self.assertRaisesRegex(RuntimeError, "ACCOUNT_QUERY_CLOSED"):
            await manager.begin_credential_change()
