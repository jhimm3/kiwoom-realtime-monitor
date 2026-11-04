from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.realtime_routes import create_realtime_router


class RealtimeHubTests(unittest.TestCase):
    def test_overflow_records_the_missing_event_count(self) -> None:
        hub = RealtimeHub()
        subscriber = hub.connect()
        for number in range(1001):
            hub.publish({"type": "trade", "number": number})
        self.assertEqual(1, subscriber.dropped_events)
        self.assertEqual(1, subscriber.queue.get_nowait()["number"])

    def test_unions_client_subscriptions_and_filters_events(self) -> None:
        hub = RealtimeHub()
        first, second = hub.connect(), hub.connect()
        hub.update_subscription(first, ["005930"], [])
        hub.update_subscription(second, ["000660"], ["000660"])
        self.assertEqual((("000660", "005930"), ("000660",)), hub.requested_codes())
        hub.publish({"type": "trade", "code": "005930"}, code="005930")
        self.assertEqual("005930", first.queue.get_nowait()["code"])
        with self.assertRaises(asyncio.QueueEmpty):
            second.queue.get_nowait()

    def test_program_codes_follow_explicit_priority_before_general_requests(self) -> None:
        hub = RealtimeHub()
        autonomous, desktop = hub.connect(), hub.connect()
        hub.update_subscription(
            autonomous, ["000003", "000002"], [],
            program_codes=["000003", "000002"], priority_codes=["000003", "000002"],
        )
        hub.update_subscription(desktop, ["000001", "000003"], [])

        self.assertEqual(("000003", "000002"), hub.requested_priority_codes())
        self.assertEqual(("000003", "000002", "000001"), hub.requested_program_codes())

    def test_websocket_requires_token_and_accepts_subscription(self) -> None:
        settings = CentralServerSettings(
            database_url="sqlite:///:memory:", access_token="server-token",
            news_naver_market_enabled=False,
        )
        with TestClient(create_app(settings)) as client:
            with self.assertRaises(WebSocketDisconnect) as denied:
                with client.websocket_connect("/api/v1/realtime"):
                    pass
            self.assertEqual(4401, denied.exception.code)
            with client.websocket_connect("/api/v1/realtime?token=server-token") as websocket:
                self.assertEqual("ready", websocket.receive_json()["type"])
                websocket.send_json({"type": "subscribe", "codes": ["005930"], "nxt_codes": []})
                response = websocket.receive_json()
                self.assertEqual("subscribed", response["type"])
                self.assertEqual(["005930"], response["codes"])
                self.assertEqual("central_ready", websocket.receive_json()["type"])
                websocket.send_json({"type": "ping"})
                self.assertEqual("pong", websocket.receive_json()["type"])


class _RouteWebSocket:
    headers = {}
    query_params = {"token": "server-token"}

    def __init__(self, messages=(), *, fail_frame=None, error=None, exhausted=None):
        self.messages = list(messages)
        self.fail_frame = fail_frame
        self.error = error
        self.exhausted = exhausted
        self.failed = asyncio.Event()
        self.receiving = asyncio.Event()
        self.sent = []
        self.closed = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, **kwargs):
        self.closed.append(kwargs)

    async def send_json(self, value):
        self.sent.append(value)
        if value.get("type") == self.fail_frame:
            self.failed.set()
            raise self.error

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        self.receiving.set()
        if self.exhausted is not None:
            await self.exhausted(self)
        raise WebSocketDisconnect(code=1000)


@asynccontextmanager
async def _asgi_realtime_socket(app, *, authorization="Bearer server-token", query=""):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "scheme": "ws", "path": "/api/v1/realtime", "raw_path": b"/api/v1/realtime",
        "query_string": query.encode(), "root_path": "", "http_version": "1.1",
        "headers": [(b"authorization", authorization.encode())],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8787), "state": {},
    }
    await incoming.put({"type": "websocket.connect"})
    route = asyncio.create_task(app(scope, incoming.get, outgoing.put))

    async def read():
        frame = await asyncio.wait_for(outgoing.get(), 1)
        return json.loads(frame["text"]) if frame["type"] == "websocket.send" else frame

    async def write(document):
        await incoming.put({"type": "websocket.receive", "text": json.dumps(document)})

    try:
        yield read, write
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(route, 2)


class RealtimeRouteCleanupTests(unittest.IsolatedAsyncioTestCase):
    def _endpoint(self, hub, snapshots=()):
        class Store:
            def load_realtime_snapshots(self, codes):
                return list(snapshots)
        return create_realtime_router(hub, Store(), lambda token: token == "server-token", None).routes[0].endpoint

    async def test_asgi_malformed_shapes_preserve_subscription_and_skip_snapshot_reads(self):
        hub, reads = RealtimeHub(), []

        class Store:
            def load_realtime_snapshots(self, codes):
                reads.append(codes)
                return []

        app = FastAPI()
        app.include_router(create_realtime_router(hub, Store(), lambda token: token == "server-token", None))
        malformed = [None, [], ["subscribe"], "subscribe", 1, True]
        malformed += [{"type": value, "codes": ["035420"]} for value in (None, [], {})]
        malformed += [{"type": "unknown", "codes": ["035420"]}]
        for field in ("codes", "nxt_codes"):
            for value in (None, "035420", 35420, True, {"035420": True},
                          [None], [["035420"]], [{"code": "035420"}]):
                malformed.append({"type": "subscribe", "codes": ["035420"], "nxt_codes": [], field: value})
        healthy = hub.connect()
        hub.update_subscription(healthy, ["000660"], ["000660"])
        try:
            for message in malformed:
                with self.subTest(message=message):
                    async with _asgi_realtime_socket(app) as (read, write):
                        self.assertEqual("websocket.accept", (await read())["type"])
                        self.assertEqual("ready", (await read())["type"])
                        await write({"type": "subscribe", "codes": ["005930"], "nxt_codes": ["005930"]})
                        self.assertEqual("subscribed", (await read())["type"])
                        self.assertEqual("central_ready", (await read())["type"])
                        reads_before = len(reads)
                        await write(message)
                        await write({"type": "ping"})
                        self.assertEqual({"type": "pong"}, await read())
                        self.assertEqual((("000660", "005930"), ("000660", "005930")), hub.requested_codes())
                        self.assertEqual(reads_before, len(reads))
                        self.assertEqual(2, hub.client_count)
                    self.assertEqual((("000660",), ("000660",)), hub.requested_codes())
                    self.assertEqual(1, hub.client_count)
            self.assertFalse(any(
                task.get_coro().__qualname__.endswith("send_events") and not task.done()
                for task in asyncio.all_tasks()
            ))
        finally:
            hub.disconnect(healthy)
        self.assertEqual(((), ()), hub.requested_codes())

    async def test_asgi_scalar_normalization_empty_subscription_and_reconnect_remain_compatible(self):
        hub, reads = RealtimeHub(), []

        class Store:
            def load_realtime_snapshots(self, codes):
                reads.append(codes)
                return []

        app = FastAPI()
        app.include_router(create_realtime_router(hub, Store(), lambda token: token == "server-token", None))
        for connection in range(2):
            async with _asgi_realtime_socket(app) as (read, write):
                self.assertEqual("websocket.accept", (await read())["type"])
                self.assertEqual("ready", (await read())["type"])
                await write({"type": "SuBsCrIbE", "codes": [" 005930 ", "005930", 5930, 0, True, "", "symbol:custom"],
                             "nxt_codes": [5930]})
                expected = ["0", "005930", "5930", "True", "symbol:custom"]
                self.assertEqual({"type": "subscribed", "codes": expected, "nxt_codes": ["5930"],
                                  "upstream_code_count": 5, "upstream_nxt_code_count": 1}, await read())
                self.assertEqual("central_ready", (await read())["type"])
                self.assertEqual(expected, reads[-1])
                await write({"type": "subscribe"})
                self.assertEqual({"type": "subscribed", "codes": [], "nxt_codes": [],
                                  "upstream_code_count": 0, "upstream_nxt_code_count": 0}, await read())
                self.assertEqual("central_ready", (await read())["type"])
                self.assertEqual([], reads[-1])
                self.assertEqual(((), ()), hub.requested_codes())
            self.assertEqual(0, hub.client_count)
        self.assertEqual(4, len(reads))
        self.assertFalse(any(
            task.get_coro().__qualname__.endswith("send_events") and not task.done()
            for task in asyncio.all_tasks()
        ))

    async def test_failed_event_or_gap_sender_is_disconnected_without_hiding_its_error(self):
        for frame, error in (("trade", OSError("event send failed")),
                             ("realtime_gap", RuntimeError("gap send failed"))):
            with self.subTest(frame=frame):
                hub = RealtimeHub()
                healthy = hub.connect()
                hub.update_subscription(healthy, ["005930"], ["005930"])

                async def after_subscribe(socket):
                    for number in range(1001 if frame == "realtime_gap" else 1):
                        hub.publish({"type": "trade", "number": number}, code="000660")
                    await asyncio.wait_for(socket.failed.wait(), 1)

                socket = _RouteWebSocket(
                    [{"type": "subscribe", "codes": ["000660"], "nxt_codes": ["000660"]}],
                    fail_frame=frame, error=error, exhausted=after_subscribe,
                )
                try:
                    with self.assertRaises(type(error)) as caught:
                        await asyncio.wait_for(self._endpoint(hub)(socket), 2)
                    self.assertIs(error, caught.exception)
                    self.assertEqual(1, hub.client_count)
                    self.assertEqual((("005930",), ("005930",)), hub.requested_codes())
                    hub.publish({"type": "trade", "number": "healthy"}, code="005930")
                    self.assertEqual("healthy", healthy.queue.get_nowait()["number"])
                finally:
                    hub.disconnect(healthy)

    async def test_ready_snapshot_and_ping_failures_release_only_the_route_subscription(self):
        for frame, messages, snapshots in (
            ("ready", (), ()),
            ("trade", ({"type": "subscribe", "codes": ["000660"]},), ({"type": "trade"},)),
            ("pong", ({"type": "ping"},), ()),
        ):
            with self.subTest(frame=frame):
                hub = RealtimeHub()
                healthy = hub.connect()
                hub.update_subscription(healthy, ["005930"], [])
                error = OSError(f"{frame} send failed")
                socket = _RouteWebSocket(messages, fail_frame=frame, error=error)
                try:
                    with self.assertRaises(OSError) as caught:
                        await self._endpoint(hub, snapshots)(socket)
                    self.assertIs(error, caught.exception)
                    self.assertEqual(1, hub.client_count)
                    self.assertEqual((("005930",), ()), hub.requested_codes())
                finally:
                    hub.disconnect(healthy)

    async def test_normal_receive_disconnect_and_route_cancellation_join_the_sender(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                hub = RealtimeHub()
                async def keep_receiving(socket):
                    await asyncio.Event().wait()
                socket = _RouteWebSocket(
                    [{"type": "subscribe", "codes": ["000660"]}],
                    exhausted=keep_receiving if cancel else None,
                )
                route = asyncio.create_task(self._endpoint(hub)(socket))
                try:
                    if cancel:
                        await asyncio.wait_for(socket.receiving.wait(), 1)
                        route.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await route
                    else:
                        await asyncio.wait_for(route, 2)
                    self.assertEqual(0, hub.client_count)
                    self.assertEqual(((), ()), hub.requested_codes())
                    self.assertFalse(any(
                        task.get_coro().__qualname__.endswith("send_events") and not task.done()
                        for task in asyncio.all_tasks()
                    ))
                finally:
                    if not route.done():
                        route.cancel()
                        try:
                            await route
                        except asyncio.CancelledError:
                            pass




class HubBoundaryTests(unittest.TestCase):
    def test_two_hub_recorders_keep_initial_epoch_and_ready_parent_separate(self):
        from datetime import datetime
        from unittest.mock import patch
        from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as recording
        from kiwoom_monitor.central_server.realtime_hub import CapturedControlReceipt

        epoch, events = ["hub-owner-one"], []
        def emit(token, kind, fields, value):
            events.append((token, fields.copy(), value.copy()))
            return True
        def inputs(hub, kind):
            component = f"realtime_hub:{id(hub):x}"
            return [row for row in events if row[0] == epoch[0]
                    and row[1]["producer_component"] == component and row[1]["input_kind"] == kind]

        left_owner, right_owner = recording.HubInputRecorder(), recording.HubInputRecorder()
        left = RealtimeHub(now_provider=lambda: datetime(2026, 10, 26, 9), observer=left_owner)
        right = RealtimeHub(now_provider=lambda: datetime(2026, 10, 26, 9), observer=right_owner)
        with patch.object(recording.trace, "input_token", side_effect=lambda _kind: epoch[0]), \
                patch.object(recording.trace, "emit_payload", side_effect=emit):
            left.connect()
            self.assertEqual(epoch[0], left_owner.lifecycle_epoch)
            self.assertIsNone(right_owner.lifecycle_epoch)
            right.connect()
            left_owner.hub_initial(left)
            right_owner.hub_initial(right)
            self.assertEqual(1, len(inputs(left, "hub_initial")))
            self.assertEqual(1, len(inputs(right, "hub_initial")))
            approval = CapturedControlReceipt(epoch[0], "collector:left", "ack:left", "subscription_ack", True)
            left.set_upstream_ready(("005930",), True, approval=approval)
            left_receipt = left_owner.ready_receipt
            self.assertIsNone(right_owner.ready_receipt)
            left.publish({"type": "subscription_ready"})
            right.publish({"type": "subscription_ready"})
            self.assertEqual(left_receipt.input_id, inputs(left, "hub_event")[-1][2]["ready_input_id"])
            self.assertEqual("", inputs(right, "hub_event")[-1][2]["ready_input_id"])
            self.assertTrue(left.upstream_ready)
            self.assertFalse(right.upstream_ready)
            epoch[0] = "hub-owner-two"
            right.publish({"type": "connection_opened"})
            self.assertEqual("hub-owner-one", left_owner.lifecycle_epoch)
            self.assertEqual(epoch[0], right_owner.lifecycle_epoch)
            left.publish({"type": "connection_opened"})
            self.assertEqual(1, len(inputs(left, "hub_initial")))
            self.assertEqual(1, len(inputs(right, "hub_initial")))
            self.assertEqual("", inputs(left, "hub_event")[-1][2]["ready_input_id"])
            self.assertIs(left_receipt, left_owner.ready_receipt)
            for hub in (left, right):
                self.assertFalse(any(name.startswith("_recorded_") for name in vars(hub)))

    def test_failed_hub_receipts_keep_peer_evidence_and_native_readiness(self):
        from datetime import datetime
        from unittest.mock import patch
        from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as recording
        from kiwoom_monitor.central_server.realtime_hub import CapturedControlReceipt

        left_owner, right_owner = recording.HubInputRecorder(), recording.HubInputRecorder()
        left = RealtimeHub(now_provider=lambda: datetime(2026, 10, 26, 9), observer=left_owner)
        right = RealtimeHub(now_provider=lambda: datetime(2026, 10, 26, 9), observer=right_owner)
        approval = CapturedControlReceipt("hub-owner-failure", "collector:left", "ack:left", "subscription_ack", True)
        with patch.object(recording.trace, "input_token", return_value=approval.trace_id), \
                patch.object(recording.trace, "emit_payload", return_value=True) as emit, \
                patch.object(recording.trace, "reject_input") as reject:
            left.set_upstream_ready(("005930",), True, approval=approval)
            accepted = left_owner.ready_receipt
            emit.return_value = False
            right.set_upstream_ready(("000660",), True, approval=approval)
            refused = right_owner.ready_receipt
            self.assertFalse(refused.complete)
            self.assertTrue(right.upstream_ready_for({"000660"}))
            self.assertIs(accepted, left_owner.ready_receipt)
            self.assertTrue(accepted.complete)
            emit.side_effect = LookupError("observer persistence failure")
            right.set_upstream_ready((), False)
            self.assertFalse(right_owner.ready_receipt.complete)
            self.assertIsNot(refused, right_owner.ready_receipt)
            self.assertFalse(right.upstream_ready)
            self.assertTrue(left.upstream_ready_for({"005930"}))
            self.assertIs(accepted, left_owner.ready_receipt)
            reject.assert_called_once()
            self.assertEqual("top20_realtime_observer_error", reject.call_args.args[1]["reason"])

    def test_installed_capture_clones_keep_one_corrected_publication_moment_and_wire_values(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as recording
        from kiwoom_monitor.central_server.realtime_hub import CapturedHubEvent, PublishedHubEvent
        from kiwoom_monitor.domain.time_basis import KST
        current = datetime(2026, 10, 25, 15, 0, 1, tzinfo=timezone.utc)
        calls = []
        def now():
            calls.append(current)
            return current
        with patch.object(recording.trace, "input_token", return_value="hub-boundary"), \
                patch.object(recording.trace, "emit_payload", return_value=True), \
                patch.object(recording.trace, "emit"), \
                patch.object(recording.trace, "reject_input"):
            hub = RealtimeHub(now_provider=now, observer=recording.HubInputRecorder())
            first = hub.connect(capture_component="top20:first")
            peer = hub.connect(capture_component="top20:peer")
            source = object()
            event = CapturedHubEvent({"type": "trade", "payload": {"code": "005930", "trade_time": "235959"}}, source=source)
            self.assertIsNone(event.published_at)
            hub.publish(event)
            left, right = first.queue.get_nowait(), peer.queue.get_nowait()
            expected = datetime(2026, 10, 26, 0, 0, 1, tzinfo=KST)
            self.assertEqual([current], calls)
            self.assertEqual(expected, left.published_at)
            self.assertEqual(expected, right.published_at)
            self.assertIsNot(left, right)
            self.assertIs(source, left.source_receipt)
            self.assertIs(source, right.source_receipt)
            self.assertEqual(json.dumps(event), json.dumps(left))
            self.assertEqual(json.dumps(event), json.dumps(right))
            prior = datetime(2026, 10, 25, 14, 59, tzinfo=timezone.utc)
            carried = CapturedHubEvent(PublishedHubEvent(event, published_at=prior), source=source)
            hub.publish(carried)
            self.assertEqual([current], calls)
            self.assertEqual(prior.astimezone(KST), first.queue.get_nowait().published_at)
            self.assertEqual(prior.astimezone(KST), peer.queue.get_nowait().published_at)
            control = {"type": "connection_opened", "scope": "upstream", "codes": ["005930"]}
            hub.publish(control)
            left, right = first.queue.get_nowait(), peer.queue.get_nowait()
            self.assertEqual([current, current], calls)
            self.assertEqual(expected, left.published_at)
            self.assertEqual(expected, right.published_at)
            self.assertEqual("hub_event", left.source_receipt.kind)
            self.assertIs(left.source_receipt, right.source_receipt)
            self.assertIsNot(left, right)
            self.assertEqual(json.dumps(control), json.dumps(left))
            self.assertEqual(json.dumps(control), json.dumps(right))

    def test_installed_lifecycle_failure_keeps_original_pre_and_post_mutation_order(self):
        from datetime import datetime
        from unittest.mock import patch
        from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as recording
        observations = []
        def reject(hub, reason):
            observations.append(("initial_failed", hub.client_count, hub.requested_codes(), reason))
        def intent(hub, subscriber, action):
            observations.append((action, hub.client_count, tuple(sorted(subscriber.codes))))
        def ready(hub, codes, value, approval):
            observations.append(("before_ready", hub.upstream_ready, hub.upstream_ready_for(set(codes)), approval))
        hub = RealtimeHub(now_provider=lambda: datetime(2026, 10, 26, 9), observer=recording.HubInputRecorder())
        with patch.object(hub._observer, "hub_initial", side_effect=RuntimeError("observer initial failure")), \
                patch.object(recording, "reject", side_effect=reject), \
                patch.object(hub._observer, "record_intent", side_effect=intent), \
                patch.object(hub._observer, "record_ready", side_effect=ready):
            subscriber = hub.connect()
            hub.update_subscription(subscriber, ["005930"], [])
            marker = object()
            hub.set_upstream_ready(("005930",), True, approval=marker)
            self.assertTrue(hub.upstream_ready_for({"005930"}))
            hub.disconnect(subscriber)
        self.assertEqual([
            ("initial_failed", 0, ((), ()), "top20_realtime_initial_invalid"),
            ("connect", 1, ()),
            ("initial_failed", 1, ((), ()), "top20_realtime_initial_invalid"),
            ("update", 1, ("005930",)),
            ("before_ready", False, False, marker),
            ("initial_failed", 1, (("005930",), ()), "top20_realtime_initial_invalid"),
            ("disconnect", 0, ("005930",)),
        ], observations)

    def test_clock_failure_does_not_drop_a_queued_market_event_or_invoke_capture(self):
        from unittest.mock import patch
        from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as recording
        def failed_clock():
            raise OSError("clock unavailable")
        observer = recording.HubInputRecorder()
        with patch.object(recording.trace, "input_token", return_value=None), \
                patch.object(observer, "prepare_publish") as prepare:
            hub = RealtimeHub(now_provider=failed_clock, observer=observer)
            subscriber = hub.connect()
            subscriber.queue = asyncio.Queue(maxsize=1)
            original = {"type": "trade", "payload": {"code": "005930", "trade_time": "090000"}}
            subscriber.queue.put_nowait(original)
            with self.assertRaisesRegex(OSError, "clock unavailable"):
                hub.publish({"type": "trade", "payload": {"code": "005930", "trade_time": "090001"}})
            self.assertIs(original, subscriber.queue.get_nowait())
            self.assertEqual(0, subscriber.dropped_events)
            self.assertFalse(subscriber.gap_scope_unknown)
            prepare.assert_not_called()

    def test_real_hub_runs_with_all_diagnostic_sources_physically_absent(self):
        import os
        from pathlib import Path
        import shutil
        import subprocess
        import sys
        import tempfile
        source = Path(__file__).resolve().parents[2] / "src"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copied = root / "src"
            shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__", "diagnostic*.py"))
            self.assertFalse(list(copied.rglob("diagnostic*.py")))
            self.assertFalse((root / "scripts").exists())
            self.assertEqual((source / "kiwoom_monitor/__init__.py").read_bytes(),
                             (copied / "kiwoom_monitor/__init__.py").read_bytes())
            self.assertEqual((source / "kiwoom_monitor/central_server/__init__.py").read_bytes(),
                             (copied / "kiwoom_monitor/central_server/__init__.py").read_bytes())
            child = r'''
import asyncio, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
if os.name == 'nt':
    import runpy
    runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
source = Path(sys.argv[1]).resolve()
assert not list(source.rglob('diagnostic*.py'))
assert not (source.parent / 'scripts').exists()
sys.path.insert(0, str(source))
from kiwoom_monitor.central_server import realtime_hub as module
from kiwoom_monitor.domain.time_basis import KST
assert Path(module.__file__).resolve() == source / 'kiwoom_monitor/central_server/realtime_hub.py'
now = [datetime(2026, 10, 25, 15, 0, 1, tzinfo=timezone.utc)]
hub = module.RealtimeHub(now_provider=lambda: now[0])
first = hub.connect(capture_component='normal:no-tools', allowed_event_types=frozenset({'trade'}))
peer = hub.connect()
hub.update_subscription(first, ['005930', '005930'], [], program_codes=['005930'], priority_codes=['005930'])
hub.update_subscription(peer, ['000660'], ['000660'])
assert hub.requested_codes() == (('000660', '005930'), ('000660',))
assert hub.requested_priority_codes() == ('005930',)
assert hub.requested_program_codes() == ('005930', '000660')
hub.publish({'type':'program_trade','payload':{'code':'005930'}}, '005930')
assert first.queue.empty() and peer.queue.empty()
hub.publish({'type':'trade','payload':{'code':'000660','trade_time':'235959'}}, '000660')
assert first.queue.empty() and peer.queue.get_nowait()['payload']['code'] == '000660'
first.queue = asyncio.Queue(maxsize=1)
lost = {'type':'trade','payload':{'code':'005930','market':'KRX','trade_time':'235959'}}
retained = {'type':'trade','payload':{'code':'005930','market':'KRX','trade_time':'000001'}}
hub.publish(lost, '005930')
hub.publish(retained, '005930')
assert first.dropped_events == 1
event = first.queue.get_nowait()
assert event == retained and json.dumps(event) == json.dumps(retained)
assert set(event) == {'type','payload'}
assert event.published_at == datetime(2026, 10, 26, 0, 0, 1, tzinfo=KST)
gap = first.take_gap(1)
assert not gap.scope_unknown and len(gap.ranges) == 1
assert gap.ranges[0].start == gap.ranges[0].end == datetime(2026, 10, 25, 23, 59, 59, tzinfo=KST)
assert gap.ranges[0].code == '005930'
for clock in ('bad', '000000'):
    now[0] = datetime(2026, 10, 26, 12, 0, 0, 500000, tzinfo=KST)
    hub.publish({'type':'trade','payload':{'code':'005930','market':'KRX','trade_time':clock}}, '005930')
    hub.publish(retained, '005930')
    assert first.take_gap(1).scope_unknown
    first.queue.get_nowait()
hub.set_upstream_ready(('005930',), True)
assert hub.upstream_ready and hub.upstream_ready_for({'005930'})
assert not hub.upstream_ready_for({'000660'})
hub.set_upstream_ready((), False)
assert not hub.upstream_ready and not hub.upstream_ready_for({'005930'})
hub.disconnect(first); hub.disconnect(peer)
assert hub.client_count == 0 and hub.requested_codes() == ((), ())
assert not any('diagnostic' in name for name in sys.modules if name.startswith('kiwoom_monitor'))
origins = {}
for name, value in list(sys.modules.items()):
    if name == 'kiwoom_monitor' or name.startswith('kiwoom_monitor.'):
        path = Path(value.__file__).resolve()
        assert path.is_relative_to(source), (name, str(path))
        origins[name] = {'file':str(path), 'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
scope = getattr(sys, '_kiwoom_regression_registry_override', None)
description = getattr(scope, 'description', {})
assert os.name != 'nt' or scope is not None
print(json.dumps({'tools_physically_absent':True, 'real_package_initializers':True,
    'all_hub_operations':True, 'published_kst_previous_day_and_unknown_gap':True,
    'origins':origins, 'joined_owned_HKCU':scope is not None,
    'registry_key':description.get('key'), 'registry_owner_pid':description.get('owner_pid')}))
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", child, str(copied)],
                                    cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                                    capture_output=True, text=True, encoding="utf-8", errors="backslashreplace", timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            evidence = json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(evidence["tools_physically_absent"])
            self.assertTrue(evidence["all_hub_operations"])
            scope = getattr(sys, "_kiwoom_regression_registry_override", None)
            if os.name == "nt":
                self.assertIsNotNone(scope)
                self.assertTrue(evidence["joined_owned_HKCU"])
                self.assertEqual(scope.description["key"], evidence["registry_key"])
                self.assertEqual(scope.description["owner_pid"], evidence["registry_owner_pid"])
            print("HUB_ABSENCE_EVIDENCE=" + json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
