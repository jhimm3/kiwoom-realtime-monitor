"""Recorded inputs around native PostgreSQL transactions, on the dedicated DB only."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_metrics import refresh_capture_state
from kiwoom_monitor.central_server.diagnostic_replay import TEST_DATABASE_NAME, _ReplayStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    capture_owner, operation_identity, thaw_payload,
)
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse, DataCompleteness, DataUnit, DataValueKind, MarketDataMetadata,
    MarketDataObservation, MarketDatasetKind, ObservationOrigin, TradingVenue,
)


_TABLES = (
    "central_dataset_snapshots", "central_market_data_observation_meta",
    "central_observation_revisions",
)


@contextmanager
def _capture(*, inputs=True):
    with tempfile.TemporaryDirectory() as directory:
        control = Path(directory) / "control.json"
        with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}):
            master = _set_tool(control, True, 300)["diagnostic_tool"]["session_id"]
            _set_trace(control, True, 120, expected_session=master)
            refresh_capture_state(force=True)
            session = trace.start(seconds=60, store_inputs=inputs)
            try:
                yield session
            finally:
                trace.stop()
                _set_tool(control, False)
        refresh_capture_state(force=True)


class _TrackingStore(_ReplayStore):
    def __init__(self, url):
        self.connections = []
        self.connection_lock = threading.Lock()
        super().__init__(url)

    def _remember(self, connection):
        with self.connection_lock:
            self.connections.append((connection, connection.info.backend_pid))
        return connection

    def _connect(self):
        return self._remember(super()._connect())


class RecordedWorkloadCapturePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not cls.url:
            raise unittest.SkipTest("dedicated PostgreSQL URL is required")
        if urlsplit(cls.url).path.lstrip("/") != TEST_DATABASE_NAME:
            raise RuntimeError("capture gate requires the dedicated diagnostic database")
        # Verify identity and existing schema before any write or cleanup.
        with psycopg.connect(cls.url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on -c statement_timeout=5000") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                if cursor.fetchone()[0] != TEST_DATABASE_NAME:
                    raise RuntimeError("connected database is not the dedicated diagnostic database")
                for table in _TABLES:
                    cursor.execute("SELECT to_regclass(%s)", (table,))
                    if cursor.fetchone()[0] is None:
                        raise RuntimeError("existing diagnostic schema is required")

    def setUp(self):
        self.prefix = "diagnostic-capture-" + uuid4().hex
        self.subjects = []
        self.store = _TrackingStore(self.url)

    def tearDown(self):
        self._cleanup(self.subjects)
        self.assertTrue(all(connection.closed for connection, _ in self.store.connections))
        self.assertEqual("", operation_identity()["input_operation_id"])

    def _subject(self, label):
        subject = self.prefix + "-" + label
        self.subjects.append(subject)
        return subject

    def _observation(self, subject, payload):
        return MarketDataObservation(
            MarketDatasetKind.CANDIDATE_SET, subject, payload,
            MarketDataMetadata(
                datetime(2099, 1, 2, 1, tzinfo=timezone.utc),
                datetime(2099, 1, 2, 1, tzinfo=timezone.utc),
                venue=TradingVenue.KRX, unit=DataUnit.UNKNOWN,
                value_kind=DataValueKind.ACTUAL, completeness=DataCompleteness.COMPLETE,
                origin=ObservationOrigin.QUERY, source="diagnostic-capture-gate",
                candidate_universe=CandidateUniverse.RANKING_TOP20,
            ),
        )

    def _counts(self, subject):
        with psycopg.connect(self.url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on -c statement_timeout=5000") as connection:
            with connection.cursor() as cursor:
                counts = []
                for table in _TABLES:
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE subject=%s", (subject,))
                    counts.append(cursor.fetchone()[0])
        return tuple(counts)

    def _cleanup(self, subjects):
        if not subjects:
            return
        # Only this test's explicit UUID subjects; no table-wide reset or sequence reset.
        with psycopg.connect(self.url, connect_timeout=5,
                             options="-c statement_timeout=5000") as connection:
            with connection.cursor() as cursor:
                for table in reversed(_TABLES):
                    cursor.execute(f"DELETE FROM {table} WHERE subject=ANY(%s)", (subjects,))
                for table in _TABLES:
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE subject=ANY(%s)", (subjects,))
                    self.assertEqual(0, cursor.fetchone()[0], table)

    def _events(self, session):
        final = trace.stop()
        self.assertEqual("complete", final["state"], final)
        manifest = trace.status(session["trace_id"])
        if manifest["schema_version"] == 2:
            manifest, events = trace.recorded_events(session["trace_id"])
        else:
            # Schema 1 intentionally contains DB-call metrics only. The normal
            # replay reader rejects it because it has no replayable inputs.
            events = [
                json.loads(line)
                for part in manifest["chunks"]
                for line in trace.chunk_bytes(session["trace_id"], part["name"]).splitlines()
            ]
        self.assertEqual(manifest["accepted"], manifest["written"])
        self.assertEqual(0, manifest["known_dropped"])
        self.assertEqual(0, manifest["charged_bytes"])
        self.assertEqual(0, manifest["copy_reserved_bytes"])
        self.assertEqual(list(range(1, manifest["last_seq"] + 1)),
                         [event["seq"] for event in events])
        return events

    def _bound_calls(self, events):
        starts = {row["operation_id"]: row for row in events if row["event_type"] == "operation_start"}
        ends = {row["operation_id"]: row for row in events if row["event_type"] == "operation_end"}
        self.assertEqual(set(starts), set(ends))
        db_starts = {row["call_id"]: row for row in events if row["event_type"] == "call_start"}
        db_ends = {row["call_id"]: row for row in events if row["event_type"] == "call_end"}
        self.assertEqual(set(db_starts), set(db_ends))
        self.assertEqual(set(starts), {row["input_operation_id"] for row in db_starts.values()})
        self.assertEqual(len(starts), len(db_starts))  # No nested public call double capture.
        for call_id, row in db_starts.items():
            start = starts[row["input_operation_id"]]
            for key in ("input_operation_id", "workload_id", "producer_component", "actor_id"):
                self.assertEqual(row[key], db_ends[call_id][key], key)
            self.assertEqual(start["actor_id"], row["actor_id"])
            self.assertEqual(TEST_DATABASE_NAME, db_ends[call_id]["database_name"])
        return starts, ends, db_starts, db_ends

    def test_capture_on_off_keeps_native_connections_results_and_typed_inputs(self):
        statistics = []
        for enabled in (False, True):
            subject = self._subject("on" if enabled else "off")
            payload = {"value": 7}
            observation = self._observation(subject, payload)
            before = len(self.store.connections)
            with _capture(inputs=enabled) as session:
                guard = patch("kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload",
                              side_effect=AssertionError("capture OFF must not copy")) if not enabled else nullcontext()
                with guard, capture_owner("top20", "top20:gate", "top20:actor"):
                    self.store.save_dataset_snapshot("ranking", subject, "2099-01-02T01:00:00Z", payload,
                                                     observation=observation)
                    rows = self.store.load_dataset_snapshots("ranking", subject)
                payload["value"] = 999
                events = self._events(session)
            self.assertEqual({"value": 7}, rows[0]["payload"])
            self.assertEqual((1, 1, 1), self._counts(subject))
            self.assertEqual(2, len(self.store.connections) - before)
            self.assertTrue(all(connection.closed for connection, _ in self.store.connections[before:]))
            call_starts = {row["call_id"]: row for row in events if row["event_type"] == "call_start"}
            statistics.append(sorted((call_starts[row["call_id"]]["access_mode"], row["sql_calls"],
                                      row["commits"], row["rollbacks"]) for row in events if row["event_type"] == "call_end"))
            if enabled:
                starts, ends, _, _ = self._bound_calls(events)
                self.assertEqual({"save_dataset_snapshot", "load_dataset_snapshots"},
                                 {row["method"] for row in starts.values()})
                writer = next(row for row in starts.values() if row["method"] == "save_dataset_snapshot")
                arguments = thaw_payload(writer["payload"])
                self.assertEqual({"value": 7}, arguments["payload"])
                self.assertEqual(self._observation(subject, {"value": 7}), arguments["observation"])
                reader = next(row for row in starts.values() if row["method"] == "load_dataset_snapshots")
                self.assertEqual(1, ends[reader["operation_id"]]["result_count"])
            else:
                self.assertFalse(any(row["event_type"] == "operation_start" for row in events))
        self.assertEqual(statistics[0], statistics[1])

    def test_partial_sql_failure_rolls_back_all_tables_and_retry_keeps_identity(self):
        first, second = self._subject("first"), self._subject("second")
        values = [("ranking", subject, "2099-01-02T01:00:00Z", {"value": index},
                   self._observation(subject, {"value": index})) for index, subject in enumerate((first, second))]
        invalid = [values[0], (*values[1][:2], None, *values[1][3:])]
        with _capture() as session, capture_owner("top20", "top20:gate", "top20:actor"):
            with self.assertRaises(psycopg.errors.NotNullViolation):
                self.store.save_dataset_snapshots(invalid)
            self.assertEqual((0, 0, 0), self._counts(first))
            self.assertEqual((0, 0, 0), self._counts(second))
            self.store.save_dataset_snapshots(values)
            self.assertEqual(1, len(self.store.load_dataset_snapshots("ranking", first)))
            events = self._events(session)
        starts, ends, db_starts, db_ends = self._bound_calls(events)
        ordered = sorted(starts.values(), key=lambda row: row["actor_sequence"])
        self.assertEqual([1, 2, 3], [row["actor_sequence"] for row in ordered])
        self.assertEqual(["failed", "returned", "returned"], [ends[row["operation_id"]]["outcome"] for row in ordered])
        failed = next(row for row in db_ends.values()
                      if db_starts[row["call_id"]]["input_operation_id"] == ordered[0]["operation_id"])
        self.assertEqual((0, 1, "rolled_back"), (failed["commits"], failed["rollbacks"], failed["outcome"]))
        self.assertIn("NotNullViolation", failed["exception_types"])
        self.assertEqual((1, 1, 1), self._counts(first))
        self.assertEqual((1, 1, 1), self._counts(second))

    def test_trace_disk_failure_keeps_native_commit_and_marks_recording_unusable(self):
        subject = self._subject("disk")
        original = trace.os.replace

        def fail_blob(source, target):
            if Path(target).suffix == ".payloads":
                raise OSError("injected capture disk failure")
            return original(source, target)

        with _capture() as session:
            with patch.object(trace.os, "replace", side_effect=fail_blob), \
                    capture_owner("top20", "top20:gate", "top20:actor"):
                self.store.save_dataset_snapshot("ranking", subject, "2099-01-02T01:00:00Z", {"value": 4},
                                                 observation=self._observation(subject, {"value": 4}))
                final = trace.stop()
            self.assertEqual("failed", final["state"])
            self.assertEqual("OSError", final["reason"])
            self.assertEqual((1, 1, 1), self._counts(subject))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                trace.recorded_events(session["trace_id"])
        self.assertEqual(1, len(self.store.connections))
        self.assertTrue(self.store.connections[0][0].closed)

    def test_peer_commit_and_lost_ack_retry_keep_independent_transactions_and_scoped_cleanup(self):
        entered, release = threading.Event(), threading.Event()

        class LostAckConnection(psycopg.Connection):
            def commit(connection):
                entered.set()
                if not release.wait(30):
                    raise TimeoutError("test COMMIT gate timed out")
                super().commit()
                raise OSError("injected loss of native COMMIT acknowledgement")

        class LostAckStore(_TrackingStore):
            def _connect(store):
                if not store.connections:
                    return store._remember(LostAckConnection.connect(
                        store._database_url, connect_timeout=5, options="-c statement_timeout=5000"))
                return super()._connect()

        owner = LostAckStore(self.url)
        peer = _TrackingStore(self.url)
        owner_subject, peer_subject = self._subject("owner"), self._subject("peer")

        def save(store, subject, actor):
            with capture_owner("top20", "top20:" + actor, actor):
                store.save_dataset_snapshot("ranking", subject, "2099-01-02T01:00:00Z", {"value": 2},
                                            observation=self._observation(subject, {"value": 2}))

        try:
            with _capture() as session:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending = executor.submit(save, owner, owner_subject, "owner")
                    try:
                        self.assertTrue(entered.wait(10))
                        executor.submit(save, peer, peer_subject, "peer").result(timeout=20)
                        self.assertFalse(pending.done())
                        self.assertEqual((1, 1, 1), self._counts(peer_subject))
                    finally:
                        release.set()
                    with self.assertRaisesRegex(OSError, "COMMIT acknowledgement"):
                        pending.result(timeout=20)
                # COMMIT took effect despite the caller receiving an error.
                self.assertEqual((1, 1, 1), self._counts(owner_subject))
                save(owner, owner_subject, "owner")
                self.assertEqual((1, 1, 1), self._counts(owner_subject))
                events = self._events(session)
            starts, ends, db_starts, db_ends = self._bound_calls(events)
            owner_operations = sorted((row for row in starts.values() if row["actor_id"] == "owner"),
                                      key=lambda row: row["actor_sequence"])
            self.assertEqual([1, 2], [row["actor_sequence"] for row in owner_operations])
            self.assertEqual(["failed", "returned"], [ends[row["operation_id"]]["outcome"] for row in owner_operations])
            owner_calls = [row for row in db_ends.values() if row["actor_id"] == "owner"]
            self.assertEqual({"unknown", "committed"}, {row["outcome"] for row in owner_calls})
            failed = next(row for row in owner_calls if row["outcome"] == "unknown")
            self.assertEqual((0, 1), (failed["commits"], failed["rollbacks"]))
            self.assertIn("OSError", failed["exception_types"])
            self.assertEqual(3, len(db_starts))
            self.assertNotEqual(owner.connections[0][1], peer.connections[0][1])
            self.assertTrue(all(connection.closed for connection, _ in owner.connections + peer.connections))
            self._cleanup([owner_subject])
            self.assertEqual((0, 0, 0), self._counts(owner_subject))
            self.assertEqual((1, 1, 1), self._counts(peer_subject))
        finally:
            release.set()
            self.assertTrue(all(connection.closed for connection, _ in owner.connections + peer.connections))


if __name__ == "__main__":
    unittest.main()
