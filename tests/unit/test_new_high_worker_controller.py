from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.kiwoom_rest.new_high_worker import NewHighWorker
from kiwoom_monitor.presentation.new_high_worker_controller import (
    NewHighWorkerController,
)


class FakeLoader:
    def refresh_new_highs(self) -> None:
        pass


class FakeNewHighWorker(NewHighWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class NewHighWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_completion_failure_and_finished(self) -> None:
        worker = FakeNewHighWorker()
        controller = NewHighWorkerController(worker_factory=lambda _loader: worker)
        completed: list[bool] = []
        failures: list[str] = []
        finished: list[bool] = []
        controller.completed.connect(lambda: completed.append(True))
        controller.failed.connect(failures.append)
        controller.finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start(FakeLoader()))
        worker.completed.emit()
        worker.failed.emit("failed")
        worker.finished.emit()

        self.assertEqual([True], completed)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeNewHighWorker()
        calls: list[object] = []
        controller = NewHighWorkerController(
            worker_factory=lambda loader: calls.append(loader) or worker
        )
        first = FakeLoader()
        self.assertTrue(controller.start(first))
        self.assertTrue(controller.start(FakeLoader()))
        self.assertEqual([first], calls)

    def test_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = NewHighWorkerController(
            worker_factory=lambda _loader: InvalidWorker()  # type: ignore[arg-type,return-value]
        )
        controller.failed.connect(failures.append)

        self.assertFalse(controller.start(FakeLoader()))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
