from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    CODEC_VERSION, capture_owner, compile_top20_queue_frontier, freeze_payload,
    install_store_capture, operation_identity,
)
from kiwoom_monitor.central_server.diagnostic_top20_input import (
    compile_delivery_coverage, consumed_delivery,
)
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import CapturedHubEvent, RealtimeHub


NOW = datetime.fromisoformat("2026-10-07T09:00:00+09:00")


def message(count=1):
    return {"trnm": "REAL", "data": [
        {"type": "0B", "item": "005930_AL", "values": {
            "10": "100", "13": str(index + 1), "14": str(index + 1),
            "15": "1", "20": "090000"}} for index in range(count)]}


class Top20DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.epoch = "epoch-one"
        self.enabled = True
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(trace, "input_token", side_effect=lambda kind:
            self.epoch if self.enabled and kind in {"collector_inputs", "top20_inputs", "store_inputs"} else None))
        self.stack.enter_context(patch.object(trace, "emit", side_effect=self.scalar))
        self.stack.enter_context(patch.object(trace, "emit_payload", side_effect=self.payload))
        self.stack.enter_context(patch.object(trace, "reject_input", side_effect=lambda token, fields:
            self.scalar(token, "input_rejected", fields)))
        self.addCleanup(self.stack.close)
        self.hub = RealtimeHub()
        self.collector = CentralRealtimeCollector(lambda: "", "real", self.hub, lambda: NOW)
        self.component = "autonomous_top20:selected"
        self.subscriber = self.hub.connect(capture_component=self.component)

    def scalar(self, token, kind, fields):
        self.events.append({"seq": len(self.events) + 1, "mono_ns": len(self.events) + 1,
                            "event_type": kind, **copy.deepcopy(fields)})

    def payload(self, token, kind, fields, value):
        self.scalar(token, kind, {**fields, "payload": freeze_payload(value).value})
        return True

    async def drain(self, subscriber=None):
        subscriber = subscriber or self.subscriber
        values, identities = [], []
        while not subscriber.queue.empty():
            event = subscriber.queue.get_nowait()
            with consumed_delivery(subscriber, event):
                values.append(dict(event))
                identities.append(operation_identity())
                # A newly spawned coroutine gets the consumed delivery cause,
                # independently of the producer task's ContextVar lifetime.
                async def child():
                    return operation_identity()
                self.assertEqual(identities[-1], await asyncio.create_task(child()))
        return values, identities

    def coverage(self, events=None, component=None):
        return compile_delivery_coverage(events or self.events, trace_id=self.epoch,
                                         component=component or self.component)

    def manifest(self, started_mono_ns=0):
        content = b"\n".join(json.dumps(row).encode() for row in self.events) + b"\n"
        coverage = {}
        for row in self.events:
            if "payload" in row:
                group = row.get("workload_id", "unsupported")
                coverage.setdefault(group, {"accepted": 0, "rejected": 0})["accepted"] += 1
        return {"trace_id": self.epoch, "state": "complete", "schema_version": 3,
                "coverage": "observed_paths_only", "input_capture_censored": False,
                "payload_capture": {key: True for key in ("store_inputs", "collector_inputs", "top20_inputs")},
                "accepted": len(self.events), "written": len(self.events), "last_seq": len(self.events),
                "known_dropped": 0, "input_rejected": 0, "queued": 0, "pending_events": 0,
                "copy_reserved_bytes": 0, "drop_reasons": {}, "input_rejected_reasons": {},
                "input_coverage": coverage, "payload_accepted": sum(item["accepted"] for item in coverage.values()),
                "started_mono_ns": started_mono_ns, "finished_mono_ns": started_mono_ns + 10**18,
                "bytes_written": len(content), "chunks": [{"name": "000001.jsonl", "first_seq": 1,
                    "last_seq": len(self.events), "count": len(self.events), "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}]}

    async def test_json_and_native_order_match_off_capture_and_two_subscribers_are_independent(self):
        peer = self.hub.connect(capture_component="autonomous_top20:peer")
        self.collector._publish_parsed(message(3))
        first = self.subscriber.queue._queue[0]
        second = peer.queue._queue[0]
        self.assertEqual(json.dumps(first), json.dumps(second))
        self.assertEqual({"type", "payload"}, set(first))
        self.assertIsNot(first, second)
        self.assertNotEqual(first.delivery_receipt["delivery_id"], second.delivery_receipt["delivery_id"])
        on, ownership = await self.drain()
        await self.drain(peer)
        self.assertEqual(3, len(self.coverage()["delivery_ids"]))
        self.assertEqual(3, len(self.coverage(component=peer.capture_component)["delivery_ids"]))
        self.assertEqual(self.component, ownership[0]["producer_component"])
        self.assertEqual(first.delivery_receipt["delivery_id"], ownership[0]["cause_input_id"])
        self.enabled = False
        self.collector._publish_parsed(message(3))
        off, _ = await self.drain()
        self.assertEqual(on, off)
        self.assertFalse(self.coverage()["top20_session_execution_ready"])

    async def test_split_message_duplicate_ticks_use_parser_ordinals_and_all_fragments(self):
        # Identical ticks cannot be matched by value; retain their distinct ordinals.
        source = message(101)
        source["data"] = [source["data"][0]] * 101
        self.collector._publish_parsed(source)
        await self.drain()
        result = self.coverage()
        self.assertEqual(101, len(result["delivery_ids"]))
        self.assertEqual(2, len(result["parent_input_ids"]))
        missing = copy.deepcopy(self.events)
        missing = [row for row in missing if row.get("input_id") != result["parent_input_ids"][0]]
        for index, row in enumerate(missing, 1):
            row["seq"] = index
        with self.assertRaisesRegex(ValueError, "parent_missing_or_shared"):
            self.coverage(missing)

    async def test_overflow_keeps_native_latest_and_rejects_coverage(self):
        self.subscriber.queue = asyncio.Queue(maxsize=1)
        self.collector._publish_parsed(message(2))
        values, _ = await self.drain()
        self.assertEqual(1, self.subscriber.dropped_events)
        self.assertEqual(2, values[0]["payload"]["cumulative_volume"])
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()

    async def test_epoch_change_and_pre_capture_queue_never_reuse_old_cause(self):
        self.collector._publish_parsed(message())
        self.epoch = "epoch-two"
        _, identities = await self.drain()
        self.assertEqual("", identities[0]["cause_input_id"])
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()
        self.events.clear()
        self.enabled = False
        self.collector._publish_parsed(message())
        self.enabled = True
        await self.drain()
        self.assertEqual("untracked_dequeue", self.events[0]["stage"])
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()

    async def test_rejected_fragment_and_clipped_trace_fail_closed(self):
        original = self.payload
        def reject(token, kind, fields, value):
            if fields.get("input_kind") == "message":
                self.scalar(token, "input_rejected", {**fields, "reason": "capture_copy_busy"})
                return False
            return original(token, kind, fields, value)
        with patch.object(trace, "emit_payload", side_effect=reject):
            self.collector._publish_parsed(message())
        await self.drain()
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()
        with self.assertRaisesRegex(ValueError, "sequence_gap"):
            self.coverage(self.events[1:])

    async def test_missing_market_operation_and_plain_clone_do_not_claim_source_coverage(self):
        self.hub.publish({"type": "market_operation", "payload": {"status_code": "3"}})
        await self.drain()
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()

        self.events.clear()
        self.collector._publish_parsed(message())
        event = self.subscriber.queue.get_nowait()
        self.subscriber.queue.put_nowait(dict(event))
        await self.drain()
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()

    async def test_receipt_from_another_subscriber_cannot_acquire_selected_ownership(self):
        peer = self.hub.connect(capture_component="autonomous_top20:peer")
        self.collector._publish_parsed(message())
        self.subscriber.queue.get_nowait()
        self.subscriber.queue.put_nowait(peer.queue.get_nowait())
        _, identities = await self.drain()
        self.assertEqual("", identities[0]["cause_input_id"])
        with self.assertRaisesRegex(ValueError, "incomplete_or_epoch"):
            self.coverage()

    async def test_real_program_batch_crossing_minute_boundary_keeps_all_causes_without_last_tick_guess(self):
        class Store:
            def __init__(self):
                self.values = []
                install_store_capture(self)

            def save_dataset_snapshots(self, values):
                self.values.extend(values)

        store = Store()
        now = [NOW + timedelta(seconds=59)]
        self.collector._now_provider = lambda: now[0]
        service = AutonomousTop20Service(None, self.hub, store, now_provider=lambda: now[0])
        service._subscriber = self.subscriber
        task = asyncio.create_task(service._event_loop())
        try:
            for code, clock in (("005930", "090059"), ("000660", "090100")):
                self.collector._publish_parsed({"trnm": "REAL", "data": [
                    {"type": "0w", "item": code, "values": {"20": clock, "210": "5"}}]})
                for _ in range(20):
                    if self.subscriber.queue.empty():
                        break
                    await asyncio.sleep(0)
                self.assertTrue(self.subscriber.queue.empty())
                now[0] += timedelta(seconds=1)
            with capture_owner("top20", self.component, self.component + ":index-loop"):
                await service._flush_program_snapshots()
            self.assertEqual({"005930", "000660"}, {row[1] for row in store.values})
            starts = [row for row in self.events if row.get("event_type") == "operation_start"]
            self.assertEqual(1, len(starts))
            self.assertEqual("", starts[0]["cause_input_id"])
            coverage = self.coverage()
            self.assertEqual(2, len(coverage["delivery_ids"]))
            self.assertEqual(2, coverage["message_count"])
            result = compile_top20_queue_frontier(self.events, trace_id=self.epoch,
                manifest=self.manifest(starts[0]["entered_mono_ns"] - 1_000_000),
                component=self.component, started_mono_ns=starts[0]["entered_mono_ns"] - 1_000_000,
                window_start_seconds=0, window_end_seconds=1, include_workloads=("top20",))
            self.assertEqual((starts[0]["operation_id"],), result["replaced_operation_ids"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_observer_error_does_not_change_native_queue_or_result(self):
        with patch.object(trace, "emit", side_effect=RuntimeError("observer failure")), \
                patch.object(trace, "reject_input", side_effect=RuntimeError("observer failure")):
            self.collector._publish_parsed(message(2))
            values, _ = await self.drain()
        self.assertEqual([1, 2], [row["payload"]["cumulative_volume"] for row in values])

    async def test_real_event_loop_consumes_trade_and_program_without_payload_changes(self):
        service = AutonomousTop20Service(None, self.hub, None, now_provider=lambda: NOW)
        service._subscriber = self.subscriber
        native = message()
        native["data"].append({"type": "0w", "item": "005930_AL",
                               "values": {"20": "090000", "210": "5", "211": "2"}})
        self.collector._publish_parsed(native)
        task = asyncio.create_task(service._event_loop())
        try:
            for _ in range(20):
                if self.subscriber.queue.empty():
                    break
                await asyncio.sleep(0)
            self.assertTrue(self.subscriber.queue.empty())
            self.assertIn("005930", service._pending_program_snapshots)
            self.assertEqual(2, len(self.coverage()["delivery_ids"]))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def operation(self, identifier, component, kinds):
        fields = {"operation_id": identifier, "method": "save_dataset_snapshots",
                  "codec_version": CODEC_VERSION, "workload_id": "top20", "actor_known": True,
                  "producer_component": component, "actor_id": component + ":writer",
                  "actor_sequence": 1, "entered_mono_ns": 50}
        self.payload(self.epoch, "operation_start", fields,
                     {"values": [(kind, "scope", "minute", {"value": 1}, None) for kind in kinds]})
        self.scalar(self.epoch, "operation_end", {**fields, "outcome": "returned", "finished_mono_ns": 60})

    async def test_multiple_causes_replace_owned_batch_keep_peer_and_reject_mixed_transaction(self):
        self.collector._publish_parsed(message(2))
        await self.drain()
        self.operation("owned", self.component, ["program_flow", "top20_index"])
        self.operation("peer", "autonomous_top20:peer", ["program_flow"])
        options = dict(trace_id=self.epoch, component=self.component, started_mono_ns=0,
                       window_start_seconds=0, window_end_seconds=1, include_workloads=("top20",))
        result = compile_top20_queue_frontier(self.events, manifest=self.manifest(), **options)
        self.assertEqual(("owned",), result["replaced_operation_ids"])
        self.assertEqual(("peer",), result["operation_ids"])
        self.assertEqual(2, len(result["coverage"]["delivery_ids"]))
        self.assertFalse(result["top20_session_execution_ready"])
        self.operation("mixed", self.component, ["program_flow", "ranking"])
        with self.assertRaisesRegex(ValueError, "mixed_transaction"):
            compile_top20_queue_frontier(self.events, manifest=self.manifest(), **options)

    async def test_wrong_parent_component_and_shared_owner_fail_before_execution(self):
        self.collector._publish_parsed(message())
        await self.drain()
        altered = copy.deepcopy(self.events)
        for row in altered:
            if row.get("event_type") == "top20_delivery":
                row["source_component"] = "realtime_collector:unrecorded"
        with self.assertRaisesRegex(ValueError, "parent_missing_or_shared"):
            self.coverage(altered)
        self.operation("shared", self.component, ["program_flow"])
        self.events[-2]["shared_owner_components"] = [self.component, "peer"]
        with self.assertRaisesRegex(ValueError, "shared_or_unknown_owner"):
            compile_top20_queue_frontier(self.events, trace_id=self.epoch, component=self.component,
                manifest=self.manifest(),
                started_mono_ns=0, window_start_seconds=0, window_end_seconds=1,
                include_workloads=("top20",))

    async def test_complete_pair_prefix_cannot_hide_manifest_tail(self):
        self.collector._publish_parsed(message())
        await self.drain()
        prefix = copy.deepcopy(self.events)
        self.collector._publish_parsed(message())
        await self.drain()
        # The previous coverage-only checker accepts this complete first pair.
        self.assertEqual(1, len(self.coverage(prefix)["delivery_ids"]))
        with self.assertRaisesRegex(ValueError, "incomplete_or_not_drained"):
            compile_top20_queue_frontier(prefix, manifest=self.manifest(), trace_id=self.epoch,
                component=self.component, window_start_seconds=0, window_end_seconds=1,
                include_workloads=("top20",))

    async def test_manifest_counters_options_rejections_and_drain_must_agree(self):
        self.collector._publish_parsed(message())
        await self.drain()
        self.operation("owned", self.component, ["program_flow"])
        good = self.manifest()
        options = dict(trace_id=self.epoch, component=self.component,
                       window_start_seconds=0, window_end_seconds=1, include_workloads=("top20",))
        result = compile_top20_queue_frontier(self.events, manifest=good, **options)
        self.assertEqual("manifest_and_events", result["source_integrity"])
        self.assertFalse(result["top20_session_execution_ready"])
        mutations = [
            {"state": "persisting"}, {"schema_version": 2}, {"trace_id": "other-epoch"},
            {"accepted": good["accepted"] + 1}, {"written": True}, {"last_seq": good["last_seq"] + 1},
            {"known_dropped": 1}, {"input_rejected": 1}, {"input_capture_censored": True},
            {"queued": 1}, {"pending_events": 1}, {"copy_reserved_bytes": 1},
            {"drop_reasons": {"full": 1}}, {"input_rejected_reasons": {"copy_busy": 1}},
            {"payload_capture": {"store_inputs": True, "collector_inputs": True}},
            {"input_coverage": {}}, {"payload_accepted": good["payload_accepted"] + 1},
            {"finished_mono_ns": 500}, {"chunks": []},
        ]
        for fields in mutations:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                compile_top20_queue_frontier(self.events, manifest={**good, **fields}, **options)
        for key in ("input_capture_censored", "input_rejected", "input_coverage", "copy_reserved_bytes"):
            missing = copy.deepcopy(good)
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                compile_top20_queue_frontier(self.events, manifest=missing, **options)
        with self.assertRaisesRegex(ValueError, "manifest_incomplete"):
            compile_top20_queue_frontier(self.events, **options)
        with self.assertRaisesRegex(ValueError, "clock_mismatch"):
            compile_top20_queue_frontier(self.events, manifest=good, started_mono_ns=1, **options)

    async def test_zero_rejection_totals_cannot_hide_rejected_row_or_coverage(self):
        self.collector._publish_parsed(message())
        await self.drain()
        good = self.manifest()
        group = next(iter(good["input_coverage"]))
        good["input_coverage"][group]["rejected"] = 1
        with self.assertRaisesRegex(ValueError, "input_coverage_invalid"):
            trace.validate_top20_capture_manifest(good, self.events, trace_id=self.epoch)
        self.scalar(self.epoch, "input_rejected", {"reason": "copy_busy"})
        with self.assertRaisesRegex(ValueError, "sequence_or_rejection_invalid"):
            trace.validate_top20_capture_manifest(self.manifest(), self.events, trace_id=self.epoch)

    async def test_disk_frontier_checks_chunks_payloads_and_keeps_execution_closed(self):
        self.epoch = "20261007T090000Z-123456789abc"
        self.collector._publish_parsed(message())
        await self.drain()
        self.operation("owned", self.component, ["program_flow"])
        with tempfile.TemporaryDirectory() as root, patch.object(trace, "_directory", return_value=Path(root)):
            directory = Path(root) / self.epoch
            directory.mkdir()
            manifest = self.manifest()
            manifest["blobs"] = {}
            disk_rows = copy.deepcopy(self.events)
            payload_paths = []
            for row in disk_rows:
                if "payload" in row:
                    content = json.dumps(row.pop("payload")).encode()
                    digest = hashlib.sha256(content).hexdigest()
                    row["payload_ref"] = digest
                    path = directory / f"payload-{digest}.json"
                    path.write_bytes(content)
                    payload_paths.append(path)
                    manifest["blobs"][digest] = {"bytes": len(content)}
            content = b"\n".join(json.dumps(row).encode() for row in disk_rows) + b"\n"
            chunk = directory / "000001.jsonl"
            chunk.write_bytes(content)
            manifest["chunks"][0].update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
            manifest["bytes_written"] = len(content) + sum(part["bytes"] for part in manifest["blobs"].values())
            manifest_path = directory / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            options = dict(component=self.component, window_start_seconds=0,
                           window_end_seconds=1, include_workloads=("top20",))
            result = trace.recorded_top20_queue_frontier(self.epoch, **options)
            self.assertEqual("checksummed_capture", result["source_integrity"])
            self.assertFalse(result["top20_session_execution_ready"])
            chunk.write_bytes(content + b" ")
            with self.assertRaisesRegex(ValueError, "chunk_checksum_mismatch"):
                trace.recorded_top20_queue_frontier(self.epoch, **options)
            chunk.write_bytes(content)
            # Two individually checksummed chunks with forged, balanced ranges
            # must fail even though total count and full event sequence agree.
            lines = content.splitlines(keepends=True)
            first, second = b"".join(lines[:2]), b"".join(lines[2:])
            chunk.write_bytes(first)
            (directory / "000002.jsonl").write_bytes(second)
            count = len(lines)
            manifest["chunks"] = [
                {"name": "000001.jsonl", "first_seq": 1, "last_seq": 3, "count": 3,
                 "bytes": len(first), "sha256": hashlib.sha256(first).hexdigest()},
                {"name": "000002.jsonl", "first_seq": 4, "last_seq": count, "count": count - 3,
                 "bytes": len(second), "sha256": hashlib.sha256(second).hexdigest()}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "chunk_manifest_mismatch"):
                trace.recorded_top20_queue_frontier(self.epoch, **options)
            chunk.write_bytes(content)
            manifest["chunks"] = [{"name": "000001.jsonl", "first_seq": 1, "last_seq": count,
                                   "count": count, "bytes": len(content),
                                   "sha256": hashlib.sha256(content).hexdigest()}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            payload_paths[0].write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "payload_checksum_mismatch"):
                trace.recorded_top20_queue_frontier(self.epoch, **options)

    async def test_chunk_ranges_and_typed_event_sequence_are_required(self):
        self.collector._publish_parsed(message())
        await self.drain()
        good = self.manifest()
        for fields in ({"count": 1}, {"first_seq": 2}, {"last_seq": True},
                       {"sha256": "missing"}, {"name": "000002.jsonl"}):
            altered = copy.deepcopy(good)
            altered["chunks"][0].update(fields)
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "chunks_invalid"):
                trace.validate_top20_capture_manifest(altered, self.events, trace_id=self.epoch)
        self.events[0]["seq"] = True
        with self.assertRaisesRegex(ValueError, "sequence_or_rejection_invalid"):
            trace.validate_top20_capture_manifest(good, self.events, trace_id=self.epoch)

    def write_capture(self, root):
        directory = Path(root) / self.epoch
        directory.mkdir(exist_ok=True)
        manifest = self.manifest()
        manifest.update(started_at=NOW.timestamp(), finished_mono_ns=3900 * 10**9,
                        blobs={}, chunks=[], bytes_written=0)
        disk_rows = copy.deepcopy(self.events)
        for row in disk_rows:
            if "payload" in row:
                content = json.dumps(row.pop("payload")).encode()
                digest = hashlib.sha256(content).hexdigest()
                row["payload_ref"] = digest
                (directory / f"payload-{digest}.json").write_bytes(content)
                manifest["blobs"][digest] = {"bytes": len(content)}
        for offset in range(0, len(disk_rows), 1000):
            chunk_rows = disk_rows[offset:offset + 1000]
            content = b"\n".join(json.dumps(row).encode() for row in chunk_rows) + b"\n"
            name = f"{len(manifest['chunks']) + 1:06d}.jsonl"
            (directory / name).write_bytes(content)
            manifest["chunks"].append({"name": name, "first_seq": offset + 1,
                "last_seq": offset + len(chunk_rows), "count": len(chunk_rows), "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()})
            manifest["bytes_written"] += len(content)
        manifest["bytes_written"] += sum(part["bytes"] for part in manifest["blobs"].values())
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    async def prepare_long_capture(self):
        self.epoch = "20261007T090000Z-123456789abc"
        self.collector._publish_parsed(message(2))
        await self.drain()
        self.operation("owned", self.component, ["program_flow"])
        self.operation("peer", "autonomous_top20:peer", ["program_flow"])
        for row in self.events:
            row["mono_ns"] += 300 * 10**9
            if "entered_mono_ns" in row:
                row["entered_mono_ns"] += 350 * 10**9
            if "finished_mono_ns" in row:
                row["finished_mono_ns"] += 350 * 10**9
        head = [{"event_type": "diagnostic_only", "mono_ns": index * 1000}
                for index in range(11000)]
        tail = [{"event_type": "diagnostic_only", "mono_ns": 3800 * 10**9 + index}
                for index in range(1000)]
        self.events = head + self.events + tail
        for sequence, row in enumerate(self.events, 1):
            row["seq"] = sequence

    async def test_65_minute_source_verifies_all_rows_and_keeps_sparse_prefix_with_peer(self):
        await self.prepare_long_capture()
        with tempfile.TemporaryDirectory() as root, patch.object(trace, "_directory", return_value=Path(root)):
            manifest = self.write_capture(root)
            with self.assertRaisesRegex(ValueError, "window_reader_required"):
                trace.recorded_top20_queue_frontier(self.epoch, component=self.component,
                    window_start_seconds=300, window_end_seconds=360, include_workloads=("top20",))
            result = trace.recorded_top20_window_frontier(self.epoch, component=self.component,
                window_start_seconds=300, window_end_seconds=360, include_workloads=("top20",))
            self.assertEqual(("owned",), result["replaced_operation_ids"])
            self.assertEqual(("peer",), result["operation_ids"])
            self.assertFalse(result["top20_session_execution_ready"])
            report = result["window_read"]
            self.assertEqual(manifest["written"], report["events_verified"])
            self.assertLess(report["events_retained"], 20)
            self.assertGreater(report["source_sequences"][0], 11000)
            self.assertFalse(report["warm_state_equivalent"])
            with patch.object(trace, "_MAX_TOP20_WINDOW_ROWS", 1), self.assertRaisesRegex(ValueError, "metadata_limit"):
                trace.recorded_top20_window_frontier(self.epoch, component=self.component,
                    window_start_seconds=300, window_end_seconds=360, include_workloads=("top20",))
            with patch.object(trace, "_MAX_WINDOW_PAYLOAD_BYTES", 1), self.assertRaisesRegex(ValueError, "limit"):
                trace.recorded_top20_window_frontier(self.epoch, component=self.component,
                    window_start_seconds=300, window_end_seconds=360, include_workloads=("top20",))

    async def test_window_checks_missing_sequence_and_payload_outside_selection(self):
        await self.prepare_long_capture()
        with tempfile.TemporaryDirectory() as root, patch.object(trace, "_directory", return_value=Path(root)):
            self.events[-1]["seq"] += 1
            self.write_capture(root)
            options = dict(component=self.component, window_start_seconds=300,
                           window_end_seconds=360, include_workloads=("top20",))
            with self.assertRaisesRegex(ValueError, "sequence_or_rejection_invalid"):
                trace.recorded_top20_window_frontier(self.epoch, **options)
            self.events[-1]["seq"] -= 1
            self.payload(self.epoch, "unselected_payload", {"workload_id": "background", "mono_ns": 3800 * 10**9},
                         {"unrelated": "payload"})
            manifest = self.write_capture(root)
            digest = next(row["payload_ref"] for part in manifest["chunks"]
                          for row in map(json.loads, (Path(root) / self.epoch / part["name"]).read_bytes().splitlines())
                          if row.get("event_type") == "unselected_payload")
            (Path(root) / self.epoch / f"payload-{digest}.json").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "payload_checksum_mismatch"):
                trace.recorded_top20_window_frontier(self.epoch, **options)

    async def test_long_reader_binds_fixed_cold_seed_without_starting_service_or_restoring_db(self):
        from kiwoom_monitor.central_server.diagnostic_replay_baseline import DEFAULT_CONFIG, TABLES
        from kiwoom_monitor.central_server.diagnostic_top20_seed import (
            Top20FixtureClock, recorded_top20_cold_fixture_preflight, seal_top20_cold_fixture,
        )
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        await self.prepare_long_capture()
        with tempfile.TemporaryDirectory() as root, patch.object(trace, "_directory", return_value=Path(root)):
            manifest = self.write_capture(root)
            baseline_manifest = {"version": 1, "origin": "controlled_fixture",
                "source_state_equivalent": False, "config": dict(DEFAULT_CONFIG),
                "tables": {name: {"rows": 0} for name in TABLES}}
            baseline = {"manifest": baseline_manifest, "baseline_id": hashlib.sha256(json.dumps(
                baseline_manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()).hexdigest()}
            clock = Top20FixtureClock(NOW)
            # Bare resources intentionally offer no database/network methods.
            store = object()
            broker = CentralRestBroker(object(), store, ranking_reservation=True)
            service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=clock.now)
            seed = seal_top20_cold_fixture(service, clock, baseline=baseline, source_manifest=manifest)
            result = recorded_top20_cold_fixture_preflight(self.epoch, seed, service, clock,
                baseline=baseline, component=self.component, window_start_seconds=300,
                window_end_seconds=360, include_workloads=("top20",))
            proof = result["fixture_preflight"]
            self.assertEqual(seed.ram_seed_id, proof["ram_seed_id"])
            self.assertTrue(proof["seed_identity_verified"])
            self.assertFalse(proof["execution_authorized"])
            self.assertFalse(proof["database_restore_verified"])
            self.assertEqual([], service._tasks)
            self.assertEqual(("owned",), result["replaced_operation_ids"])
            self.assertEqual(("peer",), result["operation_ids"])
            self.assertEqual(manifest["accepted"], result["window_read"]["events_verified"])
            # The initial topology and subscriber contribution are now retained
            # with the original thirteen queue/store frontier events.
            self.assertEqual(15, result["window_read"]["events_retained"])

    async def test_window_keeps_terminal_after_end_and_rejects_prefix_queue_drop(self):
        await self.prepare_long_capture()
        terminal = next(row for row in self.events if row.get("event_type") == "top20_delivery"
                        and row.get("stage") == "consume_end")
        terminal["mono_ns"] = 361 * 10**9
        with tempfile.TemporaryDirectory() as root, patch.object(trace, "_directory", return_value=Path(root)):
            self.write_capture(root)
            options = dict(component=self.component, window_start_seconds=300,
                           window_end_seconds=360, include_workloads=("top20",))
            result = trace.recorded_top20_window_frontier(self.epoch, **options)
            self.assertEqual(2, len(result["coverage"]["delivery_ids"]))
            terminal["stage"] = "drop"
            self.write_capture(root)
            with self.assertRaisesRegex(ValueError, "incomplete_or_epoch_mismatch"):
                trace.recorded_top20_window_frontier(self.epoch, **options)


if __name__ == "__main__":
    unittest.main()
