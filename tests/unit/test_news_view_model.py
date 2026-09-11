from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import StoredAINewsAnalysis
from kiwoom_monitor.presentation.news_view_model import (
    ai_detail_html,
    build_display_row,
    effective_judgment,
    related_articles_html,
)


def _item(title: str, link: str = "https://example.com") -> StockNewsItem:
    return StockNewsItem(
        title,
        "설명 <확인>",
        link,
        link,
        datetime(2026, 9, 9, 9, 30, tzinfo=UTC),
        assess_stock_news("테스트기업", title, "설명"),
    )


class NewsViewModelTests(unittest.TestCase):
    def test_ai_judgment_and_display_row_keep_existing_labels(self) -> None:
        item = _item("테스트기업 공급계약")
        stored = StoredAINewsAnalysis(
            AINewsAnalysis("요약", "긍정", 87, "계약 체결", ("수주",), (), "수주·계약"),
            "gemini",
            "model",
            datetime.now(UTC),
        )

        category, outlook, reason, source = effective_judgment(item, stored)
        row = build_display_row(item, NewsEventGroup(item, (item,)), stored)

        self.assertEqual("수주·계약", category)
        self.assertEqual("호재 가능성 높음", outlook)
        self.assertEqual("계약 체결", reason)
        self.assertIn("신뢰도 87%", source)
        self.assertEqual("호재 가능성 높음  ᴬᴵ", row.outlook)

    def test_detail_and_related_articles_escape_untrusted_text(self) -> None:
        first = _item("대표 기사")
        second = _item("<script>기사</script>", "https://example.com/?a=1&b=2")
        group = NewsEventGroup(first, (first, second))

        related = related_articles_html(group)
        detail = ai_detail_html(first, None)

        self.assertNotIn("<script>", related)
        self.assertIn("&lt;script&gt;", related)
        self.assertIn("관련 기사 2건", related)
        self.assertIn("아직 분석하지 않음", detail)


if __name__ == "__main__":
    unittest.main()
