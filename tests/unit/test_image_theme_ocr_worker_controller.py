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

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        self.running = True
        self.priority = priority

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True


class InvalidWorker(QObject):
    pass


class ImageThemeOcrWorkerControllerTests(unittest.TestCase):
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
        worker.finished.emit()

        self.assertEqual(["working"], progress)
        self.assertEqual([("row",)], completed)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)
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


if __name__ == "__main__":
    unittest.main()
