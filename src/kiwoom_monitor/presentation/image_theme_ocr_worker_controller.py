"""이미지 테마 OCR QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeOcrWorker


class ImageThemeOcrWorkerController(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[tuple[Path, ...], str, str], ImageThemeOcrWorker] = ImageThemeOcrWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: ImageThemeOcrWorker | None = None

    @property
    def worker(self) -> ImageThemeOcrWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self, image_paths: tuple[Path, ...], mode: str, theme_header: str) -> bool:
        if self.is_running:
            return True
        worker = self._worker_factory(image_paths, mode, theme_header)
        worker.setParent(self)
        if not isinstance(worker, ImageThemeOcrWorker):
            self.failed.emit(
                "이미지 OCR 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.progress.connect(self.progress.emit)
        worker.completed.connect(self.completed.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(self.finished.emit)
        worker.finished.connect(self._release_finished_worker)
        self._worker = worker
        worker.start(QThread.Priority.LowPriority)
        return True

    def _release_finished_worker(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def request_interruption(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def stop_for_shutdown(self, timeout_ms: int = 500) -> bool:
        """OCR native call이 중단 요청을 못 받을 때 앱 종료만은 막지 않게 한다."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return True
        worker.requestInterruption()
        if worker.wait(max(0, timeout_ms)):
            return True
        # Paddle 모델 준비/predict는 QThread interruption을 확인할 수 없는
        # native 호출이다. 앱 종료 시에만 해당 보조 worker를 강제 종료한다.
        worker.terminate()
        return worker.wait(1_500)
