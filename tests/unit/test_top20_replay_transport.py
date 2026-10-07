from __future__ import annotations

import asyncio
import copy
from contextlib import ExitStack
from datetime import datetime
from threading import Lock
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner, freeze_payload
from kiwoom_monitor.central_server.diagnostic_replay_runtime import owned_create_task
from kiwoom_monitor.central_server.diagnostic_rest_input import RequestTapeClient, TapeInputUnavailable, read_request_tape
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import (
    SubscriptionTape, SubscriptionInputUnavailable, read_realtime_tape, record_ack,
)
from kiwoom_monitor.central_server.diagnostic_top20_transport import Top20SubscriptionTransport
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from tests.unit.test_diagnostic_rest_input import Client, Store, recording, PATH
from tests.unit.test_top20_lifecycle_inputs import Socket


NOW = datetime.fromisoformat("2026-10-07T09:00:10+09:00")


class Top20ReplayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def request_tape(self, *, shared_lane=False, same_key=False):
        events, broker = [], CentralRestBroker(Client(), Store())
        try:
            with recording(events):
                for lane, code in (("source:first", "005930"), ("source:second", "000660")):
                    if shared_lane:
                        lane = "source:shared"
                    if same_key:
                        code = "005930"
                    with capture_owner("top20", "source:top20", lane):
                        await broker.request_unrecorded("ka10001", PATH, {"stk_cd": code})
        finally:
            await broker.close()
        return read_request_tape(events)

    async def test_shared_source_actor_allows_only_disjoint_native_task_signatures(self):
        for same_key in (False, True):
            with self.subTest(same_key=same_key):
                client = RequestTapeClient(await self.request_tape(shared_lane=True, same_key=same_key),
                                           preserve_transport_delay=False)
                binding = client.task_bindings([
                    {"task_name": name, "spawn_ordinal": 1, "source_lane": "source:shared"}
                    for name in ("first", "second")])
                broker = CentralRestBroker(client, Store())
                try:
                    with patch.object(trace, "input_token", return_value=None), binding.activate():
                        first = await owned_create_task(broker.request_unrecorded("ka10001", PATH,
                            {"stk_cd": "005930"}), name="first")
                        self.assertEqual("005930", first.payload["code"])
                        task = owned_create_task(broker.request_unrecorded("ka10001", PATH,
                            {"stk_cd": "005930" if same_key else "000660"}), name="second")
                        if same_key:
                            with self.assertRaisesRegex(TapeInputUnavailable, "shared_task_signature_ambiguous"):
                                await task
                            self.assertEqual(1, client.report()["used_inputs"])
                        else:
                            self.assertEqual("000660", (await task).payload["code"])
                            self.assertEqual(2, client.report()["used_inputs"])
                finally:
                    await broker.close()

    async def test_explicit_spawn_lanes_survive_reverse_request_arrival(self):
        client = RequestTapeClient(await self.request_tape(), preserve_transport_delay=False)
        binding = client.task_bindings([
            {"task_name": "entry", "spawn_ordinal": 1, "source_lane": "source:first"},
            {"task_name": "entry", "spawn_ordinal": 2, "source_lane": "source:second"},
        ])
        broker, release = CentralRestBroker(client, Store()), asyncio.Event()
        async def request(code, delayed):
            if delayed:
                await release.wait()
            value = await broker.request_unrecorded("ka10001", PATH, {"stk_cd": code})
            if not delayed:
                release.set()
            return value.payload["code"]
        try:
            with patch.object(trace, "input_token", return_value=None), binding.activate():
                first = owned_create_task(request("005930", True), name="entry")
                second = owned_create_task(request("000660", False), name="entry")
                self.assertEqual(["005930", "000660"], await asyncio.gather(first, second))
            self.assertEqual(2, client.report()["used_inputs"])
            self.assertEqual(["source:first", "source:second"],
                             [item["source_lane"] for item in binding.report()["bound_tasks"]])
        finally:
            await broker.close()

    async def test_missing_child_binding_cannot_inherit_parent_lane(self):
        client = RequestTapeClient(await self.request_tape(), preserve_transport_delay=False)
        binding = client.task_bindings([
            {"task_name": "entry", "spawn_ordinal": 1, "source_lane": "source:first"},
        ])
        broker = CentralRestBroker(client, Store())
        async def parent():
            task = owned_create_task(broker.request_unrecorded("ka10001", PATH, {"stk_cd": "005930"}),
                                     name="unbound-child")
            return await task
        try:
            with patch.object(trace, "input_token", return_value=None), binding.activate():
                with self.assertRaisesRegex(TapeInputUnavailable, "task_binding_missing"):
                    await owned_create_task(parent(), name="entry")
            self.assertEqual("market_tape_task_binding_missing", client.failure)
            self.assertEqual(0, client.report()["used_inputs"])
        finally:
            await broker.close()

    async def source_subscription(self, *, two_batches=False):
        events, lock = [], Lock()
        def scalar(token, kind, fields):
            with lock:
                events.append({"seq": len(events)+1, "mono_ns": len(events)+1,
                               "event_type": kind, **copy.deepcopy(fields)})
            return True
        def payload(token, kind, fields, value):
            return scalar(token, kind, {**fields, "payload": freeze_payload(value).value})
        hub = RealtimeHub()
        collector = CentralRealtimeCollector(lambda: "", "real", hub, lambda: NOW)
        with ExitStack() as stack:
            stack.enter_context(patch.object(trace, "input_token", side_effect=lambda kind:
                "transport-fixture" if kind == "top20_inputs" else None))
            stack.enter_context(patch.object(trace, "emit_payload", side_effect=payload))
            stack.enter_context(patch.object(trace, "emit", side_effect=scalar))
            stack.enter_context(patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", 0))
            subscriber = hub.connect(capture_component="source:top20")
            hub.update_subscription(subscriber, ["005930"], [], program_codes=["005930"])
            for _ in range(2 if two_batches else 1):
                groups = await collector._send_subscription(Socket(), "KRX", ("005930",), (), ("005930",), ())
                approval = None
                for group in groups:
                    approval = record_ack(collector, {"trnm": "REG", "grp_no": group, "return_code": 0})
                hub.set_upstream_ready(("005930",), True, approval=approval)
                if two_batches and not _:
                    hub.set_upstream_ready((), False)
                    collector._mark_capture_gap(clear_continuous=True)
        return read_realtime_tape(events), f"realtime_hub:{id(hub):x}"

    async def replay_transport(self, tape, component):
        hub = RealtimeHub()
        collector = CentralRealtimeCollector(lambda: self.fail("network token invoked"),
            "real", hub, lambda: NOW, store=Store())
        await collector.start_input_replay()
        subscriber = hub.connect(capture_component="live:top20")
        hub.update_subscription(subscriber, ["005930"], [], program_codes=["005930"])
        transport = Top20SubscriptionTransport(collector, SubscriptionTape(tape),
            source_hub_component=component, component_map={"live:top20": "source:top20"},
            preserve_ack_delay=False)
        return collector, hub, transport

    async def test_native_ack_transition_and_gap_require_fresh_matching_subscription(self):
        tape, component = await self.source_subscription(two_batches=True)
        collector, hub, transport = await self.replay_transport(tape, component)
        try:
            with patch.object(trace, "input_token", return_value=None), \
                    patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", 0):
                receipts = []
                native = collector._accept_registration_ack
                def observe(*args, **kwargs):
                    result = native(*args, **kwargs)
                    receipts.append((result, hub.upstream_ready))
                    return result
                with patch.object(collector, "_accept_registration_ack", side_effect=observe):
                    await transport.update()
                self.assertTrue(hub.upstream_ready)
                self.assertTrue(all(not ready for remaining, ready in receipts if remaining))
                self.assertEqual((0, True), receipts[-1])
                self.assertEqual({("005930", "KRX")}, collector._approved_trade_sources)
                transport.gap()
                self.assertFalse(hub.upstream_ready)
                with patch.object(collector, "accept_replay_message") as deliver:
                    self.assertEqual((1, 0), transport.registered_message({"trnm": "REAL", "data": [
                        {"type": "0B", "item": "005930", "values": {"20": "090010", "10": "100"}}]}))
                    deliver.assert_not_called()
                self.assertEqual(1, transport.report()["unapproved_rows"])
                self.assertFalse(transport.connection_approved)
                await transport.update()
                self.assertTrue(hub.upstream_ready)
                with patch.object(collector, "accept_replay_message") as deliver:
                    self.assertEqual((2, 1), transport.registered_message({"trnm": "REAL", "data": [
                        {"type": "0B", "item": ["005930"], "values": {"20": "090010", "10": "100"}},
                        {"type": "0B", "item": ["000660"], "values": {"20": "090010", "10": "100"}}]}))
                    self.assertEqual(["005930"], deliver.call_args.args[0]["data"][0]["item"])
                self.assertEqual(1, transport.report()["unregistered_rows"])
                transport.gap()
                with self.assertRaisesRegex(SubscriptionInputUnavailable, "input_unavailable"):
                    await transport.update()
                self.assertFalse(hub.upstream_ready)
            self.assertEqual(2, len(transport.report()["subscriptions"]))
            self.assertFalse(transport.report()["historical_ready_injected"])
        finally:
            await collector.close()

    async def test_changed_registration_cannot_apply_old_ready_or_deliver_excluded_ticks(self):
        tape, component = await self.source_subscription()
        collector, hub, transport = await self.replay_transport(tape, component)
        try:
            with patch.object(trace, "input_token", return_value=None), \
                    patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", 0):
                expected = transport.tape.tape["subscriptions"]
                next(iter(expected.values()))["end"]["groups"]["1000"][0]["item"] = ["000660"]
                with self.assertRaisesRegex(SubscriptionInputUnavailable, "registration_changed"):
                    await transport.update()
                self.assertFalse(hub.upstream_ready)
                self.assertEqual(set(), collector._approved_trade_sources)
                with patch.object(collector, "accept_replay_message") as deliver:
                    self.assertEqual((1, 0), transport.registered_message({"trnm": "REAL", "data": [
                        {"type": "0B", "item": "005930", "values": {"20": "090010", "10": "100"}}]}))
                    deliver.assert_not_called()
        finally:
            await collector.close()
