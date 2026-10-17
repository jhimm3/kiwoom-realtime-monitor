"""동일 시점의 TOP20 자료로 시장 특징과 실험 분류를 계산한다.

이 모듈은 저장소나 UI를 알지 못한다. 입력에 포함되지 않은 전체시장 자료를
추정하지 않으며, 분류에 필요한 근거가 모자라면 UNKNOWN을 반환한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Iterable, Mapping
from types import MappingProxyType
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.application.theme_leadership import SecondTradePoint


MARKET_RULE_VERSION = "top20_market_types/v1"
MARKET_CHANGE_RULE_VERSION = "top20_observed_changes/v1"
PRICE_TRADE_CANDIDATE_VERSION = "observed-price-trade-candidates/v1"
THEME_ALLOCATION_POLICY = "first_representative_theme/v1"
MARKET_TYPES = frozenset({
    "NORMAL_LEADER", "SINGLE_LEADER", "THEME_LED",
    "DISPERSED_ROTATION", "UNKNOWN",
})
_SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class MarketMember:
    code: str
    trade_value_million_won: int | None
    change_bps: int | None
    themes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not normalize_stock_code(self.code):
            raise ValueError("market member code is required")
        if self.trade_value_million_won is not None and self.trade_value_million_won < 0:
            raise ValueError("trade value must not be negative")


@dataclass(frozen=True)
class MarketResearchFrame:
    revision_id: str
    available_at: str
    trading_date: str
    session_minute: str
    venue: str
    window: str
    members: tuple[MarketMember, ...]
    capture_quality: str = "complete"
    index_return_bps: int | None = None
    theme_revision_id: str = ""

    def __post_init__(self) -> None:
        if not self.revision_id or not self.venue or not self.window:
            raise ValueError("market frame revision, venue, and window are required")
        _aware_datetime(self.available_at)
        if len({normalize_stock_code(row.code) for row in self.members}) != len(self.members):
            raise ValueError("market frame members must be unique")


@dataclass(frozen=True)
class MatureBreakoutSummary:
    completed_count: int
    success_count: int
    failed_count: int
    pending_count: int = 0

    def __post_init__(self) -> None:
        if min(self.completed_count, self.success_count, self.failed_count, self.pending_count) < 0:
            raise ValueError("breakout counts must not be negative")
        if self.success_count + self.failed_count != self.completed_count:
            raise ValueError("completed breakouts must equal successes plus failures")


@dataclass(frozen=True)
class MarketFeatureParameters:
    top_k: int = 5
    history_days: int = 20
    max_gap_seconds: int = 90
    min_history_observations: int = 3
    min_mature_breakouts: int = 3

    def __post_init__(self) -> None:
        if self.top_k <= 0 or self.top_k > 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.history_days <= 0 or self.max_gap_seconds <= 0:
            raise ValueError("history_days and max_gap_seconds must be positive")
        if self.min_history_observations < 2 or self.min_mature_breakouts <= 0:
            raise ValueError("minimum observations and mature breakouts must be positive")


@dataclass(frozen=True)
class MarketResearchFeatures:
    as_of: str
    venue: str
    window: str
    universe_size: int
    trade_value_denominator_million_won: int | None
    trade_value_valid_count: int
    top1_concentration_ppm: int | None
    top5_concentration_ppm: int | None
    fixed_k_turnover_ppm: int | None
    fixed_k_denominator: int | None
    jaccard_change_ppm: int | None
    jaccard_union_denominator: int | None
    current_leader_top_k_residency_ppm: int | None
    residency_observation_count: int
    leader_turnover_ppm: int | None
    leader_transition_count: int
    relative_trade_value_ppm: int | None
    historical_median_trade_value_million_won: int | None
    historical_day_count: int
    leading_theme: str
    leading_theme_concentration_ppm: int | None
    leading_theme_member_count: int
    leading_theme_active_ratio_ppm: int | None
    theme_valid_member_count: int
    theme_allocation_policy: str
    top20_breadth_ppm: int | None
    top20_breadth_denominator: int
    mature_breakout_success_ppm: int | None
    mature_breakout_failure_ppm: int | None
    mature_breakout_denominator: int
    pending_breakout_count: int
    index_return_bps: int | None
    input_refs: tuple[str, ...]
    quality: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketClassification:
    market_type: str
    candidate_types: tuple[str, ...]
    reasons: tuple[str, ...]
    quality: Mapping[str, Any]
    rule_version: str = MARKET_RULE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyMarketAssessment:
    strategy_id: str
    market_type: str
    tradability: str
    difficulty: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketCandidateObservation:
    """입력 프레임 내 출현 사실. 매수 자격이나 영속 Stock Memory가 아니다."""

    code: str
    state: str
    previous_rank: int | None
    current_rank: int | None
    presence_count: int
    observation_count: int
    first_seen_at: str
    last_seen_at: str
    change_bps_delta: int | None
    trade_value_delta_million_won: int | None
    reasons: tuple[str, ...]
    input_refs: tuple[str, ...]


@dataclass(frozen=True)
class MarketPriceTradeInputs:
    """기존 0B 집계 관측. 순위 소속과 시세의 시각/venue/연속성을 분리한다."""

    universe_ref: str
    universe_available_at: str
    member_codes: tuple[str, ...]
    venue: str
    points: tuple[SecondTradePoint, ...]
    continuity: Mapping[str, str]
    window_seconds: int = 10
    max_data_age_seconds: int = 3
    universe: str = "TOP20"
    max_membership_age_seconds: int = 90
    continuity_reasons: Mapping[str, tuple[str, ...]] | None = None
    input_resolution: str = 'seconds'

    def __post_init__(self) -> None:
        codes = tuple(normalize_stock_code(code) for code in self.member_codes)
        if (not self.universe_ref or self.venue not in {"KRX", "NXT"}
                or len(codes) != len(set(codes)) or any(not code for code in codes)
                or self.window_seconds <= 0 or self.max_data_age_seconds <= 0 or self.max_membership_age_seconds <= 0
                or self.universe not in {"TOP20", "RECORDED_COHORT"}):
            raise ValueError("market price/trade scope is invalid")
        _aware_datetime(self.universe_available_at)
        object.__setattr__(self, "member_codes", codes)


def _minute_row_json(row):
    return json.dumps({**row, "metadata": dict(row["metadata"])}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_price_trade_candidates(
    inputs: MarketPriceTradeInputs, *, as_of: datetime, top_k: int = 5,
) -> dict[str, Any]:
    """두 관측 창의 가격 반응·대금 우위를 독립 축으로 확인한다.

    조회순서/뉴스/테마/돌파 성과를 후보 선정에 사용하지 않는다. 당시 수신한
    관측 집합의 후보이며 시장 전체 대장·순유입·매수 자격은 아니다.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None or not 1 <= top_k <= 20:
        raise ValueError("price/trade cut and top_k must be explicit")
    cutoff = as_of.astimezone(timezone.utc)
    midpoint = cutoff - timedelta(seconds=inputs.window_seconds)
    start = midpoint - timedelta(seconds=inputs.window_seconds)
    reasons = []
    if _aware_datetime(inputs.universe_available_at) > cutoff:
        reasons.append("membership_not_available_at_cut")
    membership_expiry = _aware_datetime(inputs.universe_available_at) + timedelta(seconds=inputs.max_membership_age_seconds)
    if cutoff >= membership_expiry:
        reasons.append("membership_stale")
    if inputs.universe == "TOP20" and len(inputs.member_codes) != 20:
        reasons.append("top20_membership_incomplete")
    if len(inputs.member_codes) < 2:
        reasons.append("comparison_cohort_insufficient")
    if _aware_datetime(inputs.universe_available_at).astimezone(_SEOUL).date() != cutoff.astimezone(_SEOUL).date():
        reasons.append("membership_trading_day_mismatch")
    by_ref: dict[str, SecondTradePoint] = {}
    latest: dict[tuple[str, datetime], SecondTradePoint] = {}
    codes = set(inputs.member_codes) if not any(reason in reasons for reason in (
        "membership_not_available_at_cut", "membership_trading_day_mismatch",
    )) else set()
    for point in inputs.points:
        observed, available = _aware_datetime(point.observed_at), _aware_datetime(point.available_at)
        code = normalize_stock_code(point.code)
        if point.market != inputs.venue or code not in codes or available > cutoff or not start < observed <= cutoff:
            continue
        if point.input_ref in by_ref and by_ref[point.input_ref] != point:
            raise ValueError("conflicting immutable price/trade observation")
        by_ref[point.input_ref] = point
        # Historical bars choose the latest revision; individual 0B receipts
        # with the same broker second must all survive in receive order.
        key = (code, observed, point.input_ref) if hasattr(point, 'sequence') else (code, observed)
        previous = latest.get(key)
        if previous is not None and available == _aware_datetime(previous.available_at) and point != previous:
            raise ValueError("ambiguous price/trade revision at one observation time")
        if previous is None or available > _aware_datetime(previous.available_at):
            latest[key] = point
    ordered = sorted(latest.values(), key=lambda point: (_aware_datetime(point.observed_at),
        _aware_datetime(point.available_at), getattr(point, 'sequence', 0), point.input_ref))
    metrics = []
    missing_codes = []
    expiries = []
    for code in sorted(codes):
        rows = [point for point in ordered if normalize_stock_code(point.code) == code]
        before = [point for point in rows if _aware_datetime(point.observed_at) <= midpoint]
        after = [point for point in rows if _aware_datetime(point.observed_at) > midpoint]
        missing = []
        if inputs.continuity.get(code) != "COMPLETE":
            missing.append("price_trade_continuity_unconfirmed")
            missing.extend((inputs.continuity_reasons or {}).get(code, ()))
        if len(before) < 2 or len(after) < 2:
            missing.append("two_window_price_sample_insufficient")
        if len(before) < 2:
            missing.append("previous_price_sample_insufficient")
        if len(after) < 2:
            missing.append("current_price_sample_insufficient")
        if not after:
            missing.append("current_price_observation_unavailable")
        if any(point.trade_value_won is None for point in rows):
            missing.append('0B_turnover_delta_unavailable')
        if after:
            # Event time prevents a delayed old quote from refreshing the lease.
            expiry = _aware_datetime(after[-1].observed_at) + timedelta(seconds=inputs.max_data_age_seconds)
            expiries.append(expiry)
            if cutoff >= expiry:
                missing.append("price_observation_stale")
        if missing:
            missing_codes.append(code)
        metrics.append({"code": code, "quality": "PARTIAL" if missing else "COMPLETE", "reasons": tuple(dict.fromkeys(missing)),
            "previous_return_bps": (before[-1].close - before[0].close) * 10_000 // before[0].close if len(before) >= 2 else None,
            "current_return_bps": (after[-1].close - after[0].close) * 10_000 // after[0].close if len(after) >= 2 else None,
            "previous_trade_value_won": sum(point.trade_value_won for point in before) if before and all(point.trade_value_won is not None for point in before) else None,
            "current_trade_value_won": sum(point.trade_value_won for point in after) if after and all(point.trade_value_won is not None for point in after) else None,
            "previous_observation_count": len(before), "current_observation_count": len(after),
            "last_observed_at": after[-1].observed_at if after else None,
            "last_available_at": after[-1].available_at if after else None,
            "input_refs": tuple(point.input_ref for point in rows)})
    if missing_codes:
        reasons.append("comparison_cohort_price_trade_incomplete")
    selected, totals, state = _compare_price_trade_windows(metrics, top_k, reasons)
    before_set, after_set = set(selected["previous"]), set(selected["current"])
    return {"policy_version": ("observed-price-trade-candidates/0B-v2" if inputs.input_resolution == '0B' else PRICE_TRADE_CANDIDATE_VERSION),
        **({'input_resolution':'0B', 'primary_timeframe':'0B'} if inputs.input_resolution=='0B' else {}), "as_of": cutoff.isoformat(),
        "universe": inputs.universe, "universe_ref": inputs.universe_ref, "venue": inputs.venue,
        "window_seconds": inputs.window_seconds, "top_k": top_k,
        "window_start": start.isoformat(), "window_midpoint": midpoint.isoformat(),
        "expires_at": min(*expiries, membership_expiry).isoformat() if expiries and not reasons else cutoff.isoformat(),
        "state": state, "previous_candidates": selected["previous"], "current_candidates": selected["current"],
        "retained_candidates": tuple(sorted(before_set & after_set)),
        "entered_candidates": tuple(sorted(after_set - before_set)), "left_candidates": tuple(sorted(before_set - after_set)),
        "metrics": tuple(metrics), "market_flow": totals,
        "quality": {"status": "PARTIAL" if reasons else "COMPLETE", "reasons": tuple(dict.fromkeys(reasons)),
                    "missing_codes": tuple(missing_codes), "comparison_denominator": len(codes),
                    "coverage": {"expected_members": 20 if inputs.universe == "TOP20" else len(codes),
                        "evaluated_members": len(metrics),
                        "usable_members": sum(row["quality"] == "COMPLETE" for row in metrics),
                        "continuity_unconfirmed_members": sum("price_trade_continuity_unconfirmed" in row["reasons"] for row in metrics),
                        "previous_sample_insufficient_members": sum("previous_price_sample_insufficient" in row["reasons"] for row in metrics),
                        "current_sample_insufficient_members": sum("current_price_sample_insufficient" in row["reasons"] for row in metrics),
                        "stale_members": sum("price_observation_stale" in row["reasons"] for row in metrics)}},
        "input_refs": ((*((inputs.universe_ref,) if codes else ()), *(point.input_ref for point in ordered))),
        "limitations": ("observed_cohort_not_whole_market", "received_0B_not_all_exchange_trades",
                         "price_trade_axes_not_independent_sources", "candidate_rule_not_strategy_validation"),
        "order_authorized": False}


def _compare_price_trade_windows(metrics, top_k, reasons):
    """One price/money candidate policy shared by second and minute observations."""
    selected = {"previous": (), "current": ()}
    totals = {}
    if not reasons:
        for window in ("previous", "current"):
            returns = [row[f"{window}_return_bps"] for row in metrics]
            amounts = [row[f"{window}_trade_value_won"] for row in metrics]
            return_median, value_median = median(returns), median(amounts)
            candidates = []
            for row in metrics:
                change, value = row[f"{window}_return_bps"], row[f"{window}_trade_value_won"]
                # Competition ranks preserve ties; no code-based tie-breaking.
                rank = 1 + sum(amount > value for amount in amounts)
                row[f"{window}_trade_value_rank"] = rank
                row[f"{window}_relative_return_bps"] = change - return_median
                row[f"{window}_relative_trade_value_won"] = value - value_median
                if change > 0 and change > return_median and value > 0 and value > value_median and rank <= top_k:
                    candidates.append(row["code"])
            selected[window] = tuple(sorted(candidates))
            total = sum(amounts)
            totals[window] = {"trade_value_won": total, "positive_price_ratio_ppm": sum(value > 0 for value in returns) * 1_000_000 // len(returns),
                "top1_trade_value_share_ppm": max(amounts) * 1_000_000 // total if total > 0 else None,
                "price_return_median_bps": return_median, "trade_value_median_won": value_median}
    before_set, after_set = set(selected["previous"]), set(selected["current"])
    state = "UNKNOWN" if reasons else (
        "NO_CANDIDATE" if not before_set and not after_set else
        "EMERGING" if not before_set else "LOST" if not after_set else
        "RETAINED" if before_set == after_set else
        "REPLACED" if not before_set & after_set else "ROTATING_GROUP"
    )
    return selected, totals, state


@dataclass(frozen=True)
class MarketMinuteTradeInputs:
    universe_ref: str
    universe_available_at: str
    member_codes: tuple[str, ...]
    venue: str
    rows: tuple[Mapping[str, Any], ...]
    max_membership_age_seconds: int = 90
    max_bar_age_seconds: int = 90

    def __post_init__(self):
        codes = tuple(normalize_stock_code(code) for code in self.member_codes)
        if (not self.universe_ref or self.venue not in {"KRX", "NXT"} or not all(codes)
                or len(codes) != len(set(codes)) or len(codes) > 20 or len(self.rows) > 10000
                or min(self.max_membership_age_seconds, self.max_bar_age_seconds) <= 0):
            raise ValueError("minute market scope is invalid")
        _aware_datetime(self.universe_available_at)
        object.__setattr__(self, "member_codes", codes)
        scalar = (str, int, float, bool, type(None))
        if any(any(not isinstance(value, scalar) for key, value in row.items() if key != "metadata")
               or any(not isinstance(value, scalar) for value in dict(row.get("metadata") or {}).values()) for row in self.rows):
            raise ValueError("minute observations require native scalar fields")
        object.__setattr__(self, "rows", tuple(MappingProxyType({**row,
            "metadata": MappingProxyType(dict(row.get("metadata") or {}))}) for row in self.rows))

    def __deepcopy__(self, memo):
        # Frozen scalar fields and read-only row/metadata maps are safe to share
        # through the existing monitor's defensive input copy boundary.
        memo[id(self)] = self
        return self


def compute_minute_trade_candidates(inputs: MarketMinuteTradeInputs, *, as_of: datetime, top_k: int = 5):
    """Closed 1m price/money candidates, with independent rolling 5m persistence.

    No second observations or H2 leases enter this market comparison. Missing 5m
    history does not erase a complete 1m observation. In-progress/estimated or
    future bars are never treated as closed actual observations.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None or not 1 <= top_k <= 20:
        raise ValueError("minute market cut/top_k must be explicit")
    cutoff = as_of.astimezone(timezone.utc)
    end = (cutoff - timedelta(seconds=2)).replace(second=0, microsecond=0)
    available = _aware_datetime(inputs.universe_available_at)
    scope_reasons = []
    if available > cutoff:
        scope_reasons.append("membership_not_available_at_cut")
    if available.astimezone(_SEOUL).date() != cutoff.astimezone(_SEOUL).date():
        scope_reasons.append("membership_trading_day_mismatch")
    if len(inputs.member_codes) != 20:
        scope_reasons.append("top20_membership_incomplete")
    membership_expiry = available + timedelta(seconds=inputs.max_membership_age_seconds)
    if cutoff >= membership_expiry:
        scope_reasons.append("membership_stale")
    codes = set(inputs.member_codes) if not any(reason in scope_reasons for reason in (
        "membership_not_available_at_cut", "membership_trading_day_mismatch")) else set()
    latest, rejected = {}, {}
    for row in inputs.rows:
        code = normalize_stock_code(row.get("code", ""))
        if code not in codes or row.get("market") != inputs.venue:
            continue
        observed = None
        try:
            observed = datetime.fromisoformat(f"{row['trading_date']}T{row['minute']}").replace(tzinfo=_SEOUL).astimezone(timezone.utc)
            if not end - timedelta(minutes=10) <= observed < end:
                continue
            metadata = row.get("metadata", {})
            ready = _aware_datetime(metadata["available_at"])
            updated = datetime.fromtimestamp(float(row["updated_at"]), timezone.utc)
            if ready > cutoff or updated > cutoff or not end - timedelta(minutes=10) <= observed < end:
                continue
        except (KeyError, TypeError, ValueError, OSError):
            rejected[(code, observed)] = "minute_bar_lineage_unconfirmed"
            continue
        key = (code, observed)
        previous = latest.get(key)
        if previous and previous[0] == ready and _minute_row_json(previous[1]) != _minute_row_json(row):
            raise ValueError("conflicting immutable minute observation")
        if previous is None or ready > previous[0]:
            latest[key] = (ready, row)
    # Pick the cut's latest revision before testing completeness, so a later
    # partial correction cannot leave an obsolete complete value in use.
    for (code, observed), (ready, row) in tuple(latest.items()):
        reason = ""
        try:
            metadata = row["metadata"]
            if metadata.get("venue") != inputs.venue or metadata.get("completeness") != "complete" or metadata.get("value_kind") != "actual":
                reason = "minute_bar_not_closed_actual"
            elif (observed + timedelta(minutes=1) > ready or not metadata.get("source")
                    or _aware_datetime(metadata["effective_at"]) != observed):
                reason = "minute_bar_lineage_unconfirmed"
            else:
                values = (row["open"], row["close"], row["trade_value_million_won"])
                if any(type(value) is not int for value in values) or min(values[:2]) <= 0 or values[2] < 0:
                    reason = "minute_bar_values_invalid"
        except (KeyError, TypeError, ValueError, OSError):
            reason = "minute_bar_lineage_unconfirmed"
        if reason:
            rejected[(code, observed)] = reason
            del latest[(code, observed)]
    timeframes = {}
    for minutes in (1, 5):
        midpoint, start = end - timedelta(minutes=minutes), end - timedelta(minutes=2 * minutes)
        metrics = []
        reasons = list(scope_reasons)
        for code in sorted(codes):
            before_keys = tuple(start + timedelta(minutes=index) for index in range(minutes))
            after_keys = tuple(midpoint + timedelta(minutes=index) for index in range(minutes))
            before = [latest[(code, stamp)][1] for stamp in before_keys if (code, stamp) in latest]
            after = [latest[(code, stamp)][1] for stamp in after_keys if (code, stamp) in latest]
            missing = [rejected[(code, None)]] if (code, None) in rejected else []
            if len(before) != minutes:
                missing.append("previous_minute_history_incomplete")
            if len(after) != minutes:
                missing.append("current_minute_history_incomplete")
            missing.extend(dict.fromkeys(rejected[key] for key in rejected if key[0] == code
                and (key[1] is None or start <= key[1] < end)))
            if cutoff >= end + timedelta(seconds=inputs.max_bar_age_seconds):
                missing.append("minute_bar_stale")
            metrics.append({"code": code, "quality": "PARTIAL" if missing else "COMPLETE", "reasons": tuple(dict.fromkeys(missing)),
                "previous_return_bps": (before[-1]["close"] - before[0]["open"]) * 10000 // before[0]["open"] if len(before) == minutes else None,
                "current_return_bps": (after[-1]["close"] - after[0]["open"]) * 10000 // after[0]["open"] if len(after) == minutes else None,
                "previous_trade_value_won": sum(row["trade_value_million_won"] for row in before) * 1000000 if len(before) == minutes else None,
                "current_trade_value_won": sum(row["trade_value_million_won"] for row in after) * 1000000 if len(after) == minutes else None,
                "previous_observation_count": len(before), "current_observation_count": len(after)})
        if any(row["quality"] != "COMPLETE" for row in metrics):
            reasons.append("comparison_cohort_minute_history_incomplete")
        selected, flow, state = _compare_price_trade_windows(metrics, top_k, reasons)
        before_set, after_set = set(selected["previous"]), set(selected["current"])
        refs = tuple("minute-bar:" + hashlib.sha256(_minute_row_json(row).encode()).hexdigest()
            for (code, stamp), (_, row) in sorted(latest.items()) if start <= stamp < end)
        timeframes[f"{minutes}m"] = {"state": state, "minutes": minutes, "window_start": start.isoformat(),
            "window_midpoint": midpoint.isoformat(), "window_end": end.isoformat(),
            "expires_at": min(membership_expiry, end + timedelta(seconds=inputs.max_bar_age_seconds)).isoformat(),
            "previous_candidates": selected["previous"], "current_candidates": selected["current"],
            "retained_candidates": tuple(sorted(before_set & after_set)), "entered_candidates": tuple(sorted(after_set - before_set)),
            "left_candidates": tuple(sorted(before_set - after_set)), "metrics": tuple(metrics), "market_flow": flow,
            "quality": {"status": "PARTIAL" if reasons else "COMPLETE", "reasons": tuple(dict.fromkeys(reasons)),
                "coverage": {"expected_members": 20, "evaluated_members": len(metrics),
                    "usable_members": sum(row["quality"] == "COMPLETE" for row in metrics)}}, "input_refs": refs}
    primary, persistence = timeframes["1m"], timeframes["5m"]
    five_available = not scope_reasons and all(row["current_observation_count"] == 5 for row in persistence["metrics"])
    qualifying_minutes = dict.fromkeys(sorted(codes), 0)
    five_candidates = ()
    if five_available:
        for index in range(5):
            stamp = end - timedelta(minutes=5 - index)
            scratch = []
            for code in sorted(codes):
                row = latest[(code, stamp)][1]
                change = (row["close"] - row["open"]) * 10000 // row["open"]
                amount = row["trade_value_million_won"] * 1000000
                scratch.append({"code": code, "previous_return_bps": change, "current_return_bps": change,
                    "previous_trade_value_won": amount, "current_trade_value_won": amount})
            selected, _, _ = _compare_price_trade_windows(scratch, top_k, ())
            for code in selected["current"]:
                qualifying_minutes[code] += 1
        scratch = [{**row, "previous_return_bps": row["current_return_bps"],
                    "previous_trade_value_won": row["current_trade_value_won"]} for row in persistence["metrics"]]
        selected, _, _ = _compare_price_trade_windows(scratch, top_k, ())
        five_candidates = selected["current"]
    confirmed = tuple(sorted(code for code in primary["current_candidates"]
        if code in five_candidates and qualifying_minutes[code] >= 3))
    return {**primary, "policy_version": "minute-price-trade-candidates/v1", "as_of": cutoff.isoformat(),
        "universe": "TOP20", "universe_ref": inputs.universe_ref, "venue": inputs.venue, "top_k": top_k,
        "primary_timeframe": "1m", "timeframes": timeframes,
        "five_minute_confirmation": {"status": "CONFIRMED" if confirmed else "NOT_CONFIRMED"
            if primary["quality"]["status"] == "COMPLETE" and five_available else "UNAVAILABLE",
            "confirmed_candidates": confirmed, "qualifying_minutes": qualifying_minutes if five_available else {},
            "required_qualifying_minutes": 3, "observed_minutes": 5 if five_available else 0},
        "input_refs": ((inputs.universe_ref,) if codes else ()) + tuple(dict.fromkeys((*primary["input_refs"], *persistence["input_refs"]))),
        "limitations": ("observed_top20_not_whole_market", "actual_closed_bars_only", "missing_minutes_not_zero_filled",
            "five_minute_is_rolling_window", "money_unit_from_million_won_column", "not_strategy_profitability_validation"),
        "order_authorized": False}


@dataclass(frozen=True)
class MarketContextChanges:
    as_of: str
    observed_at: str | None
    expires_at: str | None
    venue: str
    window: str
    pattern: str
    reasons: tuple[str, ...]
    features: MarketResearchFeatures | None
    classification: MarketClassification | None
    changes: Mapping[str, Any]
    candidates: tuple[MarketCandidateObservation, ...]
    theme_evidence: Mapping[str, Any]
    quality: Mapping[str, Any]
    input_refs: tuple[str, ...]
    rule_version: str = MARKET_CHANGE_RULE_VERSION
    universe: str = "TOP20"
    parameters: MarketFeatureParameters = MarketFeatureParameters()
    lookup_change: Mapping[str, Any] | None = None
    limitations: tuple[str, ...] = (
        "top20_not_whole_market",
        "lookup_rank_not_trade_value_rank_or_buy_eligibility",
        "ranking_observations_not_all_trades",
        "available_at_not_exchange_event_time",
        "supplied_frame_history_not_persistent_stock_memory",
        "observed_pattern_not_validated_strategy_or_order_permission",
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_market_context_changes(
    frames: Iterable[MarketResearchFrame],
    parameters: MarketFeatureParameters = MarketFeatureParameters(),
    *,
    as_of: datetime,
    venue: str,
    window: str,
    breakout_summary: MatureBreakoutSummary | None = None,
    breakout_available_at: datetime | None = None,
    breakout_input_ref: str = "",
    _current_features: MarketResearchFeatures | None = None,
) -> MarketContextChanges:
    """선택 호출용 TOP20 변화 관측. 기존 v1 특징/분류·저장·Runtime은 유지한다.

    테마/뉴스/돌파 성과는 순위·대금 변화 관측의 필수 자료가 아니다. 선택한
    breakout_summary는 근거 ID와 가용시각을 함께 요구한다. 기본값 또는
    미래 근거는 성숙 사례 0건이며 v1 분류의 UNKNOWN을 임의 보완하지 않는다.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not venue or not window:
        raise ValueError("market change venue and window are required")
    cutoff = as_of.astimezone(timezone.utc)
    if breakout_summary is not None:
        if breakout_available_at is None or not breakout_input_ref.strip():
            raise ValueError("breakout summary requires available_at and input_ref")
        if breakout_available_at.tzinfo is None or breakout_available_at.utcoffset() is None:
            raise ValueError("breakout available_at must be timezone-aware")
    breakout_eligible = (breakout_summary is not None and breakout_available_at is not None
                         and breakout_available_at <= cutoff)
    breakout_status = "AVAILABLE" if breakout_eligible else "FUTURE" if breakout_summary is not None else "MISSING"
    unique: dict[str, MarketResearchFrame] = {}
    for frame in frames:
        if (frame.venue != venue or frame.window != window
                or _aware_datetime(frame.available_at) > cutoff):
            continue
        previous = unique.get(frame.revision_id)
        if previous is not None and previous != frame:
            raise ValueError("conflicting immutable market frame revision")
        unique[frame.revision_id] = frame
    ordered = tuple(sorted(unique.values(), key=lambda frame: (
        _aware_datetime(frame.available_at), frame.revision_id,
    )))
    if len({_aware_datetime(frame.available_at) for frame in ordered}) != len(ordered):
        raise ValueError("distinct market revisions require distinct observation times")
    if not ordered:
        return MarketContextChanges(
            cutoff.isoformat(), None, None, venue, window, "UNKNOWN",
            ("market_frames_unavailable",), None, None, {}, (),
            {"status": "UNKNOWN", "reasons": ("theme_evidence_unavailable",)},
            {"status": "MISSING", "reasons": ("market_frames_unavailable",)}, (), parameters=parameters,
        )

    current = ordered[-1]
    same_day = tuple(frame for frame in ordered if frame.trading_date == current.trading_date)
    recent = same_day[-parameters.min_history_observations:]
    previous = same_day[-2] if len(same_day) >= 2 else None
    summary = breakout_summary if breakout_eligible else MatureBreakoutSummary(0, 0, 0)
    assert summary is not None
    # Reserved for the existing H1 owner after its full immutable scope check.
    # Direct callers compute here; Runtime does not recalculate the current cut.
    features = _current_features if _current_features is not None else compute_market_research_features(
        ordered, summary, parameters, as_of=cutoff,
    )
    if (features.as_of, features.venue, features.window) != (current.available_at, venue, window):
        raise ValueError("shared market features belong to another cut or scope")
    previous_features = (
        compute_market_research_features(
            ordered, MatureBreakoutSummary(0, 0, 0), parameters,
            as_of=_aware_datetime(previous.available_at),
        ) if previous is not None else None
    )
    expires = _aware_datetime(current.available_at) + timedelta(seconds=parameters.max_gap_seconds)
    stale = cutoff >= expires or current.trading_date != cutoff.astimezone(_SEOUL).date().isoformat()
    missing: list[str] = []
    if stale:
        missing.append("market_frame_stale")
    if len(recent) < parameters.min_history_observations:
        missing.append("rank_history_warmup")
    if any(not _complete_top20_frame(frame) for frame in recent):
        missing.append("top20_membership_incomplete")
    missing.extend(_gap_reasons(recent, parameters.max_gap_seconds))
    if features.trade_value_denominator_million_won is None or (
        previous_features is None or previous_features.trade_value_denominator_million_won is None
    ):
        missing.append("trade_value_denominator_unavailable")

    def difference(field: str) -> int | None:
        after = getattr(features, field)
        before = getattr(previous_features, field) if previous_features is not None else None
        return after - before if after is not None and before is not None and not missing else None

    before_leader = normalize_stock_code(previous.members[0].code) if previous and previous.members else ""
    after_leader = normalize_stock_code(current.members[0].code) if current.members else ""
    concentration_delta = difference("top1_concentration_ppm")
    changes = {
        "previous_observed_at": previous.available_at if previous else None,
        "previous_leader_code": before_leader,
        "current_leader_code": after_leader,
        "top1_concentration_delta_ppm": concentration_delta,
        "top5_concentration_delta_ppm": difference("top5_concentration_ppm"),
        "concentration_direction": "UNKNOWN" if concentration_delta is None else (
            "INCREASED" if concentration_delta > 0 else "DECREASED" if concentration_delta < 0 else "UNCHANGED"
        ),
    }
    pattern, reasons = _observed_market_pattern(features, before_leader, after_leader, tuple(missing))
    classification = classify_market(features)
    if missing:
        classification = MarketClassification(
            "UNKNOWN", (), tuple(dict.fromkeys((*classification.reasons, *missing))),
            {**dict(classification.quality), "status": "STALE" if stale else "PARTIAL"},
        )
    theme_available = bool(current.theme_revision_id and features.theme_valid_member_count)
    theme_evidence = {
        "status": "STALE" if theme_available and stale else "OBSERVED" if theme_available else "UNKNOWN",
        "revision_id": current.theme_revision_id if theme_available else "",
        "revision_available_at": None,
        "observed_at": current.available_at if theme_available else None,
        "leading_theme": features.leading_theme if theme_available else "",
        "concentration_ppm": features.leading_theme_concentration_ppm if theme_available else None,
        "valid_member_count": features.theme_valid_member_count if theme_available else 0,
        "universe_size": features.universe_size,
        "allocation_policy": THEME_ALLOCATION_POLICY,
        "reasons": ("theme_evidence_stale",) if theme_available and stale else (
            ("representative_theme_only_not_causal_news_evidence", "theme_revision_timestamp_not_in_frame")
            if theme_available else ("theme_evidence_unavailable",)
        ),
    }
    return MarketContextChanges(
        cutoff.isoformat(), current.available_at, expires.isoformat(), venue, window,
        pattern, reasons, features, classification, changes,
        _candidate_observations(same_day, parameters, stale), theme_evidence,
        {"status": "STALE" if stale else "PARTIAL" if missing else "COMPLETE",
         "reasons": tuple(dict.fromkeys(missing)), "observation_count": len(same_day),
         "recent_observation_count": len(recent), "breakout_evidence_status": breakout_status,
         "required_data_capabilities": ("complete_top20_membership_history", "top20_trade_value_snapshot"),
         "candidate_history_status": "COMPLETE" if (
             all(_complete_top20_frame(frame) for frame in same_day)
             and not _gap_reasons(same_day, parameters.max_gap_seconds)
         ) else "PARTIAL"},
        tuple(dict.fromkeys((*(frame.revision_id for frame in same_day), *features.input_refs,
                             *((breakout_input_ref,) if breakout_eligible else ())))),
        parameters=parameters,
        lookup_change={
            "state": "UNKNOWN" if (stale or previous is None
                or not all(_complete_top20_frame(frame) for frame in same_day[-2:])
                or _gap_reasons(same_day[-2:], parameters.max_gap_seconds))
                else "CHANGED" if before_leader != after_leader else "RETAINED",
            "previous_first_code": before_leader, "current_first_code": after_leader,
            "meaning": "lookup_first_rank_only_not_price_trade_leader",
            "input_refs": tuple(frame.revision_id for frame in same_day[-2:]),
        },
    )


def _complete_top20_frame(frame: MarketResearchFrame) -> bool:
    return frame.capture_quality == "complete" and len(frame.members) == 20


def _observed_market_pattern(
    features: MarketResearchFeatures, before_leader: str, after_leader: str,
    missing: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if missing:
        return "UNKNOWN", missing
    if (features.fixed_k_turnover_ppm is not None and features.fixed_k_turnover_ppm >= 400_000
            and features.jaccard_change_ppm is not None and features.jaccard_change_ppm >= 350_000
            and features.leader_turnover_ppm is not None and features.leader_turnover_ppm >= 500_000
            and features.current_leader_top_k_residency_ppm is not None
            and features.current_leader_top_k_residency_ppm <= 500_000
            and features.top1_concentration_ppm is not None and features.top1_concentration_ppm < 400_000):
        return "DISPERSED_ROTATION", ("observed_rank_rotation_without_breakout_outcome_claim",)
    if before_leader != after_leader:
        return "LEADER_REPLACEMENT", ("observed_first_rank_changed",)
    if (features.top1_concentration_ppm is not None and features.top1_concentration_ppm >= 400_000
            and features.current_leader_top_k_residency_ppm is not None
            and features.current_leader_top_k_residency_ppm >= 667_000):
        return "LEADER_CONCENTRATION", ("observed_first_rank_trade_value_concentration_and_residency",)
    return "LEADER_RETAINED", ("observed_first_rank_retained",)


def _candidate_observations(
    frames: tuple[MarketResearchFrame, ...], parameters: MarketFeatureParameters, stale: bool,
) -> tuple[MarketCandidateObservation, ...]:
    rows = tuple({normalize_stock_code(row.code): (rank, row)
                  for rank, row in enumerate(frame.members, 1)} for frame in frames)
    seen_indices: dict[str, list[int]] = {}
    for index, frame_rows in enumerate(rows):
        for code in frame_rows:
            seen_indices.setdefault(code, []).append(index)
    current = frames[-1]
    previous = frames[-2] if len(frames) >= 2 else None
    pair_complete = (previous is not None and not stale
                     and all(_complete_top20_frame(frame) for frame in frames[-2:])
                     and not _gap_reasons(frames[-2:], parameters.max_gap_seconds))
    history_complete = (all(_complete_top20_frame(frame) for frame in frames)
                        and not _gap_reasons(frames, parameters.max_gap_seconds))
    result = []
    for code, seen in sorted(seen_indices.items()):
        before = rows[-2].get(code) if previous is not None else None
        after = rows[-1].get(code)
        price_delta = value_delta = None
        reasons = []
        if stale:
            state, reasons = "UNKNOWN", ["market_frame_stale"]
        elif previous is None:
            state, reasons = "OBSERVED", ["no_previous_rank_observation"]
        elif not pair_complete:
            state, reasons = "UNKNOWN", ["latest_membership_transition_unconfirmed"]
        elif before and after:
            state, reasons = "RETAINED", ["present_in_latest_two_frames"]
            if before[1].change_bps is not None and after[1].change_bps is not None:
                price_delta = after[1].change_bps - before[1].change_bps
            else:
                reasons.append("price_change_unavailable")
            if current.window != "cumulative_session":
                reasons.append("trade_value_window_not_cumulative")
            elif before[1].trade_value_million_won is None or after[1].trade_value_million_won is None:
                reasons.append("trade_value_unavailable")
            elif after[1].trade_value_million_won < before[1].trade_value_million_won:
                reasons.append("trade_value_counter_decreased")
            else:
                value_delta = after[1].trade_value_million_won - before[1].trade_value_million_won
        elif before:
            state, reasons = "LEFT", ["absent_from_latest_complete_top20_not_market_exit"]
        elif after and history_complete:
            state = "REAPPEARED" if seen[0] < len(frames) - 2 else "ENTERED"
            reasons = ["within_supplied_same_day_frames_only"]
        elif after:
            state, reasons = "UNKNOWN", ["earlier_membership_history_unconfirmed"]
        else:
            state, reasons = "ABSENT", ["absent_from_latest_complete_top20_not_market_exit"]
        result.append(MarketCandidateObservation(
            code, state, before[0] if before else None, after[0] if after else None,
            len(seen), len(frames), frames[seen[0]].available_at, frames[seen[-1]].available_at,
            price_delta, value_delta, tuple(reasons),
            tuple(frames[index].revision_id for index in sorted(
                {*seen, *range(max(0, len(frames) - 2), len(frames))},
            )),
        ))
    return tuple(result)


def compute_market_research_features(
    frames: Iterable[MarketResearchFrame],
    breakout_summary: MatureBreakoutSummary,
    parameters: MarketFeatureParameters = MarketFeatureParameters(),
    *,
    as_of: datetime | None = None,
) -> MarketResearchFeatures:
    """가상 시각까지 보였던 같은 venue/window 관측만 사용한다."""
    cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    ordered = sorted(
        (
            frame for frame in frames
            if cutoff is None or _aware_datetime(frame.available_at).astimezone(timezone.utc) <= cutoff
        ),
        key=lambda frame: (_aware_datetime(frame.available_at), frame.revision_id),
    )
    if not ordered:
        raise ValueError("at least one market frame is required")
    current = ordered[-1]
    comparable = tuple(
        frame for frame in ordered
        if frame.venue == current.venue and frame.window == current.window
    )
    same_day = tuple(frame for frame in comparable if frame.trading_date == current.trading_date)
    recent = same_day[-max(parameters.min_history_observations, 2):]

    valid_values = [
        row.trade_value_million_won for row in current.members
        if row.trade_value_million_won is not None
    ]
    trade_denominator = sum(valid_values) if len(valid_values) == len(current.members) else None
    if trade_denominator is not None and trade_denominator <= 0:
        trade_denominator = None
    top1 = _share(
        current.members[0].trade_value_million_won if current.members else None,
        trade_denominator,
    )
    top5_values = [row.trade_value_million_won for row in current.members[:5]]
    top5 = _share(
        sum(value for value in top5_values if value is not None)
        if top5_values and all(value is not None for value in top5_values) else None,
        trade_denominator,
    )

    previous = same_day[-2] if len(same_day) >= 2 else None
    fixed_turnover, fixed_denominator = _fixed_turnover(current, previous, parameters.top_k)
    jaccard, union_denominator = _jaccard_change(current, previous)
    gaps = _gap_reasons(recent, parameters.max_gap_seconds)
    leader_persistence, residency_count = _leader_residency(recent, parameters.top_k, gaps)
    leader_turnover, transition_count = _leader_turnover(recent, gaps)

    historical_rows = _historical_totals(comparable, current, parameters.history_days)
    historical_totals = tuple(total for _, total in historical_rows)
    historical_median = int(median(historical_totals)) if historical_totals else None
    relative = _share(trade_denominator, historical_median)

    theme_metrics = _theme_metrics(current, trade_denominator)
    breadth_values = [row.change_bps for row in current.members if row.change_bps is not None]
    breadth = (
        sum(value > 0 for value in breadth_values) * 1_000_000 // len(breadth_values)
        if breadth_values else None
    )
    success = _share(breakout_summary.success_count, breakout_summary.completed_count)
    failure = _share(breakout_summary.failed_count, breakout_summary.completed_count)

    missing: list[str] = []
    warnings: list[str] = []
    if current.capture_quality != "complete":
        missing.append("current_capture_incomplete")
    if trade_denominator is None or len(valid_values) != len(current.members):
        missing.append("trade_value_denominator_unavailable")
    if len(recent) < parameters.min_history_observations:
        missing.append("rank_history_warmup")
    if gaps:
        missing.append("rank_observation_gap")
    if theme_metrics[5] < 2:
        missing.append("theme_membership_coverage_insufficient")
    if breakout_summary.completed_count < parameters.min_mature_breakouts:
        missing.append("mature_breakout_sample_insufficient")
    if not breadth_values:
        missing.append("top20_breadth_unavailable")
    if relative is None:
        warnings.append("same_session_history_unavailable")
    quality = {
        "status": "COMPLETE" if not missing else "PARTIAL",
        "missing_inputs": tuple(dict.fromkeys(missing)),
        "warnings": tuple(warnings),
        "capture_quality": current.capture_quality,
        "valid_observation_count": len(recent),
    }
    return MarketResearchFeatures(
        as_of=current.available_at, venue=current.venue, window=current.window,
        universe_size=len(current.members),
        trade_value_denominator_million_won=trade_denominator,
        trade_value_valid_count=len(valid_values),
        top1_concentration_ppm=top1, top5_concentration_ppm=top5,
        fixed_k_turnover_ppm=fixed_turnover, fixed_k_denominator=fixed_denominator,
        jaccard_change_ppm=jaccard, jaccard_union_denominator=union_denominator,
        current_leader_top_k_residency_ppm=leader_persistence,
        residency_observation_count=residency_count,
        leader_turnover_ppm=leader_turnover, leader_transition_count=transition_count,
        relative_trade_value_ppm=relative,
        historical_median_trade_value_million_won=historical_median,
        historical_day_count=len(historical_totals),
        leading_theme=theme_metrics[0],
        leading_theme_concentration_ppm=theme_metrics[1],
        leading_theme_member_count=theme_metrics[2],
        leading_theme_active_ratio_ppm=theme_metrics[3],
        theme_valid_member_count=theme_metrics[5],
        theme_allocation_policy=THEME_ALLOCATION_POLICY,
        top20_breadth_ppm=breadth, top20_breadth_denominator=len(breadth_values),
        mature_breakout_success_ppm=success, mature_breakout_failure_ppm=failure,
        mature_breakout_denominator=breakout_summary.completed_count,
        pending_breakout_count=breakout_summary.pending_count,
        index_return_bps=current.index_return_bps,
        input_refs=tuple(dict.fromkeys((
            *(frame.revision_id for frame in recent),
            *(revision_id for revision_id, _ in historical_rows),
            *([current.theme_revision_id] if current.theme_revision_id else []),
        ))),
        quality=quality,
    )


def classify_market(features: MarketResearchFeatures) -> MarketClassification:
    """검증 전인 고정 v1 규칙으로 후보 유형을 만들고 혼재는 보류한다."""
    missing = tuple(features.quality.get("missing_inputs", ()))
    if missing:
        return MarketClassification("UNKNOWN", (), missing, features.quality)
    values = (
        features.top1_concentration_ppm,
        features.fixed_k_turnover_ppm,
        features.jaccard_change_ppm,
        features.current_leader_top_k_residency_ppm,
        features.leader_turnover_ppm,
        features.leading_theme_concentration_ppm,
        features.leading_theme_active_ratio_ppm,
        features.top20_breadth_ppm,
        features.mature_breakout_success_ppm,
        features.mature_breakout_failure_ppm,
    )
    if any(value is None for value in values):
        quality = {**dict(features.quality), "status": "PARTIAL"}
        return MarketClassification(
            "UNKNOWN", (), ("classification_input_unavailable",), quality,
        )
    top1, turnover, jaccard, persistence, leader_changes, theme_share, theme_active, breadth, success, failure = (
        int(value) for value in values
    )
    candidates: list[str] = []
    reasons: list[str] = []
    if (
        top1 >= 400_000 and persistence >= 667_000 and breadth < 500_000
        and features.leading_theme_member_count <= 1 and success >= 500_000
    ):
        candidates.append("SINGLE_LEADER")
        reasons.append("one_stock_concentration_and_persistence")
    if (
        theme_share >= 450_000 and features.leading_theme_member_count >= 2
        and theme_active >= 600_000 and persistence >= 500_000 and success >= 500_000
    ):
        candidates.append("THEME_LED")
        reasons.append("theme_concentration_with_active_spread")
    if (
        turnover >= 400_000 and jaccard >= 350_000 and leader_changes >= 500_000
        and persistence <= 500_000 and failure >= 500_000
    ):
        candidates.append("DISPERSED_ROTATION")
        reasons.append("repeated_leader_rotation_with_breakout_failures")
    if (
        top1 < 400_000 and turnover < 400_000 and persistence >= 500_000
        and theme_share < 450_000 and breadth >= 400_000 and success >= 500_000
    ):
        candidates.append("NORMAL_LEADER")
        reasons.append("persistent_leaders_with_broader_opportunity")
    if len(candidates) == 1:
        return MarketClassification(candidates[0], tuple(candidates), tuple(reasons), features.quality)
    if len(candidates) > 1:
        return MarketClassification(
            "UNKNOWN", tuple(candidates), (*reasons, "mixed_market_type_evidence"), features.quality,
        )
    if (
        features.index_return_bps is not None and features.index_return_bps >= 100
        and breadth < 400_000
    ):
        reasons.append("index_strength_without_top20_confirmation")
    else:
        reasons.append("no_experimental_market_rule_matched")
    return MarketClassification("UNKNOWN", (), tuple(reasons), features.quality)


def assess_market_for_strategy(
    classification: MarketClassification,
    *,
    strategy_id: str,
    preferred_types: Iterable[str],
    difficult_types: Iterable[str] = (),
) -> StrategyMarketAssessment:
    """시장 유형과 전략별 매매가능성/난이도를 별도 계약으로 평가한다."""
    preferred = frozenset(preferred_types)
    difficult = frozenset(difficult_types)
    if any(value not in MARKET_TYPES - {"UNKNOWN"} for value in (*preferred, *difficult)):
        raise ValueError("strategy market type is unknown")
    if classification.market_type == "UNKNOWN":
        return StrategyMarketAssessment(
            strategy_id, "UNKNOWN", "UNKNOWN", "UNKNOWN",
            ("market_classification_unavailable",),
        )
    tradability = "SUPPORTED" if classification.market_type in preferred else "NOT_PREFERRED"
    difficulty = "HIGH" if classification.market_type in difficult else "NORMAL"
    return StrategyMarketAssessment(
        strategy_id, classification.market_type, tradability, difficulty,
        ("strategy_specific_market_policy",),
    )


def market_frames_from_observations(
    observations: Iterable[Mapping[str, Any]],
    theme_snapshots: Iterable[Mapping[str, Any]] = (),
) -> tuple[MarketResearchFrame, ...]:
    """D2 ranking revision과 D5 테마 revision을 계산용 frame으로 바꾼다."""
    themes = sorted(
        (value for value in theme_snapshots if _snapshot_available_at(value) is not None),
        key=lambda value: (_snapshot_available_at(value), str(value.get("snapshot_id", ""))),
    )
    result: list[MarketResearchFrame] = []
    for observation in observations:
        if observation.get("kind") != "ranking":
            continue
        payload = observation.get("payload")
        if not isinstance(payload, Mapping) or str(payload.get("query_type", "")) != "5":
            continue
        available = _optional_aware_datetime(observation.get("available_at"))
        items = payload.get("items")
        if available is None or not isinstance(items, list):
            continue
        snapshot = _latest_theme_snapshot(themes, available)
        theme_map = _theme_map(snapshot)
        members: list[MarketMember] = []
        for item in items[:20]:
            if not isinstance(item, Mapping):
                continue
            code = normalize_stock_code(item.get("stk_cd", item.get("code", "")))
            if not code:
                continue
            members.append(MarketMember(
                code=code,
                trade_value_million_won=_optional_nonnegative_int(item.get("trde_prica")),
                change_bps=_percent_to_bps(item.get("flu_rt")),
                themes=theme_map.get(code, ()),
            ))
        effective = _optional_aware_datetime(observation.get("effective_at")) or available
        local = effective.astimezone(_SEOUL)
        result.append(MarketResearchFrame(
            revision_id=str(observation.get("revision_id", "")),
            available_at=available.isoformat(), trading_date=local.date().isoformat(),
            session_minute=local.strftime("%H:%M"),
            venue=str(observation.get("venue", "COMBINED")), window="cumulative_session",
            members=tuple(members),
            capture_quality=str(observation.get("completeness", "unknown")),
            theme_revision_id=str(snapshot.get("snapshot_id", "")) if snapshot else "",
        ))
    return tuple(sorted(result, key=lambda frame: (frame.available_at, frame.revision_id)))


def _fixed_turnover(
    current: MarketResearchFrame, previous: MarketResearchFrame | None, top_k: int,
) -> tuple[int | None, int | None]:
    if previous is None or len(current.members) < top_k or len(previous.members) < top_k:
        return None, None
    left = {normalize_stock_code(row.code) for row in current.members[:top_k]}
    right = {normalize_stock_code(row.code) for row in previous.members[:top_k]}
    return (top_k - len(left & right)) * 1_000_000 // top_k, top_k


def _jaccard_change(
    current: MarketResearchFrame, previous: MarketResearchFrame | None,
) -> tuple[int | None, int | None]:
    if previous is None:
        return None, None
    left = {normalize_stock_code(row.code) for row in current.members}
    right = {normalize_stock_code(row.code) for row in previous.members}
    union = left | right
    if not union:
        return None, None
    return (len(union) - len(left & right)) * 1_000_000 // len(union), len(union)


def _leader_residency(
    frames: tuple[MarketResearchFrame, ...], top_k: int, gaps: tuple[str, ...],
) -> tuple[int | None, int]:
    if len(frames) < 2 or gaps or not frames[-1].members:
        return None, len(frames)
    leader = normalize_stock_code(frames[-1].members[0].code)
    present = sum(
        leader in {normalize_stock_code(row.code) for row in frame.members[:top_k]}
        for frame in frames
    )
    return present * 1_000_000 // len(frames), len(frames)


def _leader_turnover(
    frames: tuple[MarketResearchFrame, ...], gaps: tuple[str, ...],
) -> tuple[int | None, int]:
    transitions = len(frames) - 1
    if transitions <= 0 or gaps or any(not frame.members for frame in frames):
        return None, max(0, transitions)
    changes = sum(
        normalize_stock_code(before.members[0].code) != normalize_stock_code(after.members[0].code)
        for before, after in zip(frames, frames[1:])
    )
    return changes * 1_000_000 // transitions, transitions


def _historical_totals(
    frames: tuple[MarketResearchFrame, ...], current: MarketResearchFrame, limit: int,
) -> tuple[tuple[str, int], ...]:
    by_date: dict[str, tuple[str, int]] = {}
    for frame in frames:
        if frame.trading_date == current.trading_date or frame.session_minute != current.session_minute:
            continue
        values = [row.trade_value_million_won for row in frame.members]
        if values and all(value is not None for value in values):
            total = sum(int(value) for value in values if value is not None)
            if total > 0:
                by_date[frame.trading_date] = (frame.revision_id, total)
    return tuple(value for _, value in sorted(by_date.items())[-limit:])


def _theme_metrics(
    frame: MarketResearchFrame, denominator: int | None,
) -> tuple[str, int | None, int, int | None, int, int]:
    totals: dict[str, int] = {}
    counts: dict[str, int] = {}
    active: dict[str, int] = {}
    valid = 0
    for row in frame.members:
        if not row.themes or row.trade_value_million_won is None:
            continue
        theme = row.themes[0].strip()
        if not theme:
            continue
        valid += 1
        key = theme.casefold()
        totals[key] = totals.get(key, 0) + row.trade_value_million_won
        counts[key] = counts.get(key, 0) + 1
        if row.change_bps is not None and row.change_bps > 0:
            active[key] = active.get(key, 0) + 1
    if not totals:
        return "", None, 0, None, 0, valid
    leader = max(totals, key=lambda key: (totals[key], counts[key], key))
    count = counts[leader]
    active_count = active.get(leader, 0)
    return (
        leader, _share(totals[leader], denominator), count,
        active_count * 1_000_000 // count if count else None, active_count, valid,
    )


def _gap_reasons(frames: tuple[MarketResearchFrame, ...], max_gap_seconds: int) -> tuple[str, ...]:
    if any(
        (_aware_datetime(after.available_at) - _aware_datetime(before.available_at)).total_seconds()
        > max_gap_seconds
        for before, after in zip(frames, frames[1:])
    ):
        return ("rank_observation_gap",)
    return ()


def _share(numerator: int | None, denominator: int | None) -> int | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return int(numerator) * 1_000_000 // int(denominator)


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("market research timestamps must be timezone-aware")
    return parsed


def _optional_aware_datetime(value: object) -> datetime | None:
    try:
        return _aware_datetime(value)
    except (TypeError, ValueError):
        return None


def _optional_nonnegative_int(value: object) -> int | None:
    try:
        parsed = int(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None
    return abs(parsed) if parsed >= 0 else None


def _percent_to_bps(value: object) -> int | None:
    try:
        return int(Decimal(str(value).replace(",", "").replace("%", "").strip()) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _snapshot_available_at(value: Mapping[str, Any]) -> datetime | None:
    raw = value.get("available_at")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    return _optional_aware_datetime(raw)


def _latest_theme_snapshot(
    values: list[Mapping[str, Any]], available: datetime,
) -> Mapping[str, Any] | None:
    eligible = [
        value for value in values
        if (_snapshot_available_at(value) or datetime.max.replace(tzinfo=timezone.utc))
        <= available.astimezone(timezone.utc)
    ]
    return eligible[-1] if eligible else None


def _theme_map(snapshot: Mapping[str, Any] | None) -> dict[str, tuple[str, ...]]:
    if snapshot is None or not isinstance(snapshot.get("document"), Mapping):
        return {}
    document = snapshot["document"]
    assert isinstance(document, Mapping)
    active = str(document.get("active_profile", "")).casefold()
    profiles = document.get("profiles")
    if not isinstance(profiles, list):
        return {}
    profile = next(
        (value for value in profiles if isinstance(value, Mapping)
         and str(value.get("name", "")).casefold() == active),
        None,
    )
    if not isinstance(profile, Mapping):
        return {}
    assignments = profile.get("stock_themes")
    if not isinstance(assignments, list):
        return {}
    result: dict[str, list[str]] = {}
    for value in assignments:
        if not isinstance(value, Mapping):
            continue
        code = normalize_stock_code(value.get("code", ""))
        theme = str(value.get("theme", "")).strip()
        if code and theme and theme.casefold() not in {item.casefold() for item in result.get(code, [])}:
            result.setdefault(code, []).append(theme)
    return {code: tuple(names) for code, names in result.items()}
