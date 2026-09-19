"""매매 자동분석 전에 필요한 저장소 조회·저장을 한 경계에서 조정한다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Protocol

from kiwoom_monitor.application.journal_enrichment import (
    JournalResearchLink,
    journal_analysis_revision,
)
from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft
from kiwoom_monitor.application.trade_journal_summary import (
    TradeEpisode,
    split_trade_episode_cycles,
    trade_fill_key,
)
from kiwoom_monitor.application.trade_setup_classification import (
    TradeSetupClassification,
    trade_setup_revision_fill_context,
)
from kiwoom_monitor.application.trade_snapshot_context import TimedSnapshotLike
from kiwoom_monitor.domain.order_contract import AccountScope
from kiwoom_monitor.domain.order_contract import LEGACY_ACCOUNT_SCOPE
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

    def load_trade_setup(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> tuple[TradeSetupClassification, str] | None: ...

    def save_trade_setup(
        self, group_id: str, classification: TradeSetupClassification, manual_type: str = "",
        now: datetime | None = None,
    ) -> None: ...

    def load_trade_setup_cycle_overrides(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> dict[int, str]: ...

    def save_trade_setup_cycle_override(
        self, group_id: str, cycle_index: int, manual_type: str, now: datetime | None = None,
    ) -> None: ...

    def save_trade_analysis_setup(
        self,
        group_id: str,
        classification: TradeSetupClassification,
        legacy_override: tuple[int, str] | None = None,
        now: datetime | None = None,
        *, account_scope: AccountScope | None = None,
        canonical_scope: AccountScope | None = None,
    ) -> None: ...


class AnalysisStrategyPack(Protocol):
    manifest: StrategyPackManifest

    def classify(
        self, episode: object, minute_rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...] = (),
    ) -> TradeSetupClassification: ...

    def classify_cycles(
        self, episode: object, minute_rows: tuple[tuple[object, ...], ...],
        daily_rows: tuple[tuple[object, ...], ...] = (),
    ) -> tuple[TradeSetupClassification, ...]: ...


SnapshotLoader = Callable[..., tuple[TimedSnapshotLike, ...]]


class LinkedNewsAssessment(Protocol):
    outlook: str


class LinkedNewsLike(Protocol):
    published_at: datetime | None
    assessment: LinkedNewsAssessment
    title: str


LinkedNewsLoader = Callable[[str, str, AccountScope], tuple[LinkedNewsLike, ...]]


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
    research_links: tuple[JournalResearchLink, ...] = ()
    analysis_revision_id: str = ""
    daily_bar_count: int = 0


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
        account_scope = episode.summary.account_scope
        origin_scope = episode.fills[0].origin_scope
        canonical_scope = account_scope if account_scope != origin_scope else None
        stored_setup = (
            self._repository.load_trade_setup(episode.group_id)
            if account_scope == LEGACY_ACCOUNT_SCOPE
            else self._repository.load_trade_setup(episode.group_id, account_scope)
        )
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
        stored_cycle_overrides = (
            self._repository.load_trade_setup_cycle_overrides(episode.group_id)
            if account_scope == LEGACY_ACCOUNT_SCOPE
            else self._repository.load_trade_setup_cycle_overrides(episode.group_id, account_scope)
        )
        selection = resolve_cycle_type_selection(
            cycle_setups,
            stored_cycle_overrides,
            legacy_manual_type,
            strategy_packs,
        )
        # 분류 계산이 모두 끝난 뒤 자동 판정과 과거 수동 유형 이전을
        # 한 트랜잭션으로 확정한다. 중간 실패가 사용자 입력을 지우면 안 된다.
        if account_scope == LEGACY_ACCOUNT_SCOPE:
            self._repository.save_trade_analysis_setup(
                episode.group_id, overall_setup, selection.legacy_override,
            )
        else:
            self._repository.save_trade_analysis_setup(
                episode.group_id, overall_setup, selection.legacy_override,
                account_scope=origin_scope, canonical_scope=canonical_scope,
            )

        entry_snapshots = (
            self._snapshot_loader(code, episode.started_at, episode.ended_at)
            if account_scope == LEGACY_ACCOUNT_SCOPE
            else self._snapshot_loader(code, episode.started_at, episode.ended_at, account_scope)
        )
        linked_news = self._linked_news_loader(episode.group_id, code, account_scope)
        execution_refs = tuple(dict.fromkeys((
            *(trade_fill_key(fill) for fill in episode.fills),
            *(str(getattr(snapshot, "execution_key", "")) for snapshot in entry_snapshots),
        )))
        link_loader = getattr(self._repository, "load_journal_research_links", None)
        research_links = (
            tuple(
                link_loader(execution_refs)
                if account_scope == LEGACY_ACCOUNT_SCOPE
                else link_loader(execution_refs, account_scope)
            )
            if callable(link_loader) else ()
        )
        draft_rule_counts: dict[str, int] = {}
        for cycle_setup in cycle_setups:
            selected_pack = strategy_pack_for_result(cycle_setup, strategy_packs)
            if selected_pack is None or selected_pack.pack_id in draft_rule_counts:
                continue
            draft = self._repository.load_strategy_pack_draft(selected_pack.pack_id)
            draft_rule_counts[selected_pack.pack_id] = len(draft.rules) if draft else 0

        active_manifest = active_pack.manifest
        revision = journal_analysis_revision(
            episode.group_id,
            "trade_setup",
            {
                "fills": tuple(trade_setup_revision_fill_context(fill) for fill in episode.fills),
                "minute_rows": tuple(tuple(str(value) for value in row) for row in minute_rows),
                "daily_rows": tuple(tuple(str(value) for value in row) for row in daily_rows),
                "entry_snapshots": tuple(repr(value) for value in entry_snapshots),
                "linked_news": tuple(
                    (
                        str(getattr(value, "title", "")),
                        str(getattr(value, "published_at", "")),
                        str(getattr(getattr(value, "assessment", None), "outlook", "")),
                    )
                    for value in linked_news
                ),
                "strategy_packs": tuple(
                    (pack.pack_id, pack.version, pack.enabled, pack.review_status)
                    for pack in (active_manifest, *strategy_packs)
                ),
                "result_mode": result_mode,
            },
            "trade-analysis/v2",
            {
                "automatic_type": overall_setup.setup_type,
                "confidence": overall_setup.confidence,
                "evidence": overall_setup.evidence,
                "cycle_types": tuple(value.setup_type for value in cycle_setups),
                "selected_types": selection.selected_types,
            },
            account_scope=origin_scope, canonical_scope=canonical_scope,
        )
        revision_saver = getattr(self._repository, "save_journal_analysis_revision", None)
        if callable(revision_saver):
            revision = revision_saver(revision)

        return PreparedTradeAnalysis(
            overall_setup,
            pack_results,
            cycles,
            cycle_setups,
            selection,
            entry_snapshots,
            linked_news,
            draft_rule_counts,
            research_links,
            revision.revision_id,
            len(daily_rows),
        )
