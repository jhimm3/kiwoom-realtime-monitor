from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import Any

from kiwoom_monitor.application.trade_episode_analysis_service import analyze_trade_cycles
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification


class TradeEpisodeAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        at = datetime(2026, 9, 9, 9, 10)
        first = group_trade_episodes((
            TradeFill("1", "000001", "테스트", "매수", at, 1, 1000),
            TradeFill("2", "000001", "테스트", "매도", at + timedelta(minutes=2), 1, 1010),
        ))[0]
        second_at = at + timedelta(minutes=10)
        second = group_trade_episodes((
            TradeFill("3", "000002", "테스트2", "매수", second_at, 1, 2000),
            TradeFill("4", "000002", "테스트2", "매도", second_at + timedelta(minutes=2), 1, 2010),
        ))[0]
        self.cycles = (first, second)

    def test_manual_and_automatic_cycles_receive_distinct_analysis_inputs(self) -> None:
        setups = (
            TradeSetupClassification(
                "기타", 45, ("자동 근거 1",), warnings=("자동 주의 1",),
                unverifiable=("당시 뉴스",),
            ),
            TradeSetupClassification(
                "돌파", 82, ("자동 근거 2",), warnings=("자동 주의 2",),
                unverifiable=("당시 시장",),
            ),
        )
        calls: list[dict[str, Any]] = []

        def analyzer(_cycle: object, _rows: object, **values: Any) -> Any:
            calls.append(values)
            return values

        results = analyze_trade_cycles(
            self.cycles, (), personal_rules=("원칙",), trade_value_threshold_eok=40,
            selected_types=("눌림", "돌파"), cycle_setups=setups,
            overrides={0: "눌림"}, entry_snapshots=(),
            manual_unverifiable_for=lambda setup_type: (f"{setup_type} 수동 확인",),
            analyzer=analyzer,  # type: ignore[arg-type]
        )

        self.assertEqual(2, len(results))
        self.assertEqual(90, calls[0]["setup_confidence"])
        self.assertEqual(("사용자가 눌림 유형으로 직접 확정했습니다.",), calls[0]["setup_evidence"])
        self.assertEqual((), calls[0]["lesson_warnings"])
        self.assertEqual(("눌림 수동 확인",), calls[0]["unverifiable_items"])
        self.assertEqual(82, calls[1]["setup_confidence"])
        self.assertEqual(("자동 근거 2",), calls[1]["setup_evidence"])
        self.assertEqual(("자동 주의 2",), calls[1]["lesson_warnings"])
        self.assertEqual(("당시 시장",), calls[1]["unverifiable_items"])

    def test_snapshot_context_is_applied_before_analyzer_runs(self) -> None:
        setup = TradeSetupClassification(
            "주도주 돌파", 80, (), unverifiable=(
                "1강 기준의 당시 시장 주도주 여부(시장 전체 순위·지속적인 관심)",
            ),
        )
        cycle = self.cycles[0]
        entry = type("Snapshot", (), {
            "executed_at": cycle.started_at,
            "side": "매수",
            "rank": 2,
            "news": (),
            "themes": (),
            "investor_flow": {},
            "orderbook": {},
            "market_state": {},
        })()
        captured: dict[str, Any] = {}

        def analyzer(_cycle: object, _rows: object, **values: Any) -> Any:
            captured.update(values)
            return values

        analyze_trade_cycles(
            (cycle,), (), personal_rules=(), trade_value_threshold_eok=0,
            selected_types=("주도주 돌파",), cycle_setups=(setup,), overrides={},
            entry_snapshots=(entry,), manual_unverifiable_for=lambda _value: (),
            analyzer=analyzer,  # type: ignore[arg-type]
        )

        self.assertEqual(
            ("1강 기준의 지속적인 시장 관심 여부(진입 순간 순위는 확인됨)",),
            captured["unverifiable_items"],
        )


if __name__ == "__main__":
    unittest.main()
