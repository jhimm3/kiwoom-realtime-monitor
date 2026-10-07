from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from kiwoom_monitor.application.trade_snapshot_context import (
    entry_snapshot_for_cycle,
    resolve_unverifiable_items,
    snapshots_for_cycle,
)
from kiwoom_monitor.domain.snapshot_provenance import mark_news_backfilled


def snapshot(executed_at: datetime, side: str = "매수", **values: object) -> SimpleNamespace:
    defaults = {
        "rank": None,
        "news": (),
        "themes": (),
        "investor_flow": {},
        "orderbook": {},
        "market_state": {},
    }
    defaults.update(values)
    return SimpleNamespace(executed_at=executed_at, side=side, **defaults)


class TradeSnapshotContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 9, 9, 9, 10)
        self.cycle = SimpleNamespace(started_at=self.start, ended_at=self.start + timedelta(minutes=5))

    def test_cycle_filter_includes_one_minute_tolerance_only(self) -> None:
        before = snapshot(self.start - timedelta(minutes=1))
        inside = snapshot(self.start + timedelta(minutes=2))
        after = snapshot(self.start + timedelta(minutes=6))
        unrelated = snapshot(self.start + timedelta(minutes=6, seconds=1))

        self.assertEqual((before, inside, after), snapshots_for_cycle(self.cycle, (before, inside, after, unrelated)))

    def test_entry_prefers_buy_even_when_sell_snapshot_is_first(self) -> None:
        sell = snapshot(self.start, "매도")
        buy = snapshot(self.start + timedelta(minutes=1), "매수")

        self.assertIs(buy, entry_snapshot_for_cycle(self.cycle, (sell, buy)))

    def test_no_snapshot_preserves_original_items(self) -> None:
        original = ("당시 뉴스·테마", "다른 항목")

        self.assertEqual(original, resolve_unverifiable_items(self.cycle, (), original))

    def test_realtime_rank_and_backfilled_fields_are_reported_separately(self) -> None:
        entry = snapshot(
            self.start,
            rank=4,
            news=mark_news_backfilled(({"title": "기사"},)),
            investor_flow={"available": True, "backfilled": True},
            themes=("반도체",),
            orderbook={"execution_strength": 110.0},
        )

        resolved = resolve_unverifiable_items(
            self.cycle,
            (entry,),
            ("1강 기준의 당시 시장 주도주 여부(시장 전체 순위·지속적인 관심)", "당시 뉴스·테마·호가·외국인·기관 수급"),
        )

        self.assertEqual(
            (
                "1강 기준의 지속적인 시장 관심 여부(진입 순간 순위는 확인됨)",
                "당시 시장 상태",
                "체결 당시 미확인(장후 보완값 있음): 뉴스·재료·외국인·기관 수급",
            ),
            resolved,
        )

    def test_unavailable_investor_flow_remains_missing(self) -> None:
        entry = snapshot(
            self.start,
            news=({"title": "실시간 기사"},),
            themes=("신규주",),
            investor_flow={"available": False},
            orderbook={"execution_strength": 99.0},
            market_state={"kospi": {"index": 3000}},
        )

        self.assertEqual(
            ("당시 외국인·기관 수급",),
            resolve_unverifiable_items(self.cycle, (entry,), ("당시 뉴스·테마·호가·외국인·기관 수급",)),
        )


if __name__ == "__main__":
    unittest.main()
