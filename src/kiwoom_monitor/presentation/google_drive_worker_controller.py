"""Google Drive 동기화 QThread의 생성과 결과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService
from kiwoom_monitor.presentation.background_workers import GoogleDriveSyncWorker


class GoogleDriveWorkerController(QObject):
    completed = Signal(str)
    metadata_received = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[GoogleDriveSyncService, str, str, bool], GoogleDriveSyncWorker] = GoogleDriveSyncWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: GoogleDriveSyncWorker | None = None

    @property
    def worker(self) -> GoogleDriveSyncWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(
        self,
        service: GoogleDriveSyncService,
        operation: str,
        target: str,
        interactive: bool,
    ) -> bool:
        if self.is_running:
            return False
        worker = self._worker_factory(service, operation, target, interactive)
        worker.setParent(self)
        if not isinstance(worker, GoogleDriveSyncWorker):
            self.failed.emit(
                "Google Drive 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.completed.connect(lambda message, current=worker: self._complete(current, message))
        worker.metadata_received.connect(lambda value, current=worker: self._metadata(current, value))
        worker.failed.connect(lambda message, current=worker: self._fail(current, message))
        worker.finished.connect(self._finish)
        self._worker = worker
        worker.start()
        return True

    def _release(self, worker: GoogleDriveSyncWorker) -> None:
        if self._worker is worker:
            self._worker = None

    def _finish(self) -> None:
        worker = self.sender()
        if isinstance(worker, GoogleDriveSyncWorker) and not worker.isRunning():
            self._release(worker)
            worker.deleteLater()

    def _complete(self, worker: GoogleDriveSyncWorker, message: str) -> None:
        self._release(worker)
        self.completed.emit(message)

    def _metadata(self, worker: GoogleDriveSyncWorker, value: str) -> None:
        self._release(worker)
        self.metadata_received.emit(value)

    def _fail(self, worker: GoogleDriveSyncWorker, message: str) -> None:
        self._release(worker)
        self.failed.emit(message)
