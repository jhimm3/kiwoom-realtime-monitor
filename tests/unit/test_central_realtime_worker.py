from __future__ import annotations

import unittest
from unittest.mock import patch

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker, _from_payload
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable


class CentralRealtimeWorkerTests(unittest.TestCase):
    def test_converts_http_server_url_to_websocket(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("local_server", "http://127.0.0.1:8787", "token"), ("005930",),
        )
        self.assertEqual("ws://127.0.0.1:8787/api/v1/realtime", worker._websocket_url())

    def test_converts_https_server_url_and_custom_port_to_secure_websocket(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example:9443", "token"), ("005930",),
        )
        self.assertEqual("wss://nas.example:9443/api/v1/realtime", worker._websocket_url())

    def test_rebuilds_domain_event_and_ignores_future_fields(self) -> None:
        tick = _from_payload(TradeTick, {
            "code": "005930", "current_price": 70000, "cumulative_volume": None,
            "cumulative_trade_value": None, "trade_volume": 10, "high_price": 70100,
            "trade_time": "101010", "future_field": "ignored",
        })
        self.assertIsInstance(tick, TradeTick)
        self.assertEqual(70000, tick.current_price)

    def test_switches_to_local_only_after_three_central_failures(self) -> None:
        class StopTest(Exception):
            pass

        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930",), fallback_factory=lambda codes, nxt_codes: None,
        )
        with patch.object(worker, "_receive", side_effect=RuntimeError("offline")), \
                patch.object(worker, "_wait_or_stop"), \
                patch.object(worker, "_run_local_fallback", side_effect=StopTest) as fallback:
            with self.assertRaises(StopTest):
                worker.run()
        fallback.assert_called_once_with()
        self.assertEqual(3, worker._consecutive_failures)

    def test_forwards_changed_codes_to_running_local_fallback(self) -> None:
        class LocalWorker:
            def __init__(self) -> None:
                self.args = None

            def update_codes(self, codes, nxt_codes) -> None:
                self.args = (codes, nxt_codes)

        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True), ("005930",),
        )
        local = LocalWorker()
        worker._fallback_worker = local
        worker.update_codes(("000660",), ("000660",))
        self.assertEqual((("000660",), ("000660",)), local.args)

    def test_server_upstream_failure_triggers_immediate_failover_path(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930",), fallback_factory=lambda codes, nxt_codes: None,
        )
        with self.assertRaises(CentralServerUnavailable):
            worker._dispatch({"type": "connection_failed", "message": "키움 원본 연결 끊김"})
        self.assertEqual(2, worker._consecutive_failures)


if __name__ == "__main__":
    unittest.main()
