"""KRX 완료봉 눌림 후 재가속 Family의 등록 계약."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyEvaluation,
    StrategyState,
    evaluate_factor_entry_bar,
)
from kiwoom_monitor.application.research_factors import (
    FACTOR_VERSION,
    PULLBACK_REACCELERATION_ID,
    RANK_PERSISTENCE_ID,
    PullbackReaccelerationParameters,
    RankPersistenceParameters,
    compute_pullback_reacceleration,
    compute_rank_persistence,
    get_factor_definition,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)
from kiwoom_monitor.domain.ranking import normalize_stock_code


STRATEGY_ID = "krx_pullback_reacceleration"
STRATEGY_VERSION = "v1"
FAMILY_ID = f"{STRATEGY_ID}/{STRATEGY_VERSION}"
FAMILY_CONTRACT: Mapping[str, Any] = {
    "required_inputs": ("minute_bar/KRX/closed/complete", "top20_membership/as_available"),
    "state_contract": "flat/candidate/open/cooldown",
    "entry_contract": "pullback_reacceleration_then_optional_rank_filter",
    "exit_contract": "stop_loss_or_target_or_max_hold",
    "size_contract": "fixed_quantity_with_capital_limit",
    "execution_environments": ("replay", "simulation"),
}


@dataclass(frozen=True)
class PullbackReaccelerationConfig:
    strategy_version: str
    pullback_factor_version: str
    rank_factor_version: str
    lookback_bars: int
    minimum_pullback_bps: int
    minimum_reacceleration_bps: int
    rank_persistence_enabled: bool
    rank_persistence_required: bool
    rank_top_k: int | None
    rank_window_seconds: int | None
    rank_max_gap_seconds: int | None
    rank_min_residency_seconds: int | None
    stop_loss_bps: int
    target_bps: int
    max_hold_minutes: int
    quantity: int
    capital_won: int
    signal_valid_seconds: int
    cooldown_seconds: int

    def __post_init__(self) -> None:
        if self.strategy_version != STRATEGY_VERSION:
            raise ValueError(f"unregistered strategy: {STRATEGY_ID}/{self.strategy_version}")
        get_factor_definition(PULLBACK_REACCELERATION_ID, self.pullback_factor_version)
        get_factor_definition(RANK_PERSISTENCE_ID, self.rank_factor_version)
        PullbackReaccelerationParameters(
            self.lookback_bars, self.minimum_pullback_bps,
            self.minimum_reacceleration_bps,
        )
        if self.rank_persistence_enabled:
            if None in (
                self.rank_top_k, self.rank_window_seconds, self.rank_max_gap_seconds,
                self.rank_min_residency_seconds,
            ):
                raise ValueError("enabled rank persistence requires all rank parameters")
            RankPersistenceParameters(
                int(self.rank_top_k), int(self.rank_window_seconds),
                int(self.rank_max_gap_seconds),
            )
            if int(self.rank_min_residency_seconds) < 0:
                raise ValueError("rank_min_residency_seconds must not be negative")
        elif self.rank_persistence_required:
            raise ValueError("disabled rank persistence cannot be required")
        if self.stop_loss_bps <= 0 or self.target_bps <= 0:
            raise ValueError("stop_loss_bps and target_bps must be positive")
        if self.max_hold_minutes <= 0 or self.quantity <= 0 or self.capital_won <= 0:
            raise ValueError("hold duration, quantity, and capital must be positive")
        if self.signal_valid_seconds <= 0 or self.cooldown_seconds < 0:
            raise ValueError("signal validity must be positive and cooldown must not be negative")

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        if not self.rank_persistence_enabled:
            document.update({
                "rank_persistence_required": False,
                "rank_top_k": None,
                "rank_window_seconds": None,
                "rank_max_gap_seconds": None,
                "rank_min_residency_seconds": None,
            })
        return document


def evaluate_pullback_reacceleration_bar(
    *,
    run_id: str,
    evaluation_bar: KrxMinuteBarFrame,
    bar_history: Iterable[KrxMinuteBarFrame],
    universe_frames: Iterable[CandidateUniverseFrame],
    config: PullbackReaccelerationConfig,
    state: StrategyState,
) -> StrategyEvaluation:
    universe_rows = tuple(universe_frames)
    factor = compute_pullback_reacceleration(
        bar_history, evaluation_bar,
        PullbackReaccelerationParameters(
            config.lookback_bars, config.minimum_pullback_bps,
            config.minimum_reacceleration_bps,
        ),
    )
    factors = [factor]
    if config.rank_persistence_enabled:
        assert config.rank_top_k is not None
        assert config.rank_window_seconds is not None
        assert config.rank_max_gap_seconds is not None
        decision_time = datetime.fromisoformat(evaluation_bar.available_at).astimezone(timezone.utc)
        factors.append(compute_rank_persistence(
            universe_rows, normalize_stock_code(evaluation_bar.code), decision_time,
            RankPersistenceParameters(
                config.rank_top_k, config.rank_window_seconds, config.rank_max_gap_seconds,
            ),
        ))
    return evaluate_factor_entry_bar(
        run_id=run_id, evaluation_bar=evaluation_bar, universe_frames=universe_rows,
        config=config, state=state, factors=tuple(factors), strategy_id=STRATEGY_ID,
        setup="pullback_reacceleration", signal_key="reaccelerated",
        negative_reason="pullback_reacceleration_not_confirmed",
        positive_reason="pullback_reacceleration_confirmed",
    )
