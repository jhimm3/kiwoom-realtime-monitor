from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.presentation.fundamentals_worker_controller import (
    FundamentalsWorkerController,
)


class FakeFundamentalsWorker(FundamentalsWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class FundamentalsWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_result_failure_and_completion_context(self) -> None:
        worker = FakeFundamentalsWorker()
        controller = FundamentalsWorkerController(worker_factory=lambda _codes: worker)
        received: list[tuple[str, object]] = []
        failures: list[str] = []
        completed: list[object] = []
        controller.received.connect(lambda code, value: received.append((code, value)))
        controller.failed.connect(failures.append)
        controller.completed.connect(completed.append)

        self.assertTrue(
            controller.start(("005930",), followup_codes=("005930", "000660"))
        )
        worker.received.emit("005930", "fundamentals")
        worker.failed.emit("failed")
        worker.completed.emit()

        self.assertEqual([("005930", "fundamentals")], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([("005930", "000660")], completed)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeFundamentalsWorker()
        calls: list[tuple[str, ...]] = []
        controller = FundamentalsWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",), followup_codes=()))
        self.assertTrue(controller.start(("000660",), followup_codes=()))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = FundamentalsWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",), followup_codes=()))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",), followup_codes=()))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
