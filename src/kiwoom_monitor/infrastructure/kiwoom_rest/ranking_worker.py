from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QThread, Signal


class RankingLoader(Protocol):
    def load_top_stocks(self) -> tuple[object, ...]: ...


class RankingWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, loader: RankingLoader) -> None:
        super().__init__()
        self._loader = loader

    def run(self) -> None:
        try:
            stocks = self._loader.load_top_stocks()
        except Exception as error:
            self.failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.completed.emit(stocks)

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
