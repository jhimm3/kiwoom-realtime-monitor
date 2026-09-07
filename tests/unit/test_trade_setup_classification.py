from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, trade_fill_key
from kiwoom_monitor.application.trade_setup_classification import (
    TRADE_SETUP_TYPES, classify_trade_setup, classify_trade_setup_cycles, normalize_trade_setup_type,
)


class TradeSetupClassificationTests(unittest.TestCase):
    def test_basic_shapes_are_available_as_manual_types(self) -> None:
        for value in ("돌파", "눌림", "시가베팅", "상따"):
            self.assertIn(value, TRADE_SETUP_TYPES)
            self.assertEqual(value, normalize_trade_setup_type(value))

    def _episode(self, at: datetime, price: int = 110, market: str = "") -> object:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", at, 10, price, "", market),
            TradeFill("2", "005930", "삼성전자", "매도", at + timedelta(minutes=2), 10, price + 1, "", market),
        )
        return group_trade_episodes(fills)[0]

    def test_classifies_opening_trade(self) -> None:
        at = datetime(2026, 8, 29, 9, 5, 30)
        rows = (("2026-08-29T09:00", 100, 110, 99, 105, 1, 1.0, "confirmed"),)
        result = classify_trade_setup(self._episode(at, 105), rows)
        self.assertEqual("기타", result.setup_type)

    def test_classifies_breakout_over_prior_high(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:58", 99, 100, 98, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 100, 105, 99, 104, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 104, 110, 103, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 109, 112, 108, 111, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 110), rows)
        self.assertEqual("주도주 돌파", result.setup_type)
        self.assertGreater(result.confidence, 70)
        self.assertIn("고점", result.subtype)
        self.assertTrue(any("시장 주도주" in value for value in result.unverifiable))

    def test_breakout_more_than_one_percent_above_prior_high_is_extension(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:58", 99, 100, 98, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 100, 105, 99, 104, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 104, 105, 103, 104, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 104, 110, 104, 109, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 108), rows)
        self.assertEqual("돌파 후 확장 진입 후보", result.subtype)
        self.assertTrue(result.warnings)

    def test_breakout_without_box_or_sustained_turnover_keeps_lecture_warnings(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:58", 99, 103, 98, 102, 1, 2.0, "confirmed"),
            ("2026-08-29T09:59", 102, 106, 101, 105, 1, 3.0, "confirmed"),
            ("2026-08-29T10:00", 105, 110, 104, 109, 1, 4.0, "confirmed"),
            ("2026-08-29T10:01", 109, 112, 108, 111, 1, 5.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 110), rows)
        self.assertEqual("직전 고점 돌파 후보", result.subtype)
        self.assertTrue(any("박스권·삼각수렴·W형" in value for value in result.warnings))
        self.assertTrue(any("거래대금의 지속성" in value for value in result.warnings))

    def test_box_breakout_with_sustained_turnover_is_high_confidence(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:56", 100, 101, 99, 100, 1, 45.0, "confirmed"),
            ("2026-08-29T09:57", 100, 101, 99, 100, 1, 42.0, "confirmed"),
            ("2026-08-29T09:58", 100, 101, 99, 100, 1, 44.0, "confirmed"),
            ("2026-08-29T09:59", 100, 101, 99, 100, 1, 43.0, "confirmed"),
            ("2026-08-29T10:00", 100, 101, 99, 100, 1, 46.0, "confirmed"),
            ("2026-08-29T10:01", 101, 103, 100, 102, 1, 50.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 101), rows)
        self.assertEqual("박스권 고점 돌파 후보", result.subtype)
        self.assertEqual(84, result.confidence)
        self.assertTrue(any("5개 분봉" in value for value in result.evidence))

    def test_triangle_convergence_breakout_is_distinguished(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:55", 100, 106, 94, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T09:56", 101, 105, 95, 100, 1, 45.0, "confirmed"),
            ("2026-08-29T09:57", 100, 104, 96, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T09:58", 101, 103, 97, 100, 1, 45.0, "confirmed"),
            ("2026-08-29T09:59", 100, 102, 98, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T10:00", 101, 102, 99, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T10:01", 102, 108, 101, 107, 1, 50.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 106), rows)
        self.assertEqual("삼각수렴 후 고점 돌파 후보", result.subtype)
        self.assertFalse(any("매물 소화는 자동 확인되지" in value for value in result.warnings))

    def test_w_recovery_breakout_is_distinguished(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:52", 100, 104, 99, 103, 1, 45.0, "confirmed"),
            ("2026-08-29T09:53", 103, 104, 96, 97, 1, 45.0, "confirmed"),
            ("2026-08-29T09:54", 97, 102, 97, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T09:55", 101, 103, 100, 102, 1, 45.0, "confirmed"),
            ("2026-08-29T09:56", 102, 103, 96.5, 97, 1, 45.0, "confirmed"),
            ("2026-08-29T09:57", 97, 101, 97, 100, 1, 45.0, "confirmed"),
            ("2026-08-29T09:58", 100, 102, 99, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T09:59", 101, 102, 100, 101, 1, 45.0, "confirmed"),
            ("2026-08-29T10:00", 101, 103, 100, 102, 1, 45.0, "confirmed"),
            ("2026-08-29T10:01", 103, 106, 102, 105, 1, 50.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 104), rows)
        self.assertEqual("W형 회복 후 고점 돌파 후보", result.subtype)

    def test_completed_entry_minute_high_does_not_leak_into_entry_context(self) -> None:
        at = datetime(2026, 8, 29, 10, 1, 20)
        rows = (
            ("2026-08-29T09:58", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 100, 102, 99, 101, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 101, 102, 100, 101, 1, 1.0, "confirmed"),
            # The high happened sometime during the entry minute and may be after the fill.
            ("2026-08-29T10:01", 101, 120, 90, 110, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 101), rows)
        self.assertNotIn("120", " ".join(result.evidence))

    def test_two_prior_minutes_are_not_called_thirty_minute_breakout(self) -> None:
        at = datetime(2026, 8, 29, 9, 2, 20)
        rows = (
            ("2026-08-29T09:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:01", 100, 102, 100, 101, 1, 1.0, "confirmed"),
            ("2026-08-29T09:02", 101, 110, 101, 109, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 103), rows)
        self.assertNotEqual("직전 30분 고점 돌파 후보", result.subtype)

    def test_classifies_new_listing_when_only_daily_bar_is_trade_day(self) -> None:
        at = datetime(2026, 8, 29, 10, 1)
        rows = (("2026-08-29T10:00", 100, 105, 99, 104, 1, 1.0, "confirmed"),)
        daily_rows = (("2026-08-29T00:00", 100, 120, 90, 110, 1000, 50.0, "daily_confirmed"),)
        result = classify_trade_setup(self._episode(at, 104), rows, daily_rows)
        self.assertEqual("신규주", result.setup_type)
        self.assertIn("일봉", " ".join(result.evidence))
        self.assertEqual("IPO 신규주 후보", result.subtype)
        self.assertTrue(any("지속적인 거래대금" in value for value in result.warnings))

    def test_new_listing_keeps_intraday_falling_stock_shape(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 100, 130, 99, 125, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 112, 114, 108, 110, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 108, 110, 105, 107, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 107, 109, 106, 108, 1, 1.0, "confirmed"),
        )
        daily_rows = (("2026-08-29T00:00", 100, 130, 99, 108, 1000, 50.0, "daily"),)
        result = classify_trade_setup(self._episode(at, 108), rows, daily_rows)
        self.assertEqual("신규주", result.setup_type)
        self.assertIn("급락 반등", result.subtype)
        self.assertTrue(result.unverifiable)

    def test_high_proximity_entry_is_flagged_as_lesson_warning(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 100, 100, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:55", 100, 110, 100, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T09:56", 109, 110, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T09:57", 109, 110, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T09:58", 109, 110, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 109, 110, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 109, 110, 108.5, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 109, 109.5, 108.5, 109, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 109), rows)
        self.assertEqual("주도주 돌파", result.setup_type)
        self.assertEqual("박스권 상단 선진입 후보", result.subtype)
        self.assertTrue(result.warnings)

    def test_sharp_rise_near_high_is_chase_candidate_not_breakout(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:59", 100, 102, 99, 101, 1, 10.0, "confirmed"),
            ("2026-08-29T10:00", 101, 108, 101, 107, 1, 20.0, "confirmed"),
            ("2026-08-29T10:01", 107, 115, 106, 114, 1, 30.0, "confirmed"),
            ("2026-08-29T10:02", 114, 120, 113, 119, 1, 35.0, "confirmed"),
            ("2026-08-29T10:03", 119, 120, 117, 118, 1, 35.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 118), rows)
        self.assertEqual("급등 후 고점 추격 후보", result.subtype)
        self.assertTrue(any("후기 발산" in value for value in result.warnings))

    def test_entry_between_one_and_two_percent_below_high_is_not_left_as_other(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 100, 100, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 100, 120, 100, 119, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 119, 119, 118, 118, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 118, 118.5, 118, 118, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 118, 118.5, 118, 118, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 118), rows)
        self.assertEqual("주도주 돌파", result.setup_type)
        self.assertEqual("급등 후 고점 추격 후보", result.subtype)

    def test_nxt_opening_minutes_use_saved_session_start_not_nine_oclock(self) -> None:
        at = datetime(2026, 8, 29, 8, 25)
        rows = tuple(
            (f"2026-08-29T08:{minute:02d}", 100, 101, 99, 100, 1, 1.0, "confirmed")
            for minute in range(26)
        )
        result = classify_trade_setup(self._episode(at, 100, "NXT"), rows)
        self.assertFalse(any("장 시작 후 0분" in value for value in result.evidence))

    def test_nxt_afternoon_is_not_mistaken_for_closing_bet(self) -> None:
        at = datetime(2026, 8, 29, 16, 0)
        rows = (
            ("2026-08-29T08:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T15:59", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T16:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 100, "NXT"), rows)
        self.assertNotEqual("종가베팅", result.setup_type)

    def test_sor_after_nine_keeps_nxt_flow_but_uses_krx_open_for_opening_label(self) -> None:
        at = datetime(2026, 8, 29, 9, 2)
        rows = (
            ("2026-08-29T08:00", 80, 85, 79, 84, 1, 1.0, "confirmed"),
            ("2026-08-29T09:00", 100, 102, 99, 101, 1, 1.0, "confirmed"),
            ("2026-08-29T09:02", 102, 104, 101, 103, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 103, "SOR"), rows)
        self.assertTrue(any("KRX 정규장 시작(09:00)" in value for value in result.evidence))
        self.assertTrue(any("+3.0%" in value for value in result.evidence))

    def test_sor_after_nine_keeps_eight_oclock_high_in_intraday_structure(self) -> None:
        at = datetime(2026, 8, 29, 10, 0)
        rows = (
            ("2026-08-29T08:00", 100, 130, 99, 125, 1, 1.0, "confirmed"),
            ("2026-08-29T09:00", 110, 112, 108, 111, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 106, 108, 105, 107, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 107, 108, 106, 107, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 107, "SOR"), rows)
        self.assertEqual("낙주", result.setup_type)
        self.assertTrue(any("당일 고점 대비" in value for value in result.evidence))

    def test_opening_rise_above_five_percent_is_separate_candidate(self) -> None:
        at = datetime(2026, 8, 29, 9, 2)
        rows = (
            ("2026-08-29T09:00", 100, 104, 99, 103, 1, 1.0, "confirmed"),
            ("2026-08-29T09:01", 103, 110, 102, 108, 1, 1.0, "confirmed"),
            ("2026-08-29T09:02", 108, 120, 107, 119, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 110, "KRX"), rows)
        self.assertEqual("장 초반 급등 진입 후보", result.subtype)

    def test_nxt_near_eight_pm_is_closing_bet(self) -> None:
        at = datetime(2026, 8, 29, 19, 45)
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", at, 10, 100, "", "NXT"),
            TradeFill("2", "005930", "삼성전자", "매도", at + timedelta(days=1, hours=-11), 10, 101, "", "NXT"),
        )
        episode = group_trade_episodes(fills)[0]
        rows = (
            ("2026-08-29T08:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T19:45", 100, 101, 99, 100, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(episode, rows)
        self.assertEqual("종가베팅", result.setup_type)
        self.assertTrue(any("큰 거래대금" in value for value in result.warnings))

    def test_closing_bet_warns_when_intraday_trend_has_broken(self) -> None:
        at = datetime(2026, 8, 29, 19, 45)
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", at, 10, 100, "", "NXT"),
            TradeFill("2", "005930", "삼성전자", "매도", at + timedelta(days=1, hours=-11), 10, 101, "", "NXT"),
        )
        rows = (
            ("2026-08-29T08:00", 100, 120, 99, 118, 1, 50.0, "confirmed"),
            ("2026-08-29T19:44", 101, 102, 99, 100, 1, 45.0, "confirmed"),
            ("2026-08-29T19:45", 100, 101, 99, 100, 1, 45.0, "confirmed"),
        )
        result = classify_trade_setup(group_trade_episodes(fills)[0], rows)
        self.assertEqual("종가베팅", result.setup_type)
        self.assertTrue(any("살아 있는 추세" in value for value in result.warnings))

    def test_same_day_exit_near_close_is_not_closing_bet(self) -> None:
        at = datetime(2026, 8, 29, 19, 45)
        rows = (
            ("2026-08-29T08:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T19:45", 100, 101, 99, 100, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 100, "NXT"), rows)
        self.assertNotEqual("종가베팅", result.setup_type)

    def test_distinguishes_daily_oversold_from_intraday_falling_stock(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-28T15:29", 72, 73, 71, 72, 1, 1.0, "confirmed"),
            ("2026-08-29T09:00", 72, 72, 70, 71, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 71, 72, 70, 72, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 72, 73, 72, 73, 1, 1.0, "confirmed"),
        )
        daily_rows = tuple(
            (f"2026-08-{day:02d}T00:00", 95, 100, 90, 95, 1000, 50.0, "daily")
            for day in range(1, 21)
        )
        result = classify_trade_setup(self._episode(at, 73), rows, daily_rows)
        self.assertEqual("과대낙폭", result.setup_type)
        self.assertIn("일봉", result.subtype)
        self.assertTrue(result.warnings)

    def test_daily_oversold_keeps_breakout_entry_shape(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 70, 71, 69, 70, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 70, 72, 70, 71, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 71, 73, 71, 72, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 72, 74, 72, 73, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 73, 75, 73, 74, 1, 1.0, "confirmed"),
        )
        daily_rows = tuple(
            (f"2026-08-{day:02d}T00:00", 95, 100, 90, 95, 1000, 50.0, "daily")
            for day in range(1, 21)
        )
        result = classify_trade_setup(self._episode(at, 74), rows, daily_rows)
        self.assertEqual("과대낙폭", result.setup_type)
        self.assertIn("과대낙폭 구간", result.subtype)
        self.assertIn("고점 돌파", result.subtype)

    def test_immediate_pullback_without_completed_recovery_bar_is_not_unclassified(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 105, 108, 104, 107, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 107, 110, 106, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 109, 112, 108, 111, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 110, 111, 106, 107, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 107), rows)
        self.assertEqual("주도주 돌파", result.setup_type)
        self.assertEqual("상승 후 조정 즉시 진입 후보", result.subtype)
        self.assertLess(result.confidence, 50)

    def test_intraday_collapse_is_falling_stock_even_if_above_previous_close(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-28T15:29", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:00", 110, 125, 109, 124, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 124, 120, 108, 110, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 110, 113, 108, 112, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 112, 114, 111, 113, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 113), rows)
        self.assertEqual("낙주", result.setup_type)
        self.assertIn("당일 고점", " ".join(result.evidence))

    def test_intraday_collapse_without_rebound_is_early_falling_stock_entry(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 110, 125, 109, 124, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 124, 111, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 109, 110, 108, 109, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 109), rows)
        self.assertEqual("낙주", result.setup_type)
        self.assertEqual("투매 진행 중 선진입 후보", result.subtype)
        self.assertTrue(result.warnings)

    def test_one_old_daily_bar_does_not_classify_trade_day_as_new_listing(self) -> None:
        at = datetime(2026, 8, 29, 10, 1)
        rows = (("2026-08-29T10:00", 100, 105, 99, 104, 1, 1.0, "confirmed"),)
        daily_rows = (("2026-08-28T00:00", 100, 120, 90, 110, 1000, 50.0, "daily_confirmed"),)
        result = classify_trade_setup(self._episode(at, 104), rows, daily_rows)
        self.assertNotEqual("신규주", result.setup_type)

    def test_manually_combined_reentries_are_classified_per_cycle(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 9, 5), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9, 6), 10, 101),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 10, 110),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 1), 10, 111),
        )
        overrides = {trade_fill_key(fill): "manual:two-cycles" for fill in fills}
        episode = group_trade_episodes(fills, overrides)[0]
        rows = tuple(
            (f"2026-08-29T09:{minute:02d}", 100, 100 + minute / 5, 99, 100 + minute / 5, 10, 1.0, "confirmed")
            for minute in range(60)
        ) + (
            ("2026-08-29T10:00", 109, 111, 108, 110, 10, 1.0, "confirmed"),
            ("2026-08-29T10:01", 110, 112, 109, 111, 10, 1.0, "confirmed"),
        )
        result = classify_trade_setup(episode, rows)
        self.assertTrue(result.setup_type.startswith("1차 "))
        self.assertIn(" · 2차 ", result.setup_type)
        self.assertTrue(any(value.startswith("1차:") for value in result.evidence))
        self.assertTrue(any(value.startswith("2차:") for value in result.evidence))

    def test_high_reentry_after_breakout_keeps_prior_cycle_context(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 0), 10, 110),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 1), 10, 111),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 29, 10, 10), 10, 111),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 10, 11), 10, 110),
        )
        overrides = {trade_fill_key(fill): "manual:reentry" for fill in fills}
        episode = group_trade_episodes(fills, overrides)[0]
        rows = (
            ("2026-08-29T09:57", 108, 108, 107, 108, 1, 1.0, "confirmed"),
            ("2026-08-29T09:58", 108, 109, 108, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T09:59", 109, 110, 109, 110, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 108, 111, 108, 110, 1, 1.0, "confirmed"),
            ("2026-08-29T10:09", 110, 112, 109, 111, 1, 1.0, "confirmed"),
            ("2026-08-29T10:10", 111, 112, 110, 111, 1, 1.0, "confirmed"),
        )
        results = classify_trade_setup_cycles(episode, rows)
        self.assertEqual("주도주 돌파", results[0].setup_type)
        self.assertEqual("돌파 후 고점 재진입 후보", results[1].subtype)

    def test_pullback_uses_low_after_peak_not_earlier_session_low(self) -> None:
        at = datetime(2026, 8, 29, 10, 3)
        rows = (
            ("2026-08-29T09:00", 100, 101, 90, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 100, 110, 100, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 109, 109, 106, 107, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 107, 107, 106, 106, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 106, 106, 106, 106, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 106), rows)
        self.assertNotIn("눌림", result.setup_type)

    def test_weak_rebound_does_not_claim_pullback_recovery(self) -> None:
        at = datetime(2026, 8, 29, 10, 4)
        rows = (
            ("2026-08-29T09:00", 100, 101, 99, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T10:00", 100, 110, 100, 109, 1, 1.0, "confirmed"),
            ("2026-08-29T10:01", 109, 108, 104, 105, 1, 1.0, "confirmed"),
            ("2026-08-29T10:02", 105, 106, 104, 105, 1, 1.0, "confirmed"),
            ("2026-08-29T10:03", 105, 106, 104, 105, 1, 1.0, "confirmed"),
            ("2026-08-29T10:04", 105, 106, 105, 106, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 106), rows)
        self.assertNotEqual("눌림 후 재상승 후보", result.subtype)
        self.assertEqual("눌림 구간 선진입 후보", result.subtype)
        self.assertTrue(result.warnings)

    def test_opening_breakout_is_classified_by_price_context_first(self) -> None:
        at = datetime(2026, 8, 29, 9, 5)
        rows = (
            ("2026-08-29T09:02", 99, 100, 98, 100, 1, 1.0, "confirmed"),
            ("2026-08-29T09:03", 100, 103, 99, 102, 1, 1.0, "confirmed"),
            ("2026-08-29T09:04", 102, 105, 102, 104, 1, 1.0, "confirmed"),
            ("2026-08-29T09:05", 104, 108, 104, 107, 1, 1.0, "confirmed"),
        )
        result = classify_trade_setup(self._episode(at, 106), rows)
        self.assertEqual("주도주 돌파", result.setup_type)


if __name__ == "__main__":
    unittest.main()
