from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.strategy_pack import StrategyPackManifest, default_strategy_pack
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.trade_strategy_coordinator import (
    classify_with_strategy_packs,
    normalize_setup_type_for_packs,
    resolve_cycle_type_selection,
    strategy_pack_for_result,
)


def manifest(
    pack_id: str = "custom", name: str = "눌림팩", *, enabled: bool = True,
    status: str = "approved",
) -> StrategyPackManifest:
    return StrategyPackManifest(
        pack_id, name, 1, enabled, 20, ("눌림",), ("분봉",), review_status=status,
    )


class TradeStrategyCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        at = datetime(2026, 9, 9, 9, 10)
        self.episode = group_trade_episodes((
            TradeFill("1", "000001", "테스트", "매수", at, 1, 1000),
            TradeFill("2", "000001", "테스트", "매도", at.replace(minute=15), 1, 1010),
        ))[0]
        self.base = TradeSetupClassification("돌파", 70, ())
        self.custom_result = TradeSetupClassification("눌림", 80, (), subtype="눌림팩 · 조건 일치")

    def test_enabled_approved_custom_pack_participates_in_selection(self) -> None:
        pack = manifest()
        loaded: list[str] = []

        def load(pack_id: str) -> ExtractedStrategyDraft:
            loaded.append(pack_id)
            return ExtractedStrategyDraft((), {})

        selected, comparison = classify_with_strategy_packs(
            default_strategy_pack(), (pack,), "strategy_priority", load,
            self.episode, (), (), base=self.base,
            evaluator=lambda *_args: self.custom_result,
        )

        self.assertIs(self.custom_result, selected)
        self.assertEqual(["custom"], loaded)
        self.assertEqual("표시방식 전략팩 우선", comparison[0])
        self.assertIn("눌림팩 눌림 80%(최종)", comparison)

    def test_disabled_or_unapproved_pack_is_not_loaded(self) -> None:
        loaded: list[str] = []
        selected, comparison = classify_with_strategy_packs(
            default_strategy_pack(), (manifest(enabled=False), manifest("draft", status="draft")),
            "strategy_priority", lambda pack_id: loaded.append(pack_id),  # type: ignore[arg-type]
            self.episode, (), (), base=self.base,
        )

        self.assertIs(self.base, selected)
        self.assertEqual([], loaded)
        self.assertEqual(("표시방식 전략팩 우선", "기본분석 돌파 70%(최종)"), comparison)

    def test_legacy_single_type_becomes_cycle_override(self) -> None:
        cycle = (TradeSetupClassification("기타", 40, ()),)

        selection = resolve_cycle_type_selection(cycle, {}, "추격매수", ())

        self.assertEqual(("주도주 돌파",), selection.selected_types)
        self.assertEqual("주도주 돌파", selection.selected_type_label)
        self.assertEqual((0, "주도주 돌파"), selection.legacy_override)
        self.assertEqual({0: "주도주 돌파"}, selection.override_map())

    def test_multiple_cycles_keep_numbered_label_and_custom_type(self) -> None:
        pack = manifest()
        cycles = (
            TradeSetupClassification("돌파", 80, ()),
            TradeSetupClassification("기타", 40, ()),
        )

        selection = resolve_cycle_type_selection(cycles, {1: "눌림"}, "", (pack,))

        self.assertEqual(("돌파", "눌림"), selection.selected_types)
        self.assertEqual("1차 돌파 · 2차 눌림", selection.selected_type_label)
        self.assertIsNone(selection.legacy_override)

    def test_result_pack_and_custom_type_follow_enabled_pack_contract(self) -> None:
        pack = manifest()
        self.assertEqual("눌림", normalize_setup_type_for_packs("눌림", (pack,)))
        self.assertIs(pack, strategy_pack_for_result(self.custom_result, (pack,)))
        self.assertIsNone(strategy_pack_for_result(self.custom_result, (manifest(enabled=False),)))


if __name__ == "__main__":
    unittest.main()
