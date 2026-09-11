from __future__ import annotations

import asyncio
import threading
import time
import unittest
from typing import Any

from kiwoom_monitor.central_server.rest_broker import CentralRestBroker, BrokerResult, MAX_MEMORY_CACHE_ENTRIES


class FakeClient:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str, str]] = []
        self.delay = delay

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((api_id, path, body, cont_yn, next_key))
        return {"return_code": 0, "api_id": api_id}, cont_yn == "N", "next" if cont_yn == "N" else ""


class CentralRestBrokerTests(unittest.TestCase):
    def test_ranking_request_runs_before_queued_historical_backfill(self) -> None:
        """A queued screen ranking must overtake lower-priority history work."""
        class BlockingClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.first_started = threading.Event()
                self.release_first = threading.Event()

            def request_with_continuation(
                self, api_id: str, path: str, body: dict[str, Any], *,
                cont_yn: str = "N", next_key: str = "",
            ) -> tuple[dict[str, Any], bool, str]:
                if not self.calls:
                    self.first_started.set()
                    self.release_first.wait(timeout=2)
                return super().request_with_continuation(
                    api_id, path, body, cont_yn=cont_yn, next_key=next_key,
                )

        async def scenario() -> None:
            client = BlockingClient()
            broker = CentralRestBroker(client)
            running = asyncio.create_task(
                broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
            )
            await asyncio.to_thread(client.first_started.wait, 2)
            historical = asyncio.create_task(
                broker.request("ka10094", "/api/dostk/chart", {"stk_cd": "005930"})
            )
            ranking = asyncio.create_task(
                broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
            )
            await asyncio.sleep(0)
            client.release_first.set()
            await asyncio.gather(running, historical, ranking)
            self.assertEqual(
                ["ka10001", "ka00198", "ka10094"],
                [call[0] for call in client.calls],
            )
            await broker.close()

        asyncio.run(scenario())

    def test_memory_cache_is_bounded_and_expired_entries_are_removed(self) -> None:
        broker = CentralRestBroker(FakeClient())
        now = time.monotonic()
        broker._cache["expired"] = (now - 1, BrokerResult({"old": True}, False, ""))
        for index in range(MAX_MEMORY_CACHE_ENTRIES + 20):
            broker._cache[f"key-{index}"] = (now + index + 1, BrokerResult({}, False, ""))
        broker._prune_memory_cache()
        self.assertNotIn("expired", broker._cache)
        self.assertLessEqual(len(broker._cache), MAX_MEMORY_CACHE_ENTRIES)

    def test_merges_same_inflight_request_and_reuses_cache(self) -> None:
        async def scenario() -> None:
            client = FakeClient(0.02)
            broker = CentralRestBroker(client)
            first, second = await asyncio.gather(
                broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}),
                broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}),
            )
            cached = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
            self.assertEqual(1, len(client.calls))
            self.assertFalse(first.cache_hit)
            self.assertFalse(second.cache_hit)
            self.assertTrue(cached.cache_hit)
            await broker.close()
        asyncio.run(scenario())

    def test_passes_continuation_information(self) -> None:
        async def scenario() -> None:
            client = FakeClient()
            broker = CentralRestBroker(client)
            result = await broker.request(
                "ka10080", "/api/dostk/chart", {"stk_cd": "005930"}, cont_yn="Y", next_key="page-2",
            )
            self.assertFalse(result.has_next)
            self.assertEqual("Y", client.calls[0][3])
            self.assertEqual("page-2", client.calls[0][4])
            await broker.close()
        asyncio.run(scenario())

    def test_rejects_order_or_unknown_api(self) -> None:
        async def scenario() -> None:
            broker = CentralRestBroker(FakeClient())
            with self.assertRaisesRegex(ValueError, "허용하지 않는"):
                await broker.request("kt10000", "/api/dostk/ordr", {})
        asyncio.run(scenario())

    def test_accepts_new_high_request_on_stock_info_path(self) -> None:
        async def scenario() -> None:
            client = FakeClient()
            broker = CentralRestBroker(client)
            await broker.request("ka10016", "/api/dostk/stkinfo", {"mrkt_tp": "000"})
            self.assertEqual("/api/dostk/stkinfo", client.calls[0][1])
            await broker.close()
        asyncio.run(scenario())

    def test_passes_successful_response_to_storage_handler(self) -> None:
        async def scenario() -> None:
            handled: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            broker = CentralRestBroker(
                FakeClient(), response_handler=lambda api_id, body, payload: handled.append((api_id, body, payload)),
            )
            await broker.request("ka10080", "/api/dostk/chart", {"stk_cd": "005930"})
            await broker.close()
            self.assertEqual("ka10080", handled[0][0])
            self.assertEqual("005930", handled[0][1]["stk_cd"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
