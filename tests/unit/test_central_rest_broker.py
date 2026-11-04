from __future__ import annotations

import asyncio
import threading
import time
import unittest
import tempfile
from pathlib import Path
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from unittest.mock import patch

from kiwoom_monitor.central_server.rest_broker import (
    CentralRestBroker, BrokerResult, MAX_MEMORY_CACHE_ENTRIES, MOCK_ACCOUNT_ENDPOINTS,
    ranking_reservation_delay,
)

from kiwoom_monitor.domain.operation_context import (
    OperationContextSnapshot, operation_context_scope, snapshot_operation_context,
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
    def test_basic_cache_lookup_crossing_0700_does_not_restamp_an_old_response(self) -> None:
        class Store:
            def __init__(self):
                self.values = {}
                self.entered, self.release = threading.Event(), threading.Event()
                self.block = False

            def load_query(self, key):
                if self.block:
                    self.entered.set()
                    self.release.wait(timeout=3)
                return self.values.get(key)

            def save_query(self, key, api_id, expires_at, value):
                self.values[key] = value

        async def scenario():
            now = [datetime.fromisoformat("2026-10-01T06:59:59+09:00")]
            store, client = Store(), FakeClient()
            with patch.object(client, "server_now", side_effect=lambda: now[0], create=True):
                broker = CentralRestBroker(client, store=store)
                await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                broker._cache.clear()
                store.block = True
                lookup = asyncio.create_task(broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}))
                try:
                    self.assertTrue(await asyncio.to_thread(store.entered.wait, 2))
                    now[0] = datetime.fromisoformat("2026-10-01T07:00:00+09:00")
                finally:
                    store.release.set()
                result = await lookup
                self.assertFalse(result.cache_hit)
                self.assertEqual(2, len(client.calls))
                await broker.close()
        asyncio.run(scenario())

    def test_basic_cache_period_changes_at_0700_and_midnight_in_memory_and_storage(self) -> None:
        class Store:
            def __init__(self):
                self.values = {}

            def load_query(self, key):
                return self.values.get(key)

            def save_query(self, key, api_id, expires_at, value):
                self.values[key] = value

        async def scenario():
            store = Store()
            client = FakeClient()
            now = [datetime.fromisoformat("2026-10-01T06:59:59+09:00")]
            with patch.object(client, "server_now", side_effect=lambda: now[0], create=True):
                broker = CentralRestBroker(client, store=store)
                await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                repeated = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                self.assertTrue(repeated.cache_hit)
                await broker.close()
                # Restart clears RAM, but retains the same pre-07 persistent response.
                broker = CentralRestBroker(client, store=store)
                repeated = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                self.assertTrue(repeated.cache_hit)
                nxt_key = broker._fingerprint("ka10100", "/api/dostk/stkinfo", {"stk_cd": "005930"}, "N", "")
                now[0] = datetime.fromisoformat("2026-10-01T07:00:00+09:00")
                refreshed = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                self.assertFalse(refreshed.cache_hit)
                self.assertEqual(nxt_key, broker._fingerprint("ka10100", "/api/dostk/stkinfo", {"stk_cd": "005930"}, "N", ""))
                broker._cache.clear()
                repeated = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                self.assertTrue(repeated.cache_hit)
                now[0] = datetime.fromisoformat("2026-10-02T00:00:00+09:00")
                refreshed = await broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
                self.assertFalse(refreshed.cache_hit)
                self.assertEqual(3, len(client.calls))
                await broker.close()
        asyncio.run(scenario())

    def test_parallel_basic_requests_wait_for_recording_result_before_reporting_success(self) -> None:
        async def scenario():
            entered, release = threading.Event(), threading.Event()

            def record(*_):
                entered.set()
                release.wait(timeout=3)
                raise OSError("document write failed")

            client = FakeClient()
            broker = CentralRestBroker(client, response_handler=record)
            first = asyncio.create_task(broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(broker.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}))
            try:
                await asyncio.sleep(0.02)
                self.assertFalse(second.done())
                self.assertEqual(1, len(client.calls))
            finally:
                release.set()
            results = await asyncio.gather(first, second)
            self.assertTrue(all(result.recording_succeeded is False for result in results))
            self.assertEqual({}, broker._cache)
            await broker.close()
        asyncio.run(scenario())

    def test_slow_persistent_lookup_does_not_hold_ranking_or_duplicate_request(self) -> None:
        class BlockingStore:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()
                self.loads = 0

            def load_query(self, _key: str) -> None:
                self.loads += 1
                self.entered.set()
                self.release.wait(timeout=3)
                return None

            def save_query(self, *_args: object) -> None:
                pass

        async def scenario() -> None:
            store = BlockingStore()
            client = FakeClient()
            broker = CentralRestBroker(client, store=store)
            first = asyncio.create_task(broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}))
            self.assertTrue(await asyncio.to_thread(store.entered.wait, 2))
            second = asyncio.create_task(broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}))
            ranking = await asyncio.wait_for(
                broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}), 1,
            )
            self.assertEqual("ka00198", ranking.payload["api_id"])
            store.release.set()
            await asyncio.gather(first, second)
            self.assertEqual(1, store.loads)
            self.assertEqual(1, sum(call[0] == "ka10081" for call in client.calls))
            await broker.close()

        asyncio.run(scenario())

    def test_credential_pause_during_cache_lookup_does_not_queue_old_request(self) -> None:
        class BlockingStore:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()

            def load_query(self, _key: str) -> None:
                self.entered.set()
                self.release.wait(timeout=3)
                return None

        class RotatingClient(FakeClient):
            def begin_credential_change(self) -> None:
                pass

        async def scenario() -> None:
            store = BlockingStore()
            client = RotatingClient()
            broker = CentralRestBroker(client, store=store)
            request = asyncio.create_task(
                broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}),
            )
            self.assertTrue(await asyncio.to_thread(store.entered.wait, 2))
            rotation = asyncio.create_task(broker.begin_credential_change())
            await asyncio.sleep(0)
            self.assertTrue(broker._credential_paused)
            store.release.set()
            await rotation
            with self.assertRaises(Exception) as error:
                await request
            self.assertIn("CREDENTIAL_CHANGE_IN_PROGRESS", str(error.exception))
            self.assertEqual([], client.calls)
            await broker.close()

        asyncio.run(scenario())

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
            self.assertFalse(result.recording_succeeded)
            self.assertTrue(any("recording_gap" in line for line in captured.output))

        asyncio.run(scenario())




class BrokerOperationContextTests(unittest.TestCase):
    def test_shared_waiter_cancel_keeps_first_origin_through_real_cache_and_persistence(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        async def scenario(directory):
            observed = []
            entered, release = threading.Event(), threading.Event()
            main_thread = threading.get_ident()

            class Store(SQLiteQueryStore):
                def load_query(inner, key):
                    observed.append(("lookup", snapshot_operation_context(), threading.get_ident()))
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("lookup barrier not released")
                    return super().load_query(key)

                def save_query(inner, *args):
                    observed.append(("cache_write", snapshot_operation_context(), threading.get_ident()))
                    return super().save_query(*args)

            class Client(FakeClient):
                def request_with_continuation(inner, *args, **kwargs):
                    observed.append(("transport", snapshot_operation_context(), threading.get_ident()))
                    return super().request_with_continuation(*args, **kwargs)

            def record(*_):
                observed.append(("ingest", snapshot_operation_context(), threading.get_ident()))

            store, client = Store(Path(directory) / "cache.db"), Client()
            store.initialize()
            broker = CentralRestBroker(client, store, record)
            origin = OperationContextSnapshot("first-owner", "first-native-id")
            waiter = OperationContextSnapshot("second-owner", "second-waiter-id")
            returned_contexts = []

            async def request(context):
                with operation_context_scope(context):
                    try:
                        return await broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"})
                    finally:
                        returned_contexts.append(snapshot_operation_context())

            first = asyncio.create_task(request(origin))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            try:
                with self.assertLogs("kiwoom_monitor.central_server.rest_broker", level="INFO") as logs:
                    second = asyncio.create_task(request(waiter))
                    await asyncio.sleep(0)
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    release.set()
                    result = await second
                self.assertEqual([
                    "INFO:kiwoom_monitor.central_server.rest_broker:shared broker request "
                    "request_id=second-waiter-id origin_request_id=first-native-id relation=shared_inflight"
                ], logs.output)
                self.assertEqual(["lookup", "transport", "ingest", "cache_write"], [row[0] for row in observed])
                self.assertTrue(all(row[1] == origin for row in observed))
                self.assertTrue(all(row[2] != main_thread for row in observed))
                self.assertEqual([origin, waiter], returned_contexts)
                self.assertEqual(1, len(client.calls))
                self.assertTrue(result.recording_succeeded)
                self.assertEqual({"return_code": 0, "api_id": "ka10081"}, result.payload)
                key = broker._fingerprint("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}, "N", "")
                stored = SQLiteQueryStore.load_query(store, key)
                self.assertEqual(result.payload, stored.payload)
                self.assertEqual({}, broker._inflight)
            finally:
                release.set()
                await broker.close()
                store.close()
            self.assertEqual(OperationContextSnapshot(), snapshot_operation_context())

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_lazy_workers_reset_between_jobs_credentials_and_service_calls(self):
        async def scenario():
            actor = ContextVar("existing-replay-actor-test", default=None)
            marker = object()
            observed, bases = [], []

            def capture(stage):
                observed.append((stage, snapshot_operation_context(), actor.get()))

            class Client(FakeClient):
                def request_with_continuation(inner, *args, **kwargs):
                    capture("transport")
                    return super().request_with_continuation(*args, **kwargs)

                def prepare_credential_token(inner, settings):
                    capture("token")
                    return settings

                def verify_prepared_credentials(inner, settings):
                    capture("identity")
                    return settings

                def prepare_drained_credentials(inner, settings):
                    capture("drained")
                    return settings

                def begin_credential_change(inner):
                    capture("drain")

                def end_credential_change(inner):
                    capture("resume")

            client = Client()
            broker = CentralRestBroker(client, response_handler=lambda *_: capture("ingest"))
            for queue in (broker._queue, broker._persist_queue):
                original = queue.get

                async def get(original=original):
                    bases.append((snapshot_operation_context(), actor.get()))
                    return await original()

                queue.get = get

            actor_token = actor.set(marker)
            try:
                with operation_context_scope(OperationContextSnapshot("first", "first-id")):
                    await broker.request("ka00198", "/api/dostk/stkinfo", {"seq": 1})
                with operation_context_scope(OperationContextSnapshot("second", "second-id")):
                    await broker.request("ka00198", "/api/dostk/stkinfo", {"seq": 2})
                with operation_context_scope(OperationContextSnapshot("credential", "credential-id")):
                    self.assertEqual("settings", await broker.prepare_credentials("settings"))
                # A context-free management operation gets one origin for both phases.
                await broker.prepare_credentials("service-settings")
                with operation_context_scope(OperationContextSnapshot("rotation", "rotation-id")):
                    await broker.begin_credential_change()
                    self.assertEqual("candidate", await broker.prepare_drained_credentials("candidate"))
                    await broker.end_credential_change()
                await broker.request("ka00198", "/api/dostk/stkinfo", {"seq": 3})
                await broker.close()
            finally:
                actor.reset(actor_token)
                await broker.close()
            first, second, credential, rotation = (
                OperationContextSnapshot("first", "first-id"), OperationContextSnapshot("second", "second-id"),
                OperationContextSnapshot("credential", "credential-id"), OperationContextSnapshot("rotation", "rotation-id"),
            )
            self.assertEqual(["transport", "ingest", "transport", "ingest", "token", "identity", "token", "identity",
                              "drain", "drained", "resume", "transport", "ingest"], [row[0] for row in observed])
            self.assertEqual([first, first, second, second, credential, credential], [row[1] for row in observed[:6]])
            service_credential = observed[6][1]
            self.assertEqual(service_credential, observed[7][1])
            self.assertEqual("central.rest_broker", service_credential.source)
            self.assertRegex(service_credential.request_id, r"^[0-9a-f]{32}$")
            self.assertEqual([rotation] * 3, [row[1] for row in observed[8:11]])
            service_query = observed[11][1]
            self.assertEqual(service_query, observed[12][1])
            self.assertEqual("central.rest_broker", service_query.source)
            self.assertRegex(service_query.request_id, r"^[0-9a-f]{32}$")
            self.assertNotEqual(service_credential.request_id, service_query.request_id)
            self.assertTrue(bases)
            self.assertTrue(all(context == OperationContextSnapshot() for context, _ in bases))
            self.assertTrue(all(value is marker for _, _, value in observed))
            self.assertTrue(all(value is marker for _, value in bases))
            self.assertEqual(3, len(client.calls))
            self.assertEqual({}, broker._inflight)
            self.assertFalse(broker._lookup_tasks)
            self.assertEqual(0, broker._queue._unfinished_tasks)
            self.assertEqual(0, broker._persist_queue._unfinished_tasks)
            self.assertEqual(OperationContextSnapshot(), snapshot_operation_context())

        asyncio.run(scenario())

    def test_lookup_native_credential_and_recording_failures_leave_no_prior_origin(self):
        async def scenario():
            observed, bases = [], []
            original_error = OSError("native failure")

            class Store:
                def load_query(inner, key):
                    observed.append(("lookup", snapshot_operation_context()))
                    raise LookupError("cache lookup failed")

            class Client(FakeClient):
                def request_with_continuation(inner, *args, **kwargs):
                    observed.append(("transport", snapshot_operation_context()))
                    if args[2].get("fail"):
                        raise original_error
                    return super().request_with_continuation(*args, **kwargs)

                def prepare_credential_token(inner, _):
                    observed.append(("credential", snapshot_operation_context()))
                    raise ValueError("credential failure")

            def record(*_):
                observed.append(("ingest", snapshot_operation_context()))
                raise OSError("recording failure")

            broker = CentralRestBroker(Client(), Store(), record)
            for queue in (broker._queue, broker._persist_queue):
                original = queue.get

                async def get(original=original):
                    bases.append(snapshot_operation_context())
                    return await original()

                queue.get = get
            try:
                with operation_context_scope(OperationContextSnapshot("lookup", "lookup-id")):
                    with self.assertRaises(LookupError):
                        await broker.request("ka10081", "/api/dostk/chart", {})
                with operation_context_scope(OperationContextSnapshot("native", "native-id")):
                    with self.assertRaises(OSError) as raised:
                        await broker.request("ka00198", "/api/dostk/stkinfo", {"fail": True})
                    self.assertIs(original_error, raised.exception)
                with operation_context_scope(OperationContextSnapshot("credential", "credential-id")):
                    with self.assertRaisesRegex(RuntimeError, "^CANDIDATE_VALIDATION_FAILED$"):
                        await broker.prepare_credentials("settings")
                with operation_context_scope(OperationContextSnapshot("persist", "persist-id")):
                    with self.assertLogs("kiwoom_monitor.central_server.rest_broker", level="ERROR"):
                        result = await broker.request("ka00198", "/api/dostk/stkinfo", {"seq": 1})
                    self.assertFalse(result.recording_succeeded)
                with self.assertLogs("kiwoom_monitor.central_server.rest_broker", level="ERROR"):
                    await broker.request("ka00198", "/api/dostk/stkinfo", {"seq": 2})
            finally:
                await broker.close()
            self.assertEqual([
                ("lookup", OperationContextSnapshot("lookup", "lookup-id")),
                ("transport", OperationContextSnapshot("native", "native-id")),
                ("credential", OperationContextSnapshot("credential", "credential-id")),
                ("transport", OperationContextSnapshot("persist", "persist-id")),
                ("ingest", OperationContextSnapshot("persist", "persist-id")),
            ], observed[:5])
            self.assertEqual(observed[5][1], observed[6][1])
            self.assertEqual("central.rest_broker", observed[5][1].source)
            self.assertRegex(observed[5][1].request_id, r"^[0-9a-f]{32}$")
            self.assertTrue(all(context == OperationContextSnapshot() for context in bases))
            self.assertEqual({}, broker._inflight)
            self.assertEqual(OperationContextSnapshot(), snapshot_operation_context())

        asyncio.run(scenario())

    def test_persistent_cache_hit_ingests_under_current_origin_without_transport_or_write(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.database_query_cache import StoredQuery

        async def scenario(directory):
            observed = []

            class Store(SQLiteQueryStore):
                def load_query(inner, key):
                    observed.append(("lookup", snapshot_operation_context()))
                    return super().load_query(key)

                def save_query(inner, *args):
                    raise AssertionError("cache hit must not rewrite persistent cache")

            store, client = Store(Path(directory) / "cache.db"), FakeClient()
            store.initialize()
            broker = CentralRestBroker(client, store, lambda *_: observed.append(("ingest", snapshot_operation_context())))
            key = broker._fingerprint("ka10081", "/api/dostk/chart", {}, "N", "")
            SQLiteQueryStore.save_query(store, key, "ka10081", broker._wall_time() + 30, StoredQuery({"value": 7}, False, ""))
            origin = OperationContextSnapshot("cache-caller", "cache-caller-id")
            try:
                with operation_context_scope(origin):
                    result = await broker.request("ka10081", "/api/dostk/chart", {})
                self.assertTrue(result.cache_hit)
                self.assertTrue(result.recording_succeeded)
                self.assertEqual({"value": 7}, result.payload)
                self.assertEqual([("lookup", origin), ("ingest", origin)], observed)
                self.assertEqual([], client.calls)
            finally:
                await broker.close()
                store.close()
            self.assertEqual(OperationContextSnapshot(), snapshot_operation_context())

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


    def test_shared_join_log_sink_failure_does_not_change_native_result_or_ownership(self):
        async def scenario():
            entered, release = threading.Event(), threading.Event()
            observed = []

            class Client(FakeClient):
                def request_with_continuation(inner, *args, **kwargs):
                    observed.append(snapshot_operation_context())
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("transport barrier not released")
                    return super().request_with_continuation(*args, **kwargs)

            client = Client()
            broker = CentralRestBroker(client)
            first = OperationContextSnapshot("first", "first-id")
            second = OperationContextSnapshot("second", "second-id")

            async def request(context):
                with operation_context_scope(context):
                    result = await broker.request("ka00001", "/api/dostk/acnt", {})
                    self.assertEqual(context, snapshot_operation_context())
                    return result

            task = asyncio.create_task(request(first))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            try:
                with patch("kiwoom_monitor.central_server.rest_broker.logger.info", side_effect=OSError("sink unavailable")) as sink:
                    waiter = asyncio.create_task(request(second))
                    await asyncio.sleep(0)
                    release.set()
                    results = await asyncio.gather(task, waiter)
                self.assertEqual(1, sink.call_count)
                self.assertEqual([first], observed)
                self.assertEqual(1, len(client.calls))
                self.assertEqual(results[0], results[1])
                self.assertEqual({}, broker._inflight)
            finally:
                release.set()
                await broker.close()
            self.assertEqual(OperationContextSnapshot(), snapshot_operation_context())

        asyncio.run(scenario())


class RestBrokerOptionalInputBoundaryTests(unittest.TestCase):
    def test_actual_full_source_broker_runs_with_all_diagnostic_modules_physically_absent(self):
        import importlib.util
        import json
        import shutil
        import subprocess
        import sys
        import textwrap
        with tempfile.TemporaryDirectory(prefix="rest-tools-absent-") as directory:
            root = Path(directory).resolve()
            source = root / "src"
            package = source / "kiwoom_monitor"
            original = Path(__file__).resolve().parents[2] / "src" / "kiwoom_monitor"
            shutil.copytree(original, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            removed = []
            for path in package.rglob("diagnostic*.py"):
                resolved = path.resolve()
                self.assertTrue(resolved.is_relative_to(package.resolve()))
                removed.append(path.relative_to(source).as_posix())
                path.unlink()
            self.assertGreater(len(removed), 10)
            self.assertTrue((package / "central_server" / "rest_broker.py").is_file())
            self.assertTrue((package / "infrastructure" / "kiwoom_rest" / "client.py").is_file())
            program = r'''
import asyncio, importlib.util, json, sys, threading
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import kiwoom_monitor.central_server.rest_broker as module
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
root = Path(sys.argv[1]).resolve()
assert Path(module.__file__).resolve() == root/'kiwoom_monitor/central_server/rest_broker.py'
assert importlib.util.find_spec('kiwoom_monitor.central_server.diagnostic_rest_input') is None
assert not list((root/'kiwoom_monitor').rglob('diagnostic*.py'))
entered, release = threading.Event(), threading.Event()
calls, effects = [], []
main_thread = threading.get_ident()
class Client:
    def request_with_continuation(self, api_id, path, body, **continuation):
        assert threading.get_ident() != main_thread
        calls.append((api_id, body.copy(), continuation))
        entered.set()
        assert release.wait(3)
        return {'code': body.get('stk_cd'), 'return_code': 0}, False, 'source-next'
class Store:
    def __init__(self): self.values = {}; self.fail = False
    def load_query(self, key): return self.values.get(key)
    def save_query(self, key, api, expiry, result):
        effects.append('cache')
        if self.fail: raise OSError('fixture cache failure')
        self.values[key] = result
store = Store()
fail = [False]
def ingest(*args):
    effects.append('ingest')
    if fail[0]: raise OSError('fixture native handler failure')
broker = CentralRestBroker(Client(), store, ingest, wall_time=lambda: datetime.fromisoformat('2001-04-03T09:00:02+09:00').timestamp())
async def exercise():
    first = second = None
    try:
        first = asyncio.create_task(broker.request('ka10001','/api/dostk/stkinfo',{'stk_cd':'005930'}))
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(broker.request('ka10001','/api/dostk/stkinfo',{'stk_cd':'005930'}))
        await asyncio.sleep(0)
        first.cancel()
        try: await first
        except asyncio.CancelledError: pass
        else: raise AssertionError('waiter cancellation swallowed')
        assert len(broker._inflight) == 1
        release.set()
        result = await second
        assert result.payload == {'code':'005930','return_code':0}
        assert (result.has_next,result.next_key,result.recording_succeeded) == (False,'source-next',True)
        assert len(calls) == 1 and effects == ['ingest','cache']
        fail[0] = True
        result = await broker.request('ka10001','/api/dostk/stkinfo',{'stk_cd':'000660'})
        assert result.recording_succeeded is False and result.payload['code'] == '000660'
        fail[0] = False; store.fail = True
        result = await broker.request('ka10001','/api/dostk/stkinfo',{'stk_cd':'000270'})
        assert result.recording_succeeded is True
        await broker.close()
        assert not broker._inflight and not broker._lookup_tasks
        assert broker._worker is None and broker._persist_worker is None
        assert all(q.empty() and q._unfinished_tasks == 0 for q in (broker._queue,broker._persist_queue))
        result = await broker.request_unrecorded('ka10001','/api/dostk/stkinfo',{'stk_cd':'035420'})
        assert result.recording_succeeded is None and result.payload['code'] == '035420'
        await broker.close()
        assert len(calls) == 4
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first,second) if t is not None),return_exceptions=True)
        await broker.close()
asyncio.run(exercise())
assert not any(name.startswith('kiwoom_monitor.') and '.diagnostic' in name for name in sys.modules)
print(json.dumps({'actual_broker_origin':module.__file__,'tools_physically_absent':True,'native_transport_ingest_cache_cancel_restart':True}))
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-c", textwrap.dedent(program), str(source)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            proof = json.loads(result.stdout.strip())
            self.assertTrue(proof["tools_physically_absent"])
            self.assertTrue(proof["native_transport_ingest_cache_cancel_restart"])
            self.assertEqual(package / "central_server" / "rest_broker.py", Path(proof["actual_broker_origin"]))



if __name__ == "__main__":
    unittest.main()
