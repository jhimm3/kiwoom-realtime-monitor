"""매매 자동분석 결과를 Qt 위젯에 넣기 전 불변 화면 모델로 변환한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol

from kiwoom_monitor.application.personal_trade_rules import StructuredTradeRule
from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
from kiwoom_monitor.application.strategy_review_context import strategy_review_reference
from kiwoom_monitor.application.trade_review_analysis import TradeReviewAnalysis
from kiwoom_monitor.application.trade_setup_classification import (
    TRADE_SETUP_TYPES,
    TradeSetupClassification,
)
from kiwoom_monitor.application.trade_snapshot_context import (
    TimedSnapshotLike,
    entry_snapshot_for_cycle,
)
from kiwoom_monitor.application.trade_strategy_coordinator import (
    CycleTypeSelection,
    strategy_pack_for_result,
)
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_cost_service import (
    DailyTradeCost,
    allocate_episode_cost,
    estimate_episode_cost,
)
from kiwoom_monitor.presentation.trade_review_formatting import (
    LinkedNewsDisplayLike,
    format_analysis_data_status,
    format_cycle_analysis_block,
    format_daily_analysis_summary,
    format_entry_snapshot_blocks,
    format_linked_news_blocks,
)


class DefaultStrategyPackDisplayLike(Protocol):
    manifest: StrategyPackManifest

    def source_text(self, setup_type: str) -> str: ...


@dataclass(frozen=True)
class CycleSetupRow:
    cycle_number: int
    automatic_type: str
    subtype: str
    confidence_text: str
    override_value: str


@dataclass(frozen=True)
class TradeAnalysisViewModel:
    setup_summary_text: str
    selected_types: tuple[str, ...]
    available_types: tuple[str, ...]
    cycle_rows: tuple[CycleSetupRow, ...]
    data_status_text: str
    analysis_text: str


@dataclass(frozen=True)
class TradeHistoryEpisodeDisplay:
    setup: tuple[TradeSetupClassification, str] | None
    cycle_overrides: Mapping[int, str]
    review_status: str
    bar_state: str


@dataclass(frozen=True)
class TradeHistorySummaryRow:
    episode: TradeEpisode
    values: tuple[str, ...]
    profit_color: str


@dataclass(frozen=True)
class TradeHistorySummaryViewModel:
    period_summary_text: str
    rows: tuple[TradeHistorySummaryRow, ...]


def build_trade_history_summary_view_model(
    *,
    episodes: tuple[TradeEpisode, ...],
    visible_fills: tuple[TradeFill, ...],
    visible_costs: tuple[DailyTradeCost, ...],
    selected_start: date,
    selected_end: date,
    estimated_buy_cost_rate: float,
    estimated_sell_cost_rate: float,
    episode_displays: Mapping[str, TradeHistoryEpisodeDisplay],
) -> TradeHistorySummaryViewModel:
    """저장소나 Qt 객체 없이 기간 요약과 매매 묶음 표의 표시값을 만든다."""
    costs = tuple(allocate_episode_cost(episode, visible_fills, visible_costs) for episode in episodes)
    estimated_costs = tuple(
        estimate_episode_cost(episode, estimated_buy_cost_rate, estimated_sell_cost_rate)
        for episode in episodes
    )
    total_profit = sum(episode.summary.realized_profit for episode in episodes)
    projected_net = sum(
        cost.net_realized_profit
        if cost.net_realized_profit is not None
        else episode.summary.realized_profit - estimated_cost
        for episode, cost, estimated_cost in zip(episodes, costs, estimated_costs)
    )
    actual_count = sum(1 for cost in costs if cost.net_realized_profit is not None)
    completed = tuple(episode.summary for episode in episodes if episode.summary.matched_cost)
    wins = sum(1 for summary in completed if summary.realized_profit > 0)
    win_rate = wins / len(completed) * 100 if completed else 0.0
    trading_days = len({
        fill.filled_at.date()
        for fill in visible_fills
        if selected_start <= fill.filled_at.date() <= selected_end
    })
    period_summary_text = (
        f"거래일 {trading_days}일  ·  매매 묶음 {len(episodes)}건  ·  "
        f"승률 {win_rate:.1f}%  ·  추정 실현손익 예정 {total_profit:+,}원  ·  "
        f"비용 반영 예정 {projected_net:+,}원(실제 비용 {actual_count}/{len(episodes)}건)"
    )

    rows: list[TradeHistorySummaryRow] = []
    for episode, cost, estimated_cost in zip(episodes, costs, estimated_costs):
        summary = episode.summary
        display = episode_displays[episode.group_id]
        setup_display = _trade_setup_display(display.setup, display.cycle_overrides)
        cost_display = (
            f"{cost.total_cost:,}원" + (" · 배분" if cost.allocated else "")
            if cost.complete
            else (
                f"예정 {estimated_cost:,}원 · 매수 {estimated_buy_cost_rate:g}% / "
                f"매도 {estimated_sell_cost_rate:g}%"
                if estimated_cost else "정산 대기"
            )
        )
        net_display = (
            f"{cost.net_realized_profit:+,}원"
            if cost.net_realized_profit is not None
            else (f"예정 {summary.realized_profit - estimated_cost:+,}원" if estimated_cost else "—")
        )
        period = episode.started_at.strftime("%Y-%m-%d %H:%M")
        if episode.ended_at != episode.started_at:
            period += " ~ " + episode.ended_at.strftime("%m-%d %H:%M")
        values = (
            period,
            summary.stock_name or summary.stock_code,
            f"{summary.buy_quantity:,}주",
            f"{summary.sell_quantity:,}주",
            f"{summary.buy_amount:,}원",
            f"{summary.sell_amount:,}원",
            f"{summary.realized_profit:+,}원",
            f"{summary.return_rate:+.2f}%",
            cost_display,
            net_display,
            ("수동 · " if episode.source == "manual" else "") + summary.state,
            f"{summary.fill_count}건",
            display.bar_state,
            display.review_status,
            setup_display,
        )
        profit_color = "#D32F2F" if summary.realized_profit > 0 else ("#1976D2" if summary.realized_profit < 0 else "#555555")
        rows.append(TradeHistorySummaryRow(episode, values, profit_color))
    return TradeHistorySummaryViewModel(period_summary_text, tuple(rows))


def _trade_setup_display(
    saved_setup: tuple[TradeSetupClassification, str] | None,
    cycle_overrides: Mapping[int, str],
) -> str:
    if saved_setup is None:
        return "분석 전"
    setup, manual_type = saved_setup
    if not cycle_overrides:
        return manual_type or setup.setup_type
    automatic_types = tuple(re.sub(r"^\d+차\s+", "", value) for value in setup.setup_type.split(" · "))
    selected = tuple(cycle_overrides.get(index, value) for index, value in enumerate(automatic_types))
    return " · ".join(
        f"{index + 1}차 {value}" if len(selected) > 1 else value
        for index, value in enumerate(selected)
    )


def build_trade_analysis_view_model(
    *,
    overall_setup: TradeSetupClassification,
    pack_results: tuple[str, ...],
    type_selection: CycleTypeSelection,
    cycles: tuple[TradeEpisode, ...],
    cycle_setups: tuple[TradeSetupClassification, ...],
    analyses: tuple[TradeReviewAnalysis, ...],
    entry_snapshots: tuple[TimedSnapshotLike, ...],
    linked_news: tuple[LinkedNewsDisplayLike, ...],
    active_pack: DefaultStrategyPackDisplayLike,
    packs: tuple[StrategyPackManifest, ...],
    structured_rules: tuple[StructuredTradeRule, ...],
    personal_rule_count: int,
    draft_rule_counts: dict[str, int],
    total_return_rate: float,
) -> TradeAnalysisViewModel:
    """저장소나 Qt 객체 없이 자동분석 화면 전체에 필요한 값을 만든다."""
    overrides = type_selection.override_map()
    selected_types = type_selection.selected_types
    available_types = tuple(dict.fromkeys((
        *TRADE_SETUP_TYPES,
        *(item for pack in packs if pack.enabled for item in pack.setup_types),
    )))
    cycle_rows = tuple(
        CycleSetupRow(
            index + 1,
            setup.setup_type,
            setup.subtype or "판정 보류",
            f"{setup.confidence}%",
            overrides.get(index, "자동 판정 사용"),
        )
        for index, setup in enumerate(cycle_setups)
    )
    setup_summary = (
        f"예상 매매유형: {type_selection.selected_type_label}\n"
        f"세부 유형: {overall_setup.subtype or '세부 유형 판정 보류'}\n"
        + " / ".join(overall_setup.evidence)
        + ("\n기본·전략팩 결과: " + " · ".join(pack_results) if pack_results else "")
    )
    lesson_counts: dict[int, int] = {}
    for rule in structured_rules:
        lesson_counts[rule.lesson] = lesson_counts.get(rule.lesson, 0) + 1
    active_pack_names = (active_pack.manifest.name, *(
        pack.name for pack in packs
        if pack.pack_id != "mimosa_v1" and pack.enabled and pack.review_status == "approved"
    ))
    data_status = format_analysis_data_status(
        active_pack_names=active_pack_names,
        lesson_counts=lesson_counts,
        personal_rule_count=personal_rule_count,
        analyses=analyses,
        snapshot_count=len(entry_snapshots),
    )
    blocks = [format_daily_analysis_summary(analyses, total_return_rate)]
    blocks.extend(format_linked_news_blocks(linked_news))
    for index, analysis in enumerate(analyses):
        selected_pack = strategy_pack_for_result(cycle_setups[index], packs)
        reference = strategy_review_reference(
            selected_types[index],
            default_pack=active_pack,
            structured_rules=structured_rules,
            selected_pack=selected_pack,
            selected_pack_rule_count=(draft_rule_counts.get(selected_pack.pack_id, 0) if selected_pack else 0),
        )
        blocks.append(format_cycle_analysis_block(
            cycle_number=index + 1,
            setup_type=selected_types[index],
            pack_name=reference.pack_name,
            source_text=reference.source_text,
            relevant_rule_count=reference.relevant_rule_count,
            analysis=analysis,
        ))
        entry = entry_snapshot_for_cycle(cycles[index], entry_snapshots)
        if entry is not None:
            blocks.extend(format_entry_snapshot_blocks(entry))
    return TradeAnalysisViewModel(
        setup_summary,
        selected_types,
        available_types,
        cycle_rows,
        data_status,
        "\n".join(blocks),
    )
