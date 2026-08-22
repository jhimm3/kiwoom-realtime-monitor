"""체결 틱을 1분 OHLCV로 집계하고 영웅문 거래대금을 계산한다."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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

    def __init__(self, max_minutes: int = 730) -> None:
        self._max_minutes = max_minutes
        self._bars: dict[str, deque[MinuteOhlcv]] = defaultdict(lambda: deque(maxlen=max_minutes))
        self._last_cumulative_volume: dict[str, int] = {}

    def ingest(self, tick: TradeTick, observed_at: datetime) -> MinuteOhlcv | None:
        if tick.current_price is None:
            return None
        minute = _trade_minute(tick, observed_at)
        bars = self._bars[tick.code]
        volume = self._trade_volume(tick)
        if volume is None:
            return None
        previous_index = next((index for index in range(len(bars) - 1, -1, -1) if bars[index].minute == minute), None)
        if previous_index is not None:
            previous = bars[previous_index]
            bar = MinuteOhlcv(
                minute=minute,
                open_price=previous.open_price,
                high_price=max(previous.high_price, tick.current_price),
                low_price=min(previous.low_price, tick.current_price),
                close_price=tick.current_price,
                volume=previous.volume + volume,
            )
            bars[previous_index] = bar
        else:
            bar = MinuteOhlcv(minute, tick.current_price, tick.current_price, tick.current_price, tick.current_price, volume)
            bars.append(bar)
        if len(bars) > 1 and bars[-1].minute < bars[-2].minute:
            self._bars[tick.code] = deque(sorted(bars, key=lambda value: value.minute)[-self._max_minutes :], maxlen=self._max_minutes)
        return bar

    def _trade_volume(self, tick: TradeTick) -> int | None:
        # 0B의 체결량은 한 건의 체결 수량이다. 누적거래량보다 우선해야
        # 수신 순서가 뒤바뀌거나 장 구분이 바뀔 때 누적값 차이가 분봉에
        # 한꺼번에 더해지는 일을 막을 수 있다.
        if tick.trade_volume is not None:
            return abs(tick.trade_volume)
        if tick.cumulative_volume is not None:
            cumulative = abs(tick.cumulative_volume)
            previous = self._last_cumulative_volume.get(tick.code)
            self._last_cumulative_volume[tick.code] = cumulative
            # 누적 거래량이 있는 체결은 차이만 사용한다. 일부 수신에서 거래량 필드가
            # 누적값으로 들어와 같은 값을 반복 합산하는 급증을 막는다.
            return max(0, cumulative - previous) if previous is not None else 0
        return None

    def trade_value_eok(self, code: str, minutes: int) -> float:
        """최근 N개의 수신 1분봉 거래대금을 합산한다."""
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        return sum(bar.trade_value_eok for bar in list(self._bars.get(code, ()))[-minutes:])

    def completed_trade_value_eok(self, code: str, minutes: int, now: datetime) -> float:
        """진행 중인 현재 분봉을 제외한 직전 완료 구간의 거래대금이다."""
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        current_minute = now.replace(second=0, microsecond=0)
        completed = [bar for bar in self._bars.get(code, ()) if bar.minute.date() == now.date() and bar.minute < current_minute]
        return sum(bar.trade_value_eok for bar in completed[-minutes:])

    def bucket_trade_value_eok(self, code: str, minutes: int, now: datetime, *, previous: bool = False) -> float:
        """시각 경계에 맞춘 현재/직전 N분봉 거래대금을 반환한다.

        예: 10:07의 5분은 10:05~10:09, 직전 5분은 10:00~10:04이다.
        """
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        current_minute = now.replace(second=0, microsecond=0)
        start = current_minute - timedelta(minutes=current_minute.minute % minutes)
        if previous:
            start -= timedelta(minutes=minutes)
        end = start + timedelta(minutes=minutes)
        return sum(bar.trade_value_eok for bar in self._bars.get(code, ()) if start <= bar.minute < end)

    def seed(self, code: str, bars: tuple[MinuteOhlcv, ...], now: datetime | None = None) -> None:
        """REST로 받은 과거 1분봉을 시간순으로 넣어 접속 전 누락분을 보완한다."""
        now = now or datetime.now()
        current_minute = now.replace(second=0, microsecond=0)
        by_minute = {
            bar.minute: bar for bar in bars if bar.minute.date() == now.date() and bar.minute < current_minute
        }
        # REST 응답이 아직 진행 중인 현재 분봉을 포함하더라도, 그 분은
        # 이미 0B 체결로 쌓인 값을 유지한다. 보완 시점에 실시간 값이
        # 사라지거나 서로 섞이는 일을 막는다.
        by_minute.update(
            {
                bar.minute: bar
                for bar in self._bars.get(code, ())
                if bar.minute.date() == now.date() and bar.minute >= current_minute
            }
        )
        ordered = sorted(by_minute.values(), key=lambda bar: bar.minute)
        self._bars[code] = deque(ordered[-self._max_minutes :], maxlen=self._max_minutes)

    def today_trade_value_eok(self, code: str, now: datetime | None = None) -> float:
        today = (now or datetime.now()).date()
        return sum(bar.trade_value_eok for bar in self._bars.get(code, ()) if bar.minute.date() == today)

    def completed_today_trade_value_eok(self, code: str, now: datetime) -> float:
        """당일 누적 중 현재 진행 분봉만 제외한 거래대금이다."""
        current_minute = now.replace(second=0, microsecond=0)
        return sum(bar.trade_value_eok for bar in self._bars.get(code, ()) if bar.minute.date() == now.date() and bar.minute < current_minute)

    def discard_before(self, today: date) -> None:
        """날짜가 바뀌면 전날 분봉과 누적 거래량 상태를 비운다."""
        self._bars = defaultdict(
            lambda: deque(maxlen=self._max_minutes),
            {
                code: deque(
                    (bar for bar in bars if bar.minute.date() >= today),
                    maxlen=self._max_minutes,
                )
                for code, bars in self._bars.items()
                if any(bar.minute.date() >= today for bar in bars)
            },
        )
        self._last_cumulative_volume.clear()


def _trade_minute(tick: TradeTick, observed_at: datetime) -> datetime:
    """수신 지연과 관계없이 0B 체결시각으로 1분봉을 구분한다."""
    value = (tick.trade_time or "").strip()
    if len(value) == 6 and value.isdigit():
        return observed_at.replace(hour=int(value[:2]), minute=int(value[2:4]), second=0, microsecond=0)
    return observed_at.replace(second=0, microsecond=0)
