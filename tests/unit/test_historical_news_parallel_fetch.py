from __future__ import annotations

import threading
import time
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from urllib.error import HTTPError

from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticleFetchAttempt,
    ArticlePublicationResult,
    NaverHistoricalNewsItem,
    NAVER_HISTORICAL_SEARCH_PROVIDER,
)
from scripts.probe_historical_backfill import (
    _ArticleFetchPool,
    _NewsTimingLog,
    _SearchPagePool,
    _diagnostic_url,
    _fetch_primary_article,
    _fetch_article_publications,
)


class HistoricalNewsParallelFetchTests(unittest.TestCase):
    def test_hankyung_original_is_excluded_without_network_or_worker_delay(self) -> None:
        item = NaverHistoricalNewsItem(
            "key", "015", "123", "한국경제", "title", "summary",
            "https://www.hankyung.com/article/123", "https://www.hankyung.com/article/123",
            "https://n.news.naver.com/a", 1,
        )
        with patch("kiwoom_monitor.infrastructure.historical_backfill.urlopen") as opener:
            with _ArticleFetchPool(workers=32, article_delay=60, primary_only=True) as pool:
                pool.submit(item)
                results = list(pool.drain(wait=True))
            opener.assert_not_called()
        self.assertEqual(1, len(results))
        self.assertEqual("source_excluded", results[0][1].status)
        self.assertEqual(item.original_url, results[0][1].attempts[0].requested_url)
        self.assertIn("user excluded", results[0][1].attempts[0].error)

    def test_hankyung_exclusion_does_not_match_other_domains(self) -> None:
        item = NaverHistoricalNewsItem(
            "key", "015", "123", "office", "title", "summary",
            "https://www.hankyung.com.other.test/a", "https://www.hankyung.com.other.test/a", "", 1,
        )
        with patch("scripts.probe_historical_backfill.fetch_article_publication") as fetch:
            _fetch_primary_article(item)
            fetch.assert_called_once()

    def test_primary_fetch_defers_archive_when_original_exists(self) -> None:
        item = NaverHistoricalNewsItem(
            "key", "001", "123", "office", "title", "summary",
            "https://publisher.test/a", "https://publisher.test/a",
            "https://n.news.naver.com/a", 1,
        )
        with patch("scripts.probe_historical_backfill.fetch_article_publication") as fetch:
            _fetch_primary_article(item)
            self.assertEqual(("publisher_original",), fetch.call_args.kwargs["source_roles"])
            self.assertEqual(2, fetch.call_args.kwargs["timeout"])
            _fetch_primary_article(NaverHistoricalNewsItem(
                "key2", "001", "124", "office", "title", "summary",
                "https://n.news.naver.com/b", "", "https://n.news.naver.com/b", 2,
            ))
            self.assertEqual(("naver_archive",), fetch.call_args.kwargs["source_roles"])

    def test_primary_only_circuit_skips_unreachable_original_even_with_archive(self) -> None:
        items = [NaverHistoricalNewsItem(
            str(index), "url", str(index), "office", "title", "summary",
            f"https://bad.example/{index}", f"https://bad.example/{index}",
            f"https://n.news.naver.com/{index}", index,
        ) for index in range(5)]
        called = []
        def fetcher(item):
            called.append(item.article_id)
            attempt = ArticleFetchAttempt(
                "publisher_original", item.original_url, item.original_url,
                "fetch_error", None, "TimeoutError: timed out", "", "", "", "",
            )
            return ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
                "fetch_error", "", "", "", "", item.original_url, item.original_url,
                "2026-09-27T00:00:00+00:00", (attempt,),
            )
        with _ArticleFetchPool(workers=1, article_delay=0, fetcher=fetcher,
                               primary_only=True) as pool:
            for item in items:
                pool.submit(item)
            rows = list(pool.drain(wait=True, wait_interval_seconds=.01))
        self.assertEqual(["0", "1", "2"], called)
        self.assertEqual(2, sum(result.attempts[0].url_role == "publisher_original_skipped"
                                for _item, result in rows))

    def test_http_403_circuit_requires_three_consecutive_responses(self) -> None:
        items = [NaverHistoricalNewsItem(
            str(index), "url", str(index), "office", "title", "summary",
            f"https://blocked.example/{index}", f"https://blocked.example/{index}",
            "", index,
        ) for index in range(6)]
        called = []

        def fetcher(item):
            called.append(item.article_id)
            code = 200 if item.article_id == "1" else 403
            attempt = ArticleFetchAttempt(
                "publisher_original", item.original_url, item.original_url,
                "blocked" if code == 403 else "time_not_found", code,
                "HTTPError: 403" if code == 403 else "", "", "", "", "",
            )
            return ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id,
                item.article_id, attempt.status, "", "", "", "",
                item.original_url, item.original_url,
                "2026-09-28T00:00:00+00:00", (attempt,),
            )

        with _ArticleFetchPool(workers=1, article_delay=0, fetcher=fetcher,
                               primary_only=True) as pool:
            for item in items:
                pool.submit(item)
            rows = list(pool.drain(wait=True))
            snapshot = pool.snapshot()
        self.assertEqual(["0", "1", "2", "3", "4"], called)
        self.assertEqual(["blocked.example"], snapshot["blocked_403_hosts"])
        self.assertEqual("deferred_403", next(
            result.status for item, result in rows if item.article_id == "5"
        ))

    def test_diagnostic_url_does_not_log_query_or_fragment(self) -> None:
        value = _diagnostic_url("https://publisher.test/a?id=123&token=secret#part")
        self.assertEqual("https://publisher.test/a", value["url_path"])
        self.assertNotIn("secret", str(value))

    def test_timing_log_persists_error_records_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timing.jsonl"
            log = _NewsTimingLog(path)
            log.write("search_request", page=2, status="http_error",
                      fetch_ms=153, error="HTTPError: 429")
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("HTTPError: 429", record["error"])
            self.assertEqual(153, record["fetch_ms"])

    def test_search_timing_records_failed_attempt_and_shared_cooldown(self) -> None:
        attempts = []
        def fetcher(*_args, **_kwargs):
            if not attempts:
                attempts.append("failed")
                raise HTTPError("https://s.search.naver.com", 429, "Too Many Requests", None, None)
            return SimpleNamespace(items=[])
        events = []
        job = SimpleNamespace(code="005930", query_text="삼성전자", target_date="2026-09-22")
        with _SearchPagePool(workers=1, request_delay=0, fetcher=fetcher,
                             throttle_delay=0, throttle_retries=1,
                             timing_observer=lambda *row: events.append(row)) as pool:
            pool.fetch_batch(job, [0])
        self.assertEqual(["http_error", "ok"], [row[1] for row in events])
        self.assertEqual(429, events[0][5].code)
        self.assertIsNone(events[1][5])

    def test_three_consecutive_original_timeouts_skip_only_articles_without_fallback(self) -> None:
        items = [NaverHistoricalNewsItem(
            article_key=str(index), office_id="url", article_id=str(index),
            office_name="", title="", summary="", article_url=f"https://bad.example/{index}",
            original_url=f"https://bad.example/{index}",
            portal_url="https://portal.example/fallback" if index == 5 else "",
            position=index,
        ) for index in range(6)]
        called: list[str] = []

        def fetcher(item: NaverHistoricalNewsItem) -> ArticlePublicationResult:
            called.append(item.article_id)
            attempt = ArticleFetchAttempt(
                "publisher_original", item.original_url, item.original_url,
                "fetch_error", None, "URLError: <urlopen error timed out>",
                "", "", "", "",
            )
            return ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
                "fetch_error", "", "", "", "", item.original_url,
                item.original_url, "2026-09-27T00:00:00+00:00", (attempt,),
            )

        with _ArticleFetchPool(
            workers=1, article_delay=0, fetcher=fetcher,
            stall_after_seconds=60,
        ) as pool:
            for item in items:
                pool.submit(item)
            rows = list(pool.drain(wait=True, wait_interval_seconds=0.01))
            snapshot = pool.snapshot()

        self.assertEqual(["0", "1", "2", "5"], called)
        self.assertEqual(6, len(rows))
        skipped = [result for _item, result in rows
                   if result.attempts[0].url_role == "publisher_original_skipped"]
        self.assertEqual({"3", "4"}, {result.article_id for result in skipped})
        self.assertTrue(all("not requested" in result.attempts[0].error for result in skipped))
        self.assertEqual(["bad.example"], snapshot["unreachable_hosts"])
        self.assertEqual(2, snapshot["skipped_articles"])

    def test_requests_overlap_across_hosts_but_serialize_per_host(self) -> None:
        items = [
            SimpleNamespace(original_url=f"https://a.example/{index}", portal_url="")
            for index in range(3)
        ] + [
            SimpleNamespace(original_url=f"https://b.example/{index}", portal_url="")
            for index in range(3)
        ]
        guard = threading.Lock()
        active_by_host = {"a.example": 0, "b.example": 0}
        maximum_by_host = {"a.example": 0, "b.example": 0}
        active_total = 0
        maximum_total = 0

        def fetcher(item: object) -> str:
            nonlocal active_total, maximum_total
            host = str(item.original_url).split("/")[2]
            with guard:
                active_by_host[host] += 1
                active_total += 1
                maximum_by_host[host] = max(maximum_by_host[host], active_by_host[host])
                maximum_total = max(maximum_total, active_total)
            time.sleep(0.03)
            with guard:
                active_by_host[host] -= 1
                active_total -= 1
            return str(item.original_url)

        results = list(_fetch_article_publications(
            items, workers=6, article_delay=0, fetcher=fetcher,
        ))

        self.assertEqual(6, len(results))
        self.assertEqual({"a.example": 1, "b.example": 1}, maximum_by_host)
        self.assertGreaterEqual(maximum_total, 2)

    def test_same_host_queue_does_not_occupy_other_host_workers(self) -> None:
        items = [
            SimpleNamespace(original_url=f"https://a.example/{index}", portal_url="")
            for index in range(3)
        ] + [
            SimpleNamespace(original_url="https://b.example/0", portal_url="")
        ]
        guard = threading.Lock()
        active_by_host = {"a.example": 0, "b.example": 0}
        maximum_by_host = {"a.example": 0, "b.example": 0}
        active_total = 0
        maximum_total = 0
        b_started = threading.Event()
        first_a_saw_b: list[bool] = []

        def fetcher(item: object) -> str:
            nonlocal active_total, maximum_total
            host = str(item.original_url).split("/")[2]
            if str(item.original_url) == "https://b.example/0":
                b_started.set()
            elif str(item.original_url) == "https://a.example/0":
                first_a_saw_b.append(b_started.wait(timeout=0.2))
            with guard:
                active_by_host[host] += 1
                active_total += 1
                maximum_by_host[host] = max(maximum_by_host[host], active_by_host[host])
                maximum_total = max(maximum_total, active_total)
            time.sleep(0.03)
            with guard:
                active_by_host[host] -= 1
                active_total -= 1
            return str(item.original_url)

        results = list(_fetch_article_publications(
            items, workers=2, article_delay=0, fetcher=fetcher,
        ))

        self.assertEqual(4, len(results))
        self.assertEqual({"a.example": 1, "b.example": 1}, maximum_by_host)
        self.assertEqual(2, maximum_total)
        self.assertEqual([True], first_a_saw_b)

    def test_stalled_host_opens_only_one_extra_request_and_keeps_every_result(self) -> None:
        items = [
            SimpleNamespace(original_url=f"https://slow.example/{index}", portal_url="")
            for index in range(4)
        ]
        release = threading.Event()
        started = [threading.Event() for _ in items]
        guard = threading.Lock()
        active = 0
        maximum_active = 0

        def fetcher(item: object) -> str:
            nonlocal active, maximum_active
            index = int(str(item.original_url).rsplit("/", 1)[1])
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            started[index].set()
            release.wait(timeout=2)
            with guard:
                active -= 1
            return str(item.original_url)

        pool = _ArticleFetchPool(
            workers=4, article_delay=0, fetcher=fetcher,
            stall_after_seconds=0.03,
        )
        for item in items:
            pool.submit(item)
        results: list[tuple[object, object]] = []
        consumer = threading.Thread(target=lambda: results.extend(
            pool.drain(wait=True, wait_interval_seconds=0.01)
        ))
        try:
            consumer.start()
            self.assertTrue(started[0].wait(timeout=1))
            self.assertTrue(started[1].wait(timeout=1))
            self.assertFalse(started[2].is_set())
            self.assertEqual(["slow.example"], pool.snapshot()["expanded_hosts"])
        finally:
            release.set()
            consumer.join(timeout=2)
            pool.close()

        self.assertFalse(consumer.is_alive())
        self.assertEqual(4, len(results))
        self.assertEqual(4, len({result for _item, result in results}))
        self.assertEqual(2, maximum_active)

    def test_wait_callback_reports_oldest_active_host_and_queued_work(self) -> None:
        started = threading.Event()
        release = threading.Event()
        observed = threading.Event()
        snapshots: list[dict[str, object]] = []

        def fetcher(_item: object) -> str:
            started.set()
            release.wait(timeout=2)
            return "done"

        pool = _ArticleFetchPool(
            workers=1, article_delay=0, fetcher=fetcher,
        )
        pool.submit(SimpleNamespace(
            original_url="https://slow.example/one", portal_url="",
        ))
        pool.submit(SimpleNamespace(
            original_url="https://slow.example/two", portal_url="",
        ))

        def report(snapshot: dict[str, object]) -> None:
            snapshots.append(snapshot)
            observed.set()

        result_rows: list[tuple[object, object]] = []
        consumer = threading.Thread(target=lambda: result_rows.extend(
            pool.drain(wait=True, on_wait=report, wait_interval_seconds=0.01)
        ))
        try:
            consumer.start()
            self.assertTrue(started.wait(timeout=1))
            self.assertTrue(observed.wait(timeout=1))
            release.set()
            consumer.join(timeout=2)
        finally:
            release.set()
            consumer.join(timeout=2)
            pool.close()

        self.assertFalse(consumer.is_alive())
        self.assertEqual(2, len(result_rows))
        self.assertEqual(1, snapshots[0]["active"])
        self.assertEqual(1, snapshots[0]["queued"])
        self.assertEqual("slow.example", snapshots[0]["oldest_host"])

    def test_search_pages_overlap_and_return_in_page_order(self) -> None:
        guard = threading.Lock()
        active = 0
        maximum_active = 0

        def fetcher(
            code: str, query: str, target_date: str, start: int, *,
            target_end_date: str = "",
        ):
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return start

        job = SimpleNamespace(code="005930", query_text="삼성전자", target_date="2026-09-22")
        with _SearchPagePool(
            workers=4, request_delay=0.005, fetcher=fetcher,
        ) as pool:
            results = pool.fetch_batch(job, [3, 0, 2, 1])

        self.assertEqual([(0, 1), (1, 11), (2, 21), (3, 31)], results)
        self.assertGreaterEqual(maximum_active, 2)

    def test_throttled_search_retries_only_the_failed_page(self) -> None:
        attempts: dict[int, int] = {}
        throttles: list[tuple[int, int, float]] = []

        def fetcher(
            code: str, query: str, target_date: str, start: int, *,
            target_end_date: str = "",
        ):
            attempts[start] = attempts.get(start, 0) + 1
            if start == 11 and attempts[start] == 1:
                raise HTTPError(
                    url="https://search.naver.com/", code=403,
                    msg="Forbidden", hdrs=None, fp=None,
                )
            return start

        job = SimpleNamespace(code="005930", query_text="삼성전자", target_date="2026-09-22")
        with _SearchPagePool(
            workers=3, request_delay=0, fetcher=fetcher,
            throttle_delay=0, throttle_retries=2,
            throttle_observer=lambda page, status, delay: throttles.append(
                (page, status, delay)
            ),
        ) as pool:
            results = pool.fetch_batch(job, [0, 1, 2])

        self.assertEqual([(0, 1), (1, 11), (2, 21)], results)
        self.assertEqual({1: 1, 11: 2, 21: 1}, attempts)
        self.assertEqual([(2, 403, 0.0)], throttles)


if __name__ == "__main__":
    unittest.main()
