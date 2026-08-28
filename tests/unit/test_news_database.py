from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis
from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository
from kiwoom_monitor.infrastructure.persistence.news_database import migrate_legacy_news_database
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class NewsDatabaseTests(unittest.TestCase):
    def test_migrates_news_tables_out_of_main_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main, news = root / "monitor.sqlite3", root / "news.sqlite3"
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            StockNewsRepository(main).upsert("000001", (item,))
            NewsAIRepository(main).save(
                "000001", item, "gemini", "model", "hash",
                AINewsAnalysis("요약", "긍정", 80, "이유", (), (), "수주·계약"),
            )

            migrate_legacy_news_database(main, news)

            self.assertEqual(1, len(StockNewsRepository(news).load("000001")))
            self.assertIsNotNone(NewsAIRepository(news).load("000001", item))
            connection = sqlite3.connect(main)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                connection.close()
            self.assertNotIn("stock_news", tables)
            self.assertNotIn("stock_news_ai", tables)

    def test_ai_backup_restores_results_without_news_articles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, backup = root / "source.sqlite3", root / "target.sqlite3", root / "ai.json"
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            NewsAIRepository(source).save(
                "000001", item, "gemini", "model", "hash",
                AINewsAnalysis("요약", "긍정", 80, "이유", ("계약",), (), "수주·계약"),
            )

            NewsAIBackupService(source).export_to(backup)
            restored = NewsAIBackupService(target).import_from(backup)

            self.assertEqual(1, restored)
            self.assertIsNotNone(NewsAIRepository(target).load("000001", item))
            self.assertEqual((), StockNewsRepository(target).load("000001"))


if __name__ == "__main__":
    unittest.main()
