from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.historical_high_service import HistoricalHighService


class HistoricalHighWorker(QThread):
    """순위·실시간 수신을 막지 않고 역사적 신고가를 낮은 우선순위로 준비한다."""

    received = Signal(str, object)
    failed = Signal(str)

    def __init__(self, service: HistoricalHighService, codes: tuple[str, ...]) -> None:
        super().__init__()
        self._service = service
        self._codes = codes

    def run(self) -> None:
        for code in self._codes:
            if self.isInterruptionRequested():
                return
            try:
                self.received.emit(code, self._service.load(code))
            except Exception as error:
                self.failed.emit(f"{code} 역사적 신고가 조회 실패: {error}")

    def stop(self, timeout_ms: int = 3_000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
