from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.presentation.background_workers import UpdateCheckWorker, UpdateDownloadWorker
from kiwoom_monitor.presentation.update_worker_controller import UpdateWorkerController


class FakeCheckWorker(UpdateCheckWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class FakeDownloadWorker(UpdateDownloadWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False
        self.interrupted = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True


class InvalidWorker(QObject):
    pass


class UpdateWorkerControllerTests(unittest.TestCase):
    def test_explicit_download_directory_reaches_existing_worker_factory(self) -> None:
        worker = FakeDownloadWorker()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "selected-profile" / "updates"
            factory = lambda steps, **options: worker
            from unittest.mock import Mock
            factory = Mock(side_effect=factory)
            controller = UpdateWorkerController(download_factory=factory)
            self.assertTrue(controller.start_download((), download_dir=folder))
            factory.assert_called_once_with((), download_dir=folder)
            worker.running = False
            worker.finished.emit()

    def test_download_worker_uses_owned_directory_and_preserves_default(self) -> None:
        from types import SimpleNamespace
        from kiwoom_monitor.presentation import background_workers as module
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                default_data = root / "default" / "data"
                chosen = root / "chosen" / "updates"
                worker = UpdateDownloadWorker((), download_dir=chosen if explicit else None)
                completed, failed = [], []
                worker.completed.connect(completed.append)
                worker.failed.connect(failed.append)
                with patch.object(module.AppPaths, "for_current_user", return_value=SimpleNamespace(data_dir=default_data)) as default:
                    worker.run()
                self.assertEqual([], failed)
                self.assertEqual([()], completed)
                self.assertTrue((chosen if explicit else default_data.parent / "updates").is_dir())
                self.assertEqual(0 if explicit else 1, default.call_count)
                self.assertFalse((default_data.parent if explicit else chosen).exists())

    def test_check_forwards_silent_context_and_releases_on_finish(self) -> None:
        worker = FakeCheckWorker()
        controller = UpdateWorkerController(check_factory=lambda: worker)
        completed: list[tuple[object, bool]] = []
        failures: list[tuple[str, bool]] = []
        controller.check_completed.connect(lambda plan, silent: completed.append((plan, silent)))
        controller.check_failed.connect(lambda message, silent: failures.append((message, silent)))

        self.assertTrue(controller.start_check(silent=True))
        worker.completed.emit("plan")
        worker.failed.emit("failed")
        worker.running = False
        worker.finished.emit()

        self.assertEqual([("plan", True)], completed)
        self.assertEqual([("failed", True)], failures)
        self.assertIsNone(controller.check_worker)

    def test_download_forwards_signals_and_supports_cancel(self) -> None:
        worker = FakeDownloadWorker()
        controller = UpdateWorkerController(download_factory=lambda _steps: worker)
        progress: list[int] = []
        completed: list[object] = []
        finished: list[bool] = []
        controller.download_progress.connect(progress.append)
        controller.download_completed.connect(completed.append)
        controller.download_finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start_download(()))
        controller.request_download_interruption()
        worker.progress.emit(40)
        worker.completed.emit(("update.zip",))
        worker.running = False
        worker.finished.emit()

        self.assertTrue(worker.interrupted)
        self.assertEqual([40], progress)
        self.assertEqual([("update.zip",)], completed)
        self.assertEqual([True], finished)
        self.assertIsNone(controller.download_worker)

    def test_running_and_invalid_workers_are_rejected(self) -> None:
        check = FakeCheckWorker()
        controller = UpdateWorkerController(check_factory=lambda: check)
        self.assertTrue(controller.start_check(silent=False))
        self.assertFalse(controller.start_check(silent=True))
        check.running = False
        check.finished.emit()

        failures: list[str] = []
        invalid = UpdateWorkerController(download_factory=lambda _steps: InvalidWorker())  # type: ignore[arg-type,return-value]
        invalid.download_failed.connect(failures.append)
        self.assertFalse(invalid.start_download(()))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
