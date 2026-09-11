from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.presentation.top20_market_repair_worker_controller import (
    Top20MarketRepairWorkerController,
)
from kiwoom_monitor.presentation.top20_trade_value import Top20MarketRepairWorker


class FakeTop20MarketRepairWorker(Top20MarketRepairWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False
        self.priority: QThread.Priority | None = None

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        self.running = True
        self.priority = priority

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class Top20MarketRepairWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_signals_and_uses_low_priority(self) -> None:
        worker = FakeTop20MarketRepairWorker()
        controller = Top20MarketRepairWorkerController(worker_factory=lambda _repository: worker)
        completed: list[int] = []
        failures: list[str] = []
        controller.completed.connect(completed.append)
        controller.failed.connect(failures.append)

        self.assertTrue(controller.start(object()))  # type: ignore[arg-type]
        worker.completed.emit(3)
        worker.failed.emit("failed")

        self.assertEqual([3], completed)
        self.assertEqual(["failed"], failures)
        self.assertEqual(QThread.Priority.LowPriority, worker.priority)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeTop20MarketRepairWorker()
        calls: list[object] = []
        controller = Top20MarketRepairWorkerController(worker_factory=lambda repository: calls.append(repository) or worker)
        repository = object()
        self.assertTrue(controller.start(repository))  # type: ignore[arg-type]
        self.assertTrue(controller.start(object()))  # type: ignore[arg-type]
        self.assertEqual([repository], calls)

    def test_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = Top20MarketRepairWorkerController(worker_factory=lambda _repository: InvalidWorker())  # type: ignore[arg-type,return-value]
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(object()))  # type: ignore[arg-type]
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
