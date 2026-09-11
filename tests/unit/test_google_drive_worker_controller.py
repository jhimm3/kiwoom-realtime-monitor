from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.presentation.background_workers import GoogleDriveSyncWorker
from kiwoom_monitor.presentation.google_drive_worker_controller import (
    GoogleDriveWorkerController,
)


class FakeGoogleDriveWorker(GoogleDriveSyncWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class GoogleDriveWorkerControllerTests(unittest.TestCase):
    def test_start_routes_results_and_releases_current_worker(self) -> None:
        workers = [FakeGoogleDriveWorker(), FakeGoogleDriveWorker()]
        controller = GoogleDriveWorkerController(worker_factory=lambda *_args: workers.pop(0))
        completed: list[str] = []
        metadata: list[str] = []
        controller.completed.connect(completed.append)
        controller.metadata_received.connect(metadata.append)

        self.assertTrue(controller.start(object(), "metadata", "both", False))  # type: ignore[arg-type]
        first = controller.worker
        assert first is not None
        first.metadata_received.emit("2026-09-11T00:00:00Z")
        self.assertIsNone(controller.worker)
        self.assertEqual(["2026-09-11T00:00:00Z"], metadata)

        self.assertTrue(controller.start(object(), "upload", "both", True))  # type: ignore[arg-type]
        second = controller.worker
        assert second is not None
        second.completed.emit("uploaded")
        self.assertIsNone(controller.worker)
        self.assertEqual(["uploaded"], completed)

    def test_running_worker_rejects_second_start_and_failure_releases_it(self) -> None:
        worker = FakeGoogleDriveWorker()
        calls: list[tuple[object, ...]] = []
        controller = GoogleDriveWorkerController(worker_factory=lambda *args: calls.append(args) or worker)
        failures: list[str] = []
        controller.failed.connect(failures.append)
        self.assertTrue(controller.start(object(), "upload", "both", False))  # type: ignore[arg-type]
        self.assertFalse(controller.start(object(), "download", "both", False))  # type: ignore[arg-type]
        worker.failed.emit("failed")
        self.assertEqual(1, len(calls))
        self.assertEqual(["failed"], failures)
        self.assertIsNone(controller.worker)

    def test_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = GoogleDriveWorkerController(worker_factory=lambda *_args: InvalidWorker())  # type: ignore[arg-type,return-value]
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(object(), "upload", "both", False))  # type: ignore[arg-type]
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
