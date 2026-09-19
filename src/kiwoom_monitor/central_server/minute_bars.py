from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import uuid

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


@dataclass
class CentralSecondTradeBar:
    trading_date: str
    trade_second: str
    code: str
    market: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_value_won: int
    trade_count: int
    available_at: float

    def as_record(self) -> dict[str, object]:
        return asdict(self)


class MinuteBarAccumulator:
    """중앙 0B 체결로 거래소별 1분 OHLCV를 만든다."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, str, str], CentralMinuteBar] = {}
        self._cumulative: dict[tuple[str, str, str], int] = {}
        self._dirty: set[tuple[str, str, str, str]] = set()
        self._open_windows: dict[tuple[str, str, str, str], bool] = {}
        self._trading_date = ""

    def add(
        self, tick: TradeTick, now: datetime, received_at: float, *, capture_complete: bool = True,
    ) -> None:
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
        self._open_windows.setdefault(key, not capture_complete)
        if not capture_complete:
            self._open_windows[key] = True

    def drain_dirty(self) -> list[dict[str, object]]:
        values = []
        for key in self._dirty:
            value = self._bars.pop(key).as_record()
            value["operation_id"] = str(uuid.uuid4())
            values.append(value)
        self._dirty.clear()
        return values

    def mark_capture_gap(self) -> None:
        """현재 열린 모든 분 구간에 구독 연속성 공백을 표시한다."""
        for key in self._open_windows:
            self._open_windows[key] = True

    def reset_cumulative_sources(self, sources: set[tuple[str, str]]) -> None:
        """재등록된 source의 첫 누적값을 새 기준점으로 사용한다."""
        self._cumulative = {
            key: value for key, value in self._cumulative.items()
            if (key[1], key[2]) not in sources
        }

    def drain_closed(self, now: datetime, *, delay_seconds: int = 2) -> list[dict[str, object]]:
        """실제 시계가 종료+지연을 지난, 체결을 한 번 이상 본 구간만 마감한다."""
        ready: list[dict[str, object]] = []
        for key, had_gap in tuple(self._open_windows.items()):
            trading_date, minute, code, market = key
            bar_start = datetime.fromisoformat(f"{trading_date}T{minute}").replace(tzinfo=now.tzinfo)
            bar_end = bar_start + timedelta(minutes=1)
            if now < bar_end + timedelta(seconds=max(0, int(delay_seconds))):
                continue
            ready.append({
                "trading_date": trading_date,
                "minute": minute,
                "code": code,
                "market": market,
                "available_at": now.timestamp(),
                "capture_quality": "partial" if had_gap else "complete",
                "finalization_source": "timer",
                "operation_id": str(uuid.uuid4()),
            })
            del self._open_windows[key]
        return ready

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


class SecondTradeAccumulator:
    """0B 체결을 거래소별 1초 OHLCV 절대 상태로 만든다.

    최근 몇 초의 완성 상태를 유지해 같은 초의 늦은 체결도 다시 저장할 수 있게 한다.
    저장소는 이 절대 상태를 교체하므로, 성공 여부가 불명확한 재시도도 거래량을 두 번
    더하지 않는다.
    """

    def __init__(self, max_late_seconds: int = 5) -> None:
        self._max_late = timedelta(seconds=max(0, int(max_late_seconds)))
        self._bars: dict[tuple[str, str, str, str], CentralSecondTradeBar] = {}
        self._dirty: set[tuple[str, str, str, str]] = set()
        self._latest_second: datetime | None = None
        self._cumulative_volume: dict[tuple[str, str, str], int] = {}
        self._cumulative_date = ""
        self._seen: dict[tuple[object, ...], datetime] = {}

    def add(self, tick: TradeTick, now: datetime, received_at: float) -> None:
        if tick.current_price is None or not tick.trade_time:
            return
        raw_time = str(tick.trade_time).strip()
        if len(raw_time) != 6 or not raw_time.isdigit():
            return
        try:
            event_second = datetime.combine(
                now.date(),
                datetime.strptime(raw_time, "%H%M%S").time(),
                tzinfo=now.tzinfo,
            )
        except ValueError:
            return
        received_second = now.replace(microsecond=0)
        if not (
            received_second - self._max_late
            <= event_second
            <= received_second + self._max_late
        ):
            return
        if self._latest_second is not None and event_second < self._latest_second - self._max_late:
            return
        if self._latest_second is None or event_second > self._latest_second:
            self._latest_second = event_second

        market = str(tick.market or "KRX").upper()
        trading_date = now.date().isoformat()
        if trading_date != self._cumulative_date:
            self._cumulative_volume.clear()
            self._cumulative_date = trading_date
        instrument_key = (trading_date, tick.code, market)
        signature = self._deduplication_signature(tick, instrument_key)
        if signature is not None:
            if signature in self._seen:
                return
            self._seen[signature] = event_second

        price = abs(int(tick.current_price))
        if price <= 0:
            return
        volume = self._volume_increment(tick, instrument_key)
        key = (trading_date, event_second.strftime("%H:%M:%S"), tick.code, market)
        bar = self._bars.get(key)
        if bar is None:
            bar = CentralSecondTradeBar(
                trading_date, key[1], tick.code, market,
                price, price, price, price,
                volume, price * volume, 1, float(received_at),
            )
            self._bars[key] = bar
        else:
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += volume
            bar.trade_value_won += price * volume
            bar.trade_count += 1
            bar.available_at = max(bar.available_at, float(received_at))
        self._dirty.add(key)

    def drain_dirty(self) -> list[dict[str, object]]:
        values = [self._bars[key].as_record() for key in self._dirty]
        self._dirty.clear()
        self._prune_old_state()
        return values

    def reset_cumulative_sources(self, sources: set[tuple[str, str]]) -> None:
        self._cumulative_volume = {
            key: value for key, value in self._cumulative_volume.items()
            if (key[1], key[2]) not in sources
        }

    def _volume_increment(
        self, tick: TradeTick, instrument_key: tuple[str, str, str],
    ) -> int:
        cumulative = tick.cumulative_volume
        previous = self._cumulative_volume.get(instrument_key)
        if cumulative is not None:
            current = max(0, abs(int(cumulative)))
            if previous is None or current > previous:
                self._cumulative_volume[instrument_key] = current
        if tick.trade_volume is not None:
            return abs(int(tick.trade_volume))
        if cumulative is None or previous is None:
            return 0
        current = max(0, abs(int(cumulative)))
        return current - previous if current > previous else 0

    @staticmethod
    def _deduplication_signature(
        tick: TradeTick, instrument_key: tuple[str, str, str],
    ) -> tuple[object, ...] | None:
        if tick.cumulative_volume is None and tick.cumulative_trade_value is None:
            return None
        return (
            *instrument_key,
            str(tick.trade_time),
            tick.current_price,
            tick.trade_volume,
            tick.cumulative_volume,
            tick.cumulative_trade_value,
        )

    def _prune_old_state(self) -> None:
        if self._latest_second is None:
            return
        cutoff = self._latest_second - self._max_late
        self._bars = {
            key: bar for key, bar in self._bars.items()
            if datetime.fromisoformat(f"{key[0]}T{key[1]}").replace(
                tzinfo=self._latest_second.tzinfo,
            ) >= cutoff
        }
        self._seen = {
            signature: event_second
            for signature, event_second in self._seen.items()
            if event_second >= cutoff
        }
