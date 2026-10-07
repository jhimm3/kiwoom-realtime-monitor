from __future__ import annotations

import asyncio
import json
import threading
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from kiwoom_monitor.central_server.mock_account_monitor import RealAccountRealtimeCollector
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.market_observations import as_kst
from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution, AccountBalanceChange
import test_real_account_monitor as support

NOW = datetime(2026, 9, 16, 17, tzinfo=timezone(timedelta(hours=9)))


def frame(raw="2345678901", state="체결", execution="fill-1"):
    return {"trnm": "REAL", "data": [{"type": "00", "item": "005930", "values": {
        "9201": raw, "913": state, "9203": "order-1", "909": execution, "9001": "A005930",
        "910": "1000", "911": "1", "905": "+매수", "908": "170000"}}]}


class RealAccountSocketTests(unittest.IsolatedAsyncioTestCase):
    def collector(self, handler, **kwargs):
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        return RealAccountRealtimeCollector(lambda: "fake-token", handler, lambda: NOW,
            account_scope_resolver=lambda raw: scope if raw == "2345678901" else None, **kwargs), scope

    async def test_real_only_subscription_extended_session_and_required_resolver(self):
        collector, _ = self.collector(lambda *_: None)
        class Socket:
            async def send(self, raw):
                self.sent = json.loads(raw)
        socket = Socket()
        await collector._send_subscription(socket)
        self.assertEqual([{"item": [""], "type": ["00", "04"]}], socket.sent["data"])
        self.assertTrue(collector._session_open())
        collector._now = lambda: NOW.replace(hour=20)
        self.assertFalse(collector._session_open())
        with self.assertRaisesRegex(ValueError, "REAL_ACCOUNT_SCOPE_RESOLVER_REQUIRED"):
            RealAccountRealtimeCollector(lambda: "fake", lambda *_: None)

    async def test_foreign_missing_and_null_rows_do_not_trigger_unscoped_recovery(self):
        delivered = []
        collector, scope = self.collector(lambda *event: delivered.append(event))
        for message in (frame("1234567890"), frame(""), {"trnm": "REAL", "data": None},
                        {**frame(), "trnm": "REG"}):
            collector._dispatch_realtime(message)
        self.assertEqual([], delivered)
        collector._dispatch_realtime(frame(state="접수"))
        self.assertEqual([("order_changed", scope)], delivered)
        collector._dispatch_realtime(frame())
        self.assertEqual("order_execution", delivered[-1][0])
        self.assertEqual(scope, delivered[-1][1].origin_scope)

    async def receive(self, messages):
        delivered = []
        collector, _ = self.collector(lambda *value: delivered.append(value))
        class Socket:
            def __init__(self):
                self.sent = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            async def send(self, raw):
                self.sent.append(json.loads(raw))
            async def recv(self):
                if not messages:
                    raise asyncio.CancelledError()
                return json.dumps(messages.pop(0))
        socket = Socket()
        with patch("kiwoom_monitor.central_server.mock_account_monitor.connect", return_value=socket) as connection:
            with self.assertRaises(asyncio.CancelledError):
                await collector._receive()
            self.assertEqual("wss://api.kiwoom.com:10000/api/dostk/websocket", connection.call_args.args[0])
        return collector, socket, delivered

    async def test_login_reg_ack_ping_and_real_are_ordered_and_preack_real_is_ignored(self):
        collector, socket, delivered = await self.receive([
            {"trnm": "LOGIN", "return_code": 0}, frame(execution="before-ack"),
            {"trnm": "REG", "return_code": 0}, {"trnm": "PING"}, frame(execution="after-ack")])
        self.assertTrue(collector.ready)
        self.assertEqual("connected", delivered[0][0])
        executions = [value for name, value in delivered if name == "order_execution"]
        self.assertEqual(["after-ack"], [value.execution_no for value in executions])
        self.assertEqual(["LOGIN", "REG", "PING"], [value["trnm"] for value in socket.sent])
        await collector.close()
        self.assertFalse(collector.ready)

    async def test_cancellation_drains_owned_token_before_opening_any_socket(self):
        entered, release = threading.Event(), threading.Event()
        collector, _ = self.collector(lambda *_: None)
        def token():
            entered.set()
            release.wait(timeout=3)
            return "temporary-fake-token"
        collector._token_provider = token
        try:
            with patch("kiwoom_monitor.central_server.mock_account_monitor.connect") as connection:
                await collector.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                closing = asyncio.create_task(collector.close())
                await asyncio.sleep(0.03)
                self.assertFalse(closing.done())
                release.set()
                await asyncio.wait_for(closing, 2)
                connection.assert_not_called()
        finally:
            release.set()
            await collector.close()

    async def test_failed_login_or_reg_never_marks_ready(self):
        for messages in ([{"trnm": "LOGIN", "return_code": 1}],
                         [{"trnm": "LOGIN", "return_code": 0}, {"trnm": "REG", "return_code": 1}]):
            delivered = []
            collector, _ = self.collector(lambda *value: delivered.append(value))
            class Socket:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    return None
                async def send(self, raw):
                    return None
                async def recv(self):
                    return json.dumps(messages.pop(0))
            with patch("kiwoom_monitor.central_server.mock_account_monitor.connect", return_value=Socket()):
                with self.assertRaises(RuntimeError):
                    await collector._receive()
            self.assertFalse(collector.ready)
            self.assertEqual([], delivered)


class SocketStub:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.ready, self.error_code = False, None
        self.starts = self.closes = 0
    async def start(self):
        self.starts += 1
    async def close(self):
        self.closes += 1
        self.ready = False
    def emit(self, name, value):
        self.kwargs["event_handler"](name, value)


class RealAccountRealtimeOwnerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.RealAccountMonitorTests.asyncSetUp
    asyncTearDown = support.RealAccountMonitorTests.asyncTearDown
    ready = support.RealAccountMonitorTests.ready
    apply = support.RealAccountMonitorTests.apply
    active = support.RealAccountMonitorTests.active
    admitted = support.RealAccountMonitorTests.admitted
    until = support.RealAccountMonitorTests.until
    toggle = support.RealAccountMonitorTests.toggle
    settings = support.RealAccountMonitorTests.settings

    async def contexts(self):
        self.owner._account_realtime_factory = SocketStub
        source, target = await self.admitted()
        await self.until(lambda: target.realtime.starts == 1)
        return source, target

    def event(self, context, execution="fill-1"):
        return OrderExecution("order-1", execution, "005930", "test", "매수", 1000, 1, "170000",
                              origin_scope=context.binding.scope)

    def documents(self, context):
        return self.store.load_documents("real_account_event", "kiwoom:real:" + context.binding.scope.account_ref)

    async def test_shared_market_event_reuses_connection_and_persists_only_matching_scope(self):
        source, target = await self.contexts()
        self.assertIsNone(source.realtime)
        collector = CentralRealtimeCollector(lambda: "not-used", "real", RealtimeHub(), lambda: NOW,
            account_scope_resolver=lambda raw: self.owner._resolve_account_scope(source, source.binding, raw),
            account_event_handler=self.owner.on_market_account_event)
        collector._publish_parsed(frame("1234567890"))
        collector._publish_parsed(frame("2345678901"))
        await self.until(lambda: self.documents(source))
        self.assertEqual([], self.documents(target))
        document = self.documents(source)[0]["document"]
        self.assertEqual(source.binding.scope.to_dict(), document["scope"])
        self.assertEqual("kiwoom_websocket", document["source"])
        self.assertNotIn("1234567890", json.dumps(document))
        self.assertEqual("shared_market", self.owner.account_monitor_status(source.binding.scope.to_dict())["realtime"]["source"])

    async def test_dedicated_scope_resolver_stale_callbacks_and_off_do_not_touch_market(self):
        source, target = await self.contexts()
        socket = target.realtime
        self.assertIsNone(socket.kwargs["account_scope_resolver"]("1234567890"))
        self.assertEqual(target.binding.scope, socket.kwargs["account_scope_resolver"]("2345678901"))
        socket.emit("order_execution", self.event(source))
        self.assertFalse(target.monitor_wake.is_set())
        await self.toggle(target, False)
        self.assertEqual(1, socket.closes)
        await self.toggle(target, True)
        socket.emit("order_execution", self.event(target, "stale"))
        await asyncio.sleep(0.03)
        self.assertEqual([], self.documents(target))
        self.assertEqual(1, self.collector.end_credential_change.await_count)

    async def test_burst_events_store_individually_but_coalesce_rest_recovery(self):
        _, target = await self.contexts()
        for number in range(20):
            target.realtime.emit("order_execution", self.event(target, str(number)))
        await self.until(lambda: len(self.documents(target)) == 20)
        await self.until(lambda: len(target.client.account_calls) == 5)
        await asyncio.sleep(0.1)
        self.assertEqual(5, len(target.client.account_calls))
        target.realtime.ready = True
        status = self.owner.account_monitor_status(target.binding.scope.to_dict())["realtime"]
        self.assertEqual("ready", status["state"])
        self.assertIsNotNone(status["last_event_at"])

    async def test_event_store_failure_retains_pending_blocks_off_and_recovers(self):
        _, target = await self.contexts()
        with patch.object(self.store, "save_real_account_event", side_effect=RuntimeError("secret-not-for-output")):
            target.realtime.emit("order_execution", self.event(target))
            await self.until(lambda: target.pending_event is not None and target.event_error_code is not None)
            with self.assertRaises(CredentialOperationError) as failure:
                await self.toggle(target, False)
            self.assertNotIn("secret-not-for-output", str(failure.exception))
            self.assertEqual(1, self.settings(target)["revision"])
            self.assertIsNotNone(target.pending_event)
        # A new notification wakes account recovery; pending evidence still has the old policy.
        await self.owner._drain_account_reads(target)
        self.owner._start_account_monitor(self.profile, target, self.settings(target))
        await self.until(lambda: self.documents(target))
        self.assertIsNone(target.pending_event)

    async def test_role_change_closes_previous_dedicated_socket_and_reverses_sources(self):
        source, target = await self.contexts()
        old = target.realtime
        await self.owner.change_market_role(self.profile, expected_revision=0, expected_binding_revision=1)
        await self.until(lambda: source.realtime.starts == 1)
        self.assertIsNone(target.realtime)
        self.assertEqual(1, old.closes)
        old.emit("order_execution", self.event(target, "old-socket"))
        await asyncio.sleep(0.03)
        self.assertEqual([], self.documents(target))
        self.assertEqual("account_only", self.owner.account_monitor_status(source.binding.scope.to_dict())["realtime"]["source"])

    async def test_event_store_fence_and_exact_retry_idempotency(self):
        _, target = await self.contexts()
        event, now = self.event(target), as_kst(target.client.server_now())
        first = self.store.save_real_account_event(target.binding, "order_execution", event, now, settings_revision=1)
        self.assertEqual(first, self.store.save_real_account_event(target.binding, "order_execution", event, now, settings_revision=1))
        self.assertEqual(1, len(self.documents(target)))
        foreign = replace(event, origin_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4())))
        for binding, value, revision in ((target.binding, foreign, 1),
                                       (replace(target.binding, binding_revision=2), event, 1), (target.binding, event, 2)):
            with self.assertRaises(ValueError):
                self.store.save_real_account_event(binding, "order_execution", value, now, settings_revision=revision)

    async def test_actual_event_write_finishes_before_cancelled_off_commits(self):
        _, target = await self.contexts()
        original = self.store.save_real_account_event
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(timeout=3)
            return original(*args, **kwargs)
        try:
            with patch.object(self.store, "save_real_account_event", side_effect=blocked):
                target.realtime.emit("order_execution", self.event(target))
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                waiter = asyncio.create_task(self.toggle(target, False))
                await self.until(lambda: target.account_reads_paused)
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiter
                self.assertEqual(1, self.settings(target)["revision"])
                release.set()
                await self.until(lambda: not self.owner._settings_tasks)
            self.assertEqual(2, self.settings(target)["revision"])
            self.assertTrue(self.documents(target))
        finally:
            release.set()

    async def test_first_market_event_during_role_resume_is_already_accepted(self):
        source, target = await self.contexts()
        async def resumed():
            self.owner.on_market_account_event("order_execution", self.event(target, "first-resumed"))
        self.collector.end_credential_change.side_effect = resumed
        await self.owner.change_market_role(self.profile, expected_revision=0, expected_binding_revision=1)
        await self.until(lambda: self.documents(target))
        self.assertEqual("first-resumed", self.documents(target)[0]["document"]["event"]["execution_no"])

    async def test_balance_event_persists_anonymous_quantity_and_requests_recovery(self):
        _, target = await self.contexts()
        balance = AccountBalanceChange("005930", "test", 20, 20, 1000, 20000, 500000, 1100,
                                       origin_scope=target.binding.scope)
        target.realtime.emit("account_balance", balance)
        await self.until(lambda: self.documents(target))
        self.assertEqual("account_balance", self.documents(target)[0]["document"]["event_type"])
        self.assertEqual(20, self.documents(target)[0]["document"]["event"]["position_quantity"])
        await self.until(lambda: len(target.client.account_calls) == 5)

    async def test_dedicated_event_publishes_once_with_scope_and_adds_market_collection_symbol(self):
        source, target = await self.contexts()
        hub = RealtimeHub()
        subscriber = hub.connect()
        collector = CentralRealtimeCollector(lambda: "unused", "real", hub, lambda: NOW)
        self.owner._account_event_publisher = collector.publish_account_event
        target.realtime.emit("order_execution", self.event(target))
        delivered = subscriber.queue.get_nowait()
        self.assertEqual(target.binding.scope.to_dict(), delivered["payload"]["origin_scope"])
        self.assertTrue(subscriber.queue.empty())
        self.assertEqual("005930", list(collector._pending_account_entry_symbols.values())[0]["document"]["code"])
        # Primary events are already published by the market collector.
        self.owner.on_market_account_event("order_execution", self.event(source))
        self.assertTrue(subscriber.queue.empty())

    async def test_full_event_queue_reports_loss_and_still_drains_accepted_events(self):
        _, target = await self.contexts()
        target.event_queue = asyncio.Queue(maxsize=1)
        original = self.store.save_real_account_event
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(timeout=3)
            return original(*args, **kwargs)
        try:
            with patch.object(self.store, "save_real_account_event", side_effect=blocked):
                target.realtime.emit("order_execution", self.event(target, "first"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                target.realtime.emit("order_execution", self.event(target, "queued"))
                target.realtime.emit("order_execution", self.event(target, "overflow"))
                self.assertEqual(1, target.dropped_events)
                self.assertEqual("REAL_ACCOUNT_EVENT_QUEUE_FULL", target.event_error_code)
                release.set()
                await self.toggle(target, False)
            self.assertEqual(2, len(self.documents(target)))
            self.assertEqual(1, self.owner.account_monitor_status(target.binding.scope.to_dict())["realtime"]["dropped_events"])
        finally:
            release.set()

    async def test_socket_close_failure_cannot_commit_off_or_claim_complete_drain(self):
        _, target = await self.contexts()
        socket = target.realtime
        async def failed_close():
            raise RuntimeError("socket close failed")
        target.realtime.close = failed_close
        with self.assertRaises(CredentialOperationError):
            await self.toggle(target, False)
        self.assertEqual(1, self.settings(target)["revision"])
        self.assertTrue(target.monitor_enabled)
        self.assertTrue(target.account_reads_paused)
        self.assertIsNone(self.owner.applied_settings_revision(target.binding.scope.to_dict()))
        self.assertIs(socket, target.realtime)


if __name__ == "__main__":
    unittest.main()
