from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity


class NewsAIRepositoryTests(unittest.TestCase):
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
                    "000000", item, "gemini", "model", "hash",
                    AINewsAnalysis(f"요약 {index}", "긍정", 80, "이유", (), (), "수주·계약"),
                )

            loaded = repository.load_many("000000", items)

            self.assertEqual({news_identity(item) for item in items}, set(loaded))
            self.assertEqual("요약 0", loaded[news_identity(items[0])].analysis.summary)


if __name__ == "__main__":
    unittest.main()
