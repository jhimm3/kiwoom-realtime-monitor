from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_news_sources import MarketFeedNewsCollector
from kiwoom_monitor.infrastructure.naver_stock_market_news import parse_page


class MarketFeedNewsCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_feed_can_stop_without_disabling_stock_news(self) -> None:
        calls = []
        collector = MarketFeedNewsCollector(
            object(), enabled=False,
            fetcher=lambda *args: calls.append(args),
        )
        await collector.run_once(now=datetime(2026, 9, 22, 10, 5, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual([], calls)

    async def test_flash_and_world_enter_existing_news_revision_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.replace_documents("stock_catalog", [{
                "owner": "krx", "key": "005930",
                "document": {"code": "005930", "name": "삼성전자"},
            }])
            calls: list[tuple[str, str, int]] = []

            def fetch(source: str, target_date: str, page: int):
                calls.append((source, target_date, page))
                if source == "flash":
                    payload = {"articles": [{"officeId": "011", "articleId": "1234",
                        "officeHname": "서울경제", "title": "삼성전자 실적 발표",
                        "subcontent": "삼성전자 영업이익 증가", "datetime": "2026-09-22 10:01:00"}]}
                else:
                    payload = [{"oid": "fnGuide", "aid": "5678", "ohnm": "로이터",
                        "tit": "미국 대통령 기자회견", "subcontent": "",
                        "dt": "20260922100200"}]
                return parse_page(source, target_date, page, payload)

            collector = MarketFeedNewsCollector(store, fetcher=fetch)
            now = datetime(2026, 9, 22, 10, 5, tzinfo=ZoneInfo("Asia/Seoul"))
            await collector.run_once(now=now)
            await collector.run_once(now=now)
            history = store.load_news_history("article", target="GLOBAL")
            diagnostics = store.load_news_source_diagnostics(limit=10)
            store.close()

        self.assertEqual([("flash", "2026-09-22", 1), ("world", "2026-09-22", 1)], calls)
        self.assertEqual(2, len(history))
        self.assertEqual({"naver_stock_market"}, {row["collection_scope"] for row in history})
        self.assertEqual(
            {"naver-stock:flash:2026-09-22", "naver-stock:world:2026-09-22"},
            {row["source_id"] for row in diagnostics["runs"]},
        )
