from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import (
    AINewsAnalysis, AICompanyImpact, AIRequestUsage, AIThemeCandidate, analysis_body_hash,
)
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class NewsAIRepositoryTests(unittest.TestCase):
    def test_theme_candidates_are_persisted_for_profile_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            item = StockNewsItem(
                "지역 개발 기사", "복수 기업 참여", "https://example.com/theme", "",
                datetime.now(UTC), assess_stock_news("테스트", "지역 개발 기사", "복수 기업 참여"),
            )
            StockNewsRepository(path).upsert("000001", (item,))
            repository.save(
                "000001", item, "openai", "model", analysis_body_hash("테스트", "본문"),
                AINewsAnalysis(
                    "요약", "긍정", 80, "근거", (), (), "산업·정책", (),
                    (AIThemeCandidate("호남클러스터", 84, "복수 상장사 참여"),),
                ),
            )

            values = repository.list_theme_suggestions()

            self.assertEqual(1, len(values))
            self.assertEqual("호남클러스터", values[0].raw_theme_name)
            self.assertEqual("복수 상장사 참여", values[0].evidence)
            self.assertEqual("지역 개발 기사", values[0].article_title)
            self.assertEqual(item.published_at, values[0].article_published_at)
            self.assertEqual("https://example.com/theme", values[0].article_url)

    def test_same_url_reuses_company_specific_impact_for_another_stock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            item = StockNewsItem(
                "공동 기사", "두 회사 계약", "https://example.com/shared", "https://example.com/shared",
                datetime.now(UTC), assess_stock_news("A회사", "공동 기사", "두 회사 계약"),
            )
            repository.save(
                "000001", item, "gemini", "model", analysis_body_hash("A회사", "본문"),
                AINewsAnalysis(
                    "공동 요약", "긍정", 90, "A회사 수주", (), (), "수주·계약",
                    (AICompanyImpact("A회사", "긍정", 90, "수주"), AICompanyImpact("B회사", "부정", 75, "경쟁 심화")),
                ),
            )

            loaded = repository.load_many("000002", (item,), "B회사")

            shared = loaded[news_identity(item)].analysis
            self.assertEqual("공동 요약", shared.summary)
            self.assertEqual("부정", shared.outlook)
            self.assertEqual("경쟁 심화", shared.reason)

    def test_request_log_counts_one_batch_as_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            repository.log_request("gemini", "flash-lite", "batch", 10, 27, AIRequestUsage(66570, 4200, 70770))

            self.assertEqual(1, repository.daily_count())
            self.assertEqual((1, 66570, 4200, 70770), repository.daily_usage())

    def test_load_many_reads_saved_results_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            items = tuple(
                StockNewsItem(
                    f"기사 {index}", "공급계약", f"https://example.com/{index}",
                    f"https://example.com/{index}", datetime.now(UTC),
                    assess_stock_news("테스트", f"기사 {index}", "공급계약"),
                )
                for index in range(2)
            )
            for index, item in enumerate(items):
                repository.save(
                    "000000", item, "gemini", "model", analysis_body_hash("테스트", "본문"),
                    AINewsAnalysis(f"요약 {index}", "긍정", 80, "이유", (), (), "수주·계약"),
                )

            loaded = repository.load_many("000000", items)

            self.assertEqual({news_identity(item) for item in items}, set(loaded))
            self.assertEqual("요약 0", loaded[news_identity(items[0])].analysis.summary)

    def test_old_prompt_results_remain_visible_but_are_marked_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            item = StockNewsItem(
                "시장 기사", "삼성전자 단순 나열", "https://example.com/old", "",
                datetime.now(UTC), assess_stock_news("삼성전자", "시장 기사", "삼성전자 주가"),
            )
            repository.save(
                "005930", item, "gemini", "model", "legacy-hash",
                AINewsAnalysis("시장 전체 요약", "부정", 70, "시장 하락"),
            )

            stored = repository.load_many("005930", (item,), "삼성전자")[news_identity(item)]
            self.assertFalse(stored.uses_current_prompt)


if __name__ == "__main__":
    unittest.main()
