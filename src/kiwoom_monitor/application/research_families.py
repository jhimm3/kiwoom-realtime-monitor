"""Registered strategy evaluator bindings; data consumers use domain contracts."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping
from kiwoom_monitor.domain.strategy_contract import (
    BREAKOUT_FAMILY_ID, PULLBACK_FAMILY_ID, LEADER_BREAKOUT_FAMILY_ID,
    BREAKOUT_STRATEGY_ID, BREAKOUT_STRATEGY_VERSION, PULLBACK_CONTRACT,
    BreakoutStrategyConfig, PullbackReaccelerationConfig, LeaderBreakoutConfig,
    RESEARCH_FAMILY_LABELS, RESEARCH_DIRECTION_NOTICE, FAMILY_CONTRACTS,
    parse_strategy_config, shadow_monitor_id_for_config,
)
from kiwoom_monitor.application.breakout_strategy import StrategyEvaluation, evaluate_breakout_bar
from kiwoom_monitor.application.pullback_reacceleration_strategy import evaluate_pullback_reacceleration_bar
from kiwoom_monitor.application.leader_breakout import evaluate_leader_breakout_bar

@dataclass(frozen=True)
class ResearchFamilyDefinition:
    family_id: str
    config_type: type
    evaluate_bar: Callable[..., StrategyEvaluation]
    factor_ids: tuple[str, ...]
    searchable_parameters: frozenset[str]
    contract: Mapping[str, Any]

_FAMILY_EVALUATORS = {
    LEADER_BREAKOUT_FAMILY_ID: evaluate_leader_breakout_bar,
    BREAKOUT_FAMILY_ID: evaluate_breakout_bar,
    PULLBACK_FAMILY_ID: evaluate_pullback_reacceleration_bar,
}
FAMILY_REGISTRY: Mapping[str, ResearchFamilyDefinition] = MappingProxyType({
    key: ResearchFamilyDefinition(
        family_id=value.family_id, config_type=value.config_type,
        evaluate_bar=_FAMILY_EVALUATORS[key], factor_ids=value.factor_ids,
        searchable_parameters=value.searchable_parameters, contract=value.contract,
    ) for key, value in FAMILY_CONTRACTS.items()
})

def get_research_family(family_id: str) -> ResearchFamilyDefinition:
    try:
        return FAMILY_REGISTRY[str(family_id)]
    except KeyError as exc:
        raise ValueError(f"unregistered research family: {family_id}") from exc


def family_for_config(config: object) -> ResearchFamilyDefinition:
    matches = [
        definition for definition in FAMILY_REGISTRY.values()
        if isinstance(config, definition.config_type)
    ]
    if len(matches) != 1:
        raise ValueError(f"unregistered research strategy config: {type(config).__name__}")
    return matches[0]
