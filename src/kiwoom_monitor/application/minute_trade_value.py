"""체결 틱을 1분 OHLCV로 집계하고 영웅문 거래대금을 계산한다."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
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
    # 0B의 누적거래대금(14) 증가분으로 받은 실제 금액이다. 값이 있으면
    # OHLC 평균가 추정식보다 우선한다.
    trade_value_eok_override: float | None = None

    @property
    def trade_value_eok(self) -> float:
        """V × (H + O + L + C) ÷ 4 ÷ 100,000,000"""
        if self.trade_value_eok_override is not None:
            return self.trade_value_eok_override
        return self.volume * (self.high_price + self.open_price + self.low_price + self.close_price) / 4 / 100_000_000


class MinuteTradeValueAggregator:
    """현재 접속 뒤 수신한 체결을 종목별 1분봉으로 집계한다."""

    def __init__(self, max_minutes: int = 730) -> None:
        self._max_minutes = max_minutes
        self._bars: dict[str, deque[MinuteOhlcv]] = defaultdict(lambda: deque(maxlen=max_minutes))
        self._last_cumulative_volume: dict[tuple[str, str], int] = {}
        self._last_cumulative_trade_value: dict[tuple[str, str], int] = {}
        self._estimated_since_cumulative: dict[tuple[str, str], dict[datetime, float]] = {}
        self._source_mode_by_code: dict[str, str] = {}

    def ingest(self, tick: TradeTick, observed_at: datetime) -> MinuteOhlcv | None:
        if tick.current_price is None:
            return None
        self._reset_baselines_on_source_mode_change(tick)
        key = (tick.code, tick.market)
        previous_cumulative = self._last_cumulative_trade_value.get(key)
        minute = _trade_minute(tick, observed_at)
        bars = self._bars[tick.code]
        volume = self._trade_volume(tick)
        trade_value_delta = self._trade_value_delta_eok(tick)
        if volume is None and trade_value_delta is None:
            return None
        volume = volume or 0
        if trade_value_delta is None and self._last_cumulative_trade_value.get(key, 0) > 0:
            estimates = self._estimated_since_cumulative.setdefault(key, {})
            estimates[minute] = estimates.get(minute, 0.0) + tick.current_price * volume / 100_000_000
        elif trade_value_delta is not None:
            estimated = self._estimated_since_cumulative.pop(key, {})
            if (
                estimated and previous_cumulative is not None and previous_cumulative > 0
                and tick.cumulative_trade_value is not None
                and abs(tick.cumulative_trade_value) >= previous_cumulative
            ):
                correction = trade_value_delta - sum(estimated.values())
                if correction < 0:
                    self._reduce_estimated_bars(tick.code, estimated, -correction)
                trade_value_delta = max(0.0, correction)
        previous_index = next((index for index in range(len(bars) - 1, -1, -1) if bars[index].minute == minute), None)
        if previous_index is not None:
            previous = bars[previous_index]
            if trade_value_delta is None:
                # 14가 일부 체결에서 비면 기존 15×현재가 추정으로만
                # 이어 붙인다. 14를 다시 받는 순간부터는 다시 실제 누적
                # 거래대금 차이를 우선한다.
                trade_value = (
                    previous.trade_value_eok_override + tick.current_price * volume / 100_000_000
                    if previous.trade_value_eok_override is not None
                    else None
                )
            else:
                trade_value = previous.trade_value_eok + trade_value_delta
            bar = MinuteOhlcv(
                minute=minute,
                open_price=previous.open_price,
                high_price=max(previous.high_price, tick.current_price),
                low_price=min(previous.low_price, tick.current_price),
                close_price=tick.current_price,
                volume=previous.volume + volume,
                trade_value_eok_override=trade_value,
            )
            bars[previous_index] = bar
        else:
            # 첫 누적거래대금은 접속 전 누적분까지 포함한다. 기준점으로만
            # 기억하고, 다음 수신부터의 증가분만 이 분봉에 반영한다.
            bar = MinuteOhlcv(
                minute,
                tick.current_price,
                tick.current_price,
                tick.current_price,
                tick.current_price,
                volume,
                trade_value_delta,
            )
            bars.append(bar)
        if len(bars) > 1 and bars[-1].minute < bars[-2].minute:
            self._bars[tick.code] = deque(sorted(bars, key=lambda value: value.minute)[-self._max_minutes :], maxlen=self._max_minutes)
        return bar

    def _reduce_estimated_bars(
        self, code: str, estimates: dict[datetime, float], excess: float,
    ) -> None:
        """Reconcile an overestimate against the minutes that contained it."""
        bars = self._bars[code]
        for minute, estimated in sorted(estimates.items(), reverse=True):
            if excess <= 0:
                break
            for index in range(len(bars) - 1, -1, -1):
                bar = bars[index]
                if bar.minute != minute:
                    continue
                reduction = min(excess, estimated, bar.trade_value_eok)
                bars[index] = replace(
                    bar, trade_value_eok_override=bar.trade_value_eok - reduction,
                )
                excess -= reduction
                break

    def _reset_baselines_on_source_mode_change(self, tick: TradeTick) -> None:
        """SOR와 거래소 상세가 전환될 때 비수신 구간 누적분을 더하지 않는다."""
        market = str(tick.market or "KRX").upper()
        mode = "SOR" if market == "SOR" else "DETAIL"
        previous = self._source_mode_by_code.get(tick.code)
        self._source_mode_by_code[tick.code] = mode
        if previous is None or previous == mode:
            return
        self._last_cumulative_volume = {
            key: value for key, value in self._last_cumulative_volume.items()
            if key[0] != tick.code
        }
        self._last_cumulative_trade_value = {
            key: value for key, value in self._last_cumulative_trade_value.items()
            if key[0] != tick.code
        }
        self._estimated_since_cumulative = {
            key: value for key, value in self._estimated_since_cumulative.items()
            if key[0] != tick.code
        }

    def _trade_volume(self, tick: TradeTick) -> int | None:
        # 0B의 체결량은 한 건의 체결 수량이다. 누적거래량보다 우선해야
        # 수신 순서가 뒤바뀌거나 장 구분이 바뀔 때 누적값 차이가 분봉에
        # 한꺼번에 더해지는 일을 막을 수 있다.
        if tick.trade_volume is not None:
            return abs(tick.trade_volume)
        if tick.cumulative_volume is not None:
            cumulative = abs(tick.cumulative_volume)
            key = (tick.code, tick.market)
            previous = self._last_cumulative_volume.get(key)
            self._last_cumulative_volume[key] = cumulative
            # 누적 거래량이 있는 체결은 차이만 사용한다. 일부 수신에서 거래량 필드가
            # 누적값으로 들어와 같은 값을 반복 합산하는 급증을 막는다.
            return max(0, cumulative - previous) if previous is not None else 0
        return None

    def _trade_value_delta_eok(self, tick: TradeTick) -> float | None:
        """0B 14(누적거래대금)의 증가분을 억 단위로 바꾼다.

        키움 0B의 14는 백만원 단위 누적값이므로 100을 나누면 억 단위가
        된다. 최초 수신과 누적값 초기화는 기준점만 갱신하고 0으로 처리해
        접속 전 거래대금이나 장 전환값이 한 번에 더해지는 일을 막는다.
        """
        if tick.cumulative_trade_value is None:
            return None
        cumulative = abs(tick.cumulative_trade_value)
        key = (tick.code, tick.market)
        previous = self._last_cumulative_trade_value.get(key)
        self._last_cumulative_trade_value[key] = cumulative
        # 신규·재구독 직후 키움이 먼저 누적값 0을 보낸 다음 실제 당일
        # 누적값을 보낼 수 있다. 0을 기준점으로 확정하면 순위에서 빠져
        # 있던 동안의 거래대금 전체가 현재 1분에 한꺼번에 들어간다.
        # 첫 양수 누적값까지 기준점으로만 사용하고 그다음 변화부터 더한다.
        if previous is None or previous == 0 or cumulative < previous:
            return 0.0
        return (cumulative - previous) / 100

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

    def seed(
        self, code: str, bars: tuple[MinuteOhlcv, ...], now: datetime | None = None,
        *, include_current_snapshot: bool = False,
    ) -> None:
        """REST로 받은 과거 1분봉을 시간순으로 넣어 접속 전 누락분을 보완한다."""
        now = now or datetime.now()
        current_minute = now.replace(second=0, microsecond=0)
        by_minute = {
            bar.minute: bar for bar in bars if bar.minute.date() == now.date() and bar.minute < current_minute
        }
        live_current = {
            bar.minute: bar
            for bar in self._bars.get(code, ())
            if bar.minute.date() == now.date() and bar.minute >= current_minute
        }
        if include_current_snapshot:
            # NAS는 앱이 열리기 전부터 현재 분봉을 계속 집계한다. 중앙
            # snapshot과 앱 접속 뒤 0B 누적이 겹칠 수 있으므로 합산하지
            # 않고 거래대금이 더 진행된 한 벌을 기준으로 이어 간다.
            for snapshot in bars:
                if snapshot.minute.date() != now.date() or snapshot.minute < current_minute:
                    continue
                live = live_current.get(snapshot.minute)
                by_minute[snapshot.minute] = (
                    snapshot
                    if live is None or snapshot.trade_value_eok >= live.trade_value_eok
                    else live
                )
            for minute, live in live_current.items():
                by_minute.setdefault(minute, live)
        else:
            # 직접 Kiwoom REST 응답의 진행 중 분봉은 실시간 수신과 시점이
            # 불명확하므로 기존처럼 이미 쌓인 0B 값을 유지한다.
            by_minute.update(live_current)
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
        self._last_cumulative_trade_value.clear()
        self._estimated_since_cumulative.clear()

    def reset_cumulative_baselines(self, codes: tuple[str, ...]) -> None:
        """신규 구독·재접속 뒤 공백 누적분을 현재 1분에 몰아 넣지 않는다."""
        selected = set(codes)
        self._last_cumulative_volume = {
            key: value for key, value in self._last_cumulative_volume.items() if key[0] not in selected
        }
        self._last_cumulative_trade_value = {
            key: value for key, value in self._last_cumulative_trade_value.items() if key[0] not in selected
        }
        self._estimated_since_cumulative = {
            key: value for key, value in self._estimated_since_cumulative.items() if key[0] not in selected
        }


def _trade_minute(tick: TradeTick, observed_at: datetime) -> datetime:
    """수신 지연과 관계없이 0B 체결시각으로 1분봉을 구분한다."""
    value = (tick.trade_time or "").strip()
    if len(value) == 6 and value.isdigit():
        return observed_at.replace(hour=int(value[:2]), minute=int(value[2:4]), second=0, microsecond=0)
    return observed_at.replace(second=0, microsecond=0)
