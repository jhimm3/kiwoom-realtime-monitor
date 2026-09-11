"""매매 회차별 자동분석 입력을 구성하고 분석기를 실행한다."""

from __future__ import annotations

from typing import Callable, Protocol, TypeVar

from kiwoom_monitor.application.trade_journal_summary import TradeEpisode
from kiwoom_monitor.application.trade_review_analysis import TradeReviewAnalysis, analyze_trade_episode
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.trade_snapshot_context import TimedSnapshotLike, resolve_unverifiable_items


class TradeAnalyzer(Protocol):
    def __call__(
        self,
        episode: TradeEpisode,
        rows: tuple[tuple[object, ...], ...],
        *,
        personal_rules: tuple[str, ...],
        trade_value_threshold_eok: float,
        setup_type: str,
        setup_confidence: int,
        setup_evidence: tuple[str, ...],
        lesson_warnings: tuple[str, ...],
        unverifiable_items: tuple[str, ...],
    ) -> TradeReviewAnalysis: ...


SnapshotT = TypeVar("SnapshotT", bound=TimedSnapshotLike)
UnverifiableLoader = Callable[[str], tuple[str, ...]]


def analyze_trade_cycles(
    cycles: tuple[TradeEpisode, ...],
    rows: tuple[tuple[object, ...], ...],
    *,
    personal_rules: tuple[str, ...],
    trade_value_threshold_eok: float,
    selected_types: tuple[str, ...],
    cycle_setups: tuple[TradeSetupClassification, ...],
    overrides: dict[int, str],
    entry_snapshots: tuple[SnapshotT, ...],
    manual_unverifiable_for: UnverifiableLoader,
    analyzer: TradeAnalyzer = analyze_trade_episode,
) -> tuple[TradeReviewAnalysis, ...]:
    """수동 확정과 자동 판정의 근거 계약을 유지해 각 회차를 분석한다."""
    results: list[TradeReviewAnalysis] = []
    for index, cycle in enumerate(cycles):
        setup = cycle_setups[index]
        setup_type = selected_types[index]
        manually_confirmed = index in overrides
        raw_unverifiable = (
            manual_unverifiable_for(setup_type)
            if manually_confirmed else setup.unverifiable
        )
        results.append(analyzer(
            cycle,
            rows,
            personal_rules=personal_rules,
            trade_value_threshold_eok=trade_value_threshold_eok,
            setup_type=setup_type,
            setup_confidence=90 if manually_confirmed else setup.confidence,
            setup_evidence=(
                (f"사용자가 {setup_type} 유형으로 직접 확정했습니다.",)
                if manually_confirmed else setup.evidence
            ),
            lesson_warnings=() if manually_confirmed else setup.warnings,
            unverifiable_items=resolve_unverifiable_items(
                cycle, entry_snapshots, raw_unverifiable,
            ),
        ))
    return tuple(results)
