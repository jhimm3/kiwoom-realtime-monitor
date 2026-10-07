from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.application.news_auto_analysis import (
    auto_candidate_identities,
    next_auto_groups,
    unanalyzed_groups_from,
)
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


def _item(index: int, *, linked: bool = True) -> StockNewsItem:
    link = f"https://example.com/{index}" if linked else ""
    return StockNewsItem(
        f"기사 {index}", "내용", link, link,
        datetime(2026, 9, 9, 9, index, tzinfo=UTC),
        assess_stock_news("테스트", f"기사 {index}", "내용"),
    )


class NewsAutoAnalysisTests(unittest.TestCase):
    def test_new_candidate_selection_keeps_group_order_and_limit(self) -> None:
        first, related, second = _item(1), _item(2), _item(3)
        groups = (
            NewsEventGroup(first, (first, related)),
            NewsEventGroup(second, (second,)),
        )

        selected = auto_candidate_identities(
            groups, (), 1, new_identities={news_identity(related), news_identity(second)},
        )

        self.assertEqual((news_identity(first),), selected)
        self.assertEqual((), auto_candidate_identities(groups, (), 0))

    def test_manual_candidates_skip_analyzed_and_unlinked_items(self) -> None:
        first, second, third = _item(1), _item(2, linked=False), _item(3)
        groups = tuple(NewsEventGroup(item, (item,)) for item in (first, second, third))

        selected = unanalyzed_groups_from(groups, 0, {news_identity(first)}, 5)

        self.assertEqual((groups[2],), selected)

    def test_next_batch_uses_visible_order_and_batch_size(self) -> None:
        items = tuple(_item(index) for index in range(1, 5))
        groups = tuple(NewsEventGroup(item, (item,)) for item in items)
        pending = {news_identity(item) for item in items}

        selected = next_auto_groups(items, groups, pending, (), "batch", 2)
        single = next_auto_groups(items, groups, pending, (), "single", 20)

        self.assertEqual(groups[:2], selected)
        self.assertEqual(groups[:1], single)


if __name__ == "__main__":
    unittest.main()
