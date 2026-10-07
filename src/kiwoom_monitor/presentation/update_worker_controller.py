"""업데이트 확인·다운로드 QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.update_planner import UpdateStep
from kiwoom_monitor.presentation.background_workers import (
    UpdateCheckWorker,
    UpdateDownloadWorker,
)


class UpdateWorkerController(QObject):
    check_completed = Signal(object, bool)
    check_failed = Signal(str, bool)
    download_progress = Signal(int)
    download_completed = Signal(object)
    download_failed = Signal(str)
    download_finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        check_factory: Callable[[], UpdateCheckWorker] = UpdateCheckWorker,
        download_factory: Callable[[tuple[UpdateStep, ...]], UpdateDownloadWorker] = UpdateDownloadWorker,
    ) -> None:
        super().__init__(parent)
        self._check_factory = check_factory
        self._download_factory = download_factory
        self._check_worker: UpdateCheckWorker | None = None
        self._download_worker: UpdateDownloadWorker | None = None

    @property
    def check_worker(self) -> UpdateCheckWorker | None:
        return self._check_worker

    @property
    def download_worker(self) -> UpdateDownloadWorker | None:
        return self._download_worker

    @property
    def is_check_running(self) -> bool:
        return self._check_worker is not None and self._check_worker.isRunning()

    @property
    def is_download_running(self) -> bool:
        return self._download_worker is not None and self._download_worker.isRunning()

    def start_check(self, *, silent: bool) -> bool:
        if self.is_check_running:
            return False
        worker = self._check_factory()
        worker.setParent(self)
        if not isinstance(worker, UpdateCheckWorker):
            self.check_failed.emit(
                "업데이트 확인 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요.",
                silent,
            )
            worker.deleteLater()
            return False
        worker.completed.connect(lambda plan: self.check_completed.emit(plan, silent))
        worker.failed.connect(lambda message: self.check_failed.emit(message, silent))
        worker.finished.connect(self._finish_check)
        self._check_worker = worker
        worker.start()
        return True

    def start_download(self, steps: tuple[UpdateStep, ...]) -> bool:
        if self.is_download_running:
            return False
        worker = self._download_factory(steps)
        worker.setParent(self)
        if not isinstance(worker, UpdateDownloadWorker):
            self.download_failed.emit(
                "업데이트 다운로드 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.progress.connect(self.download_progress.emit)
        worker.completed.connect(self.download_completed.emit)
        worker.failed.connect(self.download_failed.emit)
        worker.finished.connect(self._finish_download)
        self._download_worker = worker
        worker.start()
        return True

    def request_download_interruption(self) -> None:
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.requestInterruption()

    def _finish_check(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._check_worker is worker:
            self._check_worker = None
        worker.deleteLater()

    def _finish_download(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._download_worker is worker:
            self._download_worker = None
        worker.deleteLater()
        self.download_finished.emit()
