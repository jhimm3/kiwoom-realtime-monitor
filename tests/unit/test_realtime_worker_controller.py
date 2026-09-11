from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.presentation.realtime_worker_controller import (
    RealtimeWorkerController,
)


class FakeRealtimeWorker(QObject):
    trade_received = Signal(object)
    order_executed = Signal(object)
    market_state_received = Signal(object)
    program_trade_received = Signal(object)
    diagnostics_changed = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)
    connection_opened = Signal(object)
    codes_added = Signal(object)
    subscription_ready = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.running = False
        self.updates: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.stop_calls = 0

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def update_codes(self, codes: tuple[str, ...], nxt_codes: tuple[str, ...]) -> None:
        self.updates.append((codes, nxt_codes))

    def stop(self) -> bool:
        self.stop_calls += 1
        self.running = False
        return True


class RealtimeWorkerControllerTests(unittest.TestCase):
    def test_start_configures_worker_and_forwards_signals(self) -> None:
        worker = FakeRealtimeWorker()
        controller = RealtimeWorkerController(worker_factory=lambda _codes: worker)  # type: ignore[arg-type]
        trades: list[object] = []
        followups: list[object] = []
        controller.trade_received.connect(trades.append)
        controller.subscription_ready.connect(followups.append)

        started = controller.start(
            ("005930", "000660"), ("005930",), followup_codes=("005930",),
        )
        worker.trade_received.emit("tick")
        worker.subscription_ready.emit()

        self.assertTrue(started)
        self.assertTrue(controller.is_running)
        self.assertEqual([(("005930", "000660"), ("005930",))], worker.updates)
        self.assertEqual(["tick"], trades)
        self.assertEqual([("005930",)], followups)

    def test_update_and_stop_are_owned_by_controller(self) -> None:
        worker = FakeRealtimeWorker()
        controller = RealtimeWorkerController(worker_factory=lambda _codes: worker)  # type: ignore[arg-type]
        self.assertTrue(controller.start(("005930",), (), followup_codes=("005930",)))

        self.assertTrue(controller.update_codes(("000660",), ("000660",)))
        self.assertTrue(controller.stop())

        self.assertEqual((("000660",), ("000660",)), worker.updates[-1])
        self.assertEqual(1, worker.stop_calls)
        self.assertFalse(controller.is_running)

    def test_missing_factory_and_running_worker_reject_start(self) -> None:
        controller = RealtimeWorkerController()
        self.assertFalse(controller.available)
        self.assertFalse(controller.start(("005930",), (), followup_codes=()))

        worker = FakeRealtimeWorker()
        controller.set_factory(lambda _codes: worker)  # type: ignore[arg-type]
        self.assertTrue(controller.start(("005930",), (), followup_codes=()))
        self.assertFalse(controller.start(("000660",), (), followup_codes=()))


if __name__ == "__main__":
    unittest.main()
