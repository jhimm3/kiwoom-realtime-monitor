"""PC 시장 세션의 실시간 진입 관측 RAM과 갭 전이.

시장 입력·복구 요청 연결·당일 사실과 진입 cut을 소유한다.
집계기의 세대/품질·TOP20 코호트·native 저장은 기존 소유자를 재사용한다.
현재 시각은 호출자가 적용 중인 키움 보정 시계에서 전달한다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.application.secondary_data_schedule import minute_history_candidates
from kiwoom_monitor.application.market_session_schedule import top20_collection_available, top20_collection_open


if TYPE_CHECKING:
    from kiwoom_monitor.domain.realtime_gap import RealtimeGap
    from kiwoom_monitor.application.top20_trade_value_collector import Top20TradeValueCollector
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
        MarketIndexTick, ProgramTradeTick, RealtimeSourceTransition, TradeTick,
    )


@dataclass(frozen=True)
class MinuteHistoryUpdate:
    bars: tuple[object, ...]
    generation: int | None
    high: int | None


class PcMarketSession:
    """한 창 수명의 시장 cut과 복구 연결. Qt·writer·독립 시계는 소유하지 않는다."""

    def __init__(self, minute_aggregator: MinuteTradeValueAggregator | None = None) -> None:
        self._minute_aggregator = minute_aggregator or MinuteTradeValueAggregator()
        self._source_route = ""
        self._last_change_rates: dict[str, float] = {}
        self._today_high_prices: dict[str, int] = {}
        self._today_high_date: date | None = None
        self._minute_storage_date: date | None = None
        self._minute_history_codes: set[str] = set()
        self._minute_recovery_requests: dict[str, int] = {}
        self._minute_coverage_requests: dict[str, int | None] = {}
        self._restored_gap_days: set[tuple[str, date]] = set()
        self._realtime_pressure: dict[str, deque[tuple[datetime, int]]] = {}
        self._latest_execution_strength: dict[str, float] = {}
        self._latest_session_type: dict[str, str] = {}
        self._latest_program_trade: dict[str, dict[str, object]] = {}
        self._pressure_gap_until: dict[str, datetime] = {}
        self._latest_market_state: dict[str, object] = {}

    def accept_tick(self, tick: TradeTick, observed_at: datetime) -> None:
        """기존 visible/TOP20 admission을 통과한 0B 관측만 받는다."""
        if tick.change_rate is not None:
            self._last_change_rates[tick.code] = tick.change_rate
        if tick.execution_strength is not None:
            self._latest_execution_strength[tick.code] = tick.execution_strength
        if tick.session_type:
            self._latest_session_type[tick.code] = tick.session_type
        if tick.trade_volume:
            pressure = self._realtime_pressure.setdefault(tick.code, deque())
            pressure.append((observed_at, tick.trade_volume))
            cutoff = as_kst(observed_at) - timedelta(seconds=60)
            while pressure and as_kst(pressure[0][0]) < cutoff:
                pressure.popleft()

    def accept_program_trade(self, tick: ProgramTradeTick, observed_at: datetime) -> None:
        self._latest_program_trade[tick.code] = {
            "available": True, "source": "kiwoom_realtime_0w",
            "observed_at": observed_at.isoformat(timespec="seconds"),
            "trade_time": tick.trade_time, "market": tick.market,
            "net_buy_quantity": tick.net_buy_quantity,
            "net_buy_quantity_change": tick.net_buy_quantity_change,
            "net_buy_amount_million_won": tick.net_buy_amount_million_won,
            "net_buy_amount_change_million_won": tick.net_buy_amount_change_million_won,
        }

    def accept_market_index(self, tick: MarketIndexTick, observed_at: datetime) -> tuple[str, datetime, float, float | None] | None:
        state = dict(self._latest_market_state)
        market = dict(state.get(tick.market, {}))
        updates = {
            "index": tick.index_value,
            "change_rate": tick.change_rate,
            "trade_value_million_won": tick.cumulative_trade_value_million_won,
            "trade_value_eok": (
                tick.cumulative_trade_value_million_won / 100
                if tick.cumulative_trade_value_million_won is not None else None
            ),
            "advancing_count": tick.advancing_count,
            "declining_count": tick.declining_count,
            "flat_count": tick.flat_count,
        }
        market.update({key: value for key, value in updates.items() if value is not None})
        state[tick.market] = market
        state["source"] = "kiwoom_realtime_0J_0U"
        # Legacy naive KST remains valid at this snapshot boundary. The caller
        # supplies corrected broker time instead of creating a host timestamp.
        state["observed_at"] = observed_at.isoformat(timespec="seconds")
        state["trade_time"] = tick.trade_time
        self._latest_market_state = state
        now = observed_at
        if tick.index_value is not None:
            digits = "".join(character for character in str(tick.trade_time or "") if character.isdigit())
            clock = digits[-6:] if len(digits) >= 6 else digits
            if len(clock) >= 4:
                hour, minute = int(clock[:2]), int(clock[2:4])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    now = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            minute = now.replace(second=0, microsecond=0)
            trade_value = float(tick.cumulative_trade_value_million_won) / 100 if tick.cumulative_trade_value_million_won is not None else None
            return tick.market, minute, float(tick.index_value), trade_value

    def mark_observation_gap(
        self, gap: RealtimeGap, *, affected_trade_codes: Iterable[str],
        subscribed_codes: Iterable[str], observed_at: datetime,
    ) -> None:
        pressure_codes = set(affected_trade_codes)
        for scope in gap.ranges:
            if scope.event_type == "program_trade":
                self._latest_program_trade[scope.code] = {
                    "available": False, "source": "subscriber-gap", "completeness": "partial",
                }
        if gap.scope_unknown:
            for code in self._latest_program_trade:
                self._latest_program_trade[code] = {
                    "available": False, "source": "subscriber-gap", "completeness": "unconfirmed",
                }
            pressure_codes.update(subscribed_codes)
        for code in pressure_codes:
            self._pressure_gap_until[code] = observed_at + timedelta(seconds=60)

    def execution_pressure_at_entry(self, code: str, now: datetime) -> dict[str, object]:
        pressure = self._realtime_pressure.get(code, ())
        now = as_kst(now)
        cutoff = now - timedelta(seconds=60)
        buy_volume = sell_volume = 0
        for observed, volume in pressure:
            if as_kst(observed) < cutoff:
                continue
            if volume > 0:
                buy_volume += volume
            elif volume < 0:
                sell_volume += abs(volume)
        total = buy_volume + sell_volume
        recovery_until = self._pressure_gap_until.get(code)
        if recovery_until is not None and now < as_kst(recovery_until):
            return {
                "source": "0B_execution_flow", "window_seconds": 60, "available": False,
                "completeness": "partial", "recovery_until": recovery_until.isoformat(),
                "buy_execution_volume": None, "sell_execution_volume": None, "buy_share_percent": None,
            }
        return {
            "source": "0B_execution_flow", "window_seconds": 60,
            "execution_strength": self._latest_execution_strength.get(code),
            "buy_execution_volume": buy_volume, "sell_execution_volume": sell_volume,
            "buy_share_percent": buy_volume / total * 100 if total else None,
            "session_type": self._latest_session_type.get(code, ""),
        }

    def investor_flow_at_entry(self, code: str) -> dict[str, object]:
        program = dict(self._latest_program_trade.get(code, {}))
        return (
            {"program_trade": program} if program
            else {"program_trade": {"available": False, "backfill_pending": True}}
        )

    def market_state_at_entry(self) -> dict[str, object]:
        return {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in self._latest_market_state.items()
        }


    @property
    def source_route(self) -> str:
        return self._source_route

    @property
    def history_codes(self) -> frozenset[str]:
        return frozenset(self._minute_history_codes)

    @property
    def high_date(self) -> date | None:
        return self._today_high_date

    def current_high(self, code: str) -> int | None:
        return self._today_high_prices.get(code)

    def change_rate(self, code: str) -> float | None:
        return self._last_change_rates.get(code)

    def accept_ranking_rate(self, code: str, rate: object) -> None:
        try:
            self._last_change_rates[code] = float(rate)
        except (TypeError, ValueError):
            pass

    def ensure_high_date(self, today: date) -> None:
        if self._today_high_date != today:
            self._today_high_prices.clear()
            self._today_high_date = today

    def restore_highs(self, highs: Mapping[str, int]) -> None:
        # Stored ranking facts retain the existing overwrite semantics.
        self._today_high_prices.update(highs)

    def accept_high(self, code: str, high: int) -> bool:
        if high > self._today_high_prices.get(code, 0):
            self._today_high_prices[code] = high
            return True
        return False

    def history_loaded(self, code: str) -> bool:
        return code in self._minute_history_codes

    def history_cache_saved(self, code: str) -> None:
        self._minute_history_codes.add(code)

    def clear_history(self) -> None:
        self._minute_history_codes.clear()

    def capture_runtime(self) -> Callable[[], None]:
        route = self._source_route
        original_aggregator = self._minute_aggregator
        history = self._minute_history_codes.copy()
        def restore() -> None:
            self._source_route = route
            self._minute_aggregator = original_aggregator
            self._minute_history_codes.clear()
            self._minute_history_codes.update(history)
        return restore

    def apply_runtime(self, source_mode: str) -> None:
        self._source_route = "local" if source_mode == "local" else "central_waiting"
        self.clear_history()
        self._minute_aggregator = MinuteTradeValueAggregator()

    def source_changed(self, transition: RealtimeSourceTransition) -> bool:
        # Worker/controller already validated sender/generation/ACK. This is
        # only the application source decision, never a new transport fence.
        previous = self._source_route
        self._source_route = transition.route.value
        reload_history = self._source_route == "central" and previous in {"local_fallback", "central_retry"}
        if reload_history:
            self.clear_history()
        return reload_history

    def uses_nas_market_data(self, client: object) -> bool:
        return self._source_route not in {"local", "local_fallback"} and callable(getattr(client, "load_stored_minute_bars", None))

    def prepare_top20(self, collector: Top20TradeValueCollector, codes: tuple[str, ...], observed_at: datetime, *, enabled: bool) -> None:
        collector.prepare(codes, observed_at, enabled=enabled, collection_open=top20_collection_open(observed_at))

    def advance_top20(self, collector: Top20TradeValueCollector, now: datetime, *, enabled: bool,
            visible_codes: tuple[str, ...], markets: Mapping[str, str], persist: Callable, subscribe: Callable):
        update = collector.advance(now, enabled=enabled,
            collection_open=top20_collection_available(now, self._source_route),
            value_provider=lambda code, minute: self._minute_aggregator.bucket_trade_value_eok(code, 1, minute),
            market_provider=lambda code: markets.get(code, ""))
        if update.completed is not None:
            persist(update.completed)
        if update.cohort_changed and visible_codes:
            subscribe(collector.realtime_codes(visible_codes, enabled=enabled))
        return update

    def prepare_history(self, codes: tuple[str, ...], now: datetime, *, force: bool = False, gap_loader: Callable | None = None) -> tuple[str, ...]:
        missing = minute_history_candidates(codes, self._minute_history_codes, force=force)
        if not missing:
            return ()
        if gap_loader is not None:
            day = now.date()
            to_restore = tuple(code for code in missing if (code, day) not in self._restored_gap_days)
            from kiwoom_monitor.domain.realtime_gap import GapRange, RealtimeGap
            try:
                restored = gap_loader(to_restore, day)
            except Exception:
                self._minute_aggregator.mark_gap(RealtimeGap(1), to_restore, now)
                raise
            self._restored_gap_days.update((code, day) for code in to_restore)
            ranges = tuple(GapRange("trade", code, "", minute, minute) for code, minutes in restored.items() for minute in minutes)
            if ranges:
                self._minute_aggregator.mark_gap(RealtimeGap(len(ranges), ranges, False), (), now)
        for code in missing:
            self._minute_recovery_requests[code] = self._minute_aggregator.gap_generation(code)
        return missing

    def accept_history(self, code: str, bars: tuple[object, ...], now: datetime, *, client: object, flush: Callable[[], None], retain: Callable[[date], None] | None) -> MinuteHistoryUpdate:
        generation = self._minute_recovery_requests.pop(code, None)
        self._minute_coverage_requests[code] = generation
        bars = self._minute_aggregator.history_for_storage(code, bars, generation)
        self.ensure_minute_day(now, flush=flush, retain=retain)
        nas_source = self.uses_nas_market_data(client)
        self._minute_aggregator.seed(code, bars, now, include_current_snapshot=nas_source,
            query_authoritative=not nas_source, recovery_generation=generation)
        same_day_highs = tuple(int(bar.high_price) for bar in bars
            if getattr(bar, "minute", None) is not None and bar.minute.date() == now.date()
            and getattr(bar, "high_price", 0) > 0)
        high = max(same_day_highs) if same_day_highs else None
        if high is not None and not self.accept_high(code, high):
            high = None
        return MinuteHistoryUpdate(bars, generation, high)

    def history_quality_saved(self, code: str, receipt: object) -> bool:
        if not isinstance(receipt, dict) or type(receipt.get("generation")) is not int:
            return False
        self._minute_aggregator.confirm_history_saved(code, receipt["generation"], receipt["minutes"], empty=receipt.get("empty") is True)
        return True

    def accept_coverage(self, code: str, coverage: object) -> tuple[tuple[datetime, ...], int | None]:
        generation = self._minute_coverage_requests.pop(code, None)
        if (not isinstance(coverage, dict) or coverage.get("complete") is not True
                or coverage.get("empty_authority") is not True or not isinstance(coverage.get("checked_at"), datetime)):
            return (), generation
        minutes = self._minute_aggregator.empty_recovery_candidates(code, coverage["present_minutes"], coverage["checked_at"], generation)
        return minutes, generation

    def mark_market_gap(self, gap: RealtimeGap, collector: Top20TradeValueCollector, codes: tuple[str, ...], now: datetime):
        collector.mark_gap()
        affected = self._minute_aggregator.mark_gap(gap, codes, now)
        market_gaps = {}
        for scope in gap.ranges:
            if scope.event_type == "market_state" and scope.market in {"kospi", "kosdaq"}:
                minute = scope.start.replace(second=0, microsecond=0)
                minutes = []
                while minute <= scope.end:
                    minutes.append(minute)
                    minute += timedelta(minutes=1)
                market_gaps[f"market:{scope.market}"] = tuple(minutes)
        if gap.scope_unknown:
            for market in ("kospi", "kosdaq"):
                market_gaps[f"market:{market}"] = ()
        self.mark_observation_gap(gap, affected_trade_codes=affected, subscribed_codes=codes, observed_at=now)
        bars = tuple((code, bar) for code in affected for bar in self._minute_aggregator.bars(code) if bar.minute in affected[code])
        return affected, {**affected, **market_gaps}, bars


    def theme_ranks_at_entry(self, target_code: str, themes: tuple[str, ...], rank_by_code: Mapping[str, int], ranked_names: Mapping[str, str], theme_names: Mapping[str, str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for theme in themes:
            members = []
            for code, name in ranked_names.items():
                stock_themes = tuple(value.strip().casefold() for value in theme_names.get("".join(name.split()), "").split(","))
                if theme.casefold() in stock_themes and code in rank_by_code:
                    members.append((rank_by_code[code], code))
            members.sort()
            for position, (_, code) in enumerate(members, 1):
                if code == target_code:
                    result[theme] = position
                    break
        return result


    def entry_market_state(self, rank_by_code: Mapping[str, int], ranked_names: Mapping[str, str], theme_frequency: Mapping[str, int], now: datetime, *, trade_values: Mapping[str, tuple[float, float]] | None = None) -> dict[str, object]:
        ordered = sorted(rank_by_code.items(), key=lambda item: item[1])
        if trade_values is None:
            trade_values = {code: (self._minute_aggregator.trade_value_eok(code, 1), self._minute_aggregator.trade_value_eok(code, 5)) for code, _ in ordered}
        rates = [self._last_change_rates[code] for code, _ in ordered if code in self._last_change_rates]
        visible_state = {
            "visible_stock_count": len(ordered),
            "advancing_count": sum(rate > 0 for rate in rates),
            "declining_count": sum(rate < 0 for rate in rates),
            "flat_count": sum(rate == 0 for rate in rates),
            "average_change_rate": sum(rates) / len(rates) if rates else None,
            "visible_trade_value_1m_eok": sum(trade_values[code][0] for code, _ in ordered),
            "visible_trade_value_5m_eok": sum(trade_values[code][1] for code, _ in ordered),
            "top_stocks": [
                {"rank": rank, "code": code, "name": ranked_names.get(code, code)}
                for code, rank in ordered[:10]
            ],
            "theme_frequency": dict(theme_frequency),
        }
        input_quality = {code: {str(minutes): self._minute_aggregator.recent_quality(code, minutes, now).value
                               for minutes in (1, 5)} for code, _ in ordered}
        return {**self.market_state_at_entry(), "ranking_table": visible_state, "input_quality": input_quality}


    def entry_market_fields(self, code: str, name: str, price: int, target_high: int | None, now: datetime,
            rank_by_code: Mapping[str, int], ranked_names: Mapping[str, str],
            theme_names: Mapping[str, str], theme_frequency: Mapping[str, int]) -> dict[str, object]:
        normalized_name = "".join(name.split())
        themes = tuple(theme.strip() for theme in theme_names.get(normalized_name, "").split(",") if theme.strip())
        trade_values = {member: (self._minute_aggregator.trade_value_eok(member, 1), self._minute_aggregator.trade_value_eok(member, 5))
            for member in dict.fromkeys((*rank_by_code, code))}
        return {
            "rank": rank_by_code.get(code),
            "trade_value_1m_eok": trade_values[code][0],
            "trade_value_5m_eok": trade_values[code][1],
            "themes": themes,
            "theme_ranks": self.theme_ranks_at_entry(code, themes, rank_by_code, ranked_names, theme_names),
            "high_distance_percent": max(0.0, (target_high - price) / target_high * 100) if target_high else None,
            "orderbook": self.execution_pressure_at_entry(code, now),
            "investor_flow": self.investor_flow_at_entry(code),
            "market_state": self.entry_market_state(rank_by_code, ranked_names, theme_frequency, now, trade_values=trade_values),
        }


    @property
    def minute_storage_date(self) -> date | None:
        return self._minute_storage_date

    def ensure_minute_day(self, now: datetime, *, flush: Callable[[], None], retain: Callable[[date], None] | None) -> None:
        today = now.date()
        self.ensure_high_date(today)
        if self._minute_storage_date == today:
            return
        flush()
        self._minute_aggregator.discard_before(today)
        self.clear_history()
        if retain is not None:
            retain(today - timedelta(days=30))
        self._minute_storage_date = today

    def accept_market_tick(self, tick: TradeTick, now: datetime, *, flush: Callable[[], None], retain: Callable[[date], None] | None):
        self.accept_tick(tick, now)
        if tick.current_price is None:
            return None, None
        self.ensure_minute_day(now, flush=flush, retain=retain)
        high = tick.high_price if tick.high_price and tick.high_price > 0 and self.accept_high(tick.code, tick.high_price) else None
        return high, self._minute_aggregator.ingest(tick, now)


    @property
    def minute_aggregator(self) -> MinuteTradeValueAggregator:
        """Existing pure reducer; UI reads its display projections."""
        return self._minute_aggregator

    def activate_sources(self, sources: object) -> None:
        self._minute_aggregator.reset_cumulative_sources(sources)

    def reset_cumulative_baselines(self, codes: object) -> None:
        self._minute_aggregator.reset_cumulative_baselines(codes)

    def history_committed(self, code: str, generation: int | None, minutes: Iterable[datetime] | None, *, empty: bool = False) -> None:
        if generation is not None:
            self._minute_aggregator.confirm_history_saved(code, generation, minutes, empty=empty)

    def history_cache_failed(self, code: str) -> None:
        self._minute_aggregator.history_save_failed(code)

    def recovery_codes(self) -> tuple[str, ...]:
        return self._minute_aggregator.recovery_codes()


    def flush_partial_top20(self, collector: Top20TradeValueCollector, markets: Mapping[str, str], persist: Callable) -> None:
        partial = collector.partial_record(
            lambda code, minute: self._minute_aggregator.bucket_trade_value_eok(code, 1, minute),
            lambda code: markets.get(code, ""))
        if partial is not None:
            persist(partial)


    def accepts_tick(self, code: str, *, visible: bool, collector: Top20TradeValueCollector) -> bool:
        return visible or code in collector.active_codes
