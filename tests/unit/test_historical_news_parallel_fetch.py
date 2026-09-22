from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError

from scripts.probe_historical_backfill import _SearchPagePool, _fetch_article_publications


class HistoricalNewsParallelFetchTests(unittest.TestCase):
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
