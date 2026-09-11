from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


@dataclass
class CentralMinuteBar:
    trading_date: str
    minute: str
    code: str
    market: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_value_million_won: int
    updated_at: float

    def as_record(self) -> dict[str, object]:
        return asdict(self)


class MinuteBarAccumulator:
    """중앙 0B 체결로 거래소별 1분 OHLCV를 만든다."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, str, str], CentralMinuteBar] = {}
        self._cumulative: dict[tuple[str, str, str], int] = {}
        self._dirty: set[tuple[str, str, str, str]] = set()
        self._trading_date = ""

    def add(self, tick: TradeTick, now: datetime, received_at: float) -> None:
        if tick.current_price is None or not tick.trade_time or len(tick.trade_time) < 4:
            return
        trading_date = now.date().isoformat()
        if trading_date != self._trading_date:
            self._cumulative.clear()
            self._trading_date = trading_date
        minute = f"{tick.trade_time[:2]}:{tick.trade_time[2:4]}"
        market = tick.market or "KRX"
        key = (trading_date, minute, tick.code, market)
        price = abs(int(tick.current_price))
        volume = abs(int(tick.trade_volume or 0))
        trade_value = self._trade_value_increment(tick, trading_date, market)
        bar = self._bars.get(key)
        if bar is None:
            bar = CentralMinuteBar(
                trading_date, minute, tick.code, market, price, price, price, price,
                volume, trade_value, received_at,
            )
            self._bars[key] = bar
        else:
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += volume
            bar.trade_value_million_won += trade_value
            bar.updated_at = received_at
        self._dirty.add(key)

    def drain_dirty(self) -> list[dict[str, object]]:
        values = [self._bars.pop(key).as_record() for key in self._dirty]
        self._dirty.clear()
        return values

    def _trade_value_increment(self, tick: TradeTick, trading_date: str, market: str) -> int:
        if tick.cumulative_trade_value is None:
            return 0
        key = (trading_date, tick.code, market)
        current = max(0, int(tick.cumulative_trade_value))
        previous = self._cumulative.get(key)
        self._cumulative[key] = current
        if previous is None or current < previous:
            return 0
        return current - previous
