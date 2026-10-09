from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_payload
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom


@contextmanager
def held_capture():
    """Hold the disk worker while callers accumulate a deterministic burst."""
    with tempfile.TemporaryDirectory() as root:
        control = Path(root) / "control.json"
        entered, release = threading.Event(), threading.Event()
        original_manifest = trace._manifest

        def initial_manifest(directory, session):
            original_manifest(directory, session)
            if not session["chunks"] and session["state"] == "running":
                entered.set()
                if not release.wait(15):
                    raise TimeoutError("test disk gate timed out")

        with recorder_storage_headroom(), patch.object(trace, "control_path", return_value=control), patch(
                "kiwoom_monitor.central_server.diagnostic_workloads.control_path", return_value=control), \
                patch.object(trace, "_manifest", side_effect=initial_manifest):
            master = _set_tool(control, True, 300)["diagnostic_tool"]["session_id"]
            _set_trace(control, True, 120, expected_session=master)
            active = trace.start(seconds=60, store_inputs=True, collector_inputs=True)
            try:
                if not entered.wait(5):
                    raise TimeoutError("test disk worker did not start")
                yield active["trace_id"], release
            finally:
                release.set()
                trace.stop(timeout=15)
                _set_tool(control, False)


class DiagnosticTraceBatchTests(unittest.TestCase):
    def test_complete_waits_for_durable_final_manifest(self):
        with held_capture() as (identifier, release):
            trace.emit(identifier, "call_end", {"call_id": "final-manifest"})
            final_entered, allow_final = threading.Event(), threading.Event()
            original_manifest = trace._manifest

            def hold_final(directory, session):
                if session["state"] == "complete":
                    final_entered.set()
                    if not allow_final.wait(5):
                        raise TimeoutError("test final manifest gate timed out")
                return original_manifest(directory, session)

            with patch.object(trace, "_manifest", side_effect=hold_final):
                release.set()
                trace._WAKE.set()
                try:
                    trace.stop(timeout=0.02)
                    self.assertTrue(final_entered.wait(5))
                    self.assertEqual("stopping", trace.stop(timeout=0.02)["state"])
                    disk = json.loads((trace._directory() / identifier / "manifest.json").read_text())
                    self.assertNotEqual("complete", disk["state"])
                    with self.assertRaisesRegex(ValueError, "recorded_capture_incomplete_or_old_schema"):
                        trace.recorded_events(identifier)
                finally:
                    allow_final.set()
                    final = trace.stop(timeout=15)
            self.assertEqual("complete", final["state"])
            disk = json.loads((trace._directory() / identifier / "manifest.json").read_text())
            self.assertEqual("complete", disk["state"])
            self.assertEqual((1, 1, 0), (final["accepted"], final["written"], final["queued"]))

    def test_failed_final_manifest_never_publishes_complete(self):
        with held_capture() as (identifier, release):
            trace.emit(identifier, "call_end", {"call_id": "failed-final-manifest"})
            observed_states = []
            original_manifest = trace._manifest

            def fail_final(directory, session):
                if session["state"] == "complete":
                    observed_states.append(trace.status(identifier)["state"])
                    raise OSError("injected final manifest fsync failure")
                return original_manifest(directory, session)

            with patch.object(trace, "_manifest", side_effect=fail_final):
                release.set()
                final = trace.stop(timeout=15)
            self.assertEqual(["stopping"], observed_states)
            self.assertEqual("failed", final["state"])
            self.assertEqual("OSError", final["reason"])
            disk = json.loads((trace._directory() / identifier / "manifest.json").read_text())
            self.assertEqual("failed", disk["state"])

    def test_distinct_payloads_share_fsync_and_deduplicate_across_bounded_batches(self):
        with patch.object(trace, "_MAX_PAYLOAD_BATCH_BYTES", 600), held_capture() as (identifier, release):
            inputs = [{"index": index, "text": "가" * 30} for index in range(12)]
            expected = inputs + inputs[:3]
            for index, value in enumerate(expected):
                self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                    "input_id": str(index), "workload_id": "realtime",
                }, value))
            release.set()
            final = trace.stop(timeout=15)
            self.assertEqual("complete", final["state"])
            self.assertEqual((15, 15, 0, 0), (
                final["accepted"], final["written"], final["known_dropped"], final["charged_bytes"],
            ))
            manifest, events = trace.recorded_events(identifier)
            self.assertEqual(expected, [thaw_payload(row["payload"]) for row in events])
            self.assertEqual(list(range(1, 16)), [row["seq"] for row in events])
            self.assertEqual(12, len(manifest["blobs"]))
            bundles = list((trace._directory() / identifier).glob("*.payloads"))
            self.assertGreater(len(bundles), 1)
            self.assertLess(len(bundles), len(inputs))
            self.assertEqual(len(bundles), final["payload_fsync_count"])
            self.assertTrue(all(path.stat().st_size <= 600 for path in bundles))
            self.assertEqual(sum(path.stat().st_size for path in bundles)
                             + sum(part["bytes"] for part in final["chunks"]), final["bytes_written"])
            self.assertEqual(events[0]["payload"], events[-3]["payload"])
            self.assertFalse(list((trace._directory() / identifier).glob("payload-*.json")))

    def test_expanded_memory_keeps_a_burst_above_the_old_48_mib_queue_budget(self):
        with held_capture() as (identifier, release):
            for index in range(64):
                self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                    "input_id": str(index), "workload_id": "realtime",
                }, {"index": index, "text": "x" * (256 * 1024)}))
            buffered = trace.status(identifier)
            self.assertEqual(256 * 1024 * 1024, buffered["memory_limit_bytes"])
            self.assertGreater(buffered["charged_bytes"], 48 * 1024 * 1024)
            self.assertLessEqual(buffered["memory_high_water"], trace._MEMORY_LIMIT - trace._WORKER_RESERVE)
            self.assertEqual(0, buffered["input_rejected"])
            release.set()
            final = trace.stop(timeout=15)
            self.assertEqual("complete", final["state"])
            self.assertEqual((64, 64, 0, 0, 0), (
                final["accepted"], final["written"], final["known_dropped"],
                final["charged_bytes"], final["copy_reserved_bytes"],
            ))
            rows = [json.loads(line) for part in final["chunks"]
                    for line in trace.chunk_bytes(identifier, part["name"]).splitlines()]
            self.assertEqual(list(range(1, 65)), [row["seq"] for row in rows])
            self.assertEqual(2, final["payload_fsync_count"])
            for index, row in enumerate(rows):
                value = thaw_payload(json.loads(trace.payload_bytes(identifier, row["payload_ref"])))
                self.assertEqual(index, value["index"])
                self.assertEqual(256 * 1024, len(value["text"]))

    def test_memory_saturation_remains_visible_and_scalar_events_are_retained(self):
        limit = trace._WORKER_RESERVE + trace._SCALAR_RESERVE + trace._COPY_RESERVATION + 4096
        with patch.object(trace, "_MEMORY_LIMIT", limit), held_capture() as (identifier, release):
            value = {"text": "x" * (256 * 1024)}
            self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                "input_id": "accepted", "workload_id": "realtime",
            }, value))
            self.assertFalse(trace.emit_payload(identifier, "collector_input", {
                "input_id": "rejected", "workload_id": "realtime",
            }, value))
            trace.emit(identifier, "operation_end", {"operation_id": "native-continued"})
            buffered = trace.status(identifier)
            self.assertEqual({"capture_memory_full": 1}, buffered["input_rejected_reasons"])
            self.assertLessEqual(buffered["memory_high_water"], limit - trace._WORKER_RESERVE)
            release.set()
            final = trace.stop(timeout=15)
            rows = [json.loads(line) for part in final["chunks"]
                    for line in trace.chunk_bytes(identifier, part["name"]).splitlines()]
            self.assertEqual(["collector_input", "input_rejected", "operation_end"],
                             [row["event_type"] for row in rows])
            # Accepted scalar evidence is durable, but rejected replay inputs
            # prevent a terminal claim of complete capture.
            self.assertEqual("incomplete", final["state"])
            self.assertEqual(1, final["input_rejected"])
            self.assertEqual(0, final["charged_bytes"])

    def test_failed_bundle_sync_or_chunk_publication_never_commits_payload_references(self):
        for failure in ("bundle_sync", "chunk_rename"):
            with self.subTest(failure=failure), held_capture() as (identifier, release):
                self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                    "input_id": "one", "workload_id": "realtime",
                }, {"value": 42}))
                original_sync, original_replace = trace.os.fsync, trace.os.replace
                failed = False

                def sync(file_descriptor):
                    nonlocal failed
                    if failure == "bundle_sync" and not failed:
                        failed = True
                        raise OSError("injected payload sync failure")
                    return original_sync(file_descriptor)

                def replace(source, target):
                    if failure == "chunk_rename" and Path(target).suffix == ".jsonl":
                        raise OSError("injected chunk publication failure")
                    return original_replace(source, target)

                with patch.object(trace.os, "fsync", side_effect=sync), \
                        patch.object(trace.os, "replace", side_effect=replace):
                    release.set()
                    final = trace.stop(timeout=15)
                self.assertEqual("failed", final["state"])
                self.assertEqual((1, 0, 1), (final["accepted"], final["written"], final["queued"]))
                self.assertEqual([], final["chunks"])
                self.assertEqual({}, final["blobs"])
                digest = hashlib.sha256(b"not-committed").hexdigest()
                with self.assertRaisesRegex(KeyError, "not_committed"):
                    trace.payload_bytes(identifier, digest)
                saved = json.loads((trace._directory() / identifier / "manifest.json").read_text())
                self.assertEqual("failed", saved["state"])
                self.assertEqual({}, saved["blobs"])

    def test_stop_during_batch_sync_drains_new_arrivals_without_early_publication(self):
        with held_capture() as (identifier, release):
            for index in range(10):
                self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                    "input_id": str(index), "workload_id": "realtime",
                }, {"value": index}))
            syncing, allow_sync = threading.Event(), threading.Event()
            original_sync = trace.os.fsync
            held = False

            def sync(file_descriptor):
                nonlocal held
                if not held:
                    held = True
                    syncing.set()
                    if not allow_sync.wait(5):
                        raise TimeoutError("test payload sync gate timed out")
                return original_sync(file_descriptor)

            with patch.object(trace.os, "fsync", side_effect=sync):
                release.set()
                trace._WAKE.set()
                try:
                    self.assertTrue(syncing.wait(5))
                    during = trace.status(identifier)
                    self.assertEqual((0, {}, []), (during["written"], during["blobs"], during["chunks"]))
                    self.assertTrue(trace.emit_payload(identifier, "collector_input", {
                        "input_id": "10", "workload_id": "realtime",
                    }, {"value": 10}))
                    stopping = trace.stop(timeout=0.02)
                    self.assertEqual("stopping", stopping["state"])
                    self.assertEqual(11, stopping["queued"])
                    self.assertIsNone(trace.input_token("collector_inputs"))
                finally:
                    allow_sync.set()
                    final = trace.stop(timeout=15)
            self.assertEqual("complete", final["state"])
            self.assertEqual((11, 11, 0, 0), (
                final["accepted"], final["written"], final["queued"], final["charged_bytes"],
            ))
            _, events = trace.recorded_events(identifier)
            self.assertEqual(list(range(1, 12)), [row["seq"] for row in events])
            self.assertEqual(list(range(11)), [thaw_payload(row["payload"])["value"] for row in events])

    def test_legacy_blob_download_is_compatible_and_invalid_bundle_offsets_are_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
                trace, "control_path", return_value=Path(root) / "control.json"):
            identifier = "20261002T034553Z-48afad5c80ef"
            directory = trace._directory() / identifier
            directory.mkdir(parents=True)
            content = b'{"legacy":true}'
            digest = hashlib.sha256(content).hexdigest()
            (directory / f"payload-{digest}.json").write_bytes(content)
            manifest = {"trace_id": identifier, "state": "complete", "schema_version": 2,
                        "blobs": {digest: {"bytes": len(content)}}}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(content, trace.payload_bytes(identifier, digest))
            for invalid in ({"name": "../payloads", "offset": 0},
                            {"name": "000001.payloads", "offset": -1},
                            {"name": "000001.payloads", "offset": trace._MAX_PAYLOAD_BATCH_BYTES}):
                with self.subTest(invalid=invalid):
                    manifest["blobs"][digest] = {"bytes": len(content), **invalid}
                    (directory / "manifest.json").write_text(json.dumps(manifest))
                    with self.assertRaises(KeyError):
                        trace.payload_bytes(identifier, digest)
