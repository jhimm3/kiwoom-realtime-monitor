"""고정 연구 입력에서 계산하는 첫 두 Factor의 순수 함수 계약."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.market_research_features import (
    MARKET_RULE_VERSION,
    MarketFeatureParameters,
    MarketResearchFrame,
    MatureBreakoutSummary,
    classify_market,
    compute_market_research_features,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)
from kiwoom_monitor.application.theme_leadership import (
    LEADERSHIP_FACTOR_ID,
    LEADERSHIP_VERSION,
    SecondTradePoint,
    ThemeLeadershipParameters,
    ThemeLeadershipState,
    ThemeMembership,
    UpperLimitReference,
    evaluate_theme_leadership,
)
from kiwoom_monitor.domain.ranking import normalize_stock_code


ROLLING_HIGH_BREAKOUT_ID = "rolling_high_breakout"
PULLBACK_REACCELERATION_ID = "pullback_reacceleration"
RANK_PERSISTENCE_ID = "rank_persistence"
MARKET_REGIME_ID = "market_regime"
THEME_LEADERSHIP_ID = LEADERSHIP_FACTOR_ID
FACTOR_VERSION = "v1"
_SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    version: str
    output_schema: Mapping[str, str]
    unit: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...]
    lookback: str
    warmup: str
    max_age: str
    missing_policy: str
    compute: Callable[..., "FactorValue"]


@dataclass(frozen=True)
class FactorValue:
    factor_id: str
    version: str
    parameters_hash: str
    entity: str
    as_of: str
    available_at: str
    value: Mapping[str, Any] | None
    status: str
    reason: str
    input_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RollingHighBreakoutParameters:
    lookback_bars: int
    buffer_bps: int

    def __post_init__(self) -> None:
        if self.lookback_bars <= 0:
            raise ValueError("lookback_bars must be positive")
        if self.buffer_bps < 0:
            raise ValueError("buffer_bps must not be negative")


@dataclass(frozen=True)
class RankPersistenceParameters:
    top_k: int
    window_seconds: int
    max_gap_seconds: int

    def __post_init__(self) -> None:
        if self.top_k <= 0 or self.top_k > 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")


@dataclass(frozen=True)
class PullbackReaccelerationParameters:
    lookback_bars: int
    minimum_pullback_bps: int
    minimum_reacceleration_bps: int

    def __post_init__(self) -> None:
        if self.lookback_bars < 3:
            raise ValueError("pullback lookback_bars must be at least 3")
        if self.minimum_pullback_bps <= 0:
            raise ValueError("minimum_pullback_bps must be positive")
        if self.minimum_reacceleration_bps < 0:
            raise ValueError("minimum_reacceleration_bps must not be negative")


FACTOR_REGISTRY: Mapping[tuple[str, str], FactorDefinition] = MappingProxyType({
    (ROLLING_HIGH_BREAKOUT_ID, FACTOR_VERSION): FactorDefinition(
        factor_id=ROLLING_HIGH_BREAKOUT_ID,
        version=FACTOR_VERSION,
        output_schema={
            "breakout": "boolean",
            "reference_high": "KRW",
            "reference_revision_id": "observation_revision_id",
            "threshold_numerator": "KRW*10000",
            "distance_bps": "bps",
        },
        unit="KRW,bps",
        dependencies=("minute_bar/KRX/closed/complete",),
        parameters=("lookback_bars", "buffer_bps"),
        lookback="previous N available closed one-minute bars in the same KRX session",
        warmup="N preceding bars with continuous minute coverage",
        max_age="evaluation bar close",
        missing_policy="required; missing blocks a new entry",
        compute=lambda *args, **kwargs: compute_rolling_high_breakout(*args, **kwargs),
    ),
    (PULLBACK_REACCELERATION_ID, FACTOR_VERSION): FactorDefinition(
        factor_id=PULLBACK_REACCELERATION_ID,
        version=FACTOR_VERSION,
        output_schema={
            "reaccelerated": "boolean",
            "peak_price": "KRW",
            "trough_price": "KRW",
            "pullback_bps": "bps",
            "reacceleration_bps": "bps",
            "reference_revision_id": "observation_revision_id",
        },
        unit="KRW,bps",
        dependencies=("minute_bar/KRX/closed/complete",),
        parameters=(
            "lookback_bars", "minimum_pullback_bps", "minimum_reacceleration_bps",
        ),
        lookback="previous N available closed one-minute bars in the same KRX session",
        warmup="N continuous bars containing a peak followed by a pullback",
        max_age="evaluation bar close",
        missing_policy="required; missing blocks a new entry",
        compute=lambda *args, **kwargs: compute_pullback_reacceleration(*args, **kwargs),
    ),
    (RANK_PERSISTENCE_ID, FACTOR_VERSION): FactorDefinition(
        factor_id=RANK_PERSISTENCE_ID,
        version=FACTOR_VERSION,
        output_schema={
            "residency_seconds": "seconds",
            "residency_ratio_ppm": "ppm",
            "observation_ratio_ppm": "ppm",
            "latest_observation_at": "timestamp",
        },
        unit="seconds,ratio",
        dependencies=("top20_membership/ka00198/qry_tp=5",),
        parameters=("top_k", "window_seconds", "max_gap_seconds"),
        lookback="explicit trailing observation window",
        warmup="an observation covering the start plus a later observation",
        max_age="max_gap_seconds",
        missing_policy="strategy profile chooses required, optional, or disabled",
        compute=lambda *args, **kwargs: compute_rank_persistence(*args, **kwargs),
    ),
    (MARKET_REGIME_ID, FACTOR_VERSION): FactorDefinition(
        factor_id=MARKET_REGIME_ID,
        version=FACTOR_VERSION,
        output_schema={
            "features": "top20_market_feature_set",
            "market_type": "enum",
            "candidate_types": "enum[]",
            "reasons": "reason_code[]",
            "quality": "quality_contract",
            "rule_version": "version",
        },
        unit="ppm,count,million_won,bps",
        dependencies=(
            "ranking/ka00198/qry_tp=5",
            "theme_snapshot/as_available",
            "mature_breakout_outcomes/complete_only",
        ),
        parameters=(
            "top_k", "history_days", "max_gap_seconds",
            "min_history_observations", "min_mature_breakouts",
        ),
        lookback="same-session rank window plus previous N trading days at the same session minute",
        warmup="rank observations, theme coverage, and completed breakout outcomes",
        max_age="max_gap_seconds",
        missing_policy="optional; preserve raw features and classify UNKNOWN",
        compute=lambda *args, **kwargs: compute_market_regime(*args, **kwargs),
    ),
    (THEME_LEADERSHIP_ID, FACTOR_VERSION): FactorDefinition(
        factor_id=THEME_LEADERSHIP_ID,
        version=FACTOR_VERSION,
        output_schema={
            "raw_leader_id": "stock_code",
            "displayed_leader_id": "stock_code",
            "trend_state": "enum",
            "expansion_state": "enum",
            "limit_state": "NONE|NEAR|TOUCHED|UNKNOWN",
            "events": "leadership_event[]",
            "quality": "quality_contract",
            "rule_version": "version",
        },
        unit="KRW,bps,ppm,count,seconds",
        dependencies=(
            "second_trade_bar/as_available/single_venue",
            "theme_snapshot/as_available",
            "upper_limit_fact/as_available/optional",
        ),
        parameters=(
            "window_seconds", "max_data_age_seconds", "price_exit_bps",
            "price_reclaim_bps", "slowdown_ratio_ppm", "reacceleration_ratio_ppm",
            "near_limit_bps", "display_confirmation_seconds",
        ),
        lookback="two adjacent second-trade windows in one KRX or NXT venue",
        warmup="complete continuity and a current price for every theme member",
        max_age="max_data_age_seconds",
        missing_policy="optional; gaps keep prior display state and return missing",
        compute=lambda *args, **kwargs: compute_theme_leadership(*args, **kwargs),
    ),
})


def get_factor_definition(factor_id: str, version: str) -> FactorDefinition:
    try:
        return FACTOR_REGISTRY[(str(factor_id), str(version))]
    except KeyError as exc:
        raise ValueError(f"unregistered factor: {factor_id}/{version}") from exc


def compute_theme_leadership(
    membership: ThemeMembership,
    points: Iterable[SecondTradePoint],
    *,
    as_of: str,
    venue: str,
    continuity_status: str,
    parameters: ThemeLeadershipParameters,
    previous_state: ThemeLeadershipState | None = None,
    upper_limits: Iterable[UpperLimitReference] = (),
) -> FactorValue:
    get_factor_definition(THEME_LEADERSHIP_ID, FACTOR_VERSION)
    evaluation = evaluate_theme_leadership(
        membership, points, as_of=as_of, venue=venue,
        continuity_status=continuity_status, parameters=parameters,
        previous_state=previous_state, upper_limits=upper_limits,
    )
    quality_status = str(evaluation.quality.get("status", "PARTIAL"))
    reasons = tuple(str(value) for value in evaluation.quality.get("reasons", ()))
    return FactorValue(
        factor_id=THEME_LEADERSHIP_ID,
        version=FACTOR_VERSION,
        parameters_hash=_stable_parameters_hash(asdict(parameters)),
        entity=membership.theme_id,
        as_of=evaluation.as_of,
        available_at=evaluation.as_of,
        value={**evaluation.to_dict(), "rule_version": LEADERSHIP_VERSION},
        status="valid" if quality_status == "COMPLETE" else "missing",
        reason="" if quality_status == "COMPLETE" else ";".join(reasons),
        input_refs=evaluation.input_refs,
    )


def compute_rolling_high_breakout(
    bars: Iterable[KrxMinuteBarFrame],
    evaluation_bar: KrxMinuteBarFrame,
    parameters: RollingHighBreakoutParameters,
) -> FactorValue:
    get_factor_definition(ROLLING_HIGH_BREAKOUT_ID, FACTOR_VERSION)
    parameter_hash = _stable_parameters_hash(asdict(parameters))
    evaluation_start = _aware_datetime(evaluation_bar.bar_start)
    evaluation_end = _aware_datetime(evaluation_bar.bar_end)
    evaluation_available = _aware_datetime(evaluation_bar.available_at)
    if evaluation_bar.capture_quality != "complete":
        return _missing(
            ROLLING_HIGH_BREAKOUT_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "evaluation_bar_capture_incomplete",
            (evaluation_bar.revision_id,),
        )

    session_date = evaluation_start.astimezone(_SEOUL).date()
    eligible: list[tuple[datetime, KrxMinuteBarFrame]] = []
    for bar in bars:
        if normalize_stock_code(bar.code) != normalize_stock_code(evaluation_bar.code):
            continue
        bar_start = _aware_datetime(bar.bar_start)
        bar_end = _aware_datetime(bar.bar_end)
        available = _aware_datetime(bar.available_at)
        if (
            bar.revision_id == evaluation_bar.revision_id
            or bar.capture_quality != "complete"
            or bar_start.astimezone(_SEOUL).date() != session_date
            or not _same_research_session(bar, evaluation_bar)
            or bar_end > evaluation_start
            or available > evaluation_available
        ):
            continue
        eligible.append((bar_end, bar))
    selected = [bar for _, bar in sorted(eligible, key=lambda item: item[0])][
        -parameters.lookback_bars:
    ]
    input_refs = tuple(bar.revision_id for bar in selected) + (evaluation_bar.revision_id,)
    if len(selected) != parameters.lookback_bars:
        return _missing(
            ROLLING_HIGH_BREAKOUT_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "insufficient_preceding_bars", input_refs,
        )
    if not _has_continuous_minute_coverage(selected, evaluation_start):
        return _missing(
            ROLLING_HIGH_BREAKOUT_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "minute_coverage_gap", input_refs,
        )
    reference = max(bar.high for bar in selected)
    if reference <= 0:
        return _missing(
            ROLLING_HIGH_BREAKOUT_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "invalid_reference_high", input_refs,
        )
    threshold_numerator = reference * (10_000 + parameters.buffer_bps)
    distance_bps = ((evaluation_bar.close - reference) * 10_000) // reference
    reference_revision = next(bar.revision_id for bar in reversed(selected) if bar.high == reference)
    return FactorValue(
        factor_id=ROLLING_HIGH_BREAKOUT_ID,
        version=FACTOR_VERSION,
        parameters_hash=parameter_hash,
        entity=normalize_stock_code(evaluation_bar.code),
        as_of=evaluation_end.isoformat(),
        available_at=evaluation_available.isoformat(),
        value={
            "breakout": evaluation_bar.close * 10_000 > threshold_numerator,
            "reference_high": reference,
            "reference_revision_id": reference_revision,
            "threshold_numerator": threshold_numerator,
            "distance_bps": distance_bps,
        },
        status="valid",
        reason="",
        input_refs=input_refs,
    )


def compute_pullback_reacceleration(
    bars: Iterable[KrxMinuteBarFrame],
    evaluation_bar: KrxMinuteBarFrame,
    parameters: PullbackReaccelerationParameters,
) -> FactorValue:
    """연속 완료봉의 고점 이후 눌림과 현재 봉 재가속을 계산한다."""
    get_factor_definition(PULLBACK_REACCELERATION_ID, FACTOR_VERSION)
    parameter_hash = _stable_parameters_hash(asdict(parameters))
    evaluation_start = _aware_datetime(evaluation_bar.bar_start)
    evaluation_end = _aware_datetime(evaluation_bar.bar_end)
    evaluation_available = _aware_datetime(evaluation_bar.available_at)
    if evaluation_bar.capture_quality != "complete":
        return _missing(
            PULLBACK_REACCELERATION_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "evaluation_bar_capture_incomplete",
            (evaluation_bar.revision_id,),
        )
    session_date = evaluation_start.astimezone(_SEOUL).date()
    eligible: list[tuple[datetime, KrxMinuteBarFrame]] = []
    for bar in bars:
        if normalize_stock_code(bar.code) != normalize_stock_code(evaluation_bar.code):
            continue
        bar_start = _aware_datetime(bar.bar_start)
        bar_end = _aware_datetime(bar.bar_end)
        available = _aware_datetime(bar.available_at)
        if (
            bar.revision_id == evaluation_bar.revision_id
            or bar.capture_quality != "complete"
            or bar_start.astimezone(_SEOUL).date() != session_date
            or not _same_research_session(bar, evaluation_bar)
            or bar_end > evaluation_start
            or available > evaluation_available
        ):
            continue
        eligible.append((bar_end, bar))
    selected = [bar for _, bar in sorted(eligible, key=lambda item: item[0])][
        -parameters.lookback_bars:
    ]
    refs = tuple(bar.revision_id for bar in selected) + (evaluation_bar.revision_id,)
    if len(selected) != parameters.lookback_bars:
        return _missing(
            PULLBACK_REACCELERATION_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "insufficient_preceding_bars", refs,
        )
    if not _has_continuous_minute_coverage(selected, evaluation_start):
        return _missing(
            PULLBACK_REACCELERATION_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "minute_coverage_gap", refs,
        )
    peak_index = max(range(len(selected) - 1), key=lambda index: selected[index].high)
    after_peak = selected[peak_index + 1:]
    if not after_peak:
        return _missing(
            PULLBACK_REACCELERATION_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "pullback_window_missing", refs,
        )
    peak_price = selected[peak_index].high
    trough_bar = min(after_peak, key=lambda bar: bar.low)
    if peak_price <= 0 or trough_bar.low <= 0 or selected[-1].close <= 0:
        return _missing(
            PULLBACK_REACCELERATION_ID, parameter_hash, evaluation_bar.code,
            evaluation_end, evaluation_available, "invalid_pullback_price", refs,
        )
    pullback_bps = (peak_price - trough_bar.low) * 10_000 // peak_price
    reacceleration_bps = (
        (evaluation_bar.close - selected[-1].close) * 10_000 // selected[-1].close
    )
    reaccelerated = (
        pullback_bps >= parameters.minimum_pullback_bps
        and reacceleration_bps >= parameters.minimum_reacceleration_bps
        and evaluation_bar.close > trough_bar.low
    )
    return FactorValue(
        factor_id=PULLBACK_REACCELERATION_ID,
        version=FACTOR_VERSION,
        parameters_hash=parameter_hash,
        entity=normalize_stock_code(evaluation_bar.code),
        as_of=evaluation_end.isoformat(),
        available_at=evaluation_available.isoformat(),
        value={
            "reaccelerated": reaccelerated,
            "peak_price": peak_price,
            "trough_price": trough_bar.low,
            "pullback_bps": pullback_bps,
            "reacceleration_bps": reacceleration_bps,
            "reference_revision_id": trough_bar.revision_id,
        },
        status="valid",
        reason="",
        input_refs=refs,
    )


def compute_rank_persistence(
    frames: Iterable[CandidateUniverseFrame],
    code: str,
    as_of: datetime,
    parameters: RankPersistenceParameters,
) -> FactorValue:
    get_factor_definition(RANK_PERSISTENCE_ID, FACTOR_VERSION)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    entity = normalize_stock_code(code)
    cutoff = as_of.astimezone(timezone.utc)
    window_start = cutoff - timedelta(seconds=parameters.window_seconds)
    ordered = sorted(
        (
            (_aware_datetime(frame.available_at).astimezone(timezone.utc), frame)
            for frame in frames
            if _aware_datetime(frame.available_at).astimezone(timezone.utc) <= cutoff
        ),
        key=lambda item: (item[0], item[1].accepted_sequence, item[1].revision_id),
    )
    parameter_hash = _stable_parameters_hash(asdict(parameters))
    prior = [item for item in ordered if item[0] <= window_start]
    within = [item for item in ordered if window_start < item[0] <= cutoff]
    selected = ([prior[-1]] if prior else []) + within
    refs = tuple(frame.revision_id for _, frame in selected)
    latest_input_at = selected[-1][0] if selected else cutoff
    if not prior or not within:
        return _missing(
            RANK_PERSISTENCE_ID, parameter_hash, entity, cutoff, cutoff,
            "rank_window_warmup", refs,
        )
    if any(len(frame.codes) < parameters.top_k for _, frame in selected):
        return _missing(
            RANK_PERSISTENCE_ID, parameter_hash, entity, cutoff, cutoff,
            "partial_rank_list", refs,
        )

    points = [(window_start, selected[0][1])] + within
    max_gap = timedelta(seconds=parameters.max_gap_seconds)
    if window_start - selected[0][0] > max_gap:
        return _missing(
            RANK_PERSISTENCE_ID, parameter_hash, entity, cutoff, cutoff,
            "rank_observation_gap", refs,
        )
    residency_seconds = 0
    for index, (started_at, frame) in enumerate(points):
        ended_at = points[index + 1][0] if index + 1 < len(points) else cutoff
        if ended_at - started_at > max_gap:
            return _missing(
                RANK_PERSISTENCE_ID, parameter_hash, entity, cutoff, cutoff,
                "rank_observation_gap", refs,
            )
        if entity in frame.codes[:parameters.top_k]:
            residency_seconds += int((ended_at - started_at).total_seconds())

    observed_frames = [frame for _, frame in selected]
    present_count = sum(entity in frame.codes[:parameters.top_k] for frame in observed_frames)
    return FactorValue(
        factor_id=RANK_PERSISTENCE_ID,
        version=FACTOR_VERSION,
        parameters_hash=parameter_hash,
        entity=entity,
        as_of=cutoff.isoformat(),
        available_at=cutoff.isoformat(),
        value={
            "residency_seconds": residency_seconds,
            "window_seconds": parameters.window_seconds,
            "residency_ratio_ppm": residency_seconds * 1_000_000 // parameters.window_seconds,
            "observation_count": len(observed_frames),
            "present_observation_count": present_count,
            "observation_ratio_ppm": present_count * 1_000_000 // len(observed_frames),
            "latest_observation_at": latest_input_at.isoformat(),
        },
        status="valid",
        reason="",
        input_refs=refs,
    )


def compute_market_regime(
    frames: Iterable[MarketResearchFrame],
    breakout_summary: MatureBreakoutSummary,
    parameters: MarketFeatureParameters = MarketFeatureParameters(),
    *,
    as_of: datetime | None = None,
) -> FactorValue:
    """원시 특징과 UNKNOWN 가능한 시장 분류를 한 Factor revision으로 묶는다."""
    get_factor_definition(MARKET_REGIME_ID, FACTOR_VERSION)
    features = compute_market_research_features(
        frames, breakout_summary, parameters, as_of=as_of,
    )
    classification = classify_market(features)
    document = classification.to_dict()
    document["features"] = features.to_dict()
    missing = tuple(features.quality.get("missing_inputs", ()))
    return FactorValue(
        factor_id=MARKET_REGIME_ID,
        version=FACTOR_VERSION,
        parameters_hash=_stable_parameters_hash({
            **asdict(parameters), "rule_version": MARKET_RULE_VERSION,
        }),
        entity=f"TOP20:{features.venue}",
        as_of=features.as_of,
        available_at=features.as_of,
        value=document,
        status="valid" if not missing else "partial",
        reason=",".join(str(value) for value in missing),
        input_refs=features.input_refs,
    )


def _has_continuous_minute_coverage(
    bars: list[KrxMinuteBarFrame], evaluation_start: datetime,
) -> bool:
    expected_end = evaluation_start
    for bar in reversed(bars):
        bar_start = _aware_datetime(bar.bar_start)
        bar_end = _aware_datetime(bar.bar_end)
        if bar_end != expected_end or bar_end - bar_start != timedelta(minutes=1):
            return False
        expected_end = bar_start
    return True


def _same_research_session(
    left: KrxMinuteBarFrame, right: KrxMinuteBarFrame,
) -> bool:
    """Legacy frames remain date-scoped; profiled frames reset at each window."""
    if not left.research_session or not right.research_session:
        return True
    return left.research_session == right.research_session


def _missing(
    factor_id: str,
    parameters_hash: str,
    entity: str,
    as_of: datetime,
    available_at: datetime,
    reason: str,
    input_refs: tuple[str, ...],
) -> FactorValue:
    return FactorValue(
        factor_id=factor_id,
        version=FACTOR_VERSION,
        parameters_hash=parameters_hash,
        entity=normalize_stock_code(entity),
        as_of=as_of.isoformat(),
        available_at=available_at.isoformat(),
        value=None,
        status="missing",
        reason=reason,
        input_refs=input_refs,
    )


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("research timestamps must be timezone-aware")
    return parsed


def _stable_parameters_hash(parameters: Mapping[str, Any]) -> str:
    import hashlib
    import json

    encoded = json.dumps(
        dict(parameters), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
