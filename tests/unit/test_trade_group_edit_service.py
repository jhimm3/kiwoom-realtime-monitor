from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.trade_group_edit_service import TradeGroupEditService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, trade_fill_key


class FakeRepository:
    def __init__(self) -> None:
        self.assigned: list[tuple[tuple[str, ...], str]] = []
        self.cleared: list[tuple[str, ...]] = []

    def assign_group(self, fill_keys: tuple[str, ...], group_id: str) -> None:
        self.assigned.append((fill_keys, group_id))

    def clear_group_assignments(self, fill_keys: tuple[str, ...]) -> None:
        self.cleared.append(fill_keys)


def fill(order: str, code: str, side: str, at: datetime) -> TradeFill:
    return TradeFill(order, code, code, side, at, 1, 100)


class TradeGroupEditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = TradeGroupEditService(self.repository, lambda: "manual:fixed")
        self.at = datetime(2026, 9, 10, 9)

    def test_merge_validates_selection_and_stock_before_writing(self) -> None:
        first = group_trade_episodes((fill("1", "A", "매수", self.at),))[0]
        second = group_trade_episodes((fill("2", "A", "매수", self.at + timedelta(days=1)),))[0]
        other = group_trade_episodes((fill("3", "B", "매수", self.at),))[0]

        self.assertFalse(self.service.merge((first,)).changed)
        self.assertFalse(self.service.merge((first, other)).changed)
        result = self.service.merge((first, second))

        self.assertTrue(result.changed)
        self.assertEqual("매매 묶음 2개를 합쳤습니다.", result.message)
        self.assertEqual("manual:fixed", self.repository.assigned[0][1])
        self.assertEqual(
            (trade_fill_key(first.fills[0]), trade_fill_key(second.fills[0])),
            self.repository.assigned[0][0],
        )

    def test_split_validates_selection_and_stock_before_writing(self) -> None:
        first = fill("1", "A", "매수", self.at)
        second = fill("2", "A", "매도", self.at + timedelta(minutes=1))
        other = fill("3", "B", "매수", self.at)

        self.assertFalse(self.service.split(()).changed)
        self.assertFalse(self.service.split((first, other)).changed)
        result = self.service.split((first, second))

        self.assertTrue(result.changed)
        self.assertEqual("선택 체결 2건을 새 묶음으로 분리했습니다.", result.message)
        self.assertEqual("manual:fixed", self.repository.assigned[0][1])

    def test_reset_only_clears_selected_episode_fill_keys(self) -> None:
        episode = group_trade_episodes((
            fill("1", "A", "매수", self.at),
            fill("2", "A", "매도", self.at + timedelta(minutes=1)),
        ))[0]

        self.assertFalse(self.service.reset(()).changed)
        result = self.service.reset((episode,))

        self.assertTrue(result.changed)
        self.assertEqual("선택 묶음을 자동분류로 되돌렸습니다.", result.message)
        self.assertEqual(tuple(trade_fill_key(value) for value in episode.fills), self.repository.cleared[0])


if __name__ == "__main__":
    unittest.main()
