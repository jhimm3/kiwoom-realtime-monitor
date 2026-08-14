from __future__ import annotations
from PySide6.QtCore import QThread, Signal
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService

class FundamentalsWorker(QThread):
    received = Signal(str, object)
    def __init__(self, service: StockFundamentalsService, codes: tuple[str, ...]) -> None:
        super().__init__(); self._service = service; self._codes = codes
    def run(self) -> None:
        for code in self._codes:
            if self.isInterruptionRequested(): return
            try: self.received.emit(code, self._service.load(code))
            except Exception: continue
    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
