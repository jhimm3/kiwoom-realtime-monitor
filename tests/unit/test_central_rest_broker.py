from __future__ import annotations

import asyncio
import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

from kiwoom_monitor.central_server.rest_broker import (
    CentralRestBroker, BrokerResult, MAX_MEMORY_CACHE_ENTRIES, MOCK_ACCOUNT_ENDPOINTS,
    ranking_reservation_delay,
)


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
    def test_two_second_cache_is_memory_only_but_records_market_response(self) -> None:
        class RecordingStore:
            def __init__(self) -> None:
                self.loads: list[str] = []
                self.saves: list[str] = []

            def load_query(self, key: str) -> None:
                self.loads.append(key)
                return None

            def save_query(self, key: str, *_args: object) -> None:
                self.saves.append(key)

        async def scenario() -> None:
            store = RecordingStore()
            client = FakeClient()
            handled: list[str] = []
            broker = CentralRestBroker(
                client, store=store,
                response_handler=lambda api_id, _body, _payload: handled.append(api_id),
            )
            first = await broker.request("ka10080", "/api/dostk/chart", {"stk_cd": "005930"})
            second = await broker.request("ka10080", "/api/dostk/chart", {"stk_cd": "005930"})
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(1, len(client.calls))
            self.assertEqual(["ka10080"], handled)
            self.assertEqual([], store.loads)
            self.assertEqual([], store.saves)
            await broker.close()

        asyncio.run(scenario())

    def test_low_priority_work_reserves_the_upcoming_ranking_boundary(self) -> None:
        self.assertAlmostEqual(8.001, ranking_reservation_delay(26.999), places=3)
        self.assertEqual(5.0, ranking_reservation_delay(30.0))
        self.assertEqual(2.5, ranking_reservation_delay(32.5))
        self.assertEqual(0.0, ranking_reservation_delay(35.0))
        self.assertEqual(0.0, ranking_reservation_delay(38.0))

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

    def test_reservation_yields_worker_to_ranking_without_sleeping_full_delay(self) -> None:
        async def scenario() -> None:
            client = FakeClient()
            broker = CentralRestBroker(client, ranking_reservation=True)
            delays = iter((1.0, 0.0))
            with patch(
                "kiwoom_monitor.central_server.rest_broker.ranking_reservation_delay",
                side_effect=lambda _now: next(delays),
            ):
                historical = asyncio.create_task(
                    broker.request("ka10094", "/api/dostk/chart", {"stk_cd": "005930"})
                )
                await asyncio.sleep(0.01)
                started = time.monotonic()
                ranking = asyncio.create_task(
                    broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
                )
                await asyncio.gather(ranking, historical)
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertEqual(
                    ["ka00198", "ka10094"],
                    [call[0] for call in client.calls],
                )
            await broker.close()

        asyncio.run(scenario())

    def test_slow_response_persistence_does_not_hold_ranking_transport(self) -> None:
        async def scenario() -> None:
            storage_started = threading.Event()
            release_storage = threading.Event()

            def persist(api_id: str, _body: dict[str, Any], _payload: dict[str, Any]) -> None:
                if api_id == "ka10001":
                    storage_started.set()
                    release_storage.wait(2)

            client = FakeClient()
            broker = CentralRestBroker(client, response_handler=persist)
            try:
                low = asyncio.create_task(
                    broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                )
                self.assertTrue(await asyncio.to_thread(storage_started.wait, 2))
                ranking = await asyncio.wait_for(
                    broker.request_unrecorded("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}),
                    0.5,
                )
                self.assertEqual("ka00198", ranking.payload["api_id"])
                self.assertFalse(low.done())
                self.assertEqual(["ka10001", "ka00198"], [call[0] for call in client.calls])
                release_storage.set()
                await low
            finally:
                release_storage.set()
                await broker.close()

        asyncio.run(scenario())

    def test_slow_query_cache_save_does_not_hold_ranking_transport(self) -> None:
        class BlockingStore:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def load_query(self, _key: str) -> None:
                return None

            def save_query(self, *_args: object) -> None:
                self.started.set()
                self.release.wait(2)

        async def scenario() -> None:
            store = BlockingStore()
            broker = CentralRestBroker(FakeClient(), store=store)
            try:
                low = asyncio.create_task(
                    broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                )
                self.assertTrue(await asyncio.to_thread(store.started.wait, 2))
                ranking = await asyncio.wait_for(
                    broker.request_unrecorded("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}),
                    0.5,
                )
                self.assertEqual("ka00198", ranking.payload["api_id"])
                self.assertFalse(low.done())
                store.release.set()
                await low
            finally:
                store.release.set()
                await broker.close()

        asyncio.run(scenario())

    def test_audit_log_records_only_actual_upstream_request_metadata(self) -> None:
        async def scenario() -> None:
            client = FakeClient()
            broker = CentralRestBroker(client, namespace="market")
            with self.assertLogs("kiwoom_monitor.kiwoom_api", level="INFO") as captured:
                await broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
                await broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
            await broker.close()
            joined = "\n".join(captured.output)
            self.assertEqual(1, len(client.calls))
            self.assertEqual(1, joined.count("api_id=ka00198"))
            self.assertIn("namespace=market", joined)
            self.assertIn("queue_wait_ms=", joined)
            self.assertNotIn("qry_tp", joined)

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

    def test_unrecorded_request_skips_storage_handler(self) -> None:
        async def scenario() -> None:
            handled: list[str] = []
            broker = CentralRestBroker(
                FakeClient(),
                response_handler=lambda api_id, _body, _payload: handled.append(api_id),
            )
            result = await broker.request_unrecorded(
                "ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"},
            )
            await broker.close()
            self.assertEqual("ka00198", result.payload["api_id"])
            self.assertEqual([], handled)

        asyncio.run(scenario())

    def test_mock_account_broker_has_separate_allowlist_and_namespace(self) -> None:
        async def scenario() -> None:
            client = FakeClient()
            broker = CentralRestBroker(
                client, allowed_endpoints=MOCK_ACCOUNT_ENDPOINTS,
                namespace="mock:account-hash",
            )
            await broker.request("ka10075", "/api/dostk/acnt", {"all_stk_tp": "0"})
            await broker.request("kt00001", "/api/dostk/acnt", {"qry_tp": "3"})
            with self.assertRaisesRegex(ValueError, "허용하지 않는"):
                await broker.request("ka00198", "/api/dostk/stkinfo", {})
            with self.assertRaisesRegex(ValueError, "허용하지 않는"):
                await broker.request("kt10000", "/api/dostk/ordr", {})
            await broker.close()
            self.assertEqual(["ka10075", "kt00001"], [call[0] for call in client.calls])
        asyncio.run(scenario())

    def test_storage_failure_marks_recording_gap_without_failing_query(self) -> None:
        async def scenario() -> None:
            def fail_storage(_api_id, _body, _payload):
                raise RuntimeError("database unavailable")

            broker = CentralRestBroker(FakeClient(), response_handler=fail_storage)
            with self.assertLogs(
                "kiwoom_monitor.central_server.rest_broker", level="ERROR",
            ) as captured:
                result = await broker.request(
                    "ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"},
                )
            await broker.close()
            self.assertIsInstance(result.payload, dict)
            self.assertTrue(any("recording_gap" in line for line in captured.output))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
