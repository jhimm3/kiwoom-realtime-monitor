from __future__ import annotations

import asyncio
import copy
import json
from contextlib import ExitStack
from datetime import datetime
from threading import Event, Lock
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    capture_owner, freeze_payload, install_store_capture, operation_identity,
)
from kiwoom_monitor.central_server.diagnostic_top20_input import compile_delivery_coverage, consumed_delivery
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import (
    EVENT, SubscriptionTape, SubscriptionInputUnavailable,
    compile_lifecycle_descendants, read_realtime_tape, record_ack,
)
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub

NOW = datetime.fromisoformat("2026-10-07T09:00:00+09:00")


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))


class Store:
    def __init__(self):
        self.documents = []
        install_store_capture(self)

    def upsert_documents(self, collection, values):
        self.documents.append((collection, values, operation_identity()))


class Top20LifecycleInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events, self.enabled, self.epoch, self.lock = [], True, "epoch-one", Lock()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(trace, "input_token", side_effect=lambda kind:
            self.epoch if self.enabled and kind in {"top20_inputs", "collector_inputs", "store_inputs"} else None))
        self.stack.enter_context(patch.object(trace, "emit_payload", side_effect=self.payload))
        self.stack.enter_context(patch.object(trace, "emit", side_effect=self.scalar))
        self.stack.enter_context(patch.object(trace, "reject_input", side_effect=lambda token, fields:
            self.scalar(token, "input_rejected", fields)))
        self.stack.enter_context(patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", 0))
        self.addCleanup(self.stack.close)
        self.hub = RealtimeHub()
        self.collector = CentralRealtimeCollector(lambda: "", "real", self.hub, lambda: NOW)
        self.store = Store()
        self.service = AutonomousTop20Service(None, self.hub, self.store, now_provider=lambda: NOW)
        self.component = f"autonomous_top20:{id(self.service):x}"
        self.subscriber = self.hub.connect(capture_component=self.component)
        self.service._subscriber = self.subscriber
        self.hub.update_subscription(self.subscriber, ["005930"], [], program_codes=["005930"])

    def scalar(self, token, kind, fields):
        with self.lock:
            self.events.append({"seq": len(self.events)+1, "mono_ns": len(self.events)+1,
                                "event_type": kind, **copy.deepcopy(fields)})
        return True

    def payload(self, token, kind, fields, value):
        return self.scalar(token, kind, {**fields, "payload": freeze_payload(value).value})

    async def register(self):
        socket = Socket()
        groups = await self.collector._send_subscription(socket, "KRX", ("005930",), (), ("005930",), ())
        approval = None
        for group in groups:
            approval = record_ack(self.collector, {"trnm": "REG", "grp_no": group, "return_code": 0,
                "return_msg": "sensitive server error text"})
        self.hub.set_upstream_ready(("005930",), True, approval=approval)
        self.hub.publish({"type": "subscription_ready"})
        return socket

    def drain(self):
        while not self.subscriber.queue.empty():
            event = self.subscriber.queue.get_nowait()
            with consumed_delivery(self.subscriber, event):
                pass

    async def test_native_registration_and_zero_s_consumer_have_closed_causes(self):
        socket = await self.register()
        task = asyncio.create_task(self.service._event_loop())
        try:
            self.collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0s", "item": "",
                "values": {"215": "3", "20": "090000", "214": "0", "secret": "do not capture"}}]})
            for _ in range(50):
                if self.store.documents:
                    break
                await asyncio.sleep(.002)
            await asyncio.gather(*tuple(self.service._fundamentals_tasks))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(1, len(self.store.documents))
        collection, values, identity = self.store.documents[0]
        self.assertEqual("krx_trading_day_observations", collection)
        self.assertEqual(NOW.date().isoformat(), values[0]["document"]["trading_date"])
        coverage = compile_delivery_coverage(self.events, trace_id=self.epoch, component=self.component)
        self.assertEqual(2, len(coverage["delivery_ids"]))
        self.assertIn(identity["cause_input_id"], coverage["delivery_ids"])
        tape = read_realtime_tape(self.events)
        self.assertEqual(1, len(tape["subscriptions"]))
        self.assertTrue(self.hub.upstream_ready)
        captured = json.dumps([row for row in self.events if row["event_type"] == EVENT], default=str)
        self.assertNotIn("sensitive", captured)
        self.assertNotIn("secret", captured)
        self.assertTrue(any("00" in part["type"] for row in socket.sent for part in row["data"]))
        self.assertFalse(tape["top20_session_execution_ready"])

    async def test_capture_off_preserves_subscription_wire_shape_and_failure_is_observer_only(self):
        on = await self.register()
        self.drain()
        self.enabled = False
        off = Socket()
        before = len(self.events)
        await self.collector._send_subscription(off, "KRX", ("005930",), (), ("005930",), ())
        self.assertEqual(on.sent, off.sent)
        self.assertEqual(before, len(self.events))
        self.enabled = True
        with patch.object(trace, "emit_payload", side_effect=OSError("injected observer failure")):
            failed = Socket()
            await self.collector._send_subscription(failed, "KRX", ("005930",), (), ("005930",), ())
            self.hub.set_upstream_ready(("005930",), True)
        self.assertEqual(off.sent, failed.sent)
        self.assertTrue(self.hub.upstream_ready)
        with self.assertRaisesRegex(ValueError, "rejected_input"):
            read_realtime_tape(self.events)

    async def test_missing_ack_unknown_ready_and_epoch_crossing_fail_before_execution(self):
        await self.register()
        good = copy.deepcopy(self.events)
        missing = [row for row in good if row.get("input_kind") != "subscription_ack"]
        with self.assertRaisesRegex(ValueError, "ready_without_approval"):
            read_realtime_tape(missing)
        self.events.clear()
        self.hub.set_upstream_ready(("005930",), True)
        with self.assertRaisesRegex(ValueError, "initial_missing"):
            read_realtime_tape(self.events)
        changed = copy.deepcopy(good)
        next(row for row in changed if row.get("input_kind") == "hub_event")["trace_id"] = "different-epoch"
        with self.assertRaisesRegex(ValueError, "epoch_mismatch"):
            read_realtime_tape(changed)

    async def test_missing_subscription_end_or_pending_ack_is_censored(self):
        await self.collector._send_subscription(Socket(), "KRX", ("005930",), (), ("005930",), ())
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            read_realtime_tape(self.events)
        missing = [row for row in self.events if row.get("input_kind") != "subscription_end"]
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            read_realtime_tape(missing)

    async def test_gap_and_new_subscription_require_fresh_ack_and_keep_peer_results(self):
        await self.register()
        self.drain()
        self.hub.set_upstream_ready((), False)
        self.collector._mark_capture_gap(clear_continuous=True)
        await self.register()
        self.drain()
        with capture_owner("top20", self.component, "service-index"):
            self.store.upsert_documents("top20_daily_entrants", [{"owner": "2026-10-07", "key": "005930",
                "document": {"code": "005930"}}])
        with capture_owner("top20", "peer-service", "peer-index"):
            self.store.upsert_documents("top20_daily_entrants", [{"owner": "2026-10-07", "key": "000660",
                "document": {"code": "000660"}}])
        enabled = compile_lifecycle_descendants(self.events, component=self.component)
        excluded = compile_lifecycle_descendants(self.events, component=self.component, include_top20=False)
        self.assertEqual(1, len(enabled["replaced_operation_ids"]))
        self.assertEqual(enabled["replaced_operation_ids"], excluded["excluded_operation_ids"])
        self.assertEqual(enabled["peer_operation_ids"], excluded["peer_operation_ids"])
        self.assertEqual((), excluded["replaced_operation_ids"])
        self.assertEqual((), excluded["service_cause_ids"])
        self.assertFalse(excluded["execution_authorized"])
        tape = read_realtime_tape(self.events)
        self.assertEqual(2, len(tape["subscriptions"]))

    async def test_ownerless_subscription_and_unknown_operation_owner_are_rejected(self):
        peer = self.hub.connect()
        self.hub.update_subscription(peer, ["005930"], [])
        await self.register()
        self.drain()
        with self.assertRaisesRegex(ValueError, "ownerless_subscription"):
            compile_lifecycle_descendants(self.events, component=self.component)
        self.hub.disconnect(peer)
        with capture_owner("top20", self.component, "service-index"):
            self.store.upsert_documents("top20_daily_entrants", [{"owner": "2026-10-07", "key": "005930",
                "document": {"code": "005930"}}])
        rows = copy.deepcopy(self.events)
        for row in rows:
            if row.get("event_type") == "operation_start":
                row["actor_known"] = False
        with self.assertRaisesRegex(ValueError, "operation_owner_invalid"):
            compile_lifecycle_descendants(rows, component=self.component)

    async def test_ready_approval_cannot_be_reused_after_gap(self):
        await self.register()
        self.drain()
        from kiwoom_monitor.central_server.realtime_hub import CapturedControlReceipt
        final_ack = next(row for row in reversed(self.events) if row.get("input_kind") == "subscription_ack")
        stale = CapturedControlReceipt(self.epoch, final_ack["producer_component"], final_ack["input_id"],
                                       "subscription_ack", True)
        self.hub.set_upstream_ready((), False)
        self.hub.set_upstream_ready(("005930",), True, approval=stale)
        with self.assertRaisesRegex(ValueError, "ready_without_approval"):
            read_realtime_tape(self.events)

    async def test_actual_receive_binds_counted_native_reg_acks_before_publishing_ready(self):
        collector = self.collector
        class ReceivingSocket(Socket):
            def __init__(self):
                super().__init__()
                self.frames = [{"return_code": 0}] + [{"trnm": "REG", "return_code": 0}] * 4
                self.frames.append({"trnm": "REAL", "data": [{"type": "0s", "values": {"215": "3"}}]})

            async def recv(self):
                frame = self.frames.pop(0)
                if not self.frames:
                    collector._market_session = lambda: None
                return json.dumps(frame)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False
        socket = ReceivingSocket()
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=socket):
            await collector._receive("KRX")
        self.drain()
        tape = read_realtime_tape(self.events)
        batch = next(iter(tape["subscriptions"].values()))
        self.assertEqual(4, len(batch["acks"]))
        self.assertTrue(self.hub.upstream_ready)
        coverage = compile_delivery_coverage(self.events, trace_id=self.epoch, component=self.component)
        self.assertEqual(4, len(coverage["delivery_ids"]))

    async def test_shared_native_broker_effect_cannot_be_split_between_top20_and_peer(self):
        await self.register()
        self.drain()
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        entered, release = Event(), Event()
        class Client:
            def request_with_continuation(self, *args, **kwargs):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("fixture transport timed out")
                return {"return_code": 0}, False, ""
        broker = CentralRestBroker(Client())
        async def request(component, actor):
            with capture_owner("top20", component, actor):
                return await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
        first = asyncio.create_task(request(self.component, "selected-actor"))
        peer = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            peer = asyncio.create_task(request("peer-component", "peer-actor"))
            for _ in range(50):
                if sum(row.get("input_kind") == "logical_start" for row in self.events) == 2:
                    break
                await asyncio.sleep(.002)
            release.set()
            await asyncio.gather(first, peer)
            with self.assertRaisesRegex(ValueError, "mixed_shared_request"):
                compile_lifecycle_descendants(self.events, component=self.component, include_top20=False)
        finally:
            release.set()
            await asyncio.gather(first, *([peer] if peer else []), return_exceptions=True)
            await broker.close()

    async def test_subscription_tape_matches_new_intent_without_applying_old_ready(self):
        await self.register()
        tape = read_realtime_tape(self.events)
        fresh = RealtimeHub()
        subscriber = fresh.connect(capture_component="fresh-service")
        fresh.update_subscription(subscriber, ["005930"], [], program_codes=["005930"])
        adapter = SubscriptionTape(tape)
        source = next(iter(tape["subscriptions"].values()))["start"]["hub_component"]
        request = {"session": "KRX", "codes": ["005930"], "nxt_codes": [],
                   "program_codes": ["005930"], "priority_codes": []}
        result = adapter.match(fresh, request, source_hub_component=source,
                               component_map={"fresh-service": self.component})
        self.assertEqual(4, len(result["ack_inputs"]))
        self.assertFalse(result["ready_applied"])
        self.assertFalse(fresh.upstream_ready)
        self.assertTrue(subscriber.queue.empty())
        with self.assertRaisesRegex(SubscriptionInputUnavailable, "input_unavailable"):
            adapter.match(fresh, request, source_hub_component=source,
                          component_map={"fresh-service": self.component})

    async def test_exclusion_cannot_recreate_old_subscription_or_supply_missing_codes(self):
        await self.register()
        tape = read_realtime_tape(self.events)
        fresh = RealtimeHub()
        source = next(iter(tape["subscriptions"].values()))["start"]["hub_component"]
        request = {"session": "KRX", "codes": ["005930"], "nxt_codes": [],
                   "program_codes": ["005930"], "priority_codes": []}
        adapter = SubscriptionTape(tape)
        with self.assertRaisesRegex(SubscriptionInputUnavailable, "not_native_or_excluded"):
            adapter.match(fresh, request, source_hub_component=source, component_map={},
                          exclude_components=(self.component,))
        self.assertEqual(0, fresh.client_count)
        self.assertFalse(fresh.upstream_ready)


if __name__ == "__main__":
    unittest.main()
