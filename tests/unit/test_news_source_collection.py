from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.central_server import news_sources
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.central_server.news_sources import QuerySetNewsCollector
from kiwoom_monitor.infrastructure.naver_news import NaverNewsPage, StockNewsItem


def _item(identity: str, title: str = "삼성전자 공급계약 체결", *, minute: int = 0,
          description: str = "삼성전자와 500억원 공급계약 체결",
          original_link: str = "") -> StockNewsItem:
    return StockNewsItem(
        title, description, f"https://naver/{identity}",
        original_link or f"https://origin/{identity}", datetime(2026, 9, 12, 1, minute, tzinfo=UTC),
        NewsAssessment(True, "수주·계약", "긍정", "", 90, 80),
    )


class _ProviderFailure(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(f"provider {code}")
        self.code = code


class _PageClient:
    def __init__(self, pages: dict[tuple[str, int], object]) -> None:
        self.pages, self.calls = pages, []

    def search_page(self, query, *, start=1, display=100, request_claim=None):
        if request_claim is not None and not request_claim():
            raise RuntimeError("예산 소진")
        self.calls.append((query, start))
        value = self.pages.get((query, start), NaverNewsPage((), 0, start, 0))
        if isinstance(value, Exception):
            raise value
        return value

    def search(self, _name, *, since=None):
        return ()


class QuerySetNewsCollectorTests(unittest.IsolatedAsyncioTestCase):
    def _store(self, directory: str) -> SQLiteQueryStore:
        store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
        store.initialize()
        store.replace_documents("stock_catalog", [
            {"owner": "krx", "key": "005930", "document": {"code": "005930", "name": "삼성전자", "market": "KOSPI"}},
        ])
        return store

    async def test_service_start_collects_without_desktop_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            client = _PageClient({("증권", 1): NaverNewsPage((_item("a"),), 1, 1, 1)})
            service = CentralNewsService(
                client, store, jobs_enabled=False, query_set=("증권",), query_set_refresh_seconds=60,
            )  # type: ignore[arg-type]
            await service.start()
            for _ in range(20):
                if store.load_news_history("article", target="GLOBAL"):
                    break
                await asyncio.sleep(0.01)
            await service.close()
            history = store.load_news_history("article", target="GLOBAL")
            store.close()
        self.assertEqual([("증권", 1)], client.calls)
        self.assertEqual("query_set", history[0]["collection_scope"])

    async def test_ingest_skips_title_only_and_clearly_irrelevant_search_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            page = NaverNewsPage((
                _item("title-only", "삼성전자 새 소식", description=""),
                _item("noise", "주말 축제에 시민 북적", description="지역 먹거리와 공연을 즐겼다"),
                _item("market", "코스피 외국인 순매수", description="증시 거래대금도 증가했다"),
                _item("target", "삼성전자 새 서비스 공개", description="소비자 대상 기능을 선보였다"),
            ), 4, 1, 4)
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            history = store.load_news_history("article", target="GLOBAL")
            diagnostics = store.load_news_source_diagnostics(limit=10)
            store.close()

        self.assertEqual(
            {"https://origin/market", "https://origin/target"},
            {str(row["identity"]) for row in history},
        )
        run_document = diagnostics["runs"][0]["document"]
        self.assertEqual(4, run_document["raw_items"])
        self.assertEqual(2, run_document["stored_items"])
        self.assertEqual(1, run_document["skipped_title_only"])
        self.assertEqual(1, run_document["skipped_irrelevant"])

    def test_ingest_skips_non_investment_photo_sports_and_promotion_even_with_company_name(self) -> None:
        values = news_sources._page_items([
            _item("sports", "[골프IN화보] 삼성전자 소속 선수 경기", description="대회 최종 라운드 사진"),
            _item("promo", "삼성전자 특별 할인 이벤트", description="쿠폰을 제공한다"),
            _item("earnings", "삼성전자 영업이익 증가", description="분기 실적을 발표했다"),
        ], (("005930", "삼성전자"),))

        self.assertEqual(["https://origin/earnings"], [value["identity"] for value in values])

    async def test_processing_exclusion_keeps_article_but_defers_body_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            article = _item(
                "excluded", original_link="https://news.einfomax.co.kr/news/articleView.html?idxno=1",
            )
            collector = QuerySetNewsCollector(
                _PageClient({("증권", 1): NaverNewsPage((article,), 1, 1, 1)}), store,
                queries=("증권",), processing_excluded_providers=("연합인포맥스",),
            )  # type: ignore[arg-type]
            await collector.run_once()
            history = store.load_news_history("article", target="GLOBAL")
            excluded_jobs = store.claim_news_jobs(limit=20, now=10_000_000_000.0)

            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            collector.update(
                enabled=True, queries=("증권",), poll_seconds=300,
                processing_excluded_providers=(),
            )
            await collector.run_once()
            resumed_jobs = store.claim_news_jobs(limit=20, now=10_000_000_000.0)
            diagnostics = store.load_news_source_diagnostics(limit=10)
            store.close()

        self.assertEqual(1, len(history))
        self.assertEqual("news.einfomax.co.kr", history[0]["document"]["publisher_domain"])
        self.assertEqual("연합인포맥스", history[0]["document"]["publisher_name"])
        self.assertEqual([], excluded_jobs)
        self.assertEqual(["BODY"], [job["stage"] for job in resumed_jobs])
        self.assertTrue(diagnostics["observations"][-1]["document"]["processing_excluded"])

    async def test_restart_uses_cursor_and_stops_when_previous_marker_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            first_client = _PageClient({("증권", 1): NaverNewsPage((_item("a"),), 1, 1, 1)})
            first = QuerySetNewsCollector(first_client, store, queries=("증권",))  # type: ignore[arg-type]
            await first.run_once()
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            second_client = _PageClient({
                ("증권", 1): NaverNewsPage((_item("b", minute=1), _item("a")), 2, 1, 2),
            })
            second = QuerySetNewsCollector(second_client, store, queries=("증권",))  # type: ignore[arg-type]
            await second.run_once()
            cursor = store.load_news_source_cursor(first._queries and "naver-query:" + __import__("hashlib").sha256("증권".encode()).hexdigest()[:16])
            articles = store.load_news_history("article", target="GLOBAL")
            store.close()
        self.assertEqual(1, cursor["next_start"])
        self.assertEqual("https://origin/b", cursor["cursor_identity"])
        self.assertEqual(2, len(articles))

    async def test_multiple_queries_share_article_but_keep_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            page = NaverNewsPage((_item("same"),), 1, 1, 1)
            collector = QuerySetNewsCollector(
                _PageClient({("증권", 1): page, ("코스피", 1): page}), store, queries=("증권", "코스피"),
            )  # type: ignore[arg-type]
            await collector.run_once()
            history = store.load_news_history("article", target="GLOBAL")
            diagnostics = store.load_news_source_diagnostics()
            store.close()
        self.assertEqual(1, len(history))
        self.assertEqual(2, len(diagnostics["observations"]))
        self.assertEqual(1, diagnostics["summary"]["duplicate_count"])
        self.assertEqual([1, 1], [source["items"] for source in diagnostics["sources"]])

    async def test_same_identity_correction_appends_article_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            first = QuerySetNewsCollector(
                _PageClient({("증권", 1): NaverNewsPage((_item("a"),), 1, 1, 1)}), store, queries=("증권",),
            )  # type: ignore[arg-type]
            await first.run_once()
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            corrected = _item("a", "[정정] 삼성전자 공급계약 체결")
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): NaverNewsPage((corrected,), 1, 1, 1)}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            history = store.load_news_history("article", target="GLOBAL", identity="https://origin/a")
            store.close()
        self.assertEqual(2, len(history))
        self.assertEqual(history[1]["article_revision_id"], history[0]["revision_of"])

    async def test_cross_query_snippet_cycle_reuses_each_source_latest_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            pages = {
                ("증권", 1): NaverNewsPage((_item("same", description="요약 A"),), 1, 1, 1),
                ("코스피", 1): NaverNewsPage((_item("same", description="요약 B"),), 1, 1, 1),
            }
            await QuerySetNewsCollector(
                _PageClient(pages), store, queries=("증권", "코스피"),
            ).run_once()  # type: ignore[arg-type]
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            await QuerySetNewsCollector(
                _PageClient(pages), store, queries=("증권", "코스피"),
            ).run_once()  # type: ignore[arg-type]

            history = store.load_news_history("article", target="GLOBAL", identity="https://origin/same")
            diagnostics_one = store.load_news_source_diagnostics(limit=1)
            diagnostics_all = store.load_news_source_diagnostics(limit=20)
            source_id = "naver-query:" + __import__("hashlib").sha256("증권".encode()).hexdigest()[:16]
            source_only = store.load_news_source_diagnostics(source_id=source_id, limit=1)
            jobs = store.claim_news_jobs(limit=20, now=10_000_000_000.0)
            store.close()

        self.assertEqual(2, len(history))
        self.assertEqual(2, len([job for job in jobs if job["stage"] == "BODY"]))
        self.assertEqual(1, len(diagnostics_one["runs"]))
        self.assertEqual(4, len(diagnostics_all["runs"]))
        self.assertEqual(diagnostics_all["summary"]["raw_count"], diagnostics_one["summary"]["raw_count"])
        self.assertEqual(4, diagnostics_one["summary"]["raw_count"])
        self.assertEqual(2, diagnostics_one["summary"]["unique_count"])
        self.assertEqual(2, diagnostics_one["summary"]["duplicate_count"])
        self.assertEqual(1, diagnostics_one["summary"]["distinct_identity_count"])
        self.assertEqual(2, source_only["summary"]["raw_count"])
        self.assertEqual(1, source_only["summary"]["distinct_identity_count"])

    async def test_same_source_a_b_a_preserves_three_observation_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            for description in ("요약 A", "요약 B", "요약 A"):
                await QuerySetNewsCollector(
                    _PageClient({("증권", 1): NaverNewsPage(
                        (_item("cycle", description=description),), 1, 1, 1,
                    )}), store, queries=("증권",),
                ).run_once()  # type: ignore[arg-type]
                with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                    connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                    connection.commit()
            history = store.load_news_history("article", target="GLOBAL", identity="https://origin/cycle")
            store.close()
        self.assertEqual(3, len(history))

    def test_market_feed_separates_query_flash_and_world_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            today = datetime.now().date().isoformat()
            for source_id, title in (
                ("naver-query:abc", "공통 기사"),
                (f"naver-stock:flash:{today}", "속보 기사"),
                (f"naver-stock:world:{today}", "해외 기사"),
            ):
                store.save_news_source_page({
                    "source_id": source_id, "query_text": title, "run_id": title,
                    "items": [{"identity": f"https://example.com/{title}", "document": {
                        "title": title, "description": "요약", "link": "https://example.com/news",
                            "published_at": f"{today}T10:00:00+09:00",
                    }, "targets": []}],
                })
            store.save_news_source_page({
                "source_id": "naver-stock:flash:2020-09-23", "query_text": "flash",
                "run_id": "old", "items": [{"identity": "https://example.com/old",
                    "document": {"title": "오래된 속보", "description": "과거 자료",
                                 "link": "https://example.com/old",
                                 "published_at": "2020-09-23T10:00:00+09:00"},
                    "targets": []}],
            })
            self.assertEqual(["공통 기사"], [v["title"] for v in store.load_market_news_feed("common")])
            self.assertEqual(["속보 기사", "오래된 속보"],
                             [v["title"] for v in store.load_market_news_feed("flash")])
            self.assertEqual(["속보 기사"],
                             [v["title"] for v in store.load_market_news_feed("flash", limit=1)])
            self.assertEqual(["해외 기사"], [v["title"] for v in store.load_market_news_feed("world")])
            store.close()

    def test_confirmed_stock_news_query_excludes_unresolved_ambiguous_and_other_stock_and_uses_latest_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)

            def save(run_id: str, identity: str, title: str, target: dict) -> None:
                store.save_news_source_page({
                    "source_id": "source", "query_text": "증권", "run_id": run_id,
                    "items": [{
                        "identity": identity,
                        "document": {
                            "title": title, "description": "요약", "link": f"https://n/{identity}",
                            "original_link": f"https://o/{identity}",
                            "published_at": "2026-09-12T01:00:00+00:00",
                        },
                        "targets": [target],
                    }],
                })

            confirmed = {"stock_code": "005930", "stock_name": "삼성전자",
                         "relation_status": "confirmed", "rule_version": "exact-v1"}
            save("old", "same", "삼성전자 이전 판본", confirmed)
            save("new", "same", "삼성전자 최신 판본", confirmed)
            save("unresolved", "unresolved", "시장 뉴스", {
                "stock_code": None, "stock_name": None,
                "relation_status": "unresolved", "rule_version": "exact-v1",
            })
            save("ambiguous", "ambiguous", "동명 뉴스", {
                "stock_code": None, "stock_name": "동명",
                "relation_status": "ambiguous", "rule_version": "exact-v1",
            })
            save("other", "other", "SK하이닉스 뉴스", {
                "stock_code": "000660", "stock_name": "SK하이닉스",
                "relation_status": "confirmed", "rule_version": "exact-v1",
            })

            samsung = store.load_confirmed_news_articles("005930")
            hynix = store.load_confirmed_news_articles("000660")
            store.close()

        self.assertEqual(["삼성전자 최신 판본"], [value["title"] for value in samsung])
        self.assertEqual(["SK하이닉스 뉴스"], [value["title"] for value in hynix])

    async def test_empty_catalog_is_filled_once_and_preserves_market_for_target_matching(self) -> None:
        calls = []

        def load_catalog():
            calls.append(True)
            return (("005930", "삼성전자", "KOSPI"),)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            page = NaverNewsPage((_item("catalog"),), 1, 1, 1)
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page, ("코스피", 1): page}), store,
                queries=("증권", "코스피"), catalog_loader=load_catalog,
            ).run_once()  # type: ignore[arg-type]
            catalog = store.load_documents("stock_catalog", "krx", 10)
            diagnostics = store.load_news_source_diagnostics()
            store.close()

        self.assertEqual(1, len(calls))
        stock = next(row["document"] for row in catalog if row["key"] == "005930")
        self.assertEqual("KOSPI", stock["market"])
        self.assertEqual(1, diagnostics["summary"]["target_status"]["confirmed"])

    async def test_failed_empty_catalog_load_is_rate_limited_and_stays_unresolved(self) -> None:
        calls = []

        def fail_catalog():
            calls.append(True)
            raise RuntimeError("maintenance")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            page = NaverNewsPage((_item("catalog-failed"),), 1, 1, 1)
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page, ("코스피", 1): page}), store,
                queries=("증권", "코스피"), catalog_loader=fail_catalog,
            ).run_once()  # type: ignore[arg-type]
            diagnostics = store.load_news_source_diagnostics()
            store.close()

        self.assertEqual(1, len(calls))
        self.assertEqual(1, diagnostics["summary"]["target_status"]["unresolved"])

    async def test_catalog_loader_with_no_valid_code_name_is_not_saved_as_success(self) -> None:
        calls = []

        def invalid_catalog():
            calls.append(True)
            return (("005930", "", "KOSPI"), ("", "이름만있음", "KOSDAQ"))

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            collector = QuerySetNewsCollector(
                _PageClient({}), store, queries=("증권",), catalog_loader=invalid_catalog,
            )  # type: ignore[arg-type]

            first = await asyncio.to_thread(collector._load_catalog)
            second = await asyncio.to_thread(collector._load_catalog)
            saved = store.load_documents("stock_catalog", "krx", 10)
            store.close()

        self.assertEqual((), first)
        self.assertEqual((), second)
        self.assertEqual(1, len(calls))
        self.assertEqual([], saved)

    async def test_catalog_fill_preserves_existing_market_fields_and_concurrent_usable_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("stock_catalog", [{
                "owner": "krx", "key": "005930",
                "document": {"code": "005930", "name": "", "market": "KOSPI", "kept": True},
            }])

            def load_catalog():
                return (("005930", "삼성전자", ""),)

            collector = QuerySetNewsCollector(
                _PageClient({}), store, queries=("증권",), catalog_loader=load_catalog,
            )  # type: ignore[arg-type]
            loaded = await asyncio.to_thread(collector._load_catalog)
            saved = next(
                row["document"] for row in store.load_documents("stock_catalog", "krx", 10)
                if row["key"] == "005930"
            )

            calls = []
            store.replace_documents("stock_catalog", [])

            def concurrent_fill():
                calls.append(True)
                store.upsert_documents("stock_catalog", [{
                    "owner": "krx", "key": "000660",
                    "document": {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
                }])
                return (("999999", "덮어쓰면안됨", "KOSDAQ"),)

            concurrent = QuerySetNewsCollector(
                _PageClient({}), store, queries=("증권",), catalog_loader=concurrent_fill,
            )  # type: ignore[arg-type]
            concurrently_loaded = await asyncio.to_thread(concurrent._load_catalog)
            final_keys = {row["key"] for row in store.load_documents("stock_catalog", "krx", 10)}
            store.close()

        self.assertEqual(("005930", "삼성전자"), loaded[0])
        self.assertEqual("KOSPI", saved["market"])
        self.assertTrue(saved["kept"])
        self.assertEqual(1, len(calls))
        self.assertEqual(("000660", "SK하이닉스"), concurrently_loaded[0])
        self.assertEqual({"000660"}, final_keys)

    async def test_late_confirmed_target_reuses_body_and_enqueues_rule_idempotently(self) -> None:
        item = _item(
            "late-target", "신규기업 공급계약 체결", description="신규기업과 500억원 공급계약 체결",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            page = NaverNewsPage((item,), 1, 1, 1)
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "신규기업 공급계약 체결")
            await runner.run_once()  # BODY; unresolved라 RULE 없음
            self.assertEqual([], [job for job in store.claim_news_jobs(now=10_000_000_000.0) if job["stage"] == "RULE"])

            store.upsert_documents("stock_catalog", [{
                "owner": "krx", "key": "NEW001",
                "document": {"code": "NEW001", "name": "신규기업", "market": "KOSDAQ"},
            }])
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            await runner.run_once()  # 기존 BODY를 입력으로 새 target RULE
            # 동일 confirmed 재관측은 안정 키로 완료 RULE을 다시 만들지 않는다.
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): page}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            articles = store.load_news_history("article", target="GLOBAL", identity="https://origin/late-target")
            bodies = store.load_news_history("body", target=articles[0]["article_revision_id"])
            events = store.load_news_history("event", target="NEW001")
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                rule_jobs = connection.execute(
                    "SELECT COUNT(*) FROM central_news_jobs WHERE stage='RULE' AND target_id='NEW001'",
                ).fetchone()[0]
            store.close()

        self.assertEqual(1, len(articles))
        self.assertEqual(1, len(bodies))
        self.assertEqual(1, len(events))
        self.assertEqual(1, rule_jobs)

    async def test_failed_second_page_restarts_from_persisted_page_progress(self) -> None:
        first_page = tuple(_item(f"p1-{index}", minute=index % 60) for index in range(100))
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            failed = _PageClient({
                ("증권", 1): NaverNewsPage(first_page, 101, 1, 100),
                ("증권", 101): _ProviderFailure(429),
            })
            await QuerySetNewsCollector(failed, store, queries=("증권",)).run_once()  # type: ignore[arg-type]
            source_id = "naver-query:" + __import__("hashlib").sha256("증권".encode()).hexdigest()[:16]
            self.assertEqual(101, store.load_news_source_cursor(source_id)["next_start"])
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute("UPDATE central_news_source_cursors SET next_schedule_at=0")
                connection.commit()
            resumed = _PageClient({("증권", 101): NaverNewsPage((_item("last"),), 101, 101, 1)})
            await QuerySetNewsCollector(resumed, store, queries=("증권",)).run_once()  # type: ignore[arg-type]
            cursor = store.load_news_source_cursor(source_id)
            store.close()
        self.assertEqual([("증권", 101)], resumed.calls)
        self.assertEqual(1, cursor["next_start"])

    async def test_start_1000_records_truncation_and_gap(self) -> None:
        pages = {}
        for start in range(1, 1001, 100):
            items = tuple(_item(f"{start}-{index}", minute=index % 60) for index in range(100))
            pages[("증권", start)] = NaverNewsPage(items, 2_000, start, 100)
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            await QuerySetNewsCollector(_PageClient(pages), store, queries=("증권",)).run_once()  # type: ignore[arg-type]
            diagnostics = store.load_news_source_diagnostics(limit=20)
            store.close()
        self.assertEqual(1, diagnostics["summary"]["truncation_count"])
        self.assertEqual("query_set_truncated", diagnostics["sources"][0]["coverage"])
        self.assertTrue(diagnostics["runs"][0]["document"]["missing_gap"])

    async def test_auth_or_rate_failure_does_not_stop_other_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            client = _PageClient({
                ("증권", 1): _ProviderFailure(429),
                ("코스피", 1): NaverNewsPage((_item("ok"),), 1, 1, 1),
            })
            await QuerySetNewsCollector(client, store, queries=("증권", "코스피")).run_once()  # type: ignore[arg-type]
            diagnostics = store.load_news_source_diagnostics()
            store.close()
        self.assertEqual(1, len([row for row in diagnostics["runs"] if row["error"]]))
        self.assertEqual(1, len([row for row in diagnostics["observations"] if row["identity"] == "https://origin/ok"]))
        self.assertEqual(2, sum(diagnostics["summary"]["budget"].values()))

    async def test_exact_target_schedules_direct_rule_while_ambiguous_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_documents("stock_catalog", [
                {"owner": "krx", "key": "DUP", "document": {"code": "DUP", "name": "동명기업", "market": "KOSDAQ"}},
                {"owner": "krx", "key": "DUP2", "document": {"code": "DUP2", "name": "동명기업", "market": "KOSPI"}},
            ])
            pages = {
                ("증권", 1): NaverNewsPage((
                    _item("confirmed"),
                    _item("ambiguous", "동명기업 공급계약 체결", description="동명기업과 공급계약 체결"),
                    _item("unresolved", "약칭사 공급계약 체결", description="약칭사와 공급계약 체결"),
                ), 3, 1, 3),
            }
            await QuerySetNewsCollector(_PageClient(pages), store, queries=("증권",)).run_once()  # type: ignore[arg-type]
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "삼성전자 공급계약 체결")
            for _ in range(4):
                await runner.run_once()
            event = store.load_news_history("event", target="005930", limit=1)[0]
            diagnostics = store.load_news_source_diagnostics()
            store.close()
        self.assertEqual("TARGET_COMPANY", event["scope"])
        self.assertEqual("DIRECT", event["result"]["targets"][0]["directness"])
        self.assertEqual({"confirmed": 1, "ambiguous": 1, "unresolved": 1}, diagnostics["summary"]["target_status"])

    async def test_short_company_name_substring_does_not_become_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_documents("stock_catalog", [
                {"owner": "krx", "key": "SHORT1", "document": {"code": "SHORT1", "name": "동일"}},
                {"owner": "krx", "key": "SHORT2", "document": {"code": "SHORT2", "name": "대성"}},
            ])
            item = _item(
                "boundary", "동일한 흐름 속 대성공업 공급계약", description="일반 문장 속 부분 문자열",
            )
            await QuerySetNewsCollector(
                _PageClient({("증권", 1): NaverNewsPage((item,), 1, 1, 1)}), store, queries=("증권",),
            ).run_once()  # type: ignore[arg-type]
            diagnostics = store.load_news_source_diagnostics()
            store.close()
        targets = diagnostics["observations"][0]["document"]["targets"]
        self.assertEqual("unresolved", targets[0]["relation_status"])

    async def test_page_target_mapping_runs_outside_the_event_loop_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            client = _PageClient({
                ("증권", 1): NaverNewsPage((_item("thread"),), 1, 1, 1),
            })
            event_loop_thread = threading.get_ident()
            worker_threads: list[int] = []
            original = news_sources._page_items

            def record_thread(*args):
                worker_threads.append(threading.get_ident())
                return original(*args)

            with patch.object(news_sources, "_page_items", side_effect=record_thread):
                await QuerySetNewsCollector(client, store, queries=("증권",)).run_once()  # type: ignore[arg-type]
            store.close()
        self.assertEqual(1, len(worker_threads))
        self.assertNotEqual(event_loop_thread, worker_threads[0])

    def test_company_pattern_is_built_only_for_present_names_and_reused(self) -> None:
        news_sources._company_name_pattern.cache_clear()
        compile_pattern = news_sources.re.compile
        with patch.object(news_sources.re, "compile", wraps=compile_pattern) as compile_mock:
            self.assertFalse(news_sources._exact_company_name("없는회사", "삼성전자 공급계약"))
            self.assertTrue(news_sources._exact_company_name("삼성전자", "삼성전자 공급계약"))
            self.assertTrue(news_sources._exact_company_name("삼성전자", "삼성전자는 실적을 발표했다"))
        self.assertEqual(1, compile_mock.call_count)

    def test_atomic_scope_budget_reserves_watchlist_share(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            day = "2026-09-12"
            self.assertTrue(store.claim_news_request("query_set", scope_limit=2, hard_limit=3, budget_date=day))
            self.assertTrue(store.claim_news_request("query_set", scope_limit=2, hard_limit=3, budget_date=day))
            self.assertFalse(store.claim_news_request("query_set", scope_limit=2, hard_limit=3, budget_date=day))
            self.assertTrue(store.claim_news_request("watchlist", scope_limit=1, hard_limit=3, budget_date=day))
            self.assertFalse(store.claim_news_request("watchlist", scope_limit=1, hard_limit=3, budget_date=day))
            store.close()

    def test_source_diagnostics_api_is_authenticated(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from kiwoom_monitor.central_server.app import create_app
            from kiwoom_monitor.central_server.config import CentralServerSettings
        except ImportError:
            self.skipTest("FastAPI test dependencies are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'central.sqlite3'}", "private-token",
                news_history_jobs_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/news/sources").status_code)
                response = client.get(
                    "/api/v1/news/sources?days=7",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("query_set", response.json()["scope"])


if __name__ == "__main__":
    unittest.main()
