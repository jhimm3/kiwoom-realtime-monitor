"""동일 시점의 TOP20 자료로 시장 특징과 실험 분류를 계산한다.

이 모듈은 저장소나 UI를 알지 못한다. 입력에 포함되지 않은 전체시장 자료를
추정하지 않으며, 분류에 필요한 근거가 모자라면 UNKNOWN을 반환한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.ranking import normalize_stock_code


MARKET_RULE_VERSION = "top20_market_types/v1"
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
