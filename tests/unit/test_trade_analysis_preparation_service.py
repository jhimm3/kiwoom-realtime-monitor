from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.application.strategy_pack import default_strategy_pack
from kiwoom_monitor.application.trade_analysis_preparation_service import TradeAnalysisPreparationService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification


class FakeRepository:
    def __init__(self, *, import_error: Exception | None = None) -> None:
        self.import_error = import_error
        self.imported: list[tuple[Path, str, object, int]] = []
        self.saved_setups: list[tuple[str, TradeSetupClassification, str]] = []
        self.saved_overrides: list[tuple[str, int, str]] = []
        self.stored_setup: tuple[TradeSetupClassification, str] | None = None
        self.overrides: dict[int, str] = {}
        self.daily_rows = (("2026-09-09T00:00", 900, 1100, 800, 1000, 100, 1.0, "daily_confirmed"),)

    def import_monitor_daily_bars(self, path: Path, code: str, day: object, limit: int = 250) -> int:
        self.imported.append((path, code, day, limit))
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
        self.assertEqual(1, len(repository.saved_setups))
        self.assertEqual("", repository.saved_setups[0][2])
        self.assertEqual(1, len(result.cycles))
        self.assertEqual(("000001", episode().started_at, episode().ended_at), snapshots[0])

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
        repository = FakeRepository(import_error=sqlite3.OperationalError("locked"))
        service = TradeAnalysisPreparationService(
            repository, Path("monitor.sqlite3"), lambda *_args: (), lambda *_args: (),
        )

        result = service.prepare(
            episode(), (), active_pack=default_strategy_pack(), strategy_packs=(), result_mode="together",
        )

        self.assertEqual(1, len(result.cycles))
        self.assertEqual(1, len(repository.saved_setups))

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
