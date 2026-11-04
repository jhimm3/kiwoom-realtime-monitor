"""KRX 완료봉 눌림 후 재가속 Family의 등록 계약."""

from __future__ import annotations

from kiwoom_monitor.domain.strategy_contract import (
    PULLBACK_STRATEGY_ID as STRATEGY_ID, PULLBACK_STRATEGY_VERSION as STRATEGY_VERSION,
    PULLBACK_FAMILY_ID as FAMILY_ID, PULLBACK_CONTRACT as FAMILY_CONTRACT,
    PullbackReaccelerationConfig,
)

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyEvaluation,
    StrategyState,
    evaluate_factor_entry_bar,
    validate_session_exit_policy,
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
