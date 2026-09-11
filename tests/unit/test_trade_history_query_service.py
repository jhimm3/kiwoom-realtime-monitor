from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from kiwoom_monitor.application.trade_history_query_service import (
    TradeHistoryQueryService,
    backfill_affects_history_selection,
    history_backfill_cutoff,
    incomplete_trade_day_tasks,
    select_history_backfill_tasks,
    summarize_episode_bar_state,
    trade_episode_selection,
    trade_fill_selection,
)
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeReview


class FakeRepository:
    def __init__(self, fills: tuple[TradeFill, ...]) -> None:
        self.fills = fills
        self.reviews: dict[str, str] = {}
        self.fill_range: tuple[datetime, datetime] | None = None
        self.cost_range: tuple[datetime, datetime] | None = None

    def load_fills_with_entry_context(self, start: datetime, end: datetime, lookback_days: int = 365):
        self.fill_range = (start, end)
        return self.fills

    def load_trade_costs(self, start: datetime, end: datetime):
        self.cost_range = (start, end)
        return ()

    def load_group_overrides(self):
        return {}

    def load_review(self, group_id: str):
        return TradeReview(group_id, status=self.reviews.get(group_id, "미작성"))


def fill(order: str, code: str, name: str, side: str, at: datetime, price: int) -> TradeFill:
    return TradeFill(order, code, name, side, at, 1, price)


class TradeHistoryQueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 9, 1)
        self.end = datetime(2026, 9, 2)

    def test_prior_entry_is_kept_for_current_sell_but_old_completed_episode_is_hidden(self) -> None:
        repository = FakeRepository((
            fill("1", "A", "연결종목", "매수", self.start - timedelta(days=3), 100),
            fill("2", "A", "연결종목", "매도", self.start + timedelta(hours=10), 110),
            fill("3", "B", "과거종목", "매수", self.start - timedelta(days=2), 100),
            fill("4", "B", "과거종목", "매도", self.start - timedelta(days=2, hours=-1), 90),
        ))

        result = TradeHistoryQueryService(repository).load(self.start, self.end)

        self.assertEqual(("A",), tuple(value.summary.stock_code for value in result.episodes))
        self.assertEqual(self.start - timedelta(days=365), repository.cost_range[0])

    def test_name_result_and_review_filters_keep_existing_meanings(self) -> None:
        repository = FakeRepository((
            fill("1", "A", "수익종목", "매수", self.start + timedelta(hours=9), 100),
            fill("2", "A", "수익종목", "매도", self.start + timedelta(hours=10), 110),
            fill("3", "B", "손실종목", "매수", self.start + timedelta(hours=9), 100),
            fill("4", "B", "손실종목", "매도", self.start + timedelta(hours=10), 90),
        ))
        service = TradeHistoryQueryService(repository)
        episodes = service.load(self.start, self.end).episodes
        profit = next(value for value in episodes if value.summary.stock_code == "A")
        repository.reviews[profit.group_id] = "완료"

        self.assertEqual((profit,), service.filter(episodes, query="수익", result_filter="수익"))
        self.assertEqual((profit,), service.filter(episodes, review_filter="완료"))
        self.assertEqual("B", service.filter(episodes, result_filter="손실")[0].summary.stock_code)

    def test_episode_selection_keeps_all_cycle_days_and_earliest_fill_as_focus(self) -> None:
        fills = (
            fill("2", "A", "다일종목", "매도", self.start + timedelta(days=1, hours=10), 110),
            fill("1", "A", "다일종목", "매수", self.start + timedelta(hours=9), 100),
        )
        episode = TradeHistoryQueryService(FakeRepository(fills)).load(
            self.start, self.end + timedelta(days=1),
        ).episodes[0]

        selection = trade_episode_selection(episode)

        self.assertEqual("A", selection.stock_code)
        self.assertEqual(self.start.date(), selection.selected_day)
        self.assertEqual(self.start + timedelta(hours=9), selection.focus)
        self.assertEqual((self.start.date(), (self.start + timedelta(days=1)).date()), selection.date_range)
        self.assertEqual(("1", "2"), tuple(value.order_no for value in selection.fills))

    def test_fill_selection_prefers_active_episode_then_falls_back_to_same_stock_day(self) -> None:
        selected = fill("2", "A", "선택종목", "매도", self.start + timedelta(hours=10), 110)
        prior = fill("1", "A", "선택종목", "매수", self.start - timedelta(days=1, hours=-9), 100)
        same_day = fill("3", "A", "선택종목", "매수", self.start + timedelta(hours=9), 105)
        other = fill("4", "B", "다른종목", "매수", self.start + timedelta(hours=9), 50)
        active_episode = TradeHistoryQueryService(FakeRepository((prior, selected))).load(
            self.start, self.end,
        ).episodes[0]

        active = trade_fill_selection(
            selected, visible_fills=(same_day, selected, other), active_episode=active_episode,
        )
        fallback = trade_fill_selection(
            selected, visible_fills=(other, selected, same_day), active_episode=None,
        )

        self.assertEqual(("1", "2"), tuple(value.order_no for value in active.fills))
        self.assertEqual((prior.filled_at.date(), selected.filled_at.date()), active.date_range)
        self.assertEqual(("3", "2"), tuple(value.order_no for value in fallback.fills))
        self.assertEqual((selected.filled_at.date(), selected.filled_at.date()), fallback.date_range)

    def test_backfill_cutoff_includes_today_only_after_20(self) -> None:
        self.assertEqual(date(2026, 9, 10), history_backfill_cutoff(datetime(2026, 9, 10, 19, 59)))
        self.assertEqual(date(2026, 9, 11), history_backfill_cutoff(datetime(2026, 9, 10, 20, 0)))

    def test_backfill_task_filter_preserves_candidates_and_failed_only_rule(self) -> None:
        first = ("A", date(2026, 9, 8))
        second = ("B", date(2026, 9, 8))
        third = ("C", date(2026, 9, 9))
        states = {first: "확정", second: "실패", third: "일부"}

        self.assertEqual((second, third), select_history_backfill_tasks((first, second, third), states))
        self.assertEqual((second,), select_history_backfill_tasks((first, second, third), states, failed_only=True))

    def test_episode_bar_state_keeps_existing_priority(self) -> None:
        self.assertEqual("확정", summarize_episode_bar_state(("확정", "확정")))
        self.assertEqual("실패 1건", summarize_episode_bar_state(("확정", "실패", "일부")))
        self.assertEqual("일부 1/2", summarize_episode_bar_state(("확정", "미조회")))
        self.assertEqual("일부", summarize_episode_bar_state(("일부", "미조회")))
        self.assertEqual("미조회", summarize_episode_bar_state(()))

    def test_incomplete_trade_days_detect_missing_or_uncovered_fill_times(self) -> None:
        first_day = date(2026, 9, 8)
        second_day = date(2026, 9, 9)
        fills = (
            fill("1", "A", "종목", "매수", datetime(2026, 9, 8, 9, 1, 30), 100),
            fill("2", "A", "종목", "매도", datetime(2026, 9, 8, 9, 3), 110),
            fill("3", "A", "종목", "매수", datetime(2026, 9, 9, 10), 100),
        )
        incomplete = incomplete_trade_day_tasks(
            "A",
            fills,
            {
                first_day: (
                    ("2026-09-08T09:01:00",),
                    ("2026-09-08T09:02:00",),
                ),
                second_day: (),
            },
        )
        covered = incomplete_trade_day_tasks(
            "A",
            fills[:2],
            {first_day: (("2026-09-08T09:01:00",), ("2026-09-08T09:03:00",))},
        )

        self.assertEqual((("A", first_day), ("A", second_day)), incomplete)
        self.assertEqual((), covered)

    def test_backfill_only_affects_matching_stock_inside_selected_range(self) -> None:
        selected_range = (date(2026, 9, 8), date(2026, 9, 10))

        self.assertTrue(backfill_affects_history_selection(
            "A", date(2026, 9, 9), selected_code="A", selected_range=selected_range,
        ))
        self.assertFalse(backfill_affects_history_selection(
            "B", date(2026, 9, 9), selected_code="A", selected_range=selected_range,
        ))
        self.assertFalse(backfill_affects_history_selection(
            "A", date(2026, 9, 11), selected_code="A", selected_range=selected_range,
        ))
        self.assertFalse(backfill_affects_history_selection(
            "A", date(2026, 9, 9), selected_code="A", selected_range=None,
        ))


if __name__ == "__main__":
    unittest.main()
