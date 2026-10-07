"""기본분석·사용자 전략팩·수동 유형을 매매 회차별로 조정한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from kiwoom_monitor.application.generic_strategy_evaluator import evaluate_strategy_pack
from kiwoom_monitor.application.strategy_pack import (
    STRATEGY_RESULT_MODES,
    StrategyPackManifest,
    select_strategy_candidate,
)
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode
from kiwoom_monitor.application.trade_setup_classification import (
    TradeSetupClassification,
    normalize_trade_setup_type,
)


class DefaultStrategyPackLike(Protocol):
    def classify(
        self, episode: object, minute_rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...] = (),
    ) -> TradeSetupClassification: ...


DraftLoader = Callable[[str], ExtractedStrategyDraft | None]
StrategyEvaluator = Callable[
    [StrategyPackManifest, ExtractedStrategyDraft, TradeEpisode, tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]],
    TradeSetupClassification | None,
]


@dataclass(frozen=True)
class CycleTypeSelection:
    selected_types: tuple[str, ...]
    selected_type_label: str
    normalized_overrides: tuple[tuple[int, str], ...]
    legacy_override: tuple[int, str] | None = None

    def override_map(self) -> dict[int, str]:
        return dict(self.normalized_overrides)


def classify_with_strategy_packs(
    default_pack: DefaultStrategyPackLike,
    packs: tuple[StrategyPackManifest, ...],
    result_mode: str,
    draft_loader: DraftLoader,
    episode: TradeEpisode,
    minute_rows: tuple[tuple[object, ...], ...],
    daily_rows: tuple[tuple[object, ...], ...] = (),
    *,
    base: TradeSetupClassification | None = None,
    evaluator: StrategyEvaluator = evaluate_strategy_pack,
) -> tuple[TradeSetupClassification, tuple[str, ...]]:
    """활성·승인 전략팩만 평가하고 현재 표시 방식으로 최종 후보를 고른다."""
    base_result = base or default_pack.classify(episode, minute_rows, daily_rows)
    candidates: list[tuple[str, TradeSetupClassification]] = [("기본분석", base_result)]
    for pack in packs:
        if pack.pack_id == "mimosa_v1" or not pack.enabled or pack.review_status != "approved":
            continue
        draft = draft_loader(pack.pack_id)
        if draft is None:
            continue
        result = evaluator(pack, draft, episode, minute_rows, daily_rows)
        if result is not None:
            candidates.append((pack.name, result))
    selected_name, selected = select_strategy_candidate(tuple(candidates), result_mode)
    comparison = tuple(
        f"{name} {result.setup_type} {result.confidence}%"
        + ("(최종)" if name == selected_name else "")
        for name, result in candidates
    )
    return selected, (f"표시방식 {result_mode_label(result_mode)}", *comparison)


def result_mode_label(result_mode: str) -> str:
    return STRATEGY_RESULT_MODES.get(result_mode, STRATEGY_RESULT_MODES["together"])


def normalize_setup_type_for_packs(value: str, packs: tuple[StrategyPackManifest, ...]) -> str:
    custom = {
        item for pack in packs
        if pack.enabled and pack.review_status == "approved"
        for item in pack.setup_types
    }
    return value if value in custom else normalize_trade_setup_type(value)


def resolve_cycle_type_selection(
    cycle_setups: tuple[TradeSetupClassification, ...],
    overrides: dict[int, str],
    legacy_manual_type: str,
    packs: tuple[StrategyPackManifest, ...],
) -> CycleTypeSelection:
    """과거 단일 수동 유형을 회차별 설정으로 옮기고 최종 표시 유형을 계산한다."""
    normalized = {
        index: normalize_setup_type_for_packs(value, packs)
        for index, value in overrides.items()
    }
    legacy_override = None
    if legacy_manual_type and len(cycle_setups) == 1 and 0 not in normalized:
        value = normalize_setup_type_for_packs(legacy_manual_type, packs)
        normalized[0] = value
        legacy_override = (0, value)
    selected_types = tuple(
        normalized.get(index, normalize_setup_type_for_packs(value.setup_type, packs))
        for index, value in enumerate(cycle_setups)
    )
    label = " · ".join(
        f"{index + 1}차 {value}" if len(selected_types) > 1 else value
        for index, value in enumerate(selected_types)
    )
    return CycleTypeSelection(
        selected_types,
        label,
        tuple(sorted(normalized.items())),
        legacy_override,
    )


def strategy_pack_for_result(
    result: TradeSetupClassification,
    packs: tuple[StrategyPackManifest, ...],
) -> StrategyPackManifest | None:
    return next((
        pack for pack in packs
        if pack.pack_id != "mimosa_v1" and pack.enabled and result.subtype.startswith(pack.name)
    ), None)
