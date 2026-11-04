from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import patch

from PySide6.QtCore import QThread, Qt

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker, _from_payload
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    RealtimeSourceRoute, RealtimeSourceTransition, TradeTick,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
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


class _ChildWorker:
    def __init__(self, *, stops: bool = True) -> None:
        self.running = False
        self.stops = stops
        self.starts = 0
        self.stop_calls = []
        self.updates = []
        for name in (
            "trade_received", "order_executed", "market_state_received", "program_trade_received",
            "stock_reference_received", "connection_failed", "subscription_ready", "connection_opened",
            "codes_added", "diagnostics_changed", "status_changed", "sources_activated", "source_changed",
        ):
            setattr(self, name, _TestSignal())

    def start(self) -> None:
        self.running = True
        self.starts += 1

    def isRunning(self) -> bool:
        return self.running

    def stop(self, timeout_ms: int) -> bool:
        self.stop_calls.append(timeout_ms)
        if self.stops:
            self.running = False
        return self.stops

    def update_codes(self, codes, nxt_codes) -> None:
        self.updates.append((codes, nxt_codes))


class CentralRealtimeWorkerTests(unittest.TestCase):
    def test_stop_keeps_failed_children_owned_and_reports_failure_until_retry(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
        )
        fallback, validation = _ChildWorker(stops=False), _ChildWorker(stops=False)
        fallback.start()
        validation.start()
        worker._fallback_worker, worker._validation_worker = fallback, validation
        with patch.object(worker, "wait", return_value=True):
            self.assertFalse(worker.stop(17))
            self.assertIs(fallback, worker._fallback_worker)
            self.assertIs(validation, worker._validation_worker)
            fallback.stops = validation.stops = True
            self.assertTrue(worker.stop(17))
            self.assertTrue(worker.stop(17))
        self.assertIsNone(worker._fallback_worker)
        self.assertIsNone(worker._validation_worker)
        self.assertEqual([17, 17], fallback.stop_calls)
        self.assertEqual([17, 17], validation.stop_calls)

    def test_validation_stop_failure_prevents_fallback_start_until_retry_finishes(self) -> None:
        validation, fallback = _ChildWorker(stops=False), _ChildWorker()
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
            validation_factory=lambda *_: validation, fallback_factory=lambda *_: fallback,
        )
        worker._ensure_validation_worker()
        worker._ensure_local_fallback()
        self.assertIs(validation, worker._validation_worker)
        self.assertIsNone(worker._fallback_worker)
        self.assertEqual(0, fallback.starts)
        validation.stops = True
        worker._ensure_local_fallback()
        worker._ensure_local_fallback()
        self.assertIsNone(worker._validation_worker)
        self.assertIs(fallback, worker._fallback_worker)
        self.assertEqual(1, fallback.starts)
        self.assertTrue(worker.stop())

    def test_child_stop_success_does_not_release_a_still_running_producer(self) -> None:
        for role in ("fallback", "validation"):
            with self.subTest(role=role):
                worker = CentralRealtimeWorker(
                    DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
                )
                child = _ChildWorker()
                child.start()
                setattr(worker, f"_{role}_worker", child)
                with patch.object(child, "stop", return_value=True):
                    self.assertFalse(worker.stop(11))
                    self.assertIs(child, getattr(worker, f"_{role}_worker"))
                self.assertTrue(worker.stop(11))

    def test_run_setup_failure_drains_owned_child_before_propagating(self) -> None:
        child = _ChildWorker()
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
            validation_factory=lambda *_: child,
        )
        with patch.object(child, "start", side_effect=RuntimeError("child start failed")):
            with self.assertRaisesRegex(RuntimeError, "child start failed"):
                worker.run()
        self.assertEqual([3000], child.stop_calls)
        self.assertIsNone(worker._validation_worker)
        worker._ensure_validation_worker()
        worker._ensure_local_fallback()
        self.assertEqual(0, child.starts)

    def test_failed_fallback_stop_keeps_handle_and_old_callbacks_cannot_reenter_after_replacement(self) -> None:
        old, replacement = _ChildWorker(stops=False), _ChildWorker()
        children = iter((old, replacement))
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
            fallback_factory=lambda *_: next(children),
        )
        fills, trades, statuses, sources = [], [], [], []
        worker.order_executed.connect(fills.append)
        worker.trade_received.connect(trades.append)
        worker.status_changed.connect(statuses.append)
        worker.source_changed.connect(sources.append)
        worker._ensure_local_fallback()
        worker._dispatch({"type": "connection_opened", "scope": "client", "codes": ["005930"]})
        self.assertIs(old, worker._fallback_worker)
        old.source_changed.emit(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL, 2, True))
        self.assertEqual(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL_FALLBACK, 2, True), sources[-1])
        old.order_executed.emit("inflight-fill")
        worker._ensure_local_fallback()
        self.assertEqual(0, replacement.starts)
        old.stops = True
        worker._ensure_local_fallback()
        self.assertIs(replacement, worker._fallback_worker)
        self.assertEqual(1, replacement.starts)
        self.assertEqual(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL_FALLBACK), sources[-1])
        before = list(statuses)
        source_before = list(sources)
        old.order_executed.emit("stale-fill")
        old.trade_received.emit("stale-trade")
        old.status_changed.emit("stale-source")
        old.source_changed.emit(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL, 3, True))
        replacement.trade_received.emit("current-trade")
        self.assertEqual(["inflight-fill"], fills)
        self.assertEqual(["current-trade"], trades)
        self.assertEqual(before, statuses)
        self.assertEqual(source_before, sources)
        self.assertTrue(worker.stop())

    def test_validation_callbacks_stop_at_actual_release_and_cannot_reenter_a_new_child(self) -> None:
        class Recorder:
            def __init__(self):
                self.values = []

            def observe_local(self, event_type, value):
                self.values.append((event_type, value))

        recorder = Recorder()
        old, replacement = _ChildWorker(stops=False), _ChildWorker()
        children = iter((old, replacement))
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
            validation_factory=lambda *_: next(children), validation_recorder=recorder,
        )
        sources = []
        worker.source_changed.connect(sources.append)
        worker._ensure_validation_worker()
        old.source_changed.emit(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL, 0, True))
        self.assertFalse(worker._stop_validation_worker())
        worker._ensure_validation_worker()
        self.assertIs(old, worker._validation_worker)
        old.trade_received.emit("inflight")
        old.stops = True
        self.assertTrue(worker._stop_validation_worker())
        worker._ensure_validation_worker()
        old.trade_received.emit("stale")
        replacement.trade_received.emit("current")
        replacement.source_changed.emit(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL, 1, True))
        self.assertEqual([], sources)
        self.assertEqual([("trade", "inflight"), ("trade", "current")], recorder.values)
        self.assertTrue(worker.stop())

    def test_app_controller_interruption_wait_includes_actual_native_child_exit(self) -> None:
        from types import SimpleNamespace
        from kiwoom_monitor.presentation.app_controller import AppController

        class HeldChild(QThread):
            def __init__(self):
                super().__init__()
                self.entered, self.release = Event(), Event()
                self.stop_calls = []

            def run(self):
                self.entered.set()
                self.release.wait(5)

            def stop(self, timeout_ms):
                self.stop_calls.append(timeout_ms)
                self.requestInterruption()
                return self.wait(timeout_ms)

        for role in ("fallback", "validation"):
            with self.subTest(role=role):
                child, receive_entered = HeldChild(), Event()
                worker = CentralRealtimeWorker(
                    DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
                )
                setattr(worker, f"_{role}_worker", child)

                async def receive():
                    receive_entered.set()
                    while not worker.isInterruptionRequested():
                        Event().wait(0.001)

                child.start()
                try:
                    self.assertTrue(child.entered.wait(1))
                    with patch.object(worker, "_receive", receive):
                        worker.start()
                        self.assertTrue(receive_entered.wait(1))
                        owner = SimpleNamespace(_producer_workers=lambda: ((worker, "실시간 체결"),))
                        self.assertEqual(["실시간 체결"], AppController._interrupt_producers(owner))
                        self.assertFalse(worker.wait(30))
                        self.assertIs(child, getattr(worker, f"_{role}_worker"))
                        self.assertTrue(child.isRunning())
                        self.assertFalse(worker.stop(30))
                        child.release.set()
                        self.assertTrue(worker.wait(1000))
                    self.assertTrue(child.isFinished())
                    self.assertTrue(child.stop_calls)
                    self.assertIsNone(getattr(worker, f"_{role}_worker"))
                    self.assertTrue(worker.stop(30))
                finally:
                    child.release.set()
                    worker.requestInterruption()
                    self.assertTrue(worker.wait(1000))
                    self.assertTrue(child.wait(1000))

    def test_native_fallback_relays_inflight_fill_without_an_outer_event_loop_until_actual_exit(self) -> None:
        child_entered, receive_entered, release = Event(), Event(), Event()
        children, fills, trades = [], [], []

        class InflightChild(RealtimeTradeWorker):
            def run(self):
                child_entered.set()
                release.wait(5)
                self.order_executed.emit("native-fill")
                self.trade_received.emit("native-trade")

        def make_child(codes, nxt_codes):
            child = InflightChild(lambda: "", "real", codes, nxt_codes=nxt_codes)
            children.append(child)
            return child

        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
            fallback_factory=make_child,
        )
        worker.order_executed.connect(fills.append, Qt.ConnectionType.DirectConnection)
        worker.trade_received.connect(trades.append, Qt.ConnectionType.DirectConnection)

        async def receive():
            worker._ensure_local_fallback()
            receive_entered.set()
            while not worker.isInterruptionRequested():
                Event().wait(0.001)

        try:
            with patch.object(worker, "_receive", receive):
                worker.start()
                self.assertTrue(receive_entered.wait(1))
                self.assertTrue(child_entered.wait(1))
                child = children[0]
                self.assertIs(worker, child.thread())
                worker.requestInterruption()
                self.assertFalse(worker.wait(30))
                release.set()
                self.assertTrue(worker.wait(1000))
            self.assertTrue(child.isFinished())
            self.assertIsNone(worker._fallback_worker)
            child.order_executed.emit("stale-fill")
            child.trade_received.emit("stale-trade")
            self.assertEqual(["native-fill"], fills)
            self.assertEqual(["native-trade"], trades)
        finally:
            release.set()
            worker.requestInterruption()
            self.assertTrue(worker.wait(1000))
            for child in children:
                self.assertTrue(child.wait(1000))

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
        self.assertEqual(TradeTick("005930", 70000, None, None, 10, 70100, "101010"), tick)

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
                self.running = True
                self.can_stop = False

            def stop(self, _timeout_ms: int) -> bool:
                self.stops += 1
                if self.can_stop:
                    self.running = False
                return self.can_stop

            def isRunning(self) -> bool:
                return self.running

        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token", True),
            ("005930", "000660"),
        )
        local = LocalWorker()
        worker._fallback_worker = local
        sources = []
        worker.source_changed.connect(sources.append)

        worker._dispatch({"type": "central_ready", "codes": ["005930", "000660"]})
        worker._dispatch({"type": "connection_opened", "scope": "upstream", "codes": ["005930"]})
        self.assertIs(local, worker._fallback_worker)
        self.assertEqual(0, local.stops)
        self.assertEqual([RealtimeSourceTransition(RealtimeSourceRoute.LOCAL_FALLBACK)], sources)

        worker._dispatch({
            "type": "connection_opened", "scope": "client", "codes": ["005930", "000660"],
        })
        self.assertIs(local, worker._fallback_worker)
        self.assertEqual([RealtimeSourceTransition(RealtimeSourceRoute.LOCAL_FALLBACK)], sources)
        local.can_stop = True
        worker._dispatch({
            "type": "subscription_ready", "scope": "client", "codes": ["005930", "000660"],
        })
        self.assertIsNone(worker._fallback_worker)
        self.assertEqual(2, local.stops)
        self.assertEqual(RealtimeSourceTransition(RealtimeSourceRoute.CENTRAL, None, True), sources[-1])

    def test_legacy_central_trade_is_readiness_evidence_without_ack_or_fallback_authority(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",),
        )
        sources, trades = [], []
        worker.source_changed.connect(sources.append)
        worker.trade_received.connect(trades.append)
        worker._dispatch({"type": "central_ready", "codes": ["005930"]})
        payload = dict(code="005930", current_price=100, cumulative_volume=10,
                       cumulative_trade_value=1000, trade_volume=1, high_price=100, trade_time="100000")
        worker._dispatch({"type": "trade", "payload": payload})
        worker._dispatch({"type": "trade", "payload": payload})
        self.assertEqual([
            RealtimeSourceTransition(RealtimeSourceRoute.CENTRAL_WAITING),
            RealtimeSourceTransition(RealtimeSourceRoute.CENTRAL),
        ], sources)
        self.assertEqual(2, len(trades))
        child = _ChildWorker()
        child.start()
        worker._fallback_worker = child
        worker._publish_source(RealtimeSourceRoute.LOCAL_FALLBACK)
        worker._dispatch({"type": "trade", "payload": payload})
        self.assertEqual(RealtimeSourceRoute.LOCAL_FALLBACK, sources[-1].route)
        self.assertIs(child, worker._fallback_worker)
        self.assertEqual([], child.stop_calls)
        self.assertTrue(worker.stop())

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

    def test_added_nas_symbol_resets_local_baselines_before_request_changes(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",))
        observed = []
        worker.sources_activated.connect(lambda sources: observed.append((worker._codes,sources)))
        worker.update_codes(("005930","000660"),())
        self.assertEqual([(("005930",),(("000660","KRX"),("000660","NXT"),("000660","SOR")))],observed)

    def test_venue_only_activation_and_incremental_ready_do_not_emit_reconnect(self) -> None:
        worker = CentralRealtimeWorker(
            DataSourceSettings("personal_server", "https://nas.example", "token"), ("005930",))
        resets, opened = [], []
        worker.sources_activated.connect(resets.append)
        worker.connection_opened.connect(opened.append)
        worker._dispatch({'type':'codes_added','codes':[],
            'source_resets':[{'code':'005930','market':'NXT'}]})
        worker._dispatch({'type':'subscription_ready','scope':'upstream','codes':['005930']})
        self.assertEqual([(('005930','NXT'),)],resets)
        self.assertEqual([],opened)

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
        emitted = []
        worker.trade_received.connect(emitted.append)
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
        self.assertEqual(1, len(emitted))
        self.assertIs(recorder.values[0][1], emitted[0])


if __name__ == "__main__":
    unittest.main()
