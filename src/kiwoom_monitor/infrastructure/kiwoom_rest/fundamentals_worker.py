from __future__ import annotations
from PySide6.QtCore import QThread, Signal
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService

class FundamentalsWorker(QThread):
    received = Signal(str, object)
    failed = Signal(str)
    completed = Signal()
    def __init__(self, service: StockFundamentalsService, codes: tuple[str, ...]) -> None:
        super().__init__(); self._service = service; self._codes = codes
    def run(self) -> None:
        for code in self._codes:
            if self.isInterruptionRequested(): return
            try: self.received.emit(code, self._service.load(code))
            except Exception as error:
                self.failed.emit(f"{code} 기본정보 보강 실패: {error}")
        if not self.isInterruptionRequested():
            self.completed.emit()
    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
