from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import (
    HistoricalHighWorker,
)
from kiwoom_monitor.presentation.historical_high_worker_controller import (
    HistoricalHighWorkerController,
)


class FakeHistoricalHighWorker(HistoricalHighWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class HistoricalHighWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_result_failure_and_finished(self) -> None:
        worker = FakeHistoricalHighWorker()
        controller = HistoricalHighWorkerController(
            worker_factory=lambda _codes: worker
        )
        received: list[tuple[str, object]] = []
        failures: list[str] = []
        finished: list[bool] = []
        controller.received.connect(lambda code, value: received.append((code, value)))
        controller.failed.connect(failures.append)
        controller.finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start(("005930",)))
        worker.received.emit("005930", "target")
        worker.failed.emit("failed")
        worker.finished.emit()

        self.assertEqual([("005930", "target")], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeHistoricalHighWorker()
        calls: list[tuple[str, ...]] = []
        controller = HistoricalHighWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",)))
        self.assertTrue(controller.start(("000660",)))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = HistoricalHighWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",)))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",)))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
