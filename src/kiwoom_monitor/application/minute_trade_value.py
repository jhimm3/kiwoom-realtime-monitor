"""체결 틱을 1분 OHLCV로 집계하고 영웅문 거래대금을 계산한다."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


@dataclass(frozen=True)
class MinuteOhlcv:
    minute: datetime
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int

    @property
    def trade_value_eok(self) -> float:
        """V × (H + O + L + C) ÷ 4 ÷ 100,000,000"""
        return self.volume * (self.high_price + self.open_price + self.low_price + self.close_price) / 4 / 100_000_000


class MinuteTradeValueAggregator:
    """현재 접속 뒤 수신한 체결을 종목별 1분봉으로 집계한다."""

    def __init__(self, max_minutes: int = 390) -> None:
        self._max_minutes = max_minutes
        self._bars: dict[str, deque[MinuteOhlcv]] = defaultdict(lambda: deque(maxlen=max_minutes))

    def ingest(self, tick: TradeTick, observed_at: datetime) -> MinuteOhlcv | None:
        if tick.current_price is None or tick.trade_volume is None:
            return None
        minute = _trade_minute(tick, observed_at)
        bars = self._bars[tick.code]
        volume = abs(tick.trade_volume)
        if bars and bars[-1].minute == minute:
            previous = bars.pop()
            bar = MinuteOhlcv(
                minute=minute,
                open_price=previous.open_price,
                high_price=max(previous.high_price, tick.current_price),
                low_price=min(previous.low_price, tick.current_price),
                close_price=tick.current_price,
                volume=previous.volume + volume,
            )
        else:
            bar = MinuteOhlcv(minute, tick.current_price, tick.current_price, tick.current_price, tick.current_price, volume)
        bars.append(bar)
        return bar

    def trade_value_eok(self, code: str, minutes: int) -> float:
        """최근 N개의 수신 1분봉 거래대금을 합산한다."""
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        return sum(bar.trade_value_eok for bar in list(self._bars.get(code, ()))[-minutes:])

    def seed(self, code: str, bars: tuple[MinuteOhlcv, ...]) -> None:
        """REST로 받은 과거 1분봉을 시간순으로 넣어 접속 전 누락분을 보완한다."""
        now = datetime.now()
        current_minute = now.replace(second=0, microsecond=0)
        ordered = sorted(
            (bar for bar in bars if bar.minute.date() == now.date() and bar.minute < current_minute),
            key=lambda bar: bar.minute,
        )
        self._bars[code] = deque(ordered[-self._max_minutes :], maxlen=self._max_minutes)

    def today_trade_value_eok(self, code: str) -> float:
        today = datetime.now().date()
        return sum(bar.trade_value_eok for bar in self._bars.get(code, ()) if bar.minute.date() == today)


def _trade_minute(tick: TradeTick, observed_at: datetime) -> datetime:
    """수신 지연과 관계없이 0B 체결시각으로 1분봉을 구분한다."""
    value = (tick.trade_time or "").strip()
    if len(value) == 6 and value.isdigit():
        return observed_at.replace(hour=int(value[:2]), minute=int(value[2:4]), second=0, microsecond=0)
    return observed_at.replace(second=0, microsecond=0)
