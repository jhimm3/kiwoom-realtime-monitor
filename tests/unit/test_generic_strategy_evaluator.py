from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.generic_strategy_evaluator import evaluate_strategy_pack
from kiwoom_monitor.application.strategy_pack import StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes


class GenericStrategyEvaluatorTests(unittest.TestCase):
    def test_approved_pullback_pack_uses_only_mapped_numeric_rules(self) -> None:
        at = datetime(2026, 9, 1, 10, 5)
        episode = group_trade_episodes((
            TradeFill("1", "005930", "삼성전자", "매수", at, 10, 105),
            TradeFill("2", "005930", "삼성전자", "매도", at + timedelta(minutes=5), 10, 108),
        ))[0]
        rows = (
            ("2026-09-01T10:00", 100, 110, 100, 109, 100, 10.0, "확정"),
            ("2026-09-01T10:01", 109, 109, 102, 103, 100, 8.0, "확정"),
            ("2026-09-01T10:02", 103, 106, 102, 105, 100, 20.0, "확정"),
            ("2026-09-01T10:03", 105, 106, 104, 105, 100, 12.0, "확정"),
            ("2026-09-01T10:04", 105, 106, 104, 105, 100, 12.0, "확정"),
            ("2026-09-01T10:05", 105, 106, 104, 105, 100, 40.0, "확정"),
        )
        draft = ExtractedStrategyDraft((
            StrategyRuleDraft("진입 조건", "고점에서 4% 이상 눌림", "x", "1쪽", ("분봉",), True, "pullback_from_session_high_pct", ">=", 4.0),
            StrategyRuleDraft("진입 조건", "거래대금 30억 이상", "x", "1쪽", ("거래대금",), True, "entry_trade_value_eok", ">=", 30.0),
        ), {})
        pack = StrategyPackManifest("pullback", "눌림 강의", 1, True, 20, ("눌림",), ("분봉", "거래대금"), review_status="approved")
        result = evaluate_strategy_pack(pack, draft, episode, rows)
        self.assertIsNotNone(result)
        self.assertEqual("눌림", result.setup_type)
        self.assertGreaterEqual(result.confidence, 80)

    def test_unmapped_text_cannot_activate_pack(self) -> None:
        at = datetime(2026, 9, 1, 10, 5)
        episode = group_trade_episodes((TradeFill("1", "A", "A", "매수", at, 1, 100),))[0]
        draft = ExtractedStrategyDraft((StrategyRuleDraft("진입 조건", "좋아 보이면 산다", "x", "", (), False),), {})
        pack = StrategyPackManifest("x", "x", 1, True, 20, ("눌림",), (), review_status="approved")
        self.assertIsNone(evaluate_strategy_pack(pack, draft, episode, ((at.isoformat(), 100, 100, 100, 100, 1, 1.0, "확정"),)))


if __name__ == "__main__":
    unittest.main()
