from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_news_sources import MarketFeedNewsCollector
from kiwoom_monitor.infrastructure.naver_stock_market_news_source import parse_page


class MarketFeedNewsCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_morning_restart_reopens_yesterday_from_head_and_only_retries_failed_source(self) -> None:
        clock = [datetime.fromisoformat("2026-09-22T06:59:00+09:00")]
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "news.sqlite3", now_provider=lambda: clock[0])
            store.initialize()
            self.addCleanup(store.close)
            old_cut = datetime.fromisoformat("2026-09-21T19:58:00+09:00").timestamp()
            store.save_news_source_page({
                "source_id": "naver-stock:flash:2026-09-21", "scope": "naver_stock_market",
                "query_text": "flash", "run_id": "unfinished", "checked_at": old_cut,
                "next_start": 4, "next_schedule_at": old_cut, "items": [],
            })

            def fetch(source, day, page):
                calls.append((source, day, page))
                if source == "world" and day == "2026-09-21" and clock[0].minute == 0:
                    raise OSError("fixture provider unavailable")
                return parse_page(source, day, page, {"articles": []} if source == "flash" else [])

            collector = MarketFeedNewsCollector(store, fetcher=fetch, now_provider=lambda: clock[0])
            await collector.run_once()
            self.assertEqual([], calls)
            clock[0] += timedelta(minutes=1)
            with self.assertLogs("kiwoom_monitor.central_server.market_news_sources", "ERROR"):
                await collector.run_once()
            self.assertEqual([("flash", "2026-09-21", 1), ("world", "2026-09-21", 1),
                              ("flash", "2026-09-22", 1), ("world", "2026-09-22", 1)], calls)
            clock[0] += timedelta(minutes=5)
            calls.clear()
            await MarketFeedNewsCollector(store, fetcher=fetch, now_provider=lambda: clock[0]).run_once()
            self.assertEqual([("world", "2026-09-21", 1), ("flash", "2026-09-22", 1),
                              ("world", "2026-09-22", 1)], calls)

    async def test_disabled_market_feed_does_not_fetch(self) -> None:
        calls = []
        collector = MarketFeedNewsCollector(
            object(), enabled=False,
            fetcher=lambda *args: calls.append(args),
        )
        await collector.run_once(now=datetime(2026, 9, 22, 10, 5, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual([], calls)

    async def test_flash_and_world_enter_existing_news_revision_path(self) -> None:
        now = datetime(2026, 9, 22, 10, 5, tzinfo=ZoneInfo("Asia/Seoul"))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3", now_provider=lambda: now)
            store.initialize()
            store.save_dataset_snapshot("nxt_eligibility_catalog", "2026-09-22", "2026-09-22", {
                "flags": {}, "stocks": {"005930": ["삼성전자", "KOSPI"], "0147B0": ["래블업", "KOSDAQ"]},
            })
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

            collector = MarketFeedNewsCollector(store, fetcher=fetch, now_provider=lambda: now)
            for source in ("flash", "world"):
                await collector._collect(source, "2026-09-22")
                await collector._collect(source, "2026-09-22")
            history = store.load_news_history("article", target="GLOBAL")
            diagnostics = store.load_news_source_diagnostics(limit=10)
            store.close()

        self.assertEqual([("flash", "2026-09-22", 1), ("world", "2026-09-22", 1)], calls)
        self.assertEqual(2, len(history))
        self.assertEqual(1, diagnostics["summary"]["target_status"]["confirmed"])
        self.assertEqual({now.timestamp()}, {run["checked_at"] for run in diagnostics["runs"]})
        self.assertEqual({now.timestamp() + 300}, {
            source["next_schedule_at"] for source in diagnostics["sources"]
        })
        self.assertEqual({"2026-09-22T01:01:00+00:00", "2026-09-22T01:02:00+00:00"},
                         {row["published_at"] for row in history})
        self.assertEqual({"naver_stock_market"}, {row["collection_scope"] for row in history})
        self.assertEqual(
            {"naver-stock:flash:2026-09-22", "naver-stock:world:2026-09-22"},
            {row["source_id"] for row in diagnostics["runs"]},
        )
