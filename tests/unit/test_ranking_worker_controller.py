from __future__ import annotations

import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.ranking_worker_controller import RankingWorkerController
from kiwoom_monitor.infrastructure.kiwoom_rest.ranking_worker import RankingWorker


class FakeLoader:
    def __init__(self, result: tuple[object, ...] = (), error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def load_top_stocks(self) -> tuple[object, ...]:
        if self.error is not None:
            raise self.error
        return self.result


class BlockingLoader:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def load_top_stocks(self) -> tuple[object, ...]:
        self.entered.set()
        self.release.wait(2)
        return ()


class RankingWorkerControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_controller_forwards_completed_result(self) -> None:
        controller = RankingWorkerController()
        received: list[object] = []
        controller.completed.connect(received.append)

        self.assertTrue(controller.start(FakeLoader(("stock",))))
        assert controller.worker is not None
        controller.worker.wait()
        QApplication.processEvents()

        self.assertEqual([("stock",)], received)
        self.assertFalse(controller.is_running)

    def test_controller_forwards_failure(self) -> None:
        controller = RankingWorkerController()
        failures: list[str] = []
        controller.failed.connect(failures.append)

        self.assertTrue(controller.start(FakeLoader(error=RuntimeError("ranking failed"))))
        assert controller.worker is not None
        controller.worker.wait()
        QApplication.processEvents()

        self.assertEqual(["ranking failed"], failures)

    def test_running_worker_rejects_duplicate_start(self) -> None:
        loader = BlockingLoader()
        controller = RankingWorkerController()
        self.assertTrue(controller.start(loader))
        self.assertTrue(loader.entered.wait(1))
        self.assertFalse(controller.start(FakeLoader()))
        loader.release.set()
        assert controller.worker is not None
        controller.worker.wait()

    def test_completed_workers_are_released_between_repeated_runs(self) -> None:
        controller = RankingWorkerController()
        for _ in range(5):
            self.assertTrue(controller.start(FakeLoader()))
            worker = controller.worker
            assert worker is not None
            worker.wait()
            QApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
            self.assertIsNone(controller.worker)

        self.assertEqual([], controller.findChildren(RankingWorker))


if __name__ == "__main__":
    unittest.main()
