from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock, patch

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
from kiwoom_monitor.application.strategy_pack import StrategyPackManifest, default_strategy_pack
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft
from kiwoom_monitor.application.strategy_review_context import strategy_review_reference
from kiwoom_monitor.application.journal_enrichment import journal_research_link
from kiwoom_monitor.application.trade_analysis_preparation_service import TradeAnalysisPreparationService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification


class FakeRepository:
    def __init__(self, *, import_error: Exception | None = None) -> None:
        self.import_error = import_error
        self.imported: list[tuple[Path, str, object, int, datetime | None]] = []
        self.saved_setups: list[tuple[str, TradeSetupClassification, str]] = []
        self.saved_setup_times: list[datetime | None] = []
        self.saved_overrides: list[tuple[str, int, str]] = []
        self.stored_setup: tuple[TradeSetupClassification, str] | None = None
        self.overrides: dict[int, str] = {}
        self.daily_rows = (("2026-09-09T00:00", 900, 1100, 800, 1000, 100, 1.0, "daily_confirmed"),)
        self.analysis_revisions = []
        self.research_links = ()

    def import_monitor_daily_bars(
        self, path: Path, code: str, day: object, limit: int = 250, *, observed_at: datetime | None = None,
    ) -> int:
        self.imported.append((path, code, day, limit, observed_at))
        if self.import_error is not None:
            raise self.import_error
        return 1

    def load_daily_bars(self, code: str, day: object, limit: int = 250):
        return self.daily_rows

    def load_strategy_pack_draft(self, pack_id: str):
        return None

    def load_trade_setup(self, group_id: str):
        return self.stored_setup

    def save_trade_setup(self, group_id: str, setup: TradeSetupClassification, manual_type: str = "", now=None) -> None:
        self.saved_setups.append((group_id, setup, manual_type))

    def load_trade_setup_cycle_overrides(self, group_id: str) -> dict[int, str]:
        return dict(self.overrides)

    def save_trade_setup_cycle_override(self, group_id: str, cycle_index: int, manual_type: str, now=None) -> None:
        self.saved_overrides.append((group_id, cycle_index, manual_type))

    def save_trade_analysis_setup(self, group_id: str, setup: TradeSetupClassification,
                                  legacy_override=None, now=None) -> None:
        self.saved_setups.append((group_id, setup, ""))
        self.saved_setup_times.append(now)
        if legacy_override is not None:
            self.saved_overrides.append((group_id, *legacy_override))

    def save_journal_analysis_revision(self, revision):
        self.analysis_revisions.append(revision)
        return revision

    def load_journal_research_links(self, execution_refs):
        self.execution_refs = execution_refs
        return self.research_links


def episode():
    at = datetime(2026, 9, 9, 9, 10)
    return group_trade_episodes((
        TradeFill("1", "000001", "테스트", "매수", at, 1, 1000),
        TradeFill("2", "000001", "테스트", "매도", at.replace(minute=15), 1, 1010),
    ))[0]


def draft_episode(cycle_count=2):
    started = datetime(2026, 9, 9, 9, 10)
    return group_trade_episodes(tuple(
        TradeFill(str(index * 2 + offset), "000001", "테스트", side,
                  started + timedelta(minutes=index * 2 + offset), 1, price)
        for index in range(cycle_count)
        for offset, side, price in ((0, "매수", 1000), (1, "매도", 1010))
    ))[0]


def draft_manifest(pack_id="custom"):
    return StrategyPackManifest(
        pack_id, f"전략 {pack_id}", 1, True, 20, ("사용자형",), ("분봉",),
        source_description=f"원문 {pack_id}", review_status="approved",
    )


def strategy_draft(count=2, label="원래"):
    return ExtractedStrategyDraft(tuple(
        StrategyRuleDraft("진입 조건", f"{label} {index + 1}", "lecture.txt", "본문", ("분봉",),
                          True, "entry_time_hhmm", ">=", 0)
        for index in range(count)
    ), {"lecture.txt": label})


DRAFT_MINUTE_ROWS = (("2026-09-09T09:00:00", 1000, 1010, 990, 1000, 100, 1.0, "confirmed"),)


class DraftReadTrace:
    def __init__(self):
        self.opened = self.closed = 0
        self.statements = []

    def connect(self, original):
        trace = self

        class Connection(sqlite3.Connection):
            def close(self):
                trace.closed += 1
                super().close()

        def connect(*args, **kwargs):
            kwargs["factory"] = Connection
            connection = original(*args, **kwargs)
            trace.opened += 1
            connection.set_trace_callback(trace.statements.append)
            return connection
        return connect

    def draft_keys(self):
        prefix = "SELECT value_json FROM journal_settings WHERE setting_key='strategy_pack_draft:"
        return tuple(sql[len(prefix):-1] for sql in self.statements if sql.startswith(prefix))


class TradeAnalysisPreparationServiceTests(unittest.TestCase):
    def test_snapshot_warning_callback_is_per_call_and_default_loader_contract_stays_positional(self):
        repository = FakeRepository()
        calls = []
        def old_loader(code, started, ended):
            calls.append((code, started, ended))
            return ()
        service = TradeAnalysisPreparationService(repository, Path("unused.sqlite3"), old_loader, lambda *_args: ())
        arguments = dict(active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together")
        current = episode()
        service.prepare(current, (), **arguments)
        self.assertEqual([("000001", current.started_at, current.ended_at)], calls)
        observer = Mock()
        loader = Mock(return_value=())
        service = TradeAnalysisPreparationService(repository, Path("unused.sqlite3"), loader, lambda *_args: ())
        service.prepare(current, (), **arguments, on_snapshot_backfill_error=observer)
        loader.assert_called_once_with("000001", current.started_at, current.ended_at, on_backfill_error=observer)
        loader.reset_mock()
        service.prepare(current, (), **arguments)
        loader.assert_called_once_with("000001", current.started_at, current.ended_at)

    def test_prepares_daily_rows_and_persists_automatic_result(self) -> None:
        repository = FakeRepository()
        snapshots: list[tuple[str, datetime, datetime]] = []
        clock = Mock(return_value=datetime(2030, 1, 2, 3, 4, 5))
        with patch("kiwoom_monitor.application.trade_analysis_preparation_service.kst_now", clock):
            service = TradeAnalysisPreparationService(
                repository, Path("monitor.sqlite3"),
                lambda code, start, end: snapshots.append((code, start, end)) or (),
                lambda *_args: (),
            )

        result = service.prepare(
            episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
        )

        self.assertEqual("000001", repository.imported[0][1])
        self.assertEqual(250, repository.imported[0][3])
        self.assertEqual(episode().ended_at.date(), repository.imported[0][2])
        self.assertEqual("2030-01-02T03:04:05+09:00", repository.imported[0][4].isoformat())
        clock.assert_called_once_with()
        self.assertEqual(1, len(repository.saved_setups))
        self.assertEqual("", repository.saved_setups[0][2])
        self.assertEqual(1, len(result.cycles))
        self.assertEqual(("000001", episode().started_at, episode().ended_at), snapshots[0])
        self.assertEqual(1, len(repository.analysis_revisions))
        self.assertEqual(result.analysis_revision_id, repository.analysis_revisions[0].revision_id)
        self.assertEqual("trade-analysis/v2", repository.analysis_revisions[0].analysis_version)
        self.assertEqual([as_kst(clock.return_value)], repository.saved_setup_times)
        self.assertEqual(as_kst(clock.return_value), repository.analysis_revisions[0].created_at)

    def test_loads_explicit_research_links_without_guessing_a_decision(self) -> None:
        repository = FakeRepository()
        repository.research_links = (journal_research_link(
            "1|000001|2026-09-09T09:10:00|매수", run_id="run-1", snapshot_id="snapshot-1",
            now=datetime(2026, 9, 9, 10),
        ),)
        service = TradeAnalysisPreparationService(
            repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (),
        )

        result = service.prepare(
            episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
        )

        self.assertEqual(repository.research_links, result.research_links)
        self.assertIn(repository.research_links[0].execution_ref, repository.execution_refs)

    def test_selected_cycle_manifests_reach_reference_counts_without_name_inference(self) -> None:
        from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
        from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft
        from kiwoom_monitor.application.trade_episode_analysis_service import analyze_trade_cycles
        from kiwoom_monitor.presentation.trade_review_view_model import build_trade_analysis_view_model

        at = datetime(2026, 9, 9, 9, 10)
        current = group_trade_episodes((
            TradeFill("1", "000001", "fixture", "매수", at, 1, 1000),
            TradeFill("2", "000001", "fixture", "매도", at + timedelta(minutes=5), 1, 1010),
            TradeFill("3", "000001", "fixture", "매수", at + timedelta(minutes=20), 1, 1000),
            TradeFill("4", "000001", "fixture", "매도", at + timedelta(minutes=25), 1, 1010),
        ))[0]
        rows = tuple((moment.isoformat(), 1000, 1010, 990, 1000, 100, 1.0, "confirmed")
                     for moment in (at, at + timedelta(minutes=20)))
        early = StrategyRuleDraft("진입 조건", "early fixture", "early", "1", (), True,
                                  "entry_time_hhmm", "<", 920)
        late = replace(early, text="late fixture", source_file="late", comparison=">=", threshold=920)
        note = StrategyRuleDraft("설명", "reference only", "late", "2", (), False)
        drafts = {
            "early": ExtractedStrategyDraft((early,), {}),
            "late": ExtractedStrategyDraft((late, note, replace(note, text="another reference")), {}),
            "draft": ExtractedStrategyDraft((early,), {}),
            "disabled": ExtractedStrategyDraft((early,), {}),
        }
        for names in (("전략", "전략확장"), ("동명이름", "동명이름")):
            with self.subTest(names=names):
                packs = tuple(StrategyPackManifest(
                    key, name, 1, key != "disabled", 20, ("눌림",), ("분봉",),
                    source_description=f"source:{key}", review_status="draft" if key == "draft" else "approved",
                ) for key, name in (("draft", names[0]), ("disabled", names[0]),
                                    ("early", names[0]), ("late", names[1])))
                repository = FakeRepository()
                repository.load_strategy_pack_draft = Mock(side_effect=drafts.get)
                repository.stored_setup = (TradeSetupClassification("stale result", 1, (), subtype=names[0]), "낙주")
                repository.overrides = {0: "종가베팅"}
                active = default_strategy_pack()
                service = TradeAnalysisPreparationService(
                    repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (),
                    now_provider=lambda: as_kst(at + timedelta(days=1)),
                )
                prepared = service.prepare(current, rows, active_pack=active, strategy_packs=packs,
                                           result_mode="strategy_priority")
                self.assertEqual(("early", "late"), tuple(pack.pack_id for pack in prepared.cycle_selected_packs))
                self.assertIs(packs[2], prepared.cycle_selected_packs[0])
                self.assertIs(packs[3], prepared.cycle_selected_packs[1])
                self.assertEqual({"early": 1, "late": 3}, prepared.draft_rule_counts)
                self.assertEqual(("종가베팅", "눌림"), prepared.type_selection.selected_types)
                self.assertEqual(("눌림", "눌림"), tuple(setup.setup_type for setup in prepared.cycle_setups))
                self.assertEqual((90, 90), tuple(setup.confidence for setup in prepared.cycle_setups))
                self.assertEqual("눌림", repository.saved_setups[-1][1].setup_type)
                self.assertEqual([], repository.saved_overrides)  # Multi-cycle legacy manual value is not migrated.
                self.assertEqual({"early", "late"}, {call.args[0] for call in repository.load_strategy_pack_draft.call_args_list})
                analyses = analyze_trade_cycles(
                    prepared.cycles, rows, personal_rules=(), trade_value_threshold_eok=0,
                    selected_types=prepared.type_selection.selected_types, cycle_setups=prepared.cycle_setups,
                    overrides=prepared.type_selection.override_map(), entry_snapshots=(),
                    manual_unverifiable_for=active.unverifiable_for,
                )
                with patch("kiwoom_monitor.presentation.trade_review_view_model.strategy_pack_for_result",
                           side_effect=AssertionError("legacy inference used")):
                    model = build_trade_analysis_view_model(
                        overall_setup=prepared.overall_setup, pack_results=prepared.pack_results,
                        type_selection=prepared.type_selection, cycles=prepared.cycles, cycle_setups=prepared.cycle_setups,
                        analyses=analyses, entry_snapshots=(), linked_news=(), active_pack=active, packs=packs,
                        structured_rules=(), personal_rule_count=0, draft_rule_counts=prepared.draft_rule_counts,
                        total_return_rate=0, cycle_selected_packs=prepared.cycle_selected_packs,
                    )
                self.assertIn(f"[1차 · 종가베팅] 전략팩: {names[0]}\n적용 강의: source:early\n구조화 강의항목 1개", model.analysis_text)
                self.assertIn(f"[2차 · 눌림] 전략팩: {names[1]}\n적용 강의: source:late\n구조화 강의항목 3개", model.analysis_text)
                self.assertNotIn("source:draft", model.analysis_text)
                self.assertNotIn("source:disabled", model.analysis_text)

                # A new prepare observes a restored manifest/draft for the same stable pack ID.
                restored = replace(packs[3], version=2, source_description="source:restored")
                drafts["late"] = ExtractedStrategyDraft((late, note), {})
                repeated = service.prepare(current, rows, active_pack=active, strategy_packs=(*packs[:3], restored),
                                           result_mode="strategy_priority")
                self.assertIs(restored, repeated.cycle_selected_packs[1])
                self.assertEqual({"early": 1, "late": 2}, repeated.draft_rule_counts)
                drafts["late"] = ExtractedStrategyDraft((late, note, replace(note, text="another reference")), {})

    def test_migrates_legacy_single_manual_type_to_cycle_override(self) -> None:
        repository = FakeRepository()
        repository.stored_setup = (TradeSetupClassification("기타", 1, ()), "추격매수")
        service = TradeAnalysisPreparationService(
            repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (),
        )

        result = service.prepare(
            episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
        )

        self.assertEqual(((episode().group_id, 0, "주도주 돌파")), repository.saved_overrides[0])
        self.assertEqual(("주도주 돌파",), result.type_selection.selected_types)

    def test_daily_import_database_error_keeps_cached_analysis_path(self) -> None:
        for error in (sqlite3.OperationalError("locked"), OSError("monitor unavailable")):
            with self.subTest(error=type(error).__name__):
                repository = FakeRepository(import_error=error)
                clock = Mock(return_value=datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc))
                service = TradeAnalysisPreparationService(
                    repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (), now_provider=clock,
                )
                result = service.prepare(
                    episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
                )
                self.assertEqual(1, len(result.cycles))
                self.assertEqual(1, result.daily_bar_count)
                self.assertEqual(1, len(repository.saved_setups))
                self.assertEqual(1, len(repository.analysis_revisions))
                self.assertEqual([as_kst(clock.return_value)], repository.saved_setup_times)
                self.assertEqual(as_kst(clock.return_value), repository.analysis_revisions[0].created_at)
                self.assertEqual((Path("monitor.sqlite3"), "000001", episode().ended_at.date(), 250,
                                  as_kst(clock.return_value)), repository.imported[0])
                clock.assert_called_once_with()
        # Clock acquisition failure is not a database cache miss or a successful analysis.
        repository = FakeRepository()
        clock = Mock(side_effect=OSError("clock unavailable"))
        service = TradeAnalysisPreparationService(
            repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (), now_provider=clock,
        )
        with self.assertRaisesRegex(OSError, "clock unavailable"):
            service.prepare(episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together")
        self.assertEqual([], repository.imported)
        self.assertEqual([], repository.saved_setups)
        clock.assert_called_once_with()

    def test_native_daily_copy_uses_one_injected_receipt_and_keeps_episode_analysis_inputs(self):
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        for source_now in (datetime(2030, 1, 2, 3, 4, 5),
                           datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc)):
            with self.subTest(clock=source_now.isoformat()), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                monitor = root / "monitor.sqlite3"
                Database(monitor).initialize()
                repository = JournalRepository(root / "journal.sqlite3")
                with closing(sqlite3.connect(monitor)) as connection:
                    connection.executemany(
                        "INSERT INTO daily_bars(stock_code,trade_date,open_price,high_price,low_price,close_price,volume,trade_value_eok) "
                        "VALUES('000001',?,900,1100,800,1000,100,1)",
                        [("2026-09-08",), ("2026-09-09",), ("2030-01-02",)])
                    connection.commit()
                clock = Mock(return_value=source_now)
                snapshots = Mock(return_value=())
                service = TradeAnalysisPreparationService(
                    repository, monitor, snapshots, lambda *_args: (), now_provider=clock,
                )
                with patch("kiwoom_monitor.infrastructure.persistence.journal_bar_repository.kst_wall_now",
                           return_value=datetime(2031, 1, 2, 12, 34, 56)):
                    result = service.prepare(
                        episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
                    )
                with closing(sqlite3.connect(root / "journal.sqlite3")) as connection:
                    rows = connection.execute(
                        "SELECT trade_date,open_price,high_price,low_price,close_price,volume,trade_value_eok,confirmed_at "
                        "FROM journal_daily_bars ORDER BY trade_date").fetchall()
                self.assertEqual([
                    ("2026-09-08", 900, 1100, 800, 1000, 100, 1., "2030-01-02T03:04:05+09:00"),
                    ("2026-09-09", 900, 1100, 800, 1000, 100, 1., "2030-01-02T03:04:05+09:00"),
                ], rows)
                metadata = MarketDataMetadataRepository(root / "journal.sqlite3").load(
                    MarketDatasetKind.DAILY_BAR, "000001", "2026-09-09")
                self.assertEqual(as_kst(source_now), metadata.available_at)
                self.assertEqual(as_kst(datetime(2026, 9, 9)), metadata.effective_at)
                cached = repository.load_daily_bars("000001", episode().ended_at.date())
                self.assertEqual(("2026-09-08T00:00", "2026-09-09T00:00"), tuple(row[0] for row in cached))
                self.assertEqual(2, result.daily_bar_count)
                saved_setup, manual_type = repository.load_trade_setup(episode().group_id)
                # The existing codec persists type/confidence/evidence, not transient input warnings.
                self.assertEqual(
                    (result.overall_setup.setup_type, result.overall_setup.confidence, result.overall_setup.evidence),
                    (saved_setup.setup_type, saved_setup.confidence, saved_setup.evidence),
                )
                self.assertEqual("", manual_type)
                revisions = repository.load_journal_analysis_revisions(episode().group_id)
                self.assertEqual(1, len(revisions))
                self.assertEqual(result.analysis_revision_id, revisions[0].revision_id)
                self.assertEqual("trade-analysis/v2", revisions[0].analysis_version)
                snapshots.assert_called_once_with("000001", episode().started_at, episode().ended_at)
                clock.assert_called_once_with()

    def test_native_setup_and_revision_share_receipt_preserve_scope_and_revision_identity(self):
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

        origin = AccountScope("kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111")
        canonical = AccountScope("kiwoom", AccountEnvironment.MOCK, "22222222-2222-4222-8222-222222222222")
        other = AccountScope("kiwoom", AccountEnvironment.MOCK, "33333333-3333-4333-8333-333333333333")
        cases = (
            (LEGACY_ACCOUNT_SCOPE, None, datetime(2030, 1, 2, 3, 4, 5)),
            (origin, canonical, datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc)),
        )
        for scope, effective, source_now in cases:
            with self.subTest(scope=scope.account_ref), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                current = group_trade_episodes(tuple(
                    replace(fill, origin_scope=scope, canonical_scope=effective) for fill in episode().fills
                ))[0]
                original = TradeSetupClassification("기타", 1, ("original",))
                seed_time = datetime(2026, 9, 9, 10)
                repository.save_trade_setup(
                    current.group_id, original, "추격매수", now=seed_time,
                    account_scope=scope, canonical_scope=effective,
                )
                clock = Mock(return_value=source_now)
                service = TradeAnalysisPreparationService(
                    repository, Path(directory) / "missing-monitor.sqlite3",
                    lambda *_args: (), lambda *_args: (), now_provider=clock,
                )
                args = dict(active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together")
                with patch("kiwoom_monitor.infrastructure.persistence.journal_trade_repository.kst_wall_now",
                           return_value=datetime(2031, 1, 2, 12, 34, 56)) as repository_host, \
                     patch("kiwoom_monitor.application.journal_enrichment.kst_wall_now",
                           return_value=datetime(2031, 1, 2, 12, 34, 56)) as revision_host:
                    # Failure of the last migration write rolls back the automatic setup and manual override too.
                    with closing(sqlite3.connect(path)) as connection:
                        connection.execute("CREATE TRIGGER reject_sync BEFORE INSERT ON journal_sync_states "
                                           "BEGIN SELECT RAISE(ABORT, 'sync rejected'); END")
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "sync rejected"):
                        service.prepare(current, (), **args)
                    clock.assert_called_once_with()
                    self.assertEqual((original, "추격매수"), repository.load_trade_setup(current.group_id, effective or scope))
                    self.assertEqual({}, repository.load_trade_setup_cycle_overrides(current.group_id, effective or scope))
                    self.assertEqual((), repository.load_journal_analysis_revisions(current.group_id, effective or scope))
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual((seed_time.isoformat(),), connection.execute(
                            "SELECT updated_at FROM trade_setup_classifications").fetchone())
                        self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM journal_sync_states").fetchone()[0])
                        connection.execute("DROP TRIGGER reject_sync")

                    clock.reset_mock()
                    result = service.prepare(current, (), **args)
                    clock.assert_called_once_with()
                    receipt = as_kst(source_now)
                    scope_values = (scope.broker, scope.environment.value, scope.account_ref,
                                    (effective or scope).account_ref)
                    with closing(sqlite3.connect(path)) as connection:
                        columns = "origin_broker,origin_environment,origin_account_ref,canonical_account_ref"
                        self.assertEqual((receipt.isoformat(), "", *scope_values), connection.execute(
                            "SELECT updated_at,manual_type," + columns + " FROM trade_setup_classifications").fetchone())
                        self.assertEqual((receipt.isoformat(), 0, "주도주 돌파", *scope_values), connection.execute(
                            "SELECT updated_at,cycle_index,manual_type," + columns + " FROM trade_setup_cycle_overrides").fetchone())
                        collection = "journal_cycle_overrides" if scope == LEGACY_ACCOUNT_SCOPE else "journal_v2_cycle_overrides"
                        owner = "legacy" if scope == LEGACY_ACCOUNT_SCOPE else scope.account_ref
                        self.assertEqual((receipt.isoformat(), collection, owner, current.group_id + "|0", 0, *scope_values),
                                         connection.execute("SELECT updated_at,collection,owner,document_key,is_deleted," + columns
                                                            + " FROM journal_sync_states").fetchone())
                    saved_setup, manual_type = repository.load_trade_setup(current.group_id, effective or scope)
                    self.assertEqual((result.overall_setup.setup_type, result.overall_setup.confidence, result.overall_setup.evidence),
                                     (saved_setup.setup_type, saved_setup.confidence, saved_setup.evidence))
                    self.assertEqual("", manual_type)
                    first = repository.load_journal_analysis_revisions(current.group_id, effective or scope)[0]
                    self.assertEqual(result.analysis_revision_id, first.revision_id)
                    self.assertEqual(receipt, first.created_at)
                    self.assertEqual(scope, first.origin_scope)
                    self.assertEqual(effective or scope, first.effective_scope)
                    self.assertIsNone(repository.load_trade_setup(current.group_id, other))
                    self.assertEqual((), repository.load_journal_analysis_revisions(current.group_id, other))

                    # The next receipt updates setup time; immutable analysis inputs keep the first revision time.
                    clock.return_value = receipt + timedelta(hours=1)
                    clock.reset_mock()
                    repeated = service.prepare(current, (), **args)
                    clock.assert_called_once_with()
                    self.assertEqual(first.revision_id, repeated.analysis_revision_id)
                    self.assertEqual((first,), repository.load_journal_analysis_revisions(current.group_id, effective or scope))
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual((clock.return_value.isoformat(),), connection.execute(
                            "SELECT updated_at FROM trade_setup_classifications").fetchone())
                        self.assertEqual((receipt.isoformat(),), connection.execute(
                            "SELECT updated_at FROM trade_setup_cycle_overrides").fetchone())

                    clock.return_value += timedelta(hours=1)
                    clock.reset_mock()
                    changed = service.prepare(current, (("2026-09-09T09:10", 1000, 1100, 900, 1010, 100, 1., "api_confirmed"),), **args)
                    clock.assert_called_once_with()
                    revisions = repository.load_journal_analysis_revisions(current.group_id, effective or scope)
                    self.assertEqual(2, len(revisions))
                    self.assertEqual(first, revisions[0])
                    self.assertEqual(changed.analysis_revision_id, revisions[1].revision_id)
                    self.assertNotEqual(first.revision_id, revisions[1].revision_id)
                    self.assertNotEqual(first.input_fingerprint, revisions[1].input_fingerprint)
                    self.assertEqual(clock.return_value, revisions[1].created_at)
                    repository_host.assert_not_called()
                    revision_host.assert_not_called()

    def test_cycle_classification_failure_does_not_clear_legacy_manual_type(self) -> None:
        class FailingPack:
            def classify(self, *args, **kwargs):
                return TradeSetupClassification("기타", 1, ())

            def classify_cycles(self, *args, **kwargs):
                raise RuntimeError("classification failed")

        repository = FakeRepository()
        repository.stored_setup = (TradeSetupClassification("기타", 1, ()), "돌파")
        service = TradeAnalysisPreparationService(
            repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (),
        )

        with self.assertRaises(RuntimeError):
            service.prepare(
                episode(), (), active_pack=FailingPack(), strategy_packs=(), result_mode="together",
            )

        self.assertEqual([], repository.saved_setups)
        self.assertEqual("돌파", repository.stored_setup[1])

    def test_native_strategy_draft_reads_share_classification_count_source_and_manual_override(self):
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        for cycle_count, pack_count in ((1, 1), (2, 1), (8, 1), (2, 2)):
            with self.subTest(cycles=cycle_count, packs=pack_count), tempfile.TemporaryDirectory() as directory:
                repository = JournalRepository(Path(directory) / "journal.db")
                current = draft_episode(cycle_count)
                packs = tuple(draft_manifest(f"custom{index}") for index in range(pack_count))
                for pack in packs:
                    repository.save_strategy_pack_draft(pack.pack_id, strategy_draft(), now=datetime(2026, 9, 9, 10))
                repository.save_trade_setup_cycle_override(current.group_id, 0, "낙주", now=datetime(2026, 9, 9, 10))
                clock = Mock(return_value=datetime(2030, 1, 2, 3, 4, 5))
                service = TradeAnalysisPreparationService(
                    repository, Path(directory) / "absent-monitor.db", lambda *_args: (), lambda *_args: (),
                    now_provider=clock,
                )
                trace = DraftReadTrace()
                with patch("sqlite3.connect", side_effect=trace.connect(sqlite3.connect)):
                    prepared = service.prepare(
                        current, DRAFT_MINUTE_ROWS, active_pack=default_strategy_pack(),
                        strategy_packs=(*packs, replace(packs[0], pack_id="disabled", enabled=False),
                                        replace(packs[0], pack_id="unapproved", review_status="draft")),
                        result_mode="strategy_priority",
                    )
                self.assertEqual(tuple(pack.pack_id for pack in packs), trace.draft_keys())
                self.assertEqual(trace.opened, trace.closed)
                self.assertEqual("사용자형", prepared.overall_setup.setup_type)
                self.assertEqual(90, prepared.overall_setup.confidence)
                for index, setup in enumerate(prepared.cycle_setups):
                    expected_time = 910 + index * 2
                    self.assertEqual("사용자형", setup.setup_type)
                    self.assertEqual(90, setup.confidence)
                    self.assertEqual(tuple(
                        f"원래 {rule} (측정 {expected_time:.2f} >= 기준 0)" for rule in (1, 2)
                    ), setup.evidence)
                self.assertEqual({packs[0].pack_id: 2}, prepared.draft_rule_counts)
                self.assertEqual("낙주", prepared.type_selection.selected_types[0])
                reference = strategy_review_reference(
                    prepared.type_selection.selected_types[0], default_pack=default_strategy_pack(),
                    structured_rules=(), selected_pack=prepared.cycle_selected_packs[0],
                    selected_pack_rule_count=prepared.draft_rule_counts[packs[0].pack_id],
                )
                self.assertEqual((packs[0].name, packs[0].source_description, 2),
                                 (reference.pack_name, reference.source_text, reference.relevant_rule_count))
                stored, manual = repository.load_trade_setup(current.group_id)
                self.assertEqual(("사용자형", 90, prepared.overall_setup.evidence),
                                 (stored.setup_type, stored.confidence, stored.evidence))
                self.assertEqual("", manual)
                self.assertEqual({0: "낙주"}, repository.load_trade_setup_cycle_overrides(current.group_id))
                self.assertEqual(as_kst(clock.return_value), repository.load_journal_analysis_revisions(current.group_id)[0].created_at)
                clock.assert_called_once_with()

    def test_native_missing_corrupt_or_failed_draft_is_reobserved_on_next_prepare(self):
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        for failure in ("missing", "invalid_json", "invalid_threshold", "sqlite_error", "top_level_list"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                repository = JournalRepository(Path(directory) / "journal.db")
                current, pack = draft_episode(), draft_manifest()
                # Failed first reads retain manual input; missing drafts follow normal migration.
                repository.save_trade_setup(current.group_id, TradeSetupClassification("기타", 1, ()),
                                           "추격매수", now=datetime(2026, 9, 9, 10))
                if failure in {"invalid_json", "invalid_threshold", "top_level_list"}:
                    payload = {
                        "invalid_json": "{",
                        "invalid_threshold": '{"rules":[{"threshold":"bad"}]}',
                        "top_level_list": "[]",
                    }[failure]
                    with closing(sqlite3.connect(repository._path)) as connection, connection:
                        connection.execute("INSERT INTO journal_settings VALUES (?,?,?)",
                                           ("strategy_pack_draft:custom", payload, "2026-09-09T10:00:00+09:00"))
                clock = Mock(return_value=datetime(2030, 1, 2, 3, 4, 5))
                service = TradeAnalysisPreparationService(
                    repository, Path(directory) / "absent-monitor.db", lambda *_args: (), lambda *_args: (), now_provider=clock,
                )
                arguments = dict(active_pack=default_strategy_pack(), strategy_packs=(pack,), result_mode="strategy_priority")
                trace = DraftReadTrace()
                with patch("sqlite3.connect", side_effect=trace.connect(sqlite3.connect)):
                    if failure == "sqlite_error":
                        with patch.object(repository, "load_strategy_pack_draft", side_effect=sqlite3.OperationalError("draft unavailable")):
                            with self.assertRaisesRegex(sqlite3.OperationalError, "draft unavailable"):
                                service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                    elif failure == "top_level_list":
                        with self.assertRaises(AttributeError):
                            service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                    else:
                        first = service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                        self.assertNotEqual("사용자형", first.overall_setup.setup_type)
                        self.assertEqual({}, first.draft_rule_counts)
                self.assertEqual(trace.opened, trace.closed)
                self.assertEqual(() if failure == "sqlite_error" else ("custom",), trace.draft_keys())
                self.assertEqual("추격매수" if failure in {"sqlite_error", "top_level_list"} else "",
                                 repository.load_trade_setup(current.group_id)[1])
                self.assertEqual(0 if failure in {"sqlite_error", "top_level_list"} else 1,
                                 len(repository.load_journal_analysis_revisions(current.group_id)))
                repository.save_strategy_pack_draft(pack.pack_id, strategy_draft(), now=clock.return_value)
                if failure not in {"sqlite_error", "top_level_list"}:
                    # The old manifest version keeps its immutable input identity. Draft-only
                    # external repair is re-read but cannot overwrite that existing revision.
                    trace = DraftReadTrace()
                    with patch("sqlite3.connect", side_effect=trace.connect(sqlite3.connect)):
                        with self.assertRaisesRegex(sqlite3.IntegrityError, "같은 분석 revision ID"):
                            service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                    self.assertEqual(("custom",), trace.draft_keys())
                    self.assertEqual(trace.opened, trace.closed)
                    self.assertEqual("사용자형", repository.load_trade_setup(current.group_id)[0].setup_type)
                trace = DraftReadTrace()
                with patch("sqlite3.connect", side_effect=trace.connect(sqlite3.connect)):
                    second = service.prepare(current, DRAFT_MINUTE_ROWS,
                                             **{**arguments, "strategy_packs": (replace(pack, version=2),)})
                self.assertEqual(("custom",), trace.draft_keys())
                self.assertEqual(trace.opened, trace.closed)
                self.assertEqual("사용자형", second.overall_setup.setup_type)
                self.assertEqual({"custom": 2}, second.draft_rule_counts)
                self.assertEqual(2 if failure in {"sqlite_error", "top_level_list"} else 3, clock.call_count)

    def test_native_draft_edit_after_first_read_is_visible_only_to_next_prepare(self):
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.db")
            current, pack = draft_episode(), draft_manifest()
            repository.save_strategy_pack_draft(pack.pack_id, strategy_draft(), now=datetime(2026, 9, 9, 10))
            original = repository.load_strategy_pack_draft
            reads = []
            def load_then_edit(pack_id):
                value = original(pack_id)
                reads.append(pack_id)
                if len(reads) == 1:
                    repository.save_strategy_pack_draft(pack_id, strategy_draft(3, "편집"), now=datetime(2026, 9, 9, 11))
                return value
            clock = Mock(return_value=datetime(2030, 1, 2, 3, 4, 5))
            service = TradeAnalysisPreparationService(
                repository, Path(directory) / "absent-monitor.db", lambda *_args: (), lambda *_args: (), now_provider=clock,
            )
            arguments = dict(active_pack=default_strategy_pack(), strategy_packs=(pack,), result_mode="strategy_priority")
            with patch.object(repository, "load_strategy_pack_draft", side_effect=load_then_edit):
                first = service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                self.assertEqual(["custom"], reads)
                self.assertEqual({"custom": 2}, first.draft_rule_counts)
                for setup in (first.overall_setup, *first.cycle_setups):
                    self.assertEqual(2, len(setup.evidence))
                    self.assertTrue(all(value.startswith("원래 ") for value in setup.evidence))
                with self.assertRaisesRegex(sqlite3.IntegrityError, "같은 분석 revision ID"):
                    service.prepare(current, DRAFT_MINUTE_ROWS, **arguments)
                self.assertTrue(all(value.startswith("편집 ")
                                    for value in repository.load_trade_setup(current.group_id)[0].evidence))
                second = service.prepare(current, DRAFT_MINUTE_ROWS,
                                         **{**arguments, "strategy_packs": (replace(pack, version=2),)})
            self.assertEqual(["custom", "custom", "custom"], reads)
            self.assertEqual({"custom": 3}, second.draft_rule_counts)
            for setup in (second.overall_setup, *second.cycle_setups):
                self.assertEqual(3, len(setup.evidence))
                self.assertTrue(all(value.startswith("편집 ") for value in setup.evidence))
            self.assertNotEqual(first.analysis_revision_id, second.analysis_revision_id)
            self.assertEqual(3, clock.call_count)


if __name__ == "__main__":
    unittest.main()
