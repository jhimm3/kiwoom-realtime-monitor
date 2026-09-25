from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.naver_stock_market_news import (
    collect_day, day_state, page_url, parse_page,
)


class NaverStockMarketNewsTests(unittest.TestCase):
    def test_source_specific_timestamp_and_url(self) -> None:
        flash = parse_page("flash", "2026-09-22", 1, {"articles": [{
            "officeId": "011", "articleId": "0004664738", "officeHname": "서울경제",
            "title": "시장 뉴스", "subcontent": "요약", "datetime": "2026-09-22 23:56:13",
        }]})
        world = parse_page("world", "2026-09-22", 1, [{
            "oid": "fnGuide", "aid": "2749193", "ohnm": "로이터", "tit": "해외 뉴스",
            "subcontent": "요약", "dt": "20260922235931", "updatedt": "20260923010000",
        }])
        self.assertEqual(flash.articles[0].published_at, "2026-09-22T23:56:13+09:00")
        self.assertEqual(world.articles[0].published_at, "2026-09-22T23:59:31+09:00")
        self.assertEqual(world.articles[0].url, "https://stock.naver.com/news/worldnews/2749193")
        self.assertIn("category=FLASHNEWS", page_url("flash", "2026-09-22", 1))

    def test_resume_preserves_raw_page_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "historical.sqlite3"
            calls: list[int] = []

            def fetch(source: str, target_date: str, page: int):
                calls.append(page)
                rows = [{"oid": "fnGuide", "aid": str(i), "ohnm": "로이터",
                         "tit": "해외", "subcontent": "요약", "dt": "20260922235931"}
                        for i in range((page - 1) * 100, page * 100 if page == 1 else 101)]
                return parse_page(source, target_date, page, rows)

            partial = collect_day(database, "world", "2026-09-22", max_pages=1,
                                  delay_seconds=0, fetcher=fetch)
            self.assertEqual(partial["state"], "running")
            self.assertEqual(day_state(database, "world", "2026-09-22")["next_page"], 2)
            result = collect_day(database, "world", "2026-09-22", max_pages=2,
                                 delay_seconds=0, fetcher=fetch)
            self.assertEqual(result["state"], "complete")
            self.assertEqual(calls, [1, 2])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_news_pages").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_news_articles").fetchone()[0], 101)
                self.assertEqual(json.loads(connection.execute(
                    "SELECT payload_json FROM market_news_pages WHERE page=1").fetchone()[0])[0]["aid"], "0")

    def test_page_callback_runs_after_raw_page_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            observed = []

            def fetch(source: str, day: str, page: int):
                return parse_page(source, day, page, {"articles": [{
                    "officeId": "011", "articleId": "one",
                    "datetime": "2026-09-22 12:00:00", "title": "증시", "subcontent": "요약",
                }]})

            def on_page(page):
                with closing(sqlite3.connect(database)) as connection:
                    observed.append((page.page, connection.execute(
                        "SELECT COUNT(*) FROM market_news_articles").fetchone()[0]))

            collect_day(database, "flash", "2026-09-22", delay_seconds=0,
                        fetcher=fetch, on_page=on_page)
            self.assertEqual(observed, [(1, 1)])

    def test_resume_replays_page_committed_before_callback_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            seen = []

            def fetch(source: str, day: str, page: int):
                rows = [{"oid": "fnGuide", "aid": str(page * 100 + i),
                         "dt": "20260922235931", "tit": "해외"}
                        for i in range(100 if page == 1 else 1)]
                return parse_page(source, day, page, rows)

            def fail_after_commit(page):
                raise RuntimeError("preparation interrupted")

            with self.assertRaisesRegex(RuntimeError, "preparation interrupted"):
                collect_day(database, "world", "2026-09-22", max_pages=1,
                            delay_seconds=0, fetcher=fetch, on_page=fail_after_commit)
            self.assertEqual(day_state(database, "world", "2026-09-22")["next_page"], 2)

            result = collect_day(database, "world", "2026-09-22", max_pages=1,
                                 delay_seconds=0, fetcher=fetch,
                                 on_page=lambda page: seen.append(page.page))
            self.assertEqual(result["state"], "complete")
            self.assertEqual(seen, [1, 2])

    def test_mismatched_date_is_error_not_false_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "historical.sqlite3"
            def fetch(source: str, target_date: str, page: int):
                return parse_page(source, target_date, page, [{
                    "oid": "fnGuide", "aid": "1", "dt": "20260921235931",
                }])
            with self.assertRaisesRegex(ValueError, "coverage unknown"):
                collect_day(database, "world", "2026-09-22", delay_seconds=0, fetcher=fetch)
            self.assertEqual(day_state(database, "world", "2026-09-22")["state"], "error")

    def test_older_date_page_marks_verified_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "historical.sqlite3"
            def fetch(source: str, target_date: str, page: int):
                day = "2026-09-22" if page == 1 else "2026-09-21"
                return parse_page(source, target_date, page, {"articles": [
                    {"officeId": "011", "articleId": str(i + page * 100),
                     "datetime": f"{day} 02:11:01", "title": "증시", "subcontent": "요약"}
                    for i in range(15)
                ]})
            state = collect_day(database, "flash", "2026-09-22", delay_seconds=0, fetcher=fetch)
            self.assertEqual(state["state"], "complete_boundary")
            self.assertEqual(state["articles"], 15)
            self.assertEqual(state["pages"], 2)
