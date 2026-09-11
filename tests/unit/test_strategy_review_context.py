from __future__ import annotations

import unittest
from types import SimpleNamespace

from kiwoom_monitor.application.strategy_pack import StrategyPackManifest, default_strategy_pack
from kiwoom_monitor.application.strategy_review_context import strategy_review_reference


class StrategyReviewContextTests(unittest.TestCase):
    def test_default_pack_counts_common_lesson_and_setup_lesson(self) -> None:
        rules = (
            SimpleNamespace(lesson=1), SimpleNamespace(lesson=2),
            SimpleNamespace(lesson=2), SimpleNamespace(lesson=5),
        )

        result = strategy_review_reference(
            "주도주 돌파", default_pack=default_strategy_pack(), structured_rules=rules,
        )

        self.assertEqual("미모사 기본 전략팩", result.pack_name)
        self.assertEqual(3, result.relevant_rule_count)
        self.assertEqual("2강 주도주 돌파매매 (1강 주도주 선정은 선행 조건)", result.source_text)

    def test_custom_pack_uses_draft_rule_count_and_its_source(self) -> None:
        custom = StrategyPackManifest(
            "pullback", "눌림 전략", 1, True, 20, ("눌림",), ("1분봉",),
            source_description="눌림 강의 PDF",
        )

        result = strategy_review_reference(
            "눌림", default_pack=default_strategy_pack(), structured_rules=(),
            selected_pack=custom, selected_pack_rule_count=14,
        )

        self.assertEqual("눌림 전략", result.pack_name)
        self.assertEqual("눌림 강의 PDF", result.source_text)
        self.assertEqual(14, result.relevant_rule_count)


if __name__ == "__main__":
    unittest.main()
