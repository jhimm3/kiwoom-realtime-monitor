from __future__ import annotations

import unittest
from unittest.mock import patch

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker, _from_payload
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable


class _TestSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback, *_args) -> None:
        self.callbacks.append(callback)

    def emit(self, value=None) -> None:
        for callback in self.callbacks:
            if value is None:
                callback()
            else:
                callback(value)


class CentralRealtimeWorkerTests(unittest.TestCase):
    def test_realtime_gap_is_forwarded_only_for_positive_integer_loss(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
        )
        received = []
        worker.realtime_gap.connect(received.append)
        worker._dispatch({"type": "realtime_gap", "dropped_events": 3})
        worker._dispatch({"type": "realtime_gap", "dropped_events": 0})
        worker._dispatch({"type": "realtime_gap", "dropped_events": True})
        self.assertEqual([3], received)

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
                patch.object(worker, "_ensure_local_fallback", side_effect=StopTest) as fallback:
            with self.assertRaises(StopTest):
                worker.run()
        fallback.assert_called_once_with()
        self.assertEqual(3, worker._consecutive_failures)

    def test_local_fallback_stays_running_while_central_is_still_unavailable(self) -> None:
        class LocalWorker:
            def __init__(self) -> None:
                self.running = False
                self.starts = 0
                self.updates = []
                for name in (
                    "trade_received", "order_executed", "market_state_received", "program_trade_received",
                    "stock_reference_received", "connection_failed", "subscription_ready", "connection_opened",
                    "codes_added", "diagnostics_changed", "status_changed",
                ):
                    setattr(self, name, _TestSignal())

            def start(self) -> None:
                self.running = True
                self.starts += 1

            def stop(self, _timeout_ms: int) -> None:
                self.running = False

            def isRunning(self) -> bool:
                return self.running

            def update_codes(self, codes, nxt_codes) -> None:
                self.updates.append((codes, nxt_codes))

        local = LocalWorker()
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930",), fallback_factory=lambda codes, nxt_codes: local,
        )
        worker._ensure_local_fallback()
        worker._ensure_local_fallback()

        self.assertTrue(local.running)
        self.assertEqual(1, local.starts)
        self.assertEqual([(("005930",), ())], local.updates)

    def test_local_fallback_forwards_trade_to_outer_worker(self) -> None:
        class LocalWorker:
            def __init__(self) -> None:
                self.running = False
                for name in (
                    "trade_received", "order_executed", "market_state_received", "program_trade_received",
                    "stock_reference_received", "connection_failed", "subscription_ready", "connection_opened",
                    "codes_added", "diagnostics_changed", "status_changed",
                ):
                    setattr(self, name, _TestSignal())

            def start(self) -> None:
                self.running = True

            def isRunning(self) -> bool:
                return self.running

        local = LocalWorker()
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930",), fallback_factory=lambda codes, nxt_codes: local,
        )
        received = []
        worker.trade_received.connect(received.append)
        worker._ensure_local_fallback()
        tick = TradeTick("005930", 70000, 1, 70000, 1, 70000, "101010")
        local.trade_received.emit(tick)

        self.assertEqual([tick], received)

    def test_local_fallback_stops_only_after_central_covers_requested_codes(self) -> None:
        class LocalWorker:
            def __init__(self) -> None:
                self.stops = 0

            def stop(self, _timeout_ms: int) -> None:
                self.stops += 1

        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930", "000660"),
        )
        local = LocalWorker()
        worker._fallback_worker = local

        worker._dispatch({"type": "central_ready", "codes": ["005930", "000660"]})
        worker._dispatch({"type": "connection_opened", "scope": "upstream", "codes": ["005930"]})
        self.assertIs(local, worker._fallback_worker)
        self.assertEqual(0, local.stops)

        worker._dispatch({
            "type": "connection_opened", "scope": "client", "codes": ["005930", "000660"],
        })
        self.assertIsNone(worker._fallback_worker)
        self.assertEqual(1, local.stops)

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

    def test_labels_client_and_upstream_subscription_counts_separately(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"),
            ("005930", "000660"),
        )
        messages: list[str] = []
        worker.status_changed.connect(messages.append)

        worker._dispatch({
            "type": "connection_opened", "scope": "client",
            "codes": ["005930", "000660"],
        })
        worker._dispatch({
            "type": "connection_opened", "scope": "upstream",
            "codes": [f"{value:06d}" for value in range(110)],
        })

        self.assertEqual([
            "나스 실시간 체결 구독 중 · 2종목",
            "나스 실시간 체결 구독 중 · 110종목",
        ], messages)

    def test_validation_receives_central_envelope_metadata_without_changing_emitted_tick(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.values = []

            def observe_central(self, event_type, value, *, metadata=None) -> None:
                self.values.append((event_type, value, metadata))

        recorder = Recorder()
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"),
            ("005930",), validation_recorder=recorder,
        )
        event = {
            "type": "trade", "revision_id": "nas-r1",
            "payload": {
                "code": "005930", "current_price": 70000,
                "cumulative_volume": 100, "cumulative_trade_value": 7000000,
                "trade_volume": 10, "high_price": 70100, "trade_time": "101010",
            },
        }
        worker._dispatch(event)
        self.assertEqual("trade", recorder.values[0][0])
        self.assertEqual("005930", recorder.values[0][1].code)
        self.assertIs(event, recorder.values[0][2])


if __name__ == "__main__":
    unittest.main()
