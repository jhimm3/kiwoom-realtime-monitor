"""신고가 목록 조회가 화면을 멈추지 않도록 별도 작업에서 실행한다."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QThread, Signal


class NewHighLoader(Protocol):
    def refresh_new_highs(self) -> None: ...


class NewHighWorker(QThread):
    completed = Signal()
    failed = Signal(str)

    def __init__(self, loader: NewHighLoader) -> None:
        super().__init__()
        self._loader = loader

    def run(self) -> None:
        try:
            self._loader.refresh_new_highs()
        except Exception as error:
            self.failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.completed.emit()

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
