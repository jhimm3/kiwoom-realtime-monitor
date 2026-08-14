"""Top 종목의 REST 1분봉을 화면을 멈추지 않고 순차 보완한다."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.minute_chart_service import MinuteChartService


class MinuteHistoryWorker(QThread):
    history_received = Signal(str, object)
    status_changed = Signal(str)
    failed = Signal(str)

    def __init__(self, service: MinuteChartService, codes: tuple[str, ...]) -> None:
        super().__init__()
        self._service = service
        self._codes = codes

    def run(self) -> None:
        for index, code in enumerate(self._codes, start=1):
            if self.isInterruptionRequested():
                return
            try:
                bars = self._service.load_today(code, datetime.now())
            except Exception as error:
                self.failed.emit(f"{code} 분봉 보완 실패: {error}")
                continue
            self.history_received.emit(code, bars)
            self.status_changed.emit(f"분봉 보완 중 · {index}/{len(self._codes)}종목")
        self.status_changed.emit("분봉 보완 완료")

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
