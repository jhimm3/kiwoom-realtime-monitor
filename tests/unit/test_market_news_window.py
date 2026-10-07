from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.market_news_window import MarketNewsLoadWorker, MarketNewsWindow
from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig, LocalNewsSources, NaverNewsCredentials, NaverNewsPage,
    StockNewsItem,
)
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from datetime import UTC, datetime


class MarketNewsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_nas_feed_uses_stored_page_and_source(self) -> None:
        client = Mock()
        client.market_feed.return_value = [{"title": "저장 기사", "description": "저장 요약"}]
        worker = MarketNewsLoadWorker("flash", Path("unused"), Path("unused.sqlite3"), client)
        received = []
        worker.loaded.connect(lambda source, items: received.append((source, items)))
        worker.run()
        client.market_feed.assert_called_once_with("flash", limit=200)
        self.assertEqual("저장 요약", received[0][1][0]["description"])

    def test_enabled_tabs_match_source_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = MarketNewsWindow(Path(directory) / "news.env", Path(directory) / "news.sqlite3")
            with unittest.mock.patch.object(window, "refresh_current"):
                window._configure_tabs(("flash", "world"))
            self.assertEqual(["실시간 속보", "해외뉴스"],
                             [window._tabs.tabText(i) for i in range(window._tabs.count())])
            window.close()

    def test_direct_common_news_uses_existing_local_news_storage_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "news.env"
            database_path = Path(directory) / "market_news.sqlite3"
            config = LocalNaverNewsConfig(config_path)
            config.save(NaverNewsCredentials("id", "secret"),
                        sources=LocalNewsSources(common_enabled=True,
                                                 common_queries=("증권",)))
            item = StockNewsItem(
                "코스피 증시 상승", "시장 뉴스 요약", "https://example.com/article", "",
                datetime.now(UTC), assess_stock_news("시황", "코스피 증시 상승", "시장 뉴스 요약"),
            )
            worker = MarketNewsLoadWorker("common", config_path, database_path, None, 50)
            received = []
            worker.loaded.connect(lambda _source, items: received.append(items))
            with patch("kiwoom_monitor.presentation.market_news_window.NaverNewsClient.search_page",
                       return_value=NaverNewsPage((item,), 1, 1, 50)) as search:
                worker.run()
                worker.run()
            self.assertEqual(1, search.call_count)
            self.assertEqual("시장 뉴스 요약", received[-1][0]["description"])
            self.assertEqual(1, len(StockNewsRepository(database_path).load("MARKET:common")))


if __name__ == "__main__":
    unittest.main()
