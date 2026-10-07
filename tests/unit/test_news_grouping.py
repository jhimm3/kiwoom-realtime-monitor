from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.application.news_grouping import group_similar_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem


def item(title: str, description: str, url: str, hour: int) -> StockNewsItem:
    return StockNewsItem(
        title, description, url, url, datetime(2026, 8, 28, hour, tzinfo=UTC),
        assess_stock_news("테스트기업", title, description),
    )


class NewsGroupingTests(unittest.TestCase):
    def test_same_canonical_url_is_removed(self) -> None:
        first = item("테스트기업 공급계약", "500억원 계약", "https://example.com/a?utm_source=x", 1)
        duplicate = item("테스트기업 공급계약", "500억원 계약", "https://example.com/a", 2)

        groups = group_similar_news((first, duplicate))

        self.assertEqual(1, len(groups))
        self.assertEqual(1, len(groups[0].items))

    def test_similar_headlines_within_48_hours_are_grouped(self) -> None:
        factual = item("테스트기업, 삼성전자와 500억원 공급계약", "부품 공급계약을 체결했다", "https://a/1", 1)
        rewrite = item("테스트기업 500억 규모 삼성전자 공급 계약 체결", "관련 매출 확대 기대", "https://b/2", 3)

        groups = group_similar_news((factual, rewrite))

        self.assertEqual(1, len(groups))
        self.assertEqual(2, len(groups[0].items))

    def test_different_key_amount_is_kept_as_follow_up(self) -> None:
        first = item("테스트기업, 삼성전자와 500억원 공급계약", "계약을 체결했다", "https://a/1", 1)
        follow_up = item("테스트기업, 삼성전자와 700억원 공급계약", "추가 계약을 체결했다", "https://b/2", 3)

        groups = group_similar_news((first, follow_up))

        self.assertEqual(2, len(groups))

    def test_different_counterparty_is_kept_as_follow_up(self) -> None:
        first = item("테스트기업, 삼성전자와 공급계약", "부품 공급", "https://a/1", 1)
        follow_up = item("테스트기업, LG전자와 공급계약", "부품 공급", "https://b/2", 3)

        self.assertEqual(2, len(group_similar_news((first, follow_up))))

    def test_generic_rewrite_does_not_bridge_different_amounts(self) -> None:
        first = item("테스트기업 500억원 공급계약", "계약 체결", "https://a/1", 1)
        generic = item("테스트기업 대규모 공급계약", "계약 체결 소식", "https://b/2", 2)
        follow_up = item("테스트기업 700억원 공급계약", "추가 계약 체결", "https://c/3", 3)

        groups = group_similar_news((first, generic, follow_up))

        self.assertEqual(2, len(groups))

    def test_article_after_48_hours_is_not_grouped(self) -> None:
        first = item("테스트기업 500억원 공급계약", "계약 체결", "https://a/1", 1)
        later = StockNewsItem(
            first.title, first.description, "https://b/2", "https://b/2",
            first.published_at + timedelta(hours=49), first.assessment,
        )

        self.assertEqual(2, len(group_similar_news((first, later))))

    def test_market_reaction_rewrite_is_not_representative(self) -> None:
        factual = item("테스트기업, 삼성전자와 500억원 공급계약", "부품 공급계약 체결", "https://a/1", 1)
        reaction = item("[특징주] 테스트기업, 삼성전자 500억원 공급계약에 강세", "계약 소식에 주가 급등", "https://b/2", 3)

        groups = group_similar_news((reaction, factual))

        self.assertEqual(1, len(groups))
        self.assertEqual(factual.title, groups[0].representative.title)

    def test_event_stages_are_separated(self) -> None:
        review = item("테스트기업 공급계약 검토", "계약 가능성", "https://a/1", 1)
        signed = item("테스트기업 공급계약 체결", "계약 완료", "https://b/2", 3)

        groups = group_similar_news((review, signed))

        self.assertEqual(2, len(groups))
        self.assertEqual({"검토", "체결"}, {group.stage for group in groups})

    def test_official_disclosure_is_preferred_as_representative(self) -> None:
        article = item("테스트기업 500억원 공급계약 체결", "공급계약", "https://news.example/a", 3)
        disclosure = item(
            "테스트기업 500억원 공급계약 체결", "테스트기업 공식 공시",
            "https://dart.fss.or.kr/a", 1,
        )

        groups = group_similar_news((article, disclosure))

        self.assertEqual(disclosure, groups[0].representative)

    def test_past_event_republication_is_marked(self) -> None:
        past = item("테스트기업 지난해 공급계약 재조명", "작년 당시 계약", "https://a/1", 1)

        self.assertTrue(group_similar_news((past,))[0].past_event_republication)


if __name__ == "__main__":
    unittest.main()
