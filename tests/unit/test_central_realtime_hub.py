from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


class RealtimeHubTests(unittest.TestCase):
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

    def test_websocket_requires_token_and_accepts_subscription(self) -> None:
        settings = CentralServerSettings(
            database_url="sqlite:///:memory:", access_token="server-token",
        )
        with TestClient(create_app(settings)) as client:
            with self.assertRaises(Exception):
                with client.websocket_connect("/api/v1/realtime"):
                    pass
            with client.websocket_connect("/api/v1/realtime?token=server-token") as websocket:
                self.assertEqual("ready", websocket.receive_json()["type"])
                websocket.send_json({"type": "subscribe", "codes": ["005930"], "nxt_codes": []})
                response = websocket.receive_json()
                self.assertEqual("subscribed", response["type"])
                self.assertEqual(["005930"], response["codes"])
                self.assertEqual("central_ready", websocket.receive_json()["type"])
                websocket.send_json({"type": "ping"})
                self.assertEqual("pong", websocket.receive_json()["type"])


if __name__ == "__main__":
    unittest.main()
