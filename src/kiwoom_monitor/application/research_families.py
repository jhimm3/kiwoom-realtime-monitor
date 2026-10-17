"""주도주 구조 돌파와 보존된 비교 전략 Family의 명시적 등록표."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    STRATEGY_ID as BREAKOUT_STRATEGY_ID,
    STRATEGY_VERSION as BREAKOUT_STRATEGY_VERSION,
    BreakoutStrategyConfig,
    StrategyEvaluation,
    evaluate_breakout_bar,
)
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_CONTRACT as PULLBACK_CONTRACT,
    FAMILY_ID as PULLBACK_FAMILY_ID,
    PullbackReaccelerationConfig,
    evaluate_pullback_reacceleration_bar,
)
from kiwoom_monitor.application.leader_breakout import (
    POLICY_ID as LEADER_BREAKOUT_FAMILY_ID, LeaderBreakoutConfig, evaluate_leader_breakout_bar,
)


BREAKOUT_FAMILY_ID = f"{BREAKOUT_STRATEGY_ID}/{BREAKOUT_STRATEGY_VERSION}"

# Presentation metadata only. Keep the historical execution contracts and IDs intact.
RESEARCH_FAMILY_LABELS: Mapping[str, str] = MappingProxyType({
    LEADER_BREAKOUT_FAMILY_ID: "주도주 구조 돌파 · 실시간 0B / 연구 분봉",
    BREAKOUT_FAMILY_ID: "완료봉 고가 돌파 v1 · 기존 비교 실험",
    PULLBACK_FAMILY_ID: "눌림 재가속 v1 · 기존 비교 실험",
})
RESEARCH_DIRECTION_NOTICE = (
    "연구 방향: 사용자 주도주 돌파 구상(00 AI 인텔리전스, 미완성 초안). "
    "기존 완료봉 돌파·눌림 실험은 이 전략의 구현이나 검증 결과가 아닙니다. "
    "주도주 구조 돌파는 실시간 0B·분봉 연구 경로를 사용하며, 미정 필수 조건은 진입을 차단합니다."
)


@dataclass(frozen=True)
class ResearchFamilyDefinition:
    family_id: str
    config_type: type
    evaluate_bar: Callable[..., StrategyEvaluation]
    factor_ids: tuple[str, ...]
    searchable_parameters: frozenset[str]
    contract: Mapping[str, Any]


FAMILY_REGISTRY: Mapping[str, ResearchFamilyDefinition] = MappingProxyType({
    LEADER_BREAKOUT_FAMILY_ID: ResearchFamilyDefinition(
        family_id=LEADER_BREAKOUT_FAMILY_ID, config_type=LeaderBreakoutConfig,
        evaluate_bar=evaluate_leader_breakout_bar, factor_ids=("leader_structure/v1",),
        searchable_parameters=frozenset({"minimum_confirmation_observations", "persistence_seconds",
            "breakout_buffer_bps", "max_data_age_seconds", "signal_valid_seconds", "minimum_turnover_won",
            "research_turnover_lookback_bars"}),
        contract={"required_inputs": ("minute_bar/KRX/final_completed", "explicit_leader_structure_evidence"),
            "live_inputs": ("kiwoom-websocket-0B/KRX", "explicit_current_leader_structure_evidence"),
            "state_contract": "observation_separate_from_fill_owned_position",
            "entry_contract": "explicit_leader_structure_cross",
            "exit_contract": "pinned_absolute_hard_stop_or_structure_invalidation",
            "size_contract": "explicit_execution_contract_only",
            "execution_environments": ("replay", "simulation", "mock_0B")},
    ),
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
    if definition.config_type is LeaderBreakoutConfig:
        return LeaderBreakoutConfig.from_dict(value)
    return definition.config_type(**dict(value))


def family_for_config(config: object) -> ResearchFamilyDefinition:
    matches = [
        definition for definition in FAMILY_REGISTRY.values()
        if isinstance(config, definition.config_type)
    ]
    if len(matches) != 1:
        raise ValueError(f"unregistered research strategy config: {type(config).__name__}")
    return matches[0]


def shadow_monitor_id_for_config(
    family_id: str, config: object, session_profile: str,
) -> str:
    """Return the stable NAS shadow owner for one registered family configuration."""
    definition = get_research_family(family_id)
    if not isinstance(config, definition.config_type) or not hasattr(config, "to_dict"):
        raise ValueError("shadow monitor config does not match its registered family")
    config_document = config.to_dict()
    identity_document = (
        config_document
        if session_profile == "krx-regular/v1"
        else {"strategy": config_document, "session_profile": session_profile}
    )
    encoded = json.dumps(
        identity_document, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    strategy_id, strategy_version = family_id.rsplit("/", 1)
    return (
        f"shadow:{strategy_id}:{strategy_version}:"
        f"{hashlib.sha256(encoded).hexdigest()[:16]}"
    )
