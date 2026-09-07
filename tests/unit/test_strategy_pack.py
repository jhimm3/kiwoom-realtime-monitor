from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.application.strategy_pack import (
    MIMOSA_MANIFEST, StrategyPackManifest, default_strategy_pack, select_strategy_candidate,
)
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification, classify_trade_setup
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class StrategyPackTests(unittest.TestCase):
    def test_result_mode_controls_which_candidate_becomes_final(self) -> None:
        base = TradeSetupClassification("눌림", 70, ())
        strategy = TradeSetupClassification("돌파", 85, ())
        candidates = (("기본분석", base), ("돌파 강의", strategy))
        self.assertIs(base, select_strategy_candidate(candidates, "together")[1])
        self.assertIs(base, select_strategy_candidate(candidates, "base_priority")[1])
        self.assertIs(strategy, select_strategy_candidate(candidates, "auto_replace")[1])
        self.assertIs(strategy, select_strategy_candidate(candidates, "strategy_priority")[1])

    def test_auto_replace_keeps_stronger_base_but_replaces_other_base(self) -> None:
        strong = TradeSetupClassification("눌림", 84, ())
        weak_strategy = TradeSetupClassification("돌파", 85, ())
        other = TradeSetupClassification("기타", 90, ())
        self.assertIs(strong, select_strategy_candidate((("기본분석", strong), ("전략팩", weak_strategy)), "auto_replace")[1])
        self.assertIs(weak_strategy, select_strategy_candidate((("기본분석", other), ("전략팩", weak_strategy)), "auto_replace")[1])

    def test_mimosa_pack_preserves_existing_classifier_result(self) -> None:
        at = datetime(2026, 8, 28, 10, 5)
        episode = group_trade_episodes((
            TradeFill("1", "005930", "삼성전자", "매수", at, 10, 105),
            TradeFill("2", "005930", "삼성전자", "매도", at.replace(minute=10), 10, 110),
        ))[0]
        rows = (
            ("2026-08-28T10:01", 100, 102, 99, 101, 100, 1.0, "확정"),
            ("2026-08-28T10:02", 101, 103, 100, 102, 100, 1.0, "확정"),
            ("2026-08-28T10:03", 102, 104, 101, 103, 100, 1.0, "확정"),
            ("2026-08-28T10:04", 103, 105, 102, 104, 100, 1.0, "확정"),
        )
        self.assertEqual(classify_trade_setup(episode, rows), default_strategy_pack().classify(episode, rows))

    def test_default_manifest_is_persisted_without_source_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.db")
            self.assertEqual((MIMOSA_MANIFEST,), repository.load_strategy_packs())
            self.assertEqual((MIMOSA_MANIFEST,), JournalRepository(Path(directory) / "journal.db").load_strategy_packs())

    def test_strategy_pack_version_can_be_saved_and_loaded(self) -> None:
        pack = StrategyPackManifest(
            "course_test", "테스트", 2, False, 100, ("돌파",), ("분봉",), review_status="approved",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.db")
            repository.save_strategy_pack_version(pack, None)
            versions = repository.load_strategy_pack_versions(pack.pack_id)
            self.assertEqual(2, versions[0][0].version)
            self.assertIsNone(versions[0][1])


if __name__ == "__main__":
    unittest.main()
