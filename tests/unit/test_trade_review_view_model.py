from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from kiwoom_monitor.application.personal_trade_rules import StructuredTradeRule
from kiwoom_monitor.application.strategy_pack import StrategyPackManifest, default_strategy_pack
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.application.trade_review_analysis import TradeReviewAnalysis
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.trade_strategy_coordinator import CycleTypeSelection
from kiwoom_monitor.presentation.trade_review_view_model import (
    TradeHistoryEpisodeDisplay,
    build_trade_analysis_view_model,
    build_trade_history_summary_view_model,
)


class TradeReviewViewModelTests(unittest.TestCase):
    def test_builds_history_summary_with_actual_cost_and_cycle_override(self) -> None:
        at = datetime(2026, 9, 10, 9, 10)
        fills = (
            TradeFill("1", "000001", "테스트", "매수", at, 100, 1000),
            TradeFill("2", "000001", "테스트", "매도", at + timedelta(minutes=2), 100, 1100),
        )
        episode = group_trade_episodes(fills)[0]
        costs = (
            DailyTradeCost(date(2026, 9, 10), date(2026, 9, 12), "000001", "매수", 100_000, 99_900, 100, 0, 100),
            DailyTradeCost(date(2026, 9, 10), date(2026, 9, 12), "000001", "매도", 110_000, 109_800, 0, 200, 200),
        )
        setup = TradeSetupClassification("1차 돌파 · 2차 눌림", 70, ())

        model = build_trade_history_summary_view_model(
            episodes=(episode,), visible_fills=fills, visible_costs=costs,
            selected_start=date(2026, 9, 10), selected_end=date(2026, 9, 10),
            estimated_buy_cost_rate=0.015, estimated_sell_cost_rate=0.215,
            episode_displays={
                episode.group_id: TradeHistoryEpisodeDisplay(
                    (setup, ""), {1: "상따"}, "작성 완료", "확정",
                ),
            },
        )

        self.assertEqual(
            "거래일 1일  ·  매매 묶음 1건  ·  승률 100.0%  ·  추정 실현손익 예정 +10,000원  ·  "
            "비용 반영 예정 +9,700원(실제 비용 1/1건)",
            model.period_summary_text,
        )
        self.assertEqual("300원", model.rows[0].values[8])
        self.assertEqual("+9,700원", model.rows[0].values[9])
        self.assertEqual("확정", model.rows[0].values[12])
        self.assertEqual("작성 완료", model.rows[0].values[13])
        self.assertEqual("1차 돌파 · 2차 상따", model.rows[0].values[14])
        self.assertEqual("#D32F2F", model.rows[0].profit_color)

    def test_builds_history_summary_with_estimated_cost_without_settlement(self) -> None:
        at = datetime(2026, 9, 9, 9, 10)
        fills = (
            TradeFill("1", "000001", "테스트", "매수", at, 100, 1000),
            TradeFill("2", "000001", "테스트", "매도", at + timedelta(minutes=2), 100, 1100),
        )
        episode = group_trade_episodes(fills)[0]

        model = build_trade_history_summary_view_model(
            episodes=(episode,), visible_fills=fills, visible_costs=(),
            selected_start=date(2026, 9, 9), selected_end=date(2026, 9, 9),
            estimated_buy_cost_rate=0.015, estimated_sell_cost_rate=0.215,
            episode_displays={
                episode.group_id: TradeHistoryEpisodeDisplay(None, {}, "미작성", "미조회"),
            },
        )

        self.assertEqual("예정 251원 · 매수 0.015% / 매도 0.215%", model.rows[0].values[8])
        self.assertEqual("예정 +9,749원", model.rows[0].values[9])
        self.assertEqual("분석 전", model.rows[0].values[14])
        self.assertIn("비용 반영 예정 +9,749원(실제 비용 0/1건)", model.period_summary_text)

    def test_builds_setup_rows_status_and_analysis_document_without_qt(self) -> None:
        at = datetime(2026, 9, 10, 9, 10)
        cycle = group_trade_episodes((
            TradeFill("1", "000001", "테스트", "매수", at, 1, 1000),
            TradeFill("2", "000001", "테스트", "매도", at + timedelta(minutes=2), 1, 1010),
        ))[0]
        overall = TradeSetupClassification("돌파", 80, ("고점 돌파",), subtype="기본 분류")
        cycle_setup = TradeSetupClassification("돌파", 80, ("고점 돌파",), subtype="세부 돌파")
        selection = CycleTypeSelection(("돌파",), "돌파", (), None)
        analysis = TradeReviewAnalysis(
            120, 1000, 1010, 1.0, 2.0, -1.0, None, None,
            ("당일매매",), True, 80, (), (), (), 100, "기준 충족", 1,
        )
        rule = StructuredTradeRule(1, "주도주", "핵심", "", "core_rule", "돌파", "원칙")

        model = build_trade_analysis_view_model(
            overall_setup=overall, pack_results=("기본분석 돌파 80%(최종)",),
            type_selection=selection, cycles=(cycle,), cycle_setups=(cycle_setup,),
            analyses=(analysis,), entry_snapshots=(), linked_news=(),
            active_pack=default_strategy_pack(), packs=(), structured_rules=(rule,),
            personal_rule_count=1, draft_rule_counts={}, total_return_rate=1.0,
        )

        self.assertIn("예상 매매유형: 돌파", model.setup_summary_text)
        self.assertIn("기본·전략팩 결과: 기본분석 돌파 80%(최종)", model.setup_summary_text)
        self.assertEqual("자동 판정 사용", model.cycle_rows[0].override_value)
        self.assertEqual("세부 돌파", model.cycle_rows[0].subtype)
        self.assertIn("개인원칙 강의구조 연결 · 1강 1문단", model.data_status_text)
        self.assertIn("하루 종합 · 자동 확인 가능한 설정 기준 충족", model.analysis_text)
        self.assertIn("[1차 · 돌파] 전략팩: 미모사 기본 전략팩", model.analysis_text)

    def test_custom_override_and_enabled_pack_types_are_exposed(self) -> None:
        at = datetime(2026, 9, 10, 10, 0)
        cycle = group_trade_episodes((TradeFill("1", "1", "테스트", "매수", at, 1, 1000),))[0]
        pack = StrategyPackManifest(
            "custom", "눌림팩", 1, True, 20, ("깊은 눌림",), ("분봉",),
            source_description="사용자 강의", review_status="approved",
        )
        setup = TradeSetupClassification("깊은 눌림", 75, (), subtype="눌림팩 · 조건")
        selection = CycleTypeSelection(("깊은 눌림",), "깊은 눌림", ((0, "깊은 눌림"),), None)
        analysis = TradeReviewAnalysis(
            0, 1000, 0, 0, None, None, None, None, (), False, 40,
            (), (), (), 0, "기준 없음", 0,
        )

        model = build_trade_analysis_view_model(
            overall_setup=setup, pack_results=(), type_selection=selection,
            cycles=(cycle,), cycle_setups=(setup,), analyses=(analysis,),
            entry_snapshots=(), linked_news=(), active_pack=default_strategy_pack(),
            packs=(pack,), structured_rules=(), personal_rule_count=0,
            draft_rule_counts={"custom": 12}, total_return_rate=0,
        )

        self.assertIn("깊은 눌림", model.available_types)
        self.assertEqual("깊은 눌림", model.cycle_rows[0].override_value)
        self.assertIn("[1차 · 깊은 눌림] 전략팩: 눌림팩", model.analysis_text)
        self.assertIn("구조화 강의항목 12개", model.analysis_text)


if __name__ == "__main__":
    unittest.main()
