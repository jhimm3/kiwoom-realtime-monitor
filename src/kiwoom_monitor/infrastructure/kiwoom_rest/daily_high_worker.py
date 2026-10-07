from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.daily_high_service import DailyHighService


class DailyHighWorker(QThread):
    received = Signal(str, object)
    failed = Signal(str)

    def __init__(self, service: DailyHighService, codes: tuple[str, ...]) -> None:
        super().__init__(); self._service = service; self._codes = codes

    def run(self) -> None:
        for code in self._codes:
            if self.isInterruptionRequested(): return
            try: self.received.emit(code, self._service.load(code))
            except Exception as error:
                self.failed.emit(f"{code} 기간 신고가 조회 실패: {error}")

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
