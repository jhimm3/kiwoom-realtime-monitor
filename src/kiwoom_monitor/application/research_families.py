"""연구에서 실행할 수 있는 두 전략 Family의 명시적 등록표."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    STRATEGY_ID as BREAKOUT_STRATEGY_ID,
    STRATEGY_VERSION as BREAKOUT_STRATEGY_VERSION,
    BreakoutStrategyConfig,
    evaluate_breakout_bar,
)
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_CONTRACT as PULLBACK_CONTRACT,
    FAMILY_ID as PULLBACK_FAMILY_ID,
    PullbackReaccelerationConfig,
    evaluate_pullback_reacceleration_bar,
)


BREAKOUT_FAMILY_ID = f"{BREAKOUT_STRATEGY_ID}/{BREAKOUT_STRATEGY_VERSION}"


@dataclass(frozen=True)
class ResearchFamilyDefinition:
    family_id: str
    config_type: type
    evaluate_bar: Callable[..., Any]
    factor_ids: tuple[str, ...]
    searchable_parameters: frozenset[str]
    contract: Mapping[str, Any]


FAMILY_REGISTRY: Mapping[str, ResearchFamilyDefinition] = MappingProxyType({
    BREAKOUT_FAMILY_ID: ResearchFamilyDefinition(
        family_id=BREAKOUT_FAMILY_ID,
        config_type=BreakoutStrategyConfig,
        evaluate_bar=evaluate_breakout_bar,
        factor_ids=("rolling_high_breakout/v1", "rank_persistence/v1"),
        searchable_parameters=frozenset({
            "lookback_bars", "buffer_bps", "rank_top_k", "rank_window_seconds",
            "rank_max_gap_seconds", "rank_min_residency_seconds", "stop_loss_bps",
            "target_bps", "max_hold_minutes", "signal_valid_seconds", "cooldown_seconds",
        }),
        contract={
            "required_inputs": (
                "minute_bar/KRX/closed/complete", "top20_membership/as_available",
            ),
            "state_contract": "flat/candidate/open/cooldown",
            "entry_contract": "rolling_high_breakout_then_optional_rank_filter",
            "exit_contract": "stop_loss_or_target_or_max_hold",
            "size_contract": "fixed_quantity_with_capital_limit",
            "execution_environments": ("replay", "simulation"),
        },
    ),
    PULLBACK_FAMILY_ID: ResearchFamilyDefinition(
        family_id=PULLBACK_FAMILY_ID,
        config_type=PullbackReaccelerationConfig,
        evaluate_bar=evaluate_pullback_reacceleration_bar,
        factor_ids=("pullback_reacceleration/v1", "rank_persistence/v1"),
        searchable_parameters=frozenset({
            "lookback_bars", "minimum_pullback_bps", "minimum_reacceleration_bps",
            "rank_top_k", "rank_window_seconds", "rank_max_gap_seconds",
            "rank_min_residency_seconds", "stop_loss_bps", "target_bps",
            "max_hold_minutes", "signal_valid_seconds", "cooldown_seconds",
        }),
        contract=PULLBACK_CONTRACT,
    ),
})


def get_research_family(family_id: str) -> ResearchFamilyDefinition:
    try:
        return FAMILY_REGISTRY[str(family_id)]
    except KeyError as exc:
        raise ValueError(f"unregistered research family: {family_id}") from exc


def parse_strategy_config(family_id: str, value: Mapping[str, Any]) -> Any:
    definition = get_research_family(family_id)
    return definition.config_type(**dict(value))


def family_for_config(config: object) -> ResearchFamilyDefinition:
    matches = [
        definition for definition in FAMILY_REGISTRY.values()
        if isinstance(config, definition.config_type)
    ]
    if len(matches) != 1:
        raise ValueError(f"unregistered research strategy config: {type(config).__name__}")
    return matches[0]
