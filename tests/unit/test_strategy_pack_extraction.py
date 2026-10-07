from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.strategy_pack_extraction import (
    ExtractedStrategyDraft, StrategyRuleDraft, draft_change_labels,
    extend_reviewed_draft, extract_strategy_draft,
)
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class StrategyPackExtractionTests(unittest.TestCase):
    def test_extracts_and_classifies_review_draft_from_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "pullback.txt"
            source.write_text(
                "직전 고점에서 5% 눌린 뒤 거래량이 감소하고 지지선에서 반등하면 매수한다.\n"
                "지지선을 이탈하면 손절하고 거래대금이 부족한 종목은 매매하지 않는다.\n",
                encoding="utf-8",
            )
            draft = extract_strategy_draft((source,))
            self.assertEqual(2, len(draft.rules))
            self.assertEqual("진입 조건", draft.rules[0].category)
            self.assertIn("거래량", draft.rules[0].required_data)
            self.assertTrue(draft.rules[0].automatable)
            self.assertEqual("위험관리", draft.rules[1].category)

    def test_extracted_text_and_rules_survive_source_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "lecture.md"
            source.write_text("VWAP 위에서 거래대금이 증가하면 진입을 검토한다.", encoding="utf-8")
            draft = extract_strategy_draft((source,))
            repository = JournalRepository(Path(directory) / "journal.db")
            repository.save_strategy_pack_draft("course_test", draft)
            source.unlink()
            loaded = repository.load_strategy_pack_draft("course_test")
            self.assertIsNotNone(loaded)
            self.assertIn("VWAP", next(iter(loaded.source_texts.values())))
            self.assertEqual(draft.rules, loaded.rules)

    def test_extending_draft_preserves_reviewed_rules_and_adds_only_new_rules(self) -> None:
        reviewed = StrategyRuleDraft(
            "진입 조건", "직전 고점을 돌파하면 진입한다.", "old.txt", "본문", ("분봉",),
            True, "breakout_over_30m_high_pct", ">=", 0.3,
        )
        previous = ExtractedStrategyDraft((reviewed,), {"old.txt": reviewed.text})
        addition = ExtractedStrategyDraft((
            StrategyRuleDraft("진입 조건", reviewed.text, "copy.txt", "본문", ("분봉",), True),
            StrategyRuleDraft("위험관리", "최대 손실은 2% 이내로 제한한다.", "new.txt", "본문", (), True),
        ), {"new.txt": "새 강의"})
        merged = extend_reviewed_draft(previous, addition)
        self.assertEqual(2, len(merged.rules))
        self.assertEqual(0.3, merged.rules[0].threshold)
        self.assertIn("new.txt", merged.source_texts)
        self.assertEqual(("유지", "신규"), draft_change_labels(previous, merged))


if __name__ == "__main__":
    unittest.main()
