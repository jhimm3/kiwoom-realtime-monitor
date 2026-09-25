"""실시간 분봉·현재가·시가총액 캐시를 GUI 스레드 밖에서 저장한다."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from queue import Empty, Queue

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository


class MarketCacheWriter(QThread):
    """한 SQLite 파일의 실시간 캐시 쓰기를 직렬화해 화면 정지를 막는다."""

    minute_saved = Signal()
    minute_failed = Signal(object, object, str)
    price_failed = Signal(object, object, object, object, str)
    history_saved = Signal(str)
    history_failed = Signal(str, str)
    daily_high_failed = Signal(str, str)
    comparison_saved = Signal(int)
    comparison_failed = Signal(str)
    fundamentals_failed = Signal(str, str)

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self._database_path = database_path
        self._queue: Queue[tuple[object, ...]] = Queue()

    def enqueue_minute_bars(
        self,
        bars: dict[tuple[str, datetime], MinuteOhlcv],
        market_bars: dict[
            tuple[str, datetime], tuple[float, float, float, float, float | None]
        ],
    ) -> None:
        if bars or market_bars:
            self._queue.put(("minute", dict(bars), dict(market_bars)))

    def enqueue_price_cache(
        self, prices: dict[str, int], highs: dict[str, int],
        market_caps: dict[str, float], trade_date: date,
    ) -> None:
        if prices or highs or market_caps:
            self._queue.put(
                ("price", dict(prices), dict(highs), dict(market_caps), trade_date)
            )

    def enqueue_history_bars(
        self, code: str, bars: tuple[MinuteOhlcv, ...], trade_date: date,
        synced_at: datetime,
    ) -> None:
        if bars:
            self._queue.put(("history", code, tuple(bars), trade_date, synced_at))

    def enqueue_daily_high(self, code: str, targets: DailyHighTargets,
                           trade_date: date, observed_at: datetime) -> None:
        self._queue.put(("daily_high", code, targets, trade_date, observed_at))

    def enqueue_fundamentals(self, code: str, market_cap_eok: float | None,
                             float_ratio_percent: float | None,
                             float_shares: int | None,
                             upper_limit_price: int | None) -> None:
        self._queue.put(("fundamentals", code, market_cap_eok,
                         float_ratio_percent, float_shares, upper_limit_price))

    def enqueue_trade_comparisons(
        self, values: dict[str, tuple[tuple[str, float], ...]], trade_date: date,
    ) -> None:
        if values:
            self._queue.put(("comparison", dict(values), trade_date))

    def stop_and_drain(self, timeout_ms: int = 7_000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)

    def run(self) -> None:
        minute_repository = MinuteBarRepository(self._database_path)
        stock_repository = StockRepository(self._database_path)
        daily_repository = DailyBarRepository(self._database_path)
        while not self.isInterruptionRequested() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if job[0] == "minute":
                self._save_minute(minute_repository, job[1], job[2])
            elif job[0] == "price":
                self._save_prices(stock_repository, job[1], job[2], job[3], job[4])
            elif job[0] == "history":
                self._save_history(minute_repository, job[1], job[2], job[3], job[4])
            elif job[0] == "daily_high":
                self._save_daily_high(stock_repository, daily_repository, job[1], job[2], job[3], job[4])
            elif job[0] == "comparison":
                self._save_comparison(minute_repository, job[1], job[2])
            elif job[0] == "fundamentals":
                self._save_fundamentals(stock_repository, job[1], job[2], job[3], job[4], job[5])

    def _save_fundamentals(self, repository: StockRepository, code: str,
                           market_cap_eok: float | None, float_ratio_percent: float | None,
                           float_shares: int | None, upper_limit_price: int | None) -> None:
        try:
            repository.update_fundamentals(
                code, market_cap_eok, float_ratio_percent, None,
                float_shares, upper_limit_price,
            )
        except Exception as error:
            self.fundamentals_failed.emit(code, str(error))

    def _save_daily_high(self, stock_repository: StockRepository,
                         daily_repository: DailyBarRepository, code: str,
                         targets: DailyHighTargets, trade_date: date,
                         observed_at: datetime) -> None:
        try:
            stock_repository.update_adjusted_high_250_price(code, targets.high_250_price)
            daily_repository.upsert_targets(code, targets, trade_date, observed_at=observed_at)
        except Exception as error:
            self.daily_high_failed.emit(code, str(error))

    def _save_comparison(self, repository: MinuteBarRepository,
                         values: dict[str, tuple[tuple[str, float], ...]],
                         trade_date: date) -> None:
        try:
            count = repository.update_comparison_reports(values, trade_date)
        except Exception as error:
            self.comparison_failed.emit(str(error))
            return
        if count:
            self.comparison_saved.emit(count)

    def _save_minute(
        self,
        repository: MinuteBarRepository,
        pending: dict[tuple[str, datetime], MinuteOhlcv],
        market_pending: dict[
            tuple[str, datetime], tuple[float, float, float, float, float | None]
        ],
    ) -> None:
        grouped: dict[str, list[MinuteOhlcv]] = {}
        for (code, _), bar in pending.items():
            grouped.setdefault(code, []).append(bar)
        try:
            repository.upsert_many({code: tuple(bars) for code, bars in grouped.items()})
            repository.upsert_market_index_minutes(market_pending)
        except Exception as error:
            self.minute_failed.emit(pending, market_pending, str(error))
            return
        self.minute_saved.emit()

    def _save_prices(
        self,
        repository: StockRepository,
        prices: dict[str, int],
        highs: dict[str, int],
        market_caps: dict[str, float],
        trade_date: date,
    ) -> None:
        try:
            repository.update_last_prices(prices)
            repository.update_last_market_caps(market_caps)
            repository.update_intraday_highs(highs, trade_date)
        except Exception as error:
            self.price_failed.emit(prices, highs, market_caps, trade_date, str(error))

    def _save_history(
        self, repository: MinuteBarRepository, code: str,
        bars: tuple[MinuteOhlcv, ...], trade_date: date, synced_at: datetime,
    ) -> None:
        try:
            repository.upsert_bars(code, bars)
            repository.record_history_sync(code, trade_date, synced_at, len(bars))
        except Exception as error:
            self.history_failed.emit(code, str(error))
            return
        self.history_saved.emit(code)
