"""매매일지의 키움 조회·보완 백그라운드 worker."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.market_index_chart_service import MarketIndexChartService
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.trade_chart import DailyTradeChartService
from kiwoom_monitor.application.trade_cost_service import TradeCostService
from kiwoom_monitor.application.trade_history_service import TradeFill, TradeHistoryService


class ConfirmWorker(QThread):
    completed = Signal(str, object, object, str)
    failed = Signal(str)

    def __init__(self, service: MinuteChartService, code: str, target_day: date | None = None, include_previous: bool = False) -> None:
        super().__init__()
        self._service, self._code, self._target_day, self._include_previous = service, code, target_day, include_previous

    def run(self) -> None:
        try:
            now = datetime.now()
            target = datetime.combine(self._target_day, time()) if self._target_day else now
            after_close = target.date() < now.date() or now.hour >= 20
            bars = (
                self._service.load_two_trading_days(self._code, target)
                if self._include_previous else
                (self._service.load_today(self._code, target) if after_close else self._service.load_recent(self._code, target))
            )
            current = now.replace(second=0, microsecond=0)
            self.completed.emit(
                self._code, tuple(bar for bar in bars if bar.minute < current), now,
                "after_close_confirmed" if after_close else "api_confirmed",
            )
        except Exception as error:
            self.failed.emit(str(error))


class MarketIndexBackfillWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: MarketIndexChartService, day: date) -> None:
        super().__init__()
        self._service, self._day = service, day

    def run(self) -> None:
        try:
            combined = {}
            daily = {}
            for market in ("kospi", "kosdaq"):
                combined.update(self._service.load(market, datetime.combine(self._day, time())))
                daily[market] = self._service.load_daily(market, datetime.combine(self._day, time()))
            self.completed.emit((combined, daily))
        except Exception as error:
            self.failed.emit(str(error))


class HistoryWorker(QThread):
    completed = Signal(object, object, object)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, service: TradeHistoryService, cost_service: TradeCostService, start: date, end: date) -> None:
        super().__init__()
        self._service, self._cost_service, self._start, self._end = service, cost_service, start, end

    def run(self) -> None:
        try:
            fills: list[TradeFill] = []
            day = self._end
            while day >= self._start:
                if self.isInterruptionRequested():
                    return
                if day.weekday() < 5:
                    self.progress.emit(f"과거 체결 조회 중 · {day:%Y-%m-%d}")
                    fills.extend(self._service.load_day(day))
                day -= timedelta(days=1)
            self.progress.emit("실제 수수료·세금 확인 중…")
            cost_error = ""
            try:
                costs = self._cost_service.load_period(self._start, self._end)
            except Exception as error:
                costs = ()
                cost_error = str(error)
            self.completed.emit((tuple(fills), costs, cost_error), self._start, self._end)
        except Exception as error:
            self.failed.emit(str(error))


class BackfillWorker(QThread):
    progress = Signal(str)
    item_completed = Signal(str, object, object)
    item_failed = Signal(str, object, str)
    completed = Signal(int, int)

    def __init__(self, service: MinuteChartService, tasks: tuple[tuple[str, date], ...]) -> None:
        super().__init__()
        self._service, self._tasks = service, tasks

    def run(self) -> None:
        success = failed = 0
        total = len(self._tasks)
        for index, (code, day) in enumerate(self._tasks, 1):
            if self.isInterruptionRequested():
                break
            self.progress.emit(f"분봉 보완 {index}/{total} · {day:%Y-%m-%d} · {code}")
            try:
                bars = tuple(
                    bar for bar in self._service.load_today(code, datetime.combine(day, time()))
                    if bar.minute.date() == day
                )
                if not bars:
                    raise ValueError("해당 거래일의 분봉이 반환되지 않았습니다.")
                self.item_completed.emit(code, day, bars)
                success += 1
            except Exception as error:
                self.item_failed.emit(code, day, str(error))
                failed += 1
        self.completed.emit(success, failed)


class DailyChartWorker(QThread):
    completed = Signal(str, object, str)
    failed = Signal(str)

    def __init__(self, service: DailyTradeChartService, code: str, day: date, target: str) -> None:
        super().__init__()
        self._service, self._code, self._day, self._target = service, code, day, target

    def run(self) -> None:
        try:
            rows = self._service.load(self._code, datetime.combine(self._day, time()))
            self.completed.emit(self._code, rows, self._target)
        except Exception as error:
            self.failed.emit(str(error))
