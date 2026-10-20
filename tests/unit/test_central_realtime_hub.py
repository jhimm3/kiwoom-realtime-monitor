from __future__ import annotations

import asyncio
import unittest

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


class RealtimeRouteCleanupTests(unittest.IsolatedAsyncioTestCase):
    def _endpoint(self, hub, snapshots=()):
        class Store:
            def load_realtime_snapshots(self, codes):
                return list(snapshots)
        return create_realtime_router(hub, Store(), lambda token: token == "server-token", None).routes[0].endpoint

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


if __name__ == "__main__":
    unittest.main()
