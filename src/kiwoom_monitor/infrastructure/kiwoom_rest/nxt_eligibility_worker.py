from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService


class NxtEligibilityWorker(QThread):
    received = Signal(str, bool)
    failed = Signal(str)

    def __init__(self, service: NxtEligibilityService, codes: tuple[str, ...]) -> None:
        super().__init__()
        self._service = service
        self._codes = codes

    def run(self) -> None:
        for code in self._codes:
            if self.isInterruptionRequested():
                return
            try:
                self.received.emit(code, self._service.is_enabled(code))
            except Exception as error:
                self.failed.emit(f"{code} NXT 가능 여부 조회 실패: {error}")

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
