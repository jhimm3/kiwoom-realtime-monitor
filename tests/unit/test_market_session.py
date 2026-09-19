from __future__ import annotations

import unittest
import asyncio
import json
from datetime import datetime
from unittest.mock import patch

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker, market_session


class MarketSessionTests(unittest.TestCase):
    def test_uses_krx_during_regular_hours(self) -> None:
        self.assertEqual("KRX", market_session(datetime(2026, 8, 14, 10, 0), "real"))

    def test_uses_nxt_only_for_real_api_before_regular_hours(self) -> None:
        self.assertEqual("NXT", market_session(datetime(2026, 8, 14, 8, 30), "real"))
        self.assertIsNone(market_session(datetime(2026, 8, 14, 8, 30), "mock"))

    def test_uses_nxt_after_regular_hours_and_stops_at_twenty(self) -> None:
        self.assertEqual("NXT", market_session(datetime(2026, 8, 14, 16, 0), "real"))
        self.assertEqual("KRX", market_session(datetime(2026, 9, 14, 16, 0), "real"))
        self.assertIsNone(market_session(datetime(2026, 8, 14, 20, 0), "real"))

    def test_worker_refreshes_phase_boundary_while_messages_keep_arriving(self) -> None:
        now = [datetime(2026, 9, 14, 15, 29, 59)]

        class Socket:
            def __init__(self):
                self.sent = []
                self.received = 0

            async def send(self, raw):
                self.sent.append(json.loads(raw))

            async def recv(self):
                self.received += 1
                if self.received == 1:
                    return json.dumps({"return_code": 0})
                if self.received == 2:
                    now[0] = datetime(2026, 9, 14, 15, 30)
                    return json.dumps({"trnm": "REG", "return_code": 0})
                raise RuntimeError("test complete")

        class Connection:
            def __init__(self, socket): self.socket = socket
            async def __aenter__(self): return self.socket
            async def __aexit__(self, *_args): return False

        socket = Socket()
        worker = RealtimeTradeWorker(
            lambda: "token", "real", ("A", "B"), lambda: now[0], ("B",),
        )
        with patch(
            "kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker.connect",
            return_value=Connection(socket),
        ):
            with self.assertRaisesRegex(RuntimeError, "test complete"):
                asyncio.run(worker._receive("KRX"))
        registrations = [value for value in socket.sent if value.get("trnm") == "REG"]
        self.assertEqual(2, len(registrations))
