from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, trade_fill_key
from kiwoom_monitor.application.trade_review_analysis import analyze_trade_episode


class TradeReviewAnalysisTests(unittest.TestCase):
    def test_fill_seconds_use_the_containing_minute_bar(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1, 35), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 3, 42), 10, 108),
        )
        rows = (
            ("2026-08-29T09:01", 100, 105, 95, 102, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:02", 102, 110, 101, 109, 20, 2.0, "api_confirmed"),
            ("2026-08-29T09:03", 109, 112, 107, 108, 30, 3.0, "api_confirmed"),
        )
        result = analyze_trade_episode(group_trade_episodes(fills)[0], rows)
        self.assertTrue(result.complete)
        self.assertEqual(90, result.confidence)

    def test_calculates_holding_mfe_mae_and_exit_efficiency(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 3), 10, 108),
        )
        episode = group_trade_episodes(fills)[0]
        rows = (
            ("2026-08-29T09:01", 100, 105, 95, 102, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:02", 102, 110, 101, 109, 20, 2.0, "api_confirmed"),
            ("2026-08-29T09:03", 109, 112, 107, 108, 30, 3.0, "api_confirmed"),
        )
        result = analyze_trade_episode(episode, rows)
        self.assertEqual(120, result.holding_seconds)
        self.assertAlmostEqual(10.0, result.max_favorable_rate)
        self.assertAlmostEqual(-5.0, result.max_adverse_rate)
        self.assertAlmostEqual((108 - 95) / (110 - 95) * 100, result.exit_efficiency)
        self.assertTrue(result.complete)
        self.assertEqual(90, result.confidence)
        self.assertGreaterEqual(result.discipline_score, 0)
        self.assertTrue(result.verdict)
        self.assertTrue(result.program_warnings)
        combined = " ".join((*result.positive_points, *result.negative_points, *result.next_actions, *result.labels))
        self.assertNotIn("실현손익이", combined)
        self.assertNotIn("낮은 가격대에서 청산", combined)
        self.assertNotIn("뉴스·테마", combined)
        self.assertNotIn("개인원칙 문서", combined)

    def test_applies_supported_personal_rules_without_storing_rule_text(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1), 5, 104),
            TradeFill("2", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 2), 5, 104),
            TradeFill("3", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 3), 10, 100),
        )
        rows = (
            ("2026-08-29T09:01", 100, 105, 99, 104, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:02", 104, 105, 100, 102, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:03", 102, 103, 99, 100, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows,
            personal_rules=("급등 추격매수 금지", "분할매수 실행", "손절 3%"),
        )
        self.assertFalse(result.negative_points)
        self.assertFalse(any("개인 원칙에 맞게 매수" in value for value in result.positive_points))

    def test_document_background_sentences_are_not_echoed_as_manual_actions(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 3), 10, 101),
        )
        rows = (
            ("2026-08-29T09:01", 100, 101, 99, 100, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:02", 100, 102, 99, 101, 10, 1.0, "api_confirmed"),
            ("2026-08-29T09:03", 101, 102, 100, 101, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows,
            personal_rules=("주식과 캔들의 개념을 이해하는 것이 목표다.", "거래대금은 지속성을 함께 본다."),
        )
        self.assertFalse(any("수동 확인:" in value for value in result.next_actions))
        self.assertFalse(any("분봉 자료가 확보" in value for value in result.positive_points))

    def test_uses_configured_trade_value_threshold_before_entry(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 4, 30), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 5), 10, 101),
        )
        rows = tuple(
            (f"2026-08-29T09:0{minute}", 100, 101, 99, 100, 10, value, "api_confirmed")
            for minute, value in enumerate((5.0, 42.0, 55.0, 12.0, 60.0, 10.0))
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows, personal_rules=("거래대금 지속성을 본다",),
            trade_value_threshold_eok=40.0,
        )
        self.assertTrue(any("3개 분봉" in value and "40억" in value for value in result.user_setting_matches))

    def test_reentries_are_not_misclassified_as_split_buys_or_sells(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 2), 10, 101),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 3), 10, 102),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 4), 10, 103),
        )
        rows = tuple(
            (f"2026-08-29T09:0{minute}", 100, 104, 99, 101, 10, 1.0, "api_confirmed")
            for minute in range(1, 5)
        )
        overrides = {trade_fill_key(fill): "manual:reentries" for fill in fills}
        result = analyze_trade_episode(group_trade_episodes(fills, overrides)[0], rows)
        combined = " ".join((*result.labels, *result.positive_points))
        self.assertNotIn("분할매수", combined)
        self.assertNotIn("분할매도", combined)
        self.assertIn("재진입 2회", result.labels)

    def test_split_fills_are_counted_only_inside_one_open_position(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 1), 5, 100),
            TradeFill("2", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 2), 5, 101),
            TradeFill("3", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 3), 4, 102),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 4), 6, 103),
        )
        rows = tuple(
            (f"2026-08-29T09:0{minute}", 100, 104, 99, 101, 10, 1.0, "api_confirmed")
            for minute in range(1, 5)
        )
        result = analyze_trade_episode(group_trade_episodes(fills)[0], rows)
        self.assertIn("분할매수 2회", result.labels)
        self.assertIn("분할매도 2회", result.labels)

    def test_setup_confidence_caps_review_confidence_and_chase_warning_is_targeted(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 10, 104),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 1), 10, 105),
        )
        rows = (
            ("2026-08-29T10:00", 100, 105, 99, 104, 10, 1.0, "api_confirmed"),
            ("2026-08-29T10:01", 104, 106, 103, 105, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows, personal_rules=("추격매수 금지",),
            setup_type="기타", setup_confidence=42,
        )
        self.assertEqual(42, result.confidence)
        self.assertFalse(any("추격" in value for value in result.negative_points))

    def test_lower_price_add_is_not_automatically_praised(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 5, 100),
            TradeFill("2", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 1), 5, 95),
            TradeFill("3", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 2), 10, 96),
        )
        rows = tuple(
            (f"2026-08-29T10:0{minute}", 100, 101, 94, 96, 10, 1.0, "api_confirmed")
            for minute in range(3)
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows, personal_rules=("분할매수",),
            setup_type="눌림", setup_confidence=76,
        )
        self.assertIn("평단 낮추기", result.labels)
        self.assertFalse(any("물타기" in value for value in (*result.negative_points, *result.program_warnings, *result.program_notes)))
        self.assertTrue(any("후속 매수" in value for value in result.program_notes))
        self.assertFalse(any("개인 원칙의 분할 진입" in value for value in result.positive_points))

    def test_program_mae_alert_does_not_reduce_personal_rule_score(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 2), 10, 99),
        )
        rows = (
            ("2026-08-29T10:00", 100, 101, 95, 100, 10, 50.0, "api_confirmed"),
            ("2026-08-29T10:01", 100, 101, 94, 95, 10, 50.0, "api_confirmed"),
            ("2026-08-29T10:02", 95, 100, 94, 99, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows, personal_rules=("거래대금을 확인한다", "추격매수를 주의한다"),
            trade_value_threshold_eok=40.0, setup_type="주도주 돌파", setup_confidence=84,
        )
        self.assertEqual(100, result.discipline_score)
        self.assertEqual(1, result.evaluated_rule_count)
        self.assertTrue(result.program_warnings)
        self.assertFalse(result.negative_points)
        self.assertTrue(result.user_setting_matches)

    def test_keeps_verified_setup_and_unverifiable_lesson_context_separate(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 1), 10, 101),
        )
        rows = (
            ("2026-08-29T10:00", 100, 101, 99, 100, 10, 1.0, "api_confirmed"),
            ("2026-08-29T10:01", 100, 102, 100, 101, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(
            group_trade_episodes(fills)[0], rows, setup_type="주도주 돌파",
            setup_evidence=("직전 고점을 돌파했습니다.",),
            lesson_warnings=("고점 후기 발산 여부를 확인해야 합니다.",),
            unverifiable_items=("당시 시장 주도주 여부",),
        )
        self.assertEqual(("직전 고점을 돌파했습니다.",), result.setup_evidence)
        self.assertEqual(("고점 후기 발산 여부를 확인해야 합니다.",), result.lesson_warnings)
        self.assertEqual(("당시 시장 주도주 여부",), result.unverifiable_items)
        self.assertEqual(0, result.evaluated_rule_count)

    def test_ignores_pre_entry_and_post_exit_extremes_in_boundary_minutes(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0, 50), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 1, 10), 10, 102),
        )
        rows = (
            ("2026-08-29T10:00", 110, 120, 80, 101, 10, 1.0, "api_confirmed"),
            ("2026-08-29T10:01", 101, 130, 70, 103, 10, 1.0, "api_confirmed"),
        )
        result = analyze_trade_episode(group_trade_episodes(fills)[0], rows)
        self.assertAlmostEqual(2.0, result.max_favorable_rate)
        self.assertAlmostEqual(0.0, result.max_adverse_rate)


if __name__ == "__main__":
    unittest.main()
