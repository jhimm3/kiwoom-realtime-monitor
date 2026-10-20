from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeOcrWorker
from kiwoom_monitor.presentation.image_theme_ocr_worker_controller import (
    ImageThemeOcrWorkerController,
)


class FakeImageThemeOcrWorker(ImageThemeOcrWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False
        self.priority: QThread.Priority | None = None
        self.interrupted = False
        self.terminated = False
        self.wait_result = True

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        self.running = True
        self.priority = priority

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True

    def wait(self, _timeout: int = 0) -> bool:
        return self.wait_result

    def terminate(self) -> None:
        self.terminated = True
        self.running = False


class InvalidWorker(QObject):
    pass


class ImageThemeOcrWorkerControllerTests(unittest.TestCase):
    def test_real_worker_finishes_releases_and_cancels_after_initial_prediction(self) -> None:
        import tempfile
        from threading import Event
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from PIL import Image
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeRow

        app = QApplication.instance() or QApplication([])
        type(self)._qt_app = app
        page = {"rec_texts": ("종목명", "테마", "심텍", "반도체"), "rec_polys": (
            ((40, 16), (60, 16), (60, 24), (40, 24)),
            ((290, 16), (310, 16), (310, 24), (290, 24)),
            ((40, 56), (60, 56), (60, 64), (40, 64)),
            ((290, 56), (310, 56), (310, 64), (290, 64)),
        )}
        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory) / name for name in ("first.png", "second.png"))
            for path in paths:
                Image.new("RGB", (400, 100), "white").save(path)
            for outcome in ("completed", "failed", "cancelled"):
                with self.subTest(outcome=outcome):
                    entered, release = Event(), Event()
                    calls, workers = [], []
                    def predict(source):
                        calls.append(source)
                        if len(calls) == 1:
                            entered.set()
                            if not release.wait(5):
                                raise TimeoutError("test did not release OCR prediction")
                            if outcome == "failed":
                                raise ValueError("native predict failed")
                        yield page
                    factory = Mock(return_value=SimpleNamespace(predict=predict))
                    def make_worker(image_paths, mode, header):
                        worker = ImageThemeOcrWorker(image_paths, mode, header)
                        workers.append(worker)
                        return worker
                    controller = ImageThemeOcrWorkerController(worker_factory=make_worker)
                    completed, failed, finished = [], [], []
                    controller.completed.connect(completed.append)
                    controller.failed.connect(failed.append)
                    controller.finished.connect(lambda: finished.append(True))
                    with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                        try:
                            self.assertTrue(controller.start(paths, "theme_column", "테마"))
                            self.assertTrue(entered.wait(5))
                            self.assertTrue(controller.is_running)
                            self.assertTrue(controller.start((Path("ignored.png"),), "both", "other"))
                            self.assertEqual(1, len(workers))
                            if outcome == "cancelled":
                                controller.request_interruption()
                            release.set()
                            self.assertTrue(workers[0].wait(5_000))
                            app.processEvents()
                            self.assertIsNone(controller.worker)
                            self.assertFalse(controller.is_running)
                            self.assertEqual([True], finished)
                            self.assertEqual(["native predict failed"] if outcome == "failed" else [], failed)
                            self.assertEqual([(ImageThemeRow("심텍", "반도체"),)] if outcome == "completed" else [], completed)
                            self.assertEqual(list(map(str, paths)) if outcome == "completed" else [str(paths[0])], calls)
                            factory.assert_called_once()
                        finally:
                            release.set()
                            if workers and workers[0].isRunning():
                                workers[0].requestInterruption()
                                self.assertTrue(workers[0].wait(5_000))
                            app.processEvents()
                            controller.deleteLater()
                            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_start_forwards_signals_and_uses_low_priority(self) -> None:
        worker = FakeImageThemeOcrWorker()
        controller = ImageThemeOcrWorkerController(worker_factory=lambda _paths, _mode, _header: worker)
        progress: list[str] = []
        completed: list[object] = []
        failures: list[str] = []
        finished: list[bool] = []
        controller.progress.connect(progress.append)
        controller.completed.connect(completed.append)
        controller.failed.connect(failures.append)
        controller.finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start((Path("theme.png"),), "both", "테마"))
        worker.progress.emit("working")
        worker.completed.emit(("row",))
        worker.failed.emit("failed")
        worker.running = False
        worker.finished.emit()

        self.assertEqual(["working"], progress)
        self.assertEqual([("row",)], completed)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)
        self.assertIsNone(controller.worker)
        self.assertEqual(QThread.Priority.LowPriority, worker.priority)

    def test_running_worker_is_reused_and_can_be_interrupted(self) -> None:
        worker = FakeImageThemeOcrWorker()
        calls: list[tuple[tuple[Path, ...], str, str]] = []
        controller = ImageThemeOcrWorkerController(worker_factory=lambda paths, mode, header: calls.append((paths, mode, header)) or worker)
        paths = (Path("theme.png"),)
        self.assertTrue(controller.start(paths, "both", "테마"))
        self.assertTrue(controller.start((Path("other.png"),), "theme_column", "테마"))
        controller.request_interruption()
        self.assertEqual([(paths, "both", "테마")], calls)
        self.assertTrue(worker.interrupted)

    def test_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = ImageThemeOcrWorkerController(worker_factory=lambda _paths, _mode, _header: InvalidWorker())  # type: ignore[arg-type,return-value]
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start((Path("theme.png"),), "both", "테마"))
        self.assertEqual(1, len(failures))

    def test_shutdown_force_stops_native_ocr_call_after_grace_period(self) -> None:
        worker = FakeImageThemeOcrWorker()
        worker.wait_result = False
        controller = ImageThemeOcrWorkerController(
            worker_factory=lambda _paths, _mode, _header: worker,
        )
        controller.start((Path("theme.png"),), "both", "테마")

        self.assertFalse(controller.stop_for_shutdown(timeout_ms=0))
        self.assertTrue(worker.interrupted)
        self.assertTrue(worker.terminated)


if __name__ == "__main__":
    unittest.main()
