"""매매 자동분석 전에 필요한 저장소 조회·저장을 한 경계에서 조정한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Protocol

from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode, split_trade_episode_cycles
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.trade_snapshot_context import TimedSnapshotLike
from kiwoom_monitor.application.trade_strategy_coordinator import (
    CycleTypeSelection,
    classify_with_strategy_packs,
    resolve_cycle_type_selection,
    strategy_pack_for_result,
)


class TradeAnalysisRepository(Protocol):
    def import_monitor_daily_bars(
        self, monitor_path: Path, code: str, base_day: date, limit: int = 250,
    ) -> int: ...

    def load_daily_bars(
        self, code: str, base_day: date, limit: int = 250,
    ) -> tuple[tuple[object, ...], ...]: ...

    def load_strategy_pack_draft(self, pack_id: str) -> ExtractedStrategyDraft | None: ...

    def load_trade_setup(self, group_id: str) -> tuple[TradeSetupClassification, str] | None: ...

    def save_trade_setup(
        self, group_id: str, classification: TradeSetupClassification, manual_type: str = "",
        now: datetime | None = None,
    ) -> None: ...

    def load_trade_setup_cycle_overrides(self, group_id: str) -> dict[int, str]: ...

    def save_trade_setup_cycle_override(
        self, group_id: str, cycle_index: int, manual_type: str, now: datetime | None = None,
    ) -> None: ...

    def save_trade_analysis_setup(
        self,
        group_id: str,
        classification: TradeSetupClassification,
        legacy_override: tuple[int, str] | None = None,
        now: datetime | None = None,
    ) -> None: ...


class AnalysisStrategyPack(Protocol):
    def classify(
        self, episode: object, minute_rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...] = (),
    ) -> TradeSetupClassification: ...

    def classify_cycles(
        self, episode: object, minute_rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...] = (),
    ) -> tuple[TradeSetupClassification, ...]: ...


SnapshotLoader = Callable[[str, datetime, datetime], tuple[TimedSnapshotLike, ...]]


class LinkedNewsAssessment(Protocol):
    outlook: str


class LinkedNewsLike(Protocol):
    published_at: datetime | None
    assessment: LinkedNewsAssessment
    title: str


LinkedNewsLoader = Callable[[str, str], tuple[LinkedNewsLike, ...]]


@dataclass(frozen=True)
class PreparedTradeAnalysis:
    overall_setup: TradeSetupClassification
    pack_results: tuple[str, ...]
    cycles: tuple[TradeEpisode, ...]
    cycle_setups: tuple[TradeSetupClassification, ...]
    type_selection: CycleTypeSelection
    entry_snapshots: tuple[TimedSnapshotLike, ...]
    linked_news: tuple[LinkedNewsLike, ...]
    draft_rule_counts: dict[str, int]


class TradeAnalysisPreparationService:
    """일봉 준비, 판정 저장, 과거 수동 유형 이전을 원자적인 흐름으로 묶는다."""

    def __init__(
        self,
        repository: TradeAnalysisRepository,
        monitor_db: Path,
        snapshot_loader: SnapshotLoader,
        linked_news_loader: LinkedNewsLoader,
    ) -> None:
        self._repository = repository
        self._monitor_db = monitor_db
        self._snapshot_loader = snapshot_loader
        self._linked_news_loader = linked_news_loader

    def prepare(
        self,
        episode: TradeEpisode,
        minute_rows: tuple[tuple[object, ...], ...],
        *,
        active_pack: AnalysisStrategyPack,
        strategy_packs: tuple[StrategyPackManifest, ...],
        result_mode: str,
    ) -> PreparedTradeAnalysis:
        code = episode.summary.stock_code
        base_day = episode.ended_at.date()
        try:
            self._repository.import_monitor_daily_bars(self._monitor_db, code, base_day, 250)
        except (OSError, sqlite3.Error):
            pass
        daily_rows = self._repository.load_daily_bars(code, base_day)

        overall_setup, pack_results = classify_with_strategy_packs(
            active_pack, strategy_packs, result_mode,
            self._repository.load_strategy_pack_draft, episode, minute_rows, daily_rows,
        )
        stored_setup = self._repository.load_trade_setup(episode.group_id)
        legacy_manual_type = stored_setup[1] if stored_setup is not None else ""

        base_cycle_setups = active_pack.classify_cycles(episode, minute_rows, daily_rows)
        cycles = split_trade_episode_cycles(episode)
        cycle_setups = tuple(
            classify_with_strategy_packs(
                active_pack, strategy_packs, result_mode,
                self._repository.load_strategy_pack_draft, cycle, minute_rows, daily_rows,
                base=base_cycle_setups[index],
            )[0]
            for index, cycle in enumerate(cycles)
        )
        selection = resolve_cycle_type_selection(
            cycle_setups,
            self._repository.load_trade_setup_cycle_overrides(episode.group_id),
            legacy_manual_type,
            strategy_packs,
        )
        # 분류 계산이 모두 끝난 뒤 자동 판정과 과거 수동 유형 이전을
        # 한 트랜잭션으로 확정한다. 중간 실패가 사용자 입력을 지우면 안 된다.
        self._repository.save_trade_analysis_setup(
            episode.group_id, overall_setup, selection.legacy_override,
        )

        entry_snapshots = self._snapshot_loader(code, episode.started_at, episode.ended_at)
        linked_news = self._linked_news_loader(episode.group_id, code)
        draft_rule_counts: dict[str, int] = {}
        for cycle_setup in cycle_setups:
            selected_pack = strategy_pack_for_result(cycle_setup, strategy_packs)
            if selected_pack is None or selected_pack.pack_id in draft_rule_counts:
                continue
            draft = self._repository.load_strategy_pack_draft(selected_pack.pack_id)
            draft_rule_counts[selected_pack.pack_id] = len(draft.rules) if draft else 0

        return PreparedTradeAnalysis(
            overall_setup,
            pack_results,
            cycles,
            cycle_setups,
            selection,
            entry_snapshots,
            linked_news,
            draft_rule_counts,
        )
