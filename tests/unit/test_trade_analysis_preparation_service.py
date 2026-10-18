from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock, patch

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.application.strategy_pack import default_strategy_pack
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


class TradeAnalysisPreparationServiceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
