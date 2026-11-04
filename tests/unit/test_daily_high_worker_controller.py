from __future__ import annotations

import unittest
import weakref
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.presentation.daily_high_worker_controller import (
    DailyHighWorkerController,
)


class FakeDailyHighWorker(DailyHighWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class DailyHighWorkerControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_real_finished_worker_releases_controller_before_deferred_delete(self) -> None:
        worker = DailyHighWorker(SimpleNamespace(load=lambda _code: "targets"), ("005930",))
        controller = DailyHighWorkerController(worker_factory=lambda _codes: worker)
        finished: list[tuple[object, bool]] = []
        controller.finished.connect(
            lambda codes: finished.append((codes, QThread.currentThread() == self.app.thread()))
        )
        reference = weakref.ref(controller)
        self.assertTrue(controller.start(("005930",), followup_codes=("000660",)))
        self.assertTrue(worker.wait(5_000))
        self.app.processEvents()
        self.assertEqual([(("000660",), True)], finished)
        self.assertIsNone(controller.worker)
        del controller
        self.assertIsNone(reference())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(worker))

    def test_delayed_old_finish_keeps_its_own_followup_context(self) -> None:
        first, second = FakeDailyHighWorker(), FakeDailyHighWorker()
        workers = iter((first, second))
        controller = DailyHighWorkerController(worker_factory=lambda _codes: next(workers))
        finished: list[object] = []
        controller.finished.connect(finished.append)
        controller.start(("005930",), followup_codes=("005930",))
        first.running = False
        controller.start(("000660",), followup_codes=("000660",))
        first.finished.emit()
        self.assertEqual([("005930",)], finished)
        self.assertIs(second, controller.worker)
        second.running = False
        second.finished.emit()
        self.assertEqual([("005930",), ("000660",)], finished)
        self.assertIsNone(controller.worker)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_start_forwards_result_failure_and_finish_context(self) -> None:
        worker = FakeDailyHighWorker()
        controller = DailyHighWorkerController(worker_factory=lambda _codes: worker)
        received: list[tuple[str, object]] = []
        failures: list[str] = []
        finished: list[object] = []
        controller.received.connect(lambda code, value: received.append((code, value)))
        controller.failed.connect(failures.append)
        controller.finished.connect(finished.append)

        self.assertTrue(
            controller.start(("005930",), followup_codes=("005930", "000660"))
        )
        worker.received.emit("005930", "targets")
        worker.failed.emit("failed")
        worker.running = False
        worker.finished.emit()

        self.assertEqual([("005930", "targets")], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([("005930", "000660")], finished)
        self.assertIsNone(controller.worker)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeDailyHighWorker()
        calls: list[tuple[str, ...]] = []
        controller = DailyHighWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",), followup_codes=()))
        self.assertTrue(controller.start(("000660",), followup_codes=()))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = DailyHighWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",), followup_codes=()))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",), followup_codes=()))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
