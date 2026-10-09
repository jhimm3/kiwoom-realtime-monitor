from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import tempfile
from threading import Event, Lock
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    capture_owner, freeze_payload, install_store_capture, thaw_payload,
)
from kiwoom_monitor.central_server.diagnostic_top20_flow_input import (
    VERSION, _replay_candidate_flow, candidate_flow_cycle,
)
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker

NOW = datetime.fromisoformat("2026-10-07T09:01:02+09:00")
DAY, CODE = "2026-10-07", "005930"
PAYLOAD = {"stk_orgn_trde_trnsn": [{"dt": "20261007", "frgnr_netprps": "10", "orgn_netprps": "20"}]}


class Client:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.codes = []
        self.entered, self.release = Event(), Event()
        self.release.set()

    def request_with_continuation(self, api_id, path, body, **kwargs):
        self.codes.append(body["stk_cd"])
        self.entered.set()
        self.release.wait(3)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {**copy.deepcopy(outcome), "unused_response_field": "discard"}, False, ""


@contextmanager
def capture_events(events, *, store_inputs=True):
    lock = Lock()
    def emit(token, event_type, fields, value=None):
        with lock:
            row = {"seq": len(events) + 1, "event_type": event_type, **fields}
            if value is not None:
                row["payload"] = freeze_payload(value).value
            events.append(row)
        return True
    with patch.object(trace, "input_token", side_effect=lambda key:
            "flow-fixture" if store_inputs and key == "store_inputs" else None), \
            patch.object(trace, "emit_payload", side_effect=emit), patch.object(trace, "emit", side_effect=emit):
        yield


def identifier(events):
    return next(row["input_id"] for row in events if row.get("market_input_version") == VERSION)


class FlowInputTests(unittest.IsolatedAsyncioTestCase):
    async def source(self, store, outcomes, events=None):
        client = Client(outcomes)
        broker = CentralRestBroker(client, store, MarketDataIngestor(store).ingest)
        service = AutonomousTop20Service(broker, None, store, now_provider=lambda: NOW)
        try:
            if events is None:
                with patch.object(trace, "input_token", return_value=None):
                    await service._capture_candidate_investor_flow(CODE, DAY)
            else:
                with capture_events(events), capture_owner("top20", "ranking:parent", "ranking:actor", cause_input_id="ranking-root"):
                    await service._capture_candidate_investor_flow(CODE, DAY)
        finally:
            await broker.close()
        return client

    def store(self, directory, name="monitor"):
        store = SQLiteQueryStore(Path(directory) / (name + ".sqlite3"))
        store.initialize()
        install_store_capture(store)
        self.addCleanup(store.close)
        return store

    async def test_native_off_on_match_and_repeated_replay_uses_real_ingestor_without_historical_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            off, on = self.store(directory, "off"), self.store(directory, "on")
            await self.source(off, [OSError("private error"), PAYLOAD])
            events = []
            client = await self.source(on, [OSError("private error"), PAYLOAD], events)
            self.assertEqual([CODE + "_AL", CODE], client.codes)
            self.assertEqual(off.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1)[0]["document"],
                             on.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1)[0]["document"])
            self.assertEqual(off.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"],
                             on.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"])
            self.assertNotIn("private error", str(events))
            self.assertNotIn("unused_response_field", str(events))
            flow_id = identifier(events)
            self.assertTrue(all(row["parent_input_id"] == "ranking-root" for row in events if row.get("event_type") == "market_input"))
            starts = [row for row in events if row.get("event_type") == "operation_start"]
            self.assertEqual({"load_documents", "save_dataset_snapshot", "upsert_documents"}, {row["method"] for row in starts})
            self.assertTrue(all(row["cause_input_id"] == flow_id for row in starts))
            self.assertTrue(any(row["producer_component"].startswith("rest_broker:") for row in starts))
            events.append({"seq": len(events) + 1, "event_type": "operation_start",
                           "operation_id": "unrelated-peer", "cause_input_id": "other", "method": "not-replayed"})
            for iteration in range(3):
                replay = self.store(directory, f"replay{iteration}")
                with patch.object(trace, "input_token", return_value=None), \
                        patch.object(replay, "save_dataset_snapshot", wraps=replay.save_dataset_snapshot) as saved:
                    result = await _replay_candidate_flow(replay, events, flow_id)
                self.assertEqual(1, saved.call_count)
                self.assertEqual("returned", result["outcome"])
                self.assertEqual(2, result["attempts"])
                self.assertNotIn("unrelated-peer", result["historical_descendant_operations_not_executed"])
                self.assertEqual(on.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"],
                                 replay.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"])

    async def test_invalid_target_day_and_empty_response_preserve_ingested_data_without_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, payload in enumerate(({"stk_orgn_trde_trnsn": []}, {"stk_orgn_trde_trnsn": [{"dt": "20261006"}]})):
                source = self.store(directory, f"source{index}")
                events = []
                with self.assertRaises(RuntimeError):
                    await self.source(source, [payload], events)
                replay = self.store(directory, f"replay{index}")
                result = await _replay_candidate_flow(replay, events, identifier(events))
                self.assertEqual("error", result["outcome"])
                self.assertEqual(source.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"],
                                 replay.load_dataset_snapshots("investor_flow", CODE, 1)[0]["payload"])
                self.assertFalse(replay.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))

    async def test_cached_response_requires_existing_baseline_data_and_does_not_reingest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.store(directory, "source")
            client = Client([PAYLOAD])
            broker = CentralRestBroker(client, source, MarketDataIngestor(source).ingest)
            body = {"stk_cd": CODE + "_AL", "strt_dt": "20261007", "end_dt": "20261007", "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1"}
            await broker.request("ka10045", "/api/dostk/mrkcond", body)
            events = []
            try:
                with capture_events(events):
                    await AutonomousTop20Service(broker, None, source, now_provider=lambda: NOW)._capture_candidate_investor_flow(CODE, DAY)
            finally:
                await broker.close()
            attempt = candidate_flow_cycle(events, identifier(events))[2][0]
            self.assertEqual("ram_cache", attempt["receipt"]["origin"])
            replay = self.store(directory, "replay")
            with self.assertRaisesRegex(ValueError, "cache_dependency_missing"):
                await _replay_candidate_flow(replay, events, identifier(events))
            self.assertFalse(replay.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))
            replay.save_dataset_snapshot("investor_flow", CODE, "20261007:SOR", {"market": "SOR", "rows": PAYLOAD["stk_orgn_trde_trnsn"]})
            with patch.object(replay, "save_dataset_snapshot", wraps=replay.save_dataset_snapshot) as saved:
                await _replay_candidate_flow(replay, events, identifier(events))
            saved.assert_not_called()

    async def test_shared_inflight_is_explicitly_unsupported_and_independent_peer_has_no_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(directory)
            client = Client([PAYLOAD, PAYLOAD])
            client.release.clear()
            broker = CentralRestBroker(client, store, MarketDataIngestor(store).ingest)
            events = []
            try:
                with capture_events(events):
                    service = AutonomousTop20Service(broker, None, store, now_provider=lambda: NOW)
                    first = asyncio.create_task(service._capture_candidate_investor_flow(CODE, DAY))
                    await asyncio.to_thread(client.entered.wait, 2)
                    second = asyncio.create_task(service._capture_candidate_investor_flow(CODE, DAY))
                    for _ in range(100):
                        if any(link["receipt"]["shared"] for link in broker._flow_inflight.values()):
                            break
                        await asyncio.sleep(.001)
                    client.release.set()
                    await asyncio.gather(first, second)
                    await broker.request("ka10045", "/api/dostk/mrkcond", {"stk_cd": "000660", "end_dt": "20261007"})
            finally:
                client.release.set()
                await broker.close()
            self.assertEqual(2, len(client.codes))
            ids = {row["input_id"] for row in events if row.get("event_type") == "market_input"}
            for flow_id in ids:
                with self.assertRaisesRegex(ValueError, "receipt_unsupported"):
                    candidate_flow_cycle(events, flow_id)
            writes = [row for row in events if row.get("event_type") == "operation_start" and row.get("method") == "save_dataset_snapshot"]
            self.assertEqual(2, len(writes))
            self.assertEqual("", writes[-1]["cause_input_id"])
            self.assertFalse(broker._flow_inflight)

    async def test_ack_loss_and_malformed_inputs_fail_before_replay_store_access(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(directory)
            native = store.save_dataset_snapshot
            def ack_loss(*args, **kwargs):
                native(*args, **kwargs)
                raise OSError("private lost ack")
            events = []
            with patch.object(store, "save_dataset_snapshot", side_effect=ack_loss), self.assertRaises(RuntimeError):
                await self.source(store, [PAYLOAD], events)
            self.assertTrue(store.load_dataset_snapshots("investor_flow", CODE, 1))
            with patch.object(store, "load_documents") as load, self.assertRaisesRegex(ValueError, "save_unconfirmed"):
                await _replay_candidate_flow(store, events, identifier(events))
            load.assert_not_called()

    async def test_replay_cancellation_waits_for_native_ingest_and_completion_write(self):
        with tempfile.TemporaryDirectory() as directory:
            source, replay = self.store(directory, "source"), self.store(directory, "replay")
            events = []
            await self.source(source, [PAYLOAD], events)
            entered, release = Event(), Event()
            native = replay.save_dataset_snapshot
            def blocked(*args, **kwargs):
                entered.set()
                release.wait(3)
                return native(*args, **kwargs)
            with patch.object(replay, "save_dataset_snapshot", side_effect=blocked):
                task = asyncio.create_task(_replay_candidate_flow(replay, events, identifier(events)))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(replay.load_dataset_snapshots("investor_flow", CODE, 1))
            self.assertTrue(replay.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))

    async def test_malformed_cycle_never_touches_replay_store(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.store(directory)
            events = []
            await self.source(source, [PAYLOAD], events)
            flow_id = identifier(events)
            mutations = ("sequence", "missing_clock", "shared", "unconfirmed", "wrong_request", "extra_payload", "cancelled")
            for mutation in mutations:
                damaged = copy.deepcopy(events)
                row = next(row for row in damaged if row.get("input_kind") == "attempt")
                value = thaw_payload(row["payload"])
                if mutation == "sequence":
                    damaged[0]["seq"] = True
                elif mutation == "missing_clock":
                    damaged[:] = [row for row in damaged if row.get("input_kind") != "marker_time"]
                    for seq, row in enumerate(damaged, 1):
                        row["seq"] = seq
                elif mutation == "shared":
                    value["receipt"]["shared"] = True
                elif mutation == "unconfirmed":
                    value["recording_succeeded"] = False
                elif mutation == "wrong_request":
                    value["body"]["stk_cd"] = "000660_AL"
                elif mutation == "extra_payload":
                    value["payload"]["unused_response_field"] = "unrecorded"
                else:
                    ending = next(row for row in damaged if row.get("input_kind") == "cycle_end")
                    ending["payload"] = freeze_payload({"outcome": "cancelled", "stage": "request", "error_type": "CancelledError"}).value
                row = next(row for row in damaged if row.get("input_kind") == "attempt")
                row["payload"] = freeze_payload(value).value
                with self.subTest(mutation=mutation), patch.object(source, "load_documents") as read:
                    with self.assertRaises(ValueError):
                        await _replay_candidate_flow(source, damaged, flow_id)
                    read.assert_not_called()

    async def test_new_ingest_failure_cannot_match_recorded_verification_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source, replay = self.store(directory, "source"), self.store(directory, "replay")
            events = []
            with self.assertRaises(RuntimeError):
                await self.source(source, [{"stk_orgn_trde_trnsn": []}], events)
            with patch.object(replay, "save_dataset_snapshot", side_effect=RuntimeError("new native save failure")), \
                    patch.object(replay, "upsert_documents") as marker:
                with self.assertRaisesRegex(RuntimeError, "new native save failure"):
                    await _replay_candidate_flow(replay, events, identifier(events))
                marker.assert_not_called()

    async def test_cancelled_baseline_read_drains_before_replay_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            source, replay = self.store(directory, "source"), self.store(directory, "replay")
            events = []
            await self.source(source, [PAYLOAD], events)
            entered, release = Event(), Event()
            native = replay.load_documents
            def blocked(*args, **kwargs):
                entered.set()
                release.wait(3)
                return native(*args, **kwargs)
            with patch.object(replay, "load_documents", side_effect=blocked):
                task = asyncio.create_task(_replay_candidate_flow(replay, events, identifier(events)))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(replay.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))

    async def test_cancelled_source_rejected_even_when_native_broker_write_later_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(directory)
            client = Client([PAYLOAD])
            client.release.clear()
            broker = CentralRestBroker(client, store, MarketDataIngestor(store).ingest)
            events = []
            try:
                with capture_events(events):
                    task = asyncio.create_task(AutonomousTop20Service(broker, None, store, now_provider=lambda: NOW)._capture_candidate_investor_flow(CODE, DAY))
                    await asyncio.to_thread(client.entered.wait, 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    client.release.set()
                    await broker.close()
            finally:
                client.release.set()
                await broker.close()
            self.assertTrue(store.load_dataset_snapshots("investor_flow", CODE, 1))
            self.assertFalse(store.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))
            with self.assertRaises(ValueError):
                candidate_flow_cycle(events, identifier(events))
            write = next(row for row in events if row.get("event_type") == "operation_start" and row["method"] == "save_dataset_snapshot")
            self.assertEqual(identifier(events), write["cause_input_id"])

    async def test_existing_marker_skip_is_baseline_dependent_and_never_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            source, replay = self.store(directory, "source"), self.store(directory, "replay")
            await self.source(source, [PAYLOAD])
            events = []
            client = await self.source(source, [], events)
            self.assertFalse(client.codes)
            with self.assertRaisesRegex(ValueError, "baseline_marker_mismatch"):
                await _replay_candidate_flow(replay, events, identifier(events))
            replay.upsert_documents("candidate_flow_capture", source.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))
            with patch.object(replay, "save_dataset_snapshot") as saved, patch.object(replay, "upsert_documents") as marker:
                result = await _replay_candidate_flow(replay, events, identifier(events))
            self.assertEqual(0, result["attempts"])
            saved.assert_not_called()
            marker.assert_not_called()

    async def test_observer_errors_leave_native_success_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(directory)
            events = []
            with patch.object(trace, "reject_input") as rejected, \
                    patch.object(trace, "emit_payload", side_effect=OSError("observer")), \
                    patch.object(trace, "input_token", side_effect=lambda key: "capture" if key == "store_inputs" else None):
                client = Client([PAYLOAD])
                broker = CentralRestBroker(client, store, MarketDataIngestor(store).ingest)
                try:
                    await AutonomousTop20Service(broker, None, store, now_provider=lambda: NOW)._capture_candidate_investor_flow(CODE, DAY)
                finally:
                    await broker.close()
            self.assertTrue(rejected.called)
            self.assertTrue(store.load_documents("candidate_flow_capture", DAY + ":" + CODE, 1))
