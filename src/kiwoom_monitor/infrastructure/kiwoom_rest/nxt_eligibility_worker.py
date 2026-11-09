from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService


class NxtEligibilityWorker(QThread):
    received = Signal(str, bool)
    failed = Signal(str)

    def __init__(self, service: NxtEligibilityService, codes: tuple[str, ...],
                 now_provider: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self._service = service
        self._codes = codes
        self._now = now_provider

    def run(self) -> None:
        if not self._codes and self._now is not None:
            try:
                self._service.prepare(self._now, self.isInterruptionRequested)
            except Exception as error:
                self.failed.emit(f"NXT 종목 목록 준비 실패: {error}")
            return
        for code in self._codes:
            if self.isInterruptionRequested():
                return
            try:
                self.received.emit(code, self._service.is_enabled(code, self._now))
            except Exception as error:
                self.failed.emit(f"{code} NXT 가능 여부 조회 실패: {error}")

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
