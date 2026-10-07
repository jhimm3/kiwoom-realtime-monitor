from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import tempfile
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner, freeze_payload, operation_identity
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.diagnostic_rest_input import (
    EVENT, RequestTapeClient, TapeInputUnavailable, read_request_tape,
)
from kiwoom_monitor.central_server.rest_broker import BrokerResult, CentralRestBroker
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub

PATH = "/api/dostk/stkinfo"


@contextmanager
def recording(events):
    lock = Lock()
    def emit(token, kind, fields, value):
        with lock:
            events.append({"seq": len(events) + 1, "event_type": kind, **fields,
                           "payload": freeze_payload(value).value})
        return True
    with patch.object(trace, "input_token", side_effect=lambda kind: "tape-test" if kind == "top20_inputs" else None), \
            patch.object(trace, "emit_payload", side_effect=emit):
        yield


class Client:
    def __init__(self):
        self.calls = []
        self.entered, self.release = Event(), Event()
        self.release.set()
        self.error = None

    def request_with_continuation(self, api_id, path, body, **kwargs):
        self.calls.append((api_id, body, kwargs))
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError("blocked fixture")
        if self.error:
            raise self.error
        return {"code": body.get("stk_cd"), "return_code": 0}, kwargs["cont_yn"] == "N", "page2"


class Store:
    def __init__(self):
        self.queries = {}
        self.effects = []
        self.documents = []

    def load_query(self, key):
        self.effects.append(("load", operation_identity()))
        return self.queries.get(key)

    def save_query(self, key, api_id, expiry, value):
        self.effects.append(("save", operation_identity()))
        self.queries[key] = value

    def load_documents(self, *args):
        return self.documents

    def replace_documents(self, collection, values):
        self.effects.append(("replace_documents", operation_identity()))
        self.documents = [{"key": row["key"], "document": row["document"]} for row in values]


class RequestInputTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, *, cached=False, failure=False):
        events, store, client = [], Store(), Client()
        if failure:
            client.error = TimeoutError("secret response text must not be recorded")
        handlers = []
        broker = CentralRestBroker(client, store,
            lambda *args: handlers.append((args, operation_identity())))
        try:
            with recording(events), capture_owner("top20", "service:one", "lane:basics", cause_input_id="ranking-root"):
                if failure:
                    with self.assertRaises(TimeoutError):
                        await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                else:
                    await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                    if cached:
                        await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                        broker._cache.clear()
                        await broker.request("ka10001", PATH, {"stk_cd": "005930"})
        finally:
            await broker.close()
        return events, store, client, handlers

    async def test_transport_and_native_cache_effects_are_linked_without_duplicate_payloads(self):
        events, store, client, handlers = await self.collect(cached=True)
        tape = read_request_tape(events)
        calls = list(tape["calls"].values())
        self.assertEqual(["network", "ram_cache", "persistent_cache"], [call["end"]["origin"] for call in calls])
        self.assertEqual(1, len(client.calls))
        self.assertEqual(2, len(handlers))
        self.assertEqual(1, sum(len(call["transport"]) // 2 for call in calls))
        first_id = next(iter(tape["calls"]))
        self.assertEqual("ranking-root", calls[0]["start"]["parent_input_id"])
        self.assertEqual(first_id, handlers[0][1]["cause_input_id"])
        self.assertEqual(first_id, next(value for name, value in store.effects if name == "save")["cause_input_id"])

    async def test_native_broker_replay_recomputes_cache_and_handler_and_does_not_inject_old_cache(self):
        events, _, _, _ = await self.collect(cached=True)
        tape = read_request_tape(events)
        client, store, handlers = RequestTapeClient(tape, preserve_transport_delay=False), Store(), []
        broker = CentralRestBroker(client, store, lambda *args: handlers.append(args))
        try:
            with client.lane("lane:basics"):
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                broker._cache.clear()
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
            self.assertEqual(2, len(handlers))
            self.assertEqual(1, len(client.used))
            self.assertIsNone(client.failure)
            self.assertEqual([], client.report()["unused_external_inputs"])
        finally:
            await broker.close()
        # If a new run loses the cache path, a past cache-hit payload is unavailable
        # as a transport input; nothing seeds it into the new store.
        client, empty = RequestTapeClient(tape, preserve_transport_delay=False), Store()
        broker = CentralRestBroker(client, empty)
        try:
            with client.lane("lane:basics"):
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                broker._cache.clear()
                empty.queries.clear()
                with self.assertRaises(TapeInputUnavailable):
                    await broker.request("ka10001", PATH, {"stk_cd": "005930"})
            self.assertEqual("market_tape_cache_hit_has_no_transport", client.failure)
        finally:
            await broker.close()

    async def test_shared_requests_keep_all_parents_and_only_one_native_transport(self):
        events, client = [], Client()
        client.release.clear()
        broker = CentralRestBroker(client)
        async def request(lane):
            with capture_owner("top20", "service:one", lane):
                return await broker.request("ka10001", PATH, {"stk_cd": "005930"})
        try:
            with recording(events):
                a = asyncio.create_task(request("lane:a"))
                self.assertTrue(await asyncio.to_thread(client.entered.wait, 2))
                b = asyncio.create_task(request("lane:b"))
                await asyncio.sleep(.01)
                client.release.set()
                await asyncio.gather(a, b)
            tape = read_request_tape(events)
            primary = next(call for call in tape["calls"].values() if call["transport"])
            self.assertEqual(set(tape["calls"]), set(primary["transport"][-1][1]["parent_request_ids"]))
            self.assertEqual(1, len(client.calls))
        finally:
            client.release.set()
            await broker.close()

    async def test_failure_pairs_replay_error_without_leaking_error_text(self):
        events, _, _, _ = await self.collect(failure=True)
        tape = read_request_tape(events)
        self.assertNotIn("secret response text", str(events))
        client = RequestTapeClient(tape, preserve_transport_delay=False)
        broker = CentralRestBroker(client)
        try:
            with client.lane("lane:basics"), self.assertRaises(TimeoutError):
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
            self.assertIsNone(client.failure)
        finally:
            await broker.close()

    async def test_continuation_identity_and_missing_or_changed_input_fail_closed(self):
        events, client = [], Client()
        broker = CentralRestBroker(client)
        try:
            with recording(events), capture_owner("top20", "service:one", "lane:pages"):
                await broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}, cont_yn="Y", next_key="key1")
        finally:
            await broker.close()
        tape = read_request_tape(events)
        client = RequestTapeClient(tape, preserve_transport_delay=False)
        broker = CentralRestBroker(client)
        try:
            with client.lane("lane:pages"), self.assertRaises(TapeInputUnavailable):
                await broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930"}, cont_yn="Y", next_key="wrong")
            self.assertEqual("market_tape_logical_input_unavailable", client.failure)
        finally:
            await broker.close()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            read_request_tape(events[:-1])
        damaged = copy.deepcopy(events)
        damaged[-1]["producer_component"] = "wrong"
        with self.assertRaisesRegex(ValueError, "owner_changed"):
            read_request_tape(damaged)

    async def test_catalog_native_update_replay_and_cache_avoid_unneeded_loader(self):
        events, store, client = [], Store(), Client()
        broker = CentralRestBroker(client)
        count = []
        def loader():
            count.append(True)
            return (("005930", "삼성전자", "KOSPI"),)
        now = datetime.fromisoformat("2001-04-03T09:00:02+09:00")
        service = AutonomousTop20Service(broker, None, store, catalog_loader=loader, now_provider=lambda: now)
        with recording(events), capture_owner("top20", "service:one", "lane:catalog"):
            await service._ensure_market_catalog("2001-04-03")
            await service._ensure_market_catalog("2001-04-03")
        self.assertEqual(1, len(count))
        tape = read_request_tape(events)
        self.assertEqual(next(iter(tape["catalogs"])), store.effects[-1][1]["cause_input_id"])
        client, other = RequestTapeClient(tape, preserve_transport_delay=False), Store()
        service = AutonomousTop20Service(broker, None, other,
            catalog_loader=client.catalog_loader("lane:catalog"), now_provider=lambda: now)
        await service._ensure_market_catalog("2001-04-03")
        self.assertEqual(store.documents, other.documents)
        await service._ensure_market_catalog("2001-04-03")
        self.assertEqual(1, len(client.used))
        self.assertFalse(client.report()["top20_session_execution_ready"])

    async def test_capture_off_and_account_paths_do_not_copy_or_change_results(self):
        client, events = Client(), []
        broker = CentralRestBroker(client)
        try:
            with patch.object(trace, "input_token", return_value=None), patch.object(trace, "emit_payload") as emit:
                result = await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                emit.assert_not_called()
                self.assertEqual("005930", result.payload["code"])
            with recording(events):
                await broker.request("kt00018", "/api/dostk/acnt", {})
            self.assertEqual([], events)
        finally:
            await broker.close()

    async def test_omitting_an_unneeded_different_request_does_not_shift_the_remaining_lane(self):
        events, client = [], Client()
        broker = CentralRestBroker(client)
        try:
            with recording(events), capture_owner("top20", "service:one", "lane:prep"):
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
                await broker.request("ka10100", PATH, {"stk_cd": "005930"})
        finally:
            await broker.close()
        tape = read_request_tape(events)
        client = RequestTapeClient(tape, preserve_transport_delay=False)
        broker = CentralRestBroker(client)
        try:
            with client.lane("lane:prep"):
                await broker.request("ka10100", PATH, {"stk_cd": "005930"})
            self.assertIsNone(client.failure)
            self.assertEqual(1, len(client.report()["unused_external_inputs"]))
        finally:
            await broker.close()
        missing_transport = [row for row in events if not row["input_kind"].startswith("transport_")]
        with self.assertRaisesRegex(ValueError, "transport_incomplete"):
            read_request_tape(missing_transport)

    async def test_late_shared_owner_during_effect_is_retained_but_rejected_as_uncertain(self):
        events, client = [], Client()
        entered, release = Event(), Event()
        def handler(*args):
            entered.set()
            release.wait(3)
        broker = CentralRestBroker(client, response_handler=handler)
        async def request(lane):
            with capture_owner("top20", "service:one", lane):
                await broker.request("ka10001", PATH, {"stk_cd": "005930"})
        try:
            with recording(events):
                a = asyncio.create_task(request("lane:a"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                # Force the native in-flight branch rather than its already
                # populated RAM cache. Ownership changes during the DB effect.
                broker._cache.clear()
                b = asyncio.create_task(request("lane:b"))
                await asyncio.sleep(.01)
                release.set()
                await asyncio.gather(a, b)
            with self.assertRaisesRegex(ValueError, "shared_effect_changed"):
                read_request_tape(events)
        finally:
            release.set()
            await broker.close()

    async def test_observer_rejection_does_not_break_native_request(self):
        broker = CentralRestBroker(Client())
        try:
            with recording([]), patch("kiwoom_monitor.central_server.diagnostic_rest_input._signature",
                    side_effect=ValueError("observer fixture")), patch.object(trace, "reject_input") as rejected:
                result = await broker.request("ka10001", PATH, {"stk_cd": "005930"})
            self.assertEqual("005930", result.payload["code"])
            rejected.assert_called_once()
        finally:
            await broker.close()

    async def test_membership_publication_timestamp_follows_delayed_source_response(self):
        origin = datetime.fromisoformat("2001-04-03T09:00:00+09:00")
        published = datetime.fromisoformat("2001-04-03T09:00:02+09:00")
        now = [origin]
        items = [{"stk_cd": f"{index:06d}"} for index in range(20)]
        store = Store()
        store.save_dataset_snapshot = lambda *args, **kwargs: None
        service = AutonomousTop20Service(None, RealtimeHub(), store, now_provider=lambda: now[0])
        service._entrants_day = "2001-04-03"
        service._entrant_persisted_codes = {item["stk_cd"] for item in items}
        async def delayed(*args):
            now[0] = published
            return BrokerResult({}, False, ""), items, False, False
        with patch.object(service, "_select_ranking_response", side_effect=delayed), \
                patch.object(service, "_schedule_market_catalog"), patch.object(service, "_schedule_subscription_update"), \
                patch.object(service, "_advance", return_value=SimpleNamespace(completed=None)):
            await service.refresh_ranking_once(origin)
        projection = service.latest_membership_snapshot("2001-04-03")
        self.assertEqual(published.timestamp(), projection["saved_at"])
        self.assertIn("09:00:00", projection["payload"]["observed_at"])

    async def test_native_daily_ingestor_and_changed_callback_run_with_same_recorded_response(self):
        events = []
        payload = {"stk_dt_pole_chart_qry": [{"dt": "20010402", "open_pric": "100", "high_pric": "120",
            "low_pric": "90", "cur_prc": "110", "trde_qty": "1000"}]}
        class DailyClient:
            def request_with_continuation(self, *args, **kwargs):
                return payload, False, ""
        with tempfile.TemporaryDirectory() as root:
            for mode in ("capture", "replay"):
                store = SQLiteQueryStore(Path(root) / (mode + ".sqlite3"))
                store.initialize()
                changes = []
                client = DailyClient() if mode == "capture" else RequestTapeClient(read_request_tape(events), preserve_transport_delay=False)
                ingestor = MarketDataIngestor(store,
                    now_provider=lambda: datetime.fromisoformat("2001-04-03T09:00:02+09:00"),
                    on_daily_change=lambda *args: changes.append(args))
                broker = CentralRestBroker(client, store, ingestor.ingest)
                try:
                    scope = recording(events) if mode == "capture" else client.lane("lane:daily")
                    with scope, capture_owner("top20", "service:one", "lane:daily"):
                        result = await broker.request("ka10081", "/api/dostk/chart", {"stk_cd": "005930", "base_dt": "20010403"})
                    self.assertTrue(result.recording_succeeded)
                    self.assertEqual([("005930", "KRX")], changes)
                    self.assertEqual(1, len(store.load_daily_bars("005930", "KRX", 250)))
                finally:
                    await broker.close()
                    store.close()

    async def test_capture_started_mid_request_keeps_native_result_and_rejects_missing_primary(self):
        events, client = [], Client()
        client.release.clear()
        broker = CentralRestBroker(client)
        try:
            with patch.object(trace, "input_token", return_value=None):
                first = asyncio.create_task(broker.request("ka10001", PATH, {"stk_cd": "005930"}))
                self.assertTrue(await asyncio.to_thread(client.entered.wait, 2))
            with recording(events):
                second = asyncio.create_task(broker.request("ka10001", PATH, {"stk_cd": "005930"}))
                await asyncio.sleep(.01)
                client.release.set()
                results = await asyncio.gather(first, second)
            self.assertEqual(results[0].payload, results[1].payload)
            self.assertEqual(1, len(client.calls))
            with self.assertRaisesRegex(ValueError, "origin_missing"):
                read_request_tape(events)
        finally:
            client.release.set()
            await broker.close()

    async def test_tape_client_owns_input_snapshot_and_rejects_dropped_effect_pairs(self):
        events, _, _, _ = await self.collect()
        tape = read_request_tape(events)
        client = RequestTapeClient(tape, preserve_transport_delay=False)
        first = next(iter(tape["calls"]))
        tape["calls"][first]["transport"][-1][1]["result"][0]["code"] = "tampered"
        broker = CentralRestBroker(client)
        try:
            with client.lane("lane:basics"):
                result = await broker.request("ka10001", PATH, {"stk_cd": "005930"})
            self.assertEqual("005930", result.payload["code"])
        finally:
            await broker.close()
        missing_effect = [row for row in events if row["input_kind"] not in {"effect_start", "effect_end"}]
        with self.assertRaisesRegex(ValueError, "effect_incomplete"):
            read_request_tape(missing_effect)
