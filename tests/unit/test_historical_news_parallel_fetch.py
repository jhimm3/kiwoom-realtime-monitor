from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

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

    def test_search_pages_overlap_and_return_in_page_order(self) -> None:
        guard = threading.Lock()
        active = 0
        maximum_active = 0

        def fetcher(code: str, query: str, target_date: str, start: int):
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


if __name__ == "__main__":
    unittest.main()
