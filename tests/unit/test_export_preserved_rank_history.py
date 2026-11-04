"""Preservation oracles use real native writers, never operating databases."""
from __future__ import annotations

import ast
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import export_preserved_rank_history as export
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import (
    ranking_observation, market_state_observation, minute_bar_observation, bar_observation_key,
)
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, TradingVenue, ObservationOrigin, DataValueKind


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 24, 10, 0, 0, 123456, tzinfo=KST)
ROOT = Path(__file__).resolve().parents[2]


def seed_rank_fixture(store):
    """All source rows are accepted by the existing native writer."""
    a = {"query_type": "5", "items": [
        {"stk_cd": "A005930", "stk_nm": "당시명칭", "bigd_rank": "01", "dt": "20261024", "tm": "090000",
         "market": "NXT", "price": 1.25, "large": 10 ** 40 + 1, "unknown": None},
        {"stk_cd": "000660", "stk_nm": "둘째", "bigd_rank": 2, "flag": False},
    ]}
    b = {"query_type": "5", "items": list(reversed(a["items"]))}
    old, current = "2026-10-24T09:00:00", "2026-10-24T09:00:30+09:00"
    for offset, payload in enumerate((a, b, a)):
        store.save_dataset_snapshot("ranking", "5", old, payload, observation=ranking_observation(
            "5", old, payload, NOW + timedelta(seconds=offset), source="kiwoom-ka00198"))
    store.save_dataset_snapshot("ranking", "5", current, a, observation=ranking_observation(
        "5", current, a, NOW + timedelta(seconds=4), source="kiwoom-ka00198"))
    # A late old-slot correction must remain evidence, not a new latest policy.
    store.save_dataset_snapshot("ranking", "5", old, b, observation=ranking_observation(
        "5", old, b, NOW + timedelta(seconds=5), source="kiwoom-ka00198"))
    membership_key = "2026-10-24T09:01:00"
    membership = {"observed_at": membership_key, "codes": ["005930"], "items": a["items"]}
    observation = ranking_observation("2026-10-24", membership_key, membership, NOW,
                                      source="nas-autonomous-ka00198")
    observation = replace(observation, metadata=replace(observation.metadata,
                          completeness=DataCompleteness.PARTIAL, venue=TradingVenue.UNKNOWN))
    store.save_dataset_snapshot("top20_membership", "2026-10-24", membership_key, membership, observation=observation)
    store.save_dataset_snapshot("ranking", "legacy", "unknown-slot", {"items": [], "unverified": True})
    # Global visible highwater must not be mistaken for selected-rank highwater.
    market = {"secret_other_market": "MUST_NOT_EXPORT"}
    store.save_dataset_snapshot("market_state", "OTHER", "2026-10-24T09:03:00", market,
                                observation=market_state_observation("OTHER", "2026-10-24T09:03:00", market, NOW))
    bar = dict(trading_date="2026-10-24", minute="09:03", code="OTHER", market="KRX",
               open=100, high=110, low=90, close=105, volume=10, trade_value_million_won=2,
               updated_at=NOW.timestamp(), operation_id="rank-export-unselected-minute")
    observation = minute_bar_observation(bar, origin=ObservationOrigin.REALTIME,
        completeness=DataCompleteness.IN_PROGRESS, source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL)
    store.save_minute_bars([bar], observations=[(bar_observation_key(observation), observation)])
    store.upsert_documents("rank_export_test_private_account", [
        {"owner": "private", "key": "account", "document": {"secret_account": "MUST_NOT_EXPORT"}},
    ])
    return {"a": a, "b": b, "old": old, "current": current, "membership": membership_key}


class PreservedRankHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rank-preservation-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "test-owned.sqlite3"
        self.store = SQLiteQueryStore(self.source, now_provider=lambda: NOW)
        self.store.initialize()
        self.seed = seed_rank_fixture(self.store)
        self.addCleanup(self.store.close)

    def preserve(self, name="artifact", **kwargs):
        return export.export_rank_history(source_instance="fixture-sqlite", database=self.source,
                                         output=self.directory / name, artifact_now=NOW,
                                         sqlite_frozen=True, **kwargs)

    def rows(self, artifact, ordinal):
        return [json.loads(line)["row"] for line in (artifact / export.FILES[ordinal]).read_text(encoding="utf-8").splitlines()]

    def test_v3_preserves_raw_current_references_and_rejects_wrong_fact(self):
        report = self.preserve()
        artifact = self.directory / "artifact"
        self.assertEqual("preserved-rank-history/v3", report["format"])
        snapshots = self.rows(artifact, 1)
        head = next(row for row in snapshots if row["subject"] == "5")
        self.assertIsNone(head["payload_json"])
        self.assertIsInstance(head["revision_id"], str)
        metadata = self.rows(artifact, 2)
        self.assertTrue(all(row["revision_id"] is not None for row in metadata))
        self.assertTrue(all(row["venue"] is None and row["source"] is None for row in metadata))
        # Keep the stream digest valid so failure must be relationship integrity.
        stream = artifact / "snapshots.jsonl"
        records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
        record = next(value for value in records if value["row"]["subject"] == "5")
        wrong = next(row for row in self.rows(artifact, 0) if row["kind"] == "top20_membership")
        record["row"]["revision_id"] = wrong["revision_id"]
        raw = "".join(export._json(value) + "\n" for value in records).encode()
        stream.write_bytes(raw)
        manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
        manifest["streams"][1].update(rows=len(records), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(export.PreservationError, "source fact|conflicting source"):
            export._verify_directory(artifact, manifest)

    def test_native_source_fields_corrections_times_types_and_unknown_are_preserved(self):
        # This oracle reads source fields directly, without exporter codecs.
        with closing(sqlite3.connect(self.source.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            expected = []
            for table, sql in zip(export.TABLES, (
                "WHERE kind IN ('ranking','top20_membership') ORDER BY accepted_sequence",
                "WHERE kind IN ('ranking','top20_membership') ORDER BY kind,subject,snapshot_key",
                "WHERE dataset_kind='candidate_set' ORDER BY subject,observation_key",
            )):
                cursor = connection.execute(f"SELECT * FROM {table} {sql}")
                names = [item[0] for item in cursor.description]
                expected.append([dict(zip(names, row)) for row in cursor.fetchall()])
        before = self.source.read_bytes()
        report = self.preserve()
        artifact = self.directory / "artifact"
        for ordinal in range(3):
            self.assertEqual(expected[ordinal], self.rows(artifact, ordinal))
        revisions = self.rows(artifact, 0)
        self.assertEqual(6, len(revisions))
        self.assertEqual([self.seed["a"], self.seed["b"], self.seed["a"]],
                         [json.loads(row["payload_json"]) for row in revisions[:3]])
        self.assertEqual(revisions[0]["payload_hash"], revisions[2]["payload_hash"])
        self.assertNotEqual(revisions[0]["available_at"], revisions[2]["available_at"])
        self.assertEqual([None, revisions[0]["revision_id"], revisions[1]["revision_id"]],
                         [row["revision_of"] for row in revisions[:3]])
        self.assertEqual("partial", revisions[-1]["completeness"])
        self.assertEqual("UNKNOWN", revisions[-1]["venue"])
        self.assertEqual(self.seed["old"], revisions[4]["observation_key"])
        self.assertEqual(7, report["global_visible_accepted_highwater"])
        self.assertEqual({"connections": 1, "begin": 1, "rollback": 1, "selects": 6, "data_selects": 3},
                         {key: report["metrics"][key] for key in ("connections", "begin", "rollback", "selects", "data_selects")})
        self.assertEqual([6, 4, 3], [row["rows"] for row in report["streams"]])
        self.assertEqual(13, report["metrics"]["rows"])
        self.assertEqual(1, report["verification"]["sequence"]["unselected_or_unallocated_positions"])
        self.assertNotEqual(report["artifact_id"], revisions[0]["revision_id"])
        self.assertEqual(before, self.source.read_bytes())
        self.assertFalse(any(Path(str(self.source) + ending).exists() for ending in ("-wal", "-shm")))
        raw_artifact = b"".join(path.read_bytes() for path in artifact.iterdir() if path.is_file())
        self.assertNotIn(b"MUST_NOT_EXPORT", raw_artifact)
        self.assertNotIn(str(self.source).encode(), raw_artifact)
        self.assertEqual("verified_artifact_only", export.verify_artifact(artifact)["state"])

    def test_candidate_set_and_missing_descriptive_parent_are_not_fabricated(self):
        report = self.preserve()
        artifact = self.directory / "artifact"
        envelopes = [json.loads(line) for line in (artifact / "revisions.jsonl").read_text(encoding="utf-8").splitlines()]
        membership = next(row for row in envelopes if row["row"]["kind"] == "top20_membership")
        self.assertEqual("absent_in_source", membership["references"]["source_ref"])
        self.assertEqual(2, report["verification"]["relationships"]["absent_in_source"])
        self.assertEqual({"candidate_set"}, {row["dataset_kind"] for row in self.rows(artifact, 2)})
        snapshots = self.rows(artifact, 1)
        legacy = next(row for row in snapshots if row["subject"] == "legacy")
        self.assertEqual("unknown-slot", legacy["snapshot_key"])
        self.assertEqual([], json.loads(legacy["payload_json"])["items"])
        self.assertFalse(any(row["subject"] == "legacy" for row in self.rows(artifact, 0)))

    def test_live_wal_snapshot_ignores_peer_commit_after_fixed_cut_then_restart_sees_it(self):
        keeper = sqlite3.connect(self.source)
        self.addCleanup(keeper.close)
        keeper.execute("PRAGMA journal_mode=WAL")
        self.store.save_dataset_snapshot("ranking", "wal", "initial", {"items": []})
        before_main = self.source.read_bytes()
        before_wal = Path(str(self.source) + "-wal").read_bytes()
        original = export._write_stream
        committed = False
        def peer(path, rows, relation):
            nonlocal committed
            if not committed:
                committed = True
                self.store.save_dataset_snapshot("ranking", "peer", "late", {"items": [{"bigd_rank": "09"}]})
            return original(path, rows, relation)
        with patch.object(export, "_write_stream", side_effect=peer):
            report = export.export_rank_history(source_instance="live-wal", database=self.source,
                                               output=self.directory / "cut", artifact_now=NOW)
        self.assertEqual("mode_ro_begin_read_snapshot_honors_wal", report["source"]["snapshot"]["protocol"])
        self.assertFalse(any(row["subject"] == "peer" for row in self.rows(self.directory / "cut", 1)))
        self.assertEqual(before_main, self.source.read_bytes())
        self.assertNotEqual(before_wal, Path(str(self.source) + "-wal").read_bytes(), "peer really committed")
        second = export.export_rank_history(source_instance="live-wal", database=self.source,
                                            output=self.directory / "restart", artifact_now=NOW)
        self.assertTrue(any(row["subject"] == "peer" for row in self.rows(self.directory / "restart", 1)))
        self.assertEqual(report["streams"][1]["rows"] + 1, second["streams"][1]["rows"])

    def test_readonly_sql_is_one_snapshot_six_selects_and_no_source_main_or_wal_write(self):
        keeper = sqlite3.connect(self.source)
        self.addCleanup(keeper.close)
        keeper.execute("PRAGMA journal_mode=WAL")
        self.store.save_dataset_snapshot("ranking", "wal", "initial", {"items": []})
        protected = [self.source, Path(str(self.source) + "-wal")]
        before = [path.read_bytes() for path in protected]
        statements = []
        native = sqlite3.connect
        def traced(*args, **kwargs):
            connection = native(*args, **kwargs)
            if "mode=ro" in str(args[0]):
                connection.set_trace_callback(statements.append)
            return connection
        with patch.object(export.sqlite3, "connect", side_effect=traced):
            report = export.export_rank_history(source_instance="wal-read-only", database=self.source,
                                               output=self.directory / "read-only", artifact_now=NOW)
        self.assertEqual(before, [path.read_bytes() for path in protected])
        verbs = [sql.strip().split()[0].upper() for sql in statements if not sql.lstrip().startswith("--")]
        self.assertEqual(6, verbs.count("SELECT"))
        self.assertEqual(1, verbs.count("BEGIN"))
        self.assertEqual(1, verbs.count("ROLLBACK"))
        self.assertTrue(set(verbs) <= {"SELECT", "BEGIN", "ROLLBACK", "PRAGMA"})
        self.assertEqual(0, report["metrics"]["checkpoints"])

    def test_existing_output_aliases_frozen_wal_and_source_instance_are_rejected(self):
        output = self.directory / "user-folder"
        output.mkdir()
        sentinel = output / "user.txt"
        sentinel.write_text("keep", encoding="utf-8")
        before = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            self.preserve("user-folder")
        for target in (self.source, Path(str(self.source) + "-wal"), Path(str(self.source) + "-shm")):
            with self.assertRaises((FileExistsError, export.PreservationError)):
                export.export_rank_history(source_instance="fixture", database=self.source, output=target, sqlite_frozen=True)
        with self.assertRaises(export.PreservationError):
            export.export_rank_history(source_instance="", database=self.source, output=self.directory / "bad-label")
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        keeper = sqlite3.connect(self.source)
        self.addCleanup(keeper.close)
        keeper.execute("PRAGMA journal_mode=WAL")
        self.store.save_dataset_snapshot("ranking", "wal", "initial", {"items": []})
        with self.assertRaises(export.PreservationError):
            self.preserve("not-frozen")
        self.assertFalse((self.directory / "not-frozen" / "manifest.json").exists())

    def test_read_failure_and_staging_cleanup_failure_never_publish_complete(self):
        original = export._select
        def failing(connection, sql, metrics, **kwargs):
            if kwargs.get("data"):
                raise sqlite3.OperationalError("injected test-owned read error")
            yield from original(connection, sql, metrics, **kwargs)
        before = self.source.read_bytes()
        with patch.object(export, "_select", side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.preserve("read-error")
        output = self.directory / "read-error"
        self.assertFalse((output / "manifest.json").exists())
        self.assertEqual(1, len(list(output.glob(".staging-*/failure.json"))))
        native_remove = shutil.rmtree
        def failed_cleanup(path, *args, **kwargs):
            if Path(path).name.startswith(".staging-"):
                raise OSError("injected owned-stage cleanup failure")
            return native_remove(path, *args, **kwargs)
        with patch.object(export.shutil, "rmtree", side_effect=failed_cleanup):
            with self.assertRaises(OSError):
                self.preserve("cleanup-error")
        self.assertFalse((self.directory / "cleanup-error" / "manifest.json").exists())
        self.assertTrue(list((self.directory / "cleanup-error").glob(".staging-*/failure.json")))
        self.assertEqual(before, self.source.read_bytes())
        self.preserve("read-restart")

    def test_stream_fsync_waits_for_real_source_rollback_and_native_close(self):
        original_source, original_fsync = export._source, os.fsync
        state = {"exited": False, "connection": None}
        synced_streams = []
        output = self.directory / "durability-order"
        before = self.source.read_bytes()

        @contextmanager
        def observed_source(database, dsn, metrics, **kwargs):
            with original_source(database, dsn, metrics, **kwargs) as connection:
                state["connection"] = connection
                yield connection
                self.assertEqual([], synced_streams)
            self.assertEqual(1, metrics["rollback"])
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            state["exited"] = True

        def observed_fsync(fd):
            self.assertTrue(state["exited"])
            with self.assertRaises(sqlite3.ProgrammingError):
                state["connection"].execute("SELECT 1")
            actual = os.fstat(fd)
            for path in output.glob(".staging-*/*.jsonl"):
                expected = path.stat()
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    synced_streams.append(path.name)
            return original_fsync(fd)

        with patch.object(export, "_source", side_effect=observed_source), patch.object(export.os, "fsync", side_effect=observed_fsync):
            report = self.preserve("durability-order")
        self.assertEqual(list(export.FILES), synced_streams)
        self.assertEqual([6, 4, 3], [stream["rows"] for stream in report["streams"]])
        self.assertEqual("verified_artifact_only", export.verify_artifact(output)["state"])
        self.assertEqual(before, self.source.read_bytes())

    def test_stream_fsync_failure_after_native_close_keeps_incomplete_without_receipt(self):
        original_source, original_fsync = export._source, os.fsync
        state = {"exited": False}
        synced_streams = []
        output = self.directory / "durability-error"
        before = self.source.read_bytes()

        @contextmanager
        def observed_source(database, dsn, metrics, **kwargs):
            with original_source(database, dsn, metrics, **kwargs) as connection:
                yield connection
                self.assertEqual([], synced_streams)
            self.assertEqual(1, metrics["rollback"])
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            state["exited"] = True

        def fail_snapshot_fsync(fd):
            self.assertTrue(state["exited"])
            actual = os.fstat(fd)
            for path in output.glob(".staging-*/*.jsonl"):
                expected = path.stat()
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    synced_streams.append(path.name)
                    if path.name == "snapshots.jsonl":
                        raise OSError("injected owned stream durability failure")
            return original_fsync(fd)

        with patch.object(export, "_source", side_effect=observed_source), patch.object(export.os, "fsync", side_effect=fail_snapshot_fsync):
            with self.assertRaisesRegex(OSError, "owned stream durability failure"):
                self.preserve("durability-error")
        self.assertEqual(["revisions.jsonl", "snapshots.jsonl"], synced_streams)
        self.assertFalse((output / "manifest.json").exists())
        self.assertFalse((output / "prepared-manifest.json").exists())
        failure_path = next(output.glob(".staging-*/failure.json"))
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(("incomplete", "OSError"), (failure["state"], failure["error_type"]))
        self.assertEqual({"connections": 1, "rollback": 1, "selects": 6, "data_selects": 3, "rows": 13, "sql_commits": 0},
                         {key: failure["metrics"][key] for key in ("connections", "rollback", "selects", "data_selects", "rows", "sql_commits")})
        self.assertEqual([6, 4, 3], [len((failure_path.parent / name).read_bytes().splitlines()) for name in export.FILES])
        self.assertEqual(before, self.source.read_bytes())

    def test_verifier_detects_truncation_changed_time_duplicate_json_and_missing_selected_parent(self):
        self.preserve()
        artifact = self.directory / "artifact"
        manifest_path = artifact / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stream = artifact / "revisions.jsonl"
        original = stream.read_bytes()
        stream.write_bytes(original[:-1])
        with self.assertRaises(export.PreservationError):
            export.verify_artifact(artifact)
        stream.write_bytes(original.replace(b'"received_at":', b'"received_at":"changed","received_at":', 1))
        with self.assertRaises(export.PreservationError):
            export.verify_artifact(artifact)
        stream.write_bytes(original)
        values = original.splitlines(True)
        # Remove an earlier revision that another selected correction points to,
        # and update byte/count hashes. Independent relationship join must fail.
        stream.write_bytes(b"".join(values[1:]))
        changed = stream.read_bytes()
        manifest["streams"][0].update(rows=len(values) - 1, bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(export.PreservationError, "source-present reference"):
            export.verify_artifact(artifact)

    def test_external_sort_has_bounded_open_files_across_multiple_merge_generations(self):
        scratch = self.directory / "sort"
        scratch.mkdir()
        raw = scratch / "index.jsonl"
        # 34 chunks forces a bounded merge generation without huge payloads.
        with raw.open("w", encoding="utf-8") as stream:
            for ordinal in range(34 * 2048 - 1, -1, -1):
                stream.write(json.dumps([f"{ordinal:08}", "T", ""]) + "\n")
        count, previous = 0, ""
        for row in export._sort_index(raw, scratch):
            self.assertGreater(row[0], previous)
            previous = row[0]
            count += 1
        self.assertEqual(34 * 2048, count)
        self.assertTrue(list(scratch.glob("merge-0-*.jsonl")))

    def test_empty_rank_and_history_disabled_snapshot_only_remain_explicit(self):
        empty = self.directory / "empty.sqlite3"
        store = SQLiteQueryStore(empty, now_provider=lambda: NOW, observation_history_enabled=False)
        self.addCleanup(store.close)
        store.initialize()
        result = export.export_rank_history(source_instance="empty", database=empty,
                                            output=self.directory / "empty", artifact_now=NOW.astimezone(timezone.utc), sqlite_frozen=True)
        self.assertEqual([0, 0, 0], [stream["rows"] for stream in result["streams"]])
        self.assertIsNone(result["global_visible_accepted_highwater"])
        self.assertEqual(NOW.isoformat(), result["created_at"])
        payload = {"items": []}
        store.save_dataset_snapshot("ranking", "5", "unknown-slot", payload,
                                    observation=ranking_observation("5", "unknown-slot", payload, NOW, source="kiwoom-ka00198"))
        result = export.export_rank_history(source_instance="snapshot-only", database=empty,
                                            output=self.directory / "legacy", artifact_now=NOW, sqlite_frozen=True)
        self.assertEqual([0, 1, 1], [stream["rows"] for stream in result["streams"]])
        self.assertEqual(1, result["verification"]["relationships"]["absent_in_source"])
        subjects = ["Z", "a", "가", "Å"]
        for subject in subjects:
            store.save_dataset_snapshot("ranking", subject, "mixed", payload,
                observation=ranking_observation(subject, "mixed", payload, NOW, source="kiwoom-ka00198"))
        export.export_rank_history(source_instance="mixed-text", database=empty,
            output=self.directory / "mixed", artifact_now=NOW, sqlite_frozen=True)
        self.assertEqual(sorted(["5", *subjects]), [row["subject"] for row in self.rows(self.directory / "mixed", 1)])
        self.assertEqual(sorted(["5", *subjects]), [row["subject"] for row in self.rows(self.directory / "mixed", 2)])

    def test_cli_verifier_is_source_free_and_operator_removal_does_not_affect_product_imports(self):
        self.preserve()
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        command = [sys.executable, "-m", "scripts.export_preserved_rank_history", "verify", str(self.directory / "artifact")]
        with patch.object(export.sqlite3, "connect", side_effect=AssertionError("verifier must not access SQLite")):
            self.assertEqual("verified_artifact_only", export.verify_artifact(self.directory / "artifact")["state"])
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=25)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual("verified_artifact_only", json.loads(result.stdout)["state"])
        pruned = self.directory / "product-without-operator"
        shutil.copytree(ROOT / "src", pruned / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.assertFalse((pruned / "scripts").exists())
        environment["PYTHONPATH"] = str(pruned / "src")
        probe = "from pathlib import Path; import kiwoom_monitor.central_server.database as d; assert Path(d.__file__).resolve().is_relative_to(Path.cwd()/'src')"
        result = subprocess.run([sys.executable, "-c", probe], cwd=pruned, env=environment,
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(0, result.returncode, result.stderr)
        for path in (ROOT / "src").rglob("*.py"):
            if "__pycache__" not in path.parts:
                for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(item, ast.ImportFrom):
                        self.assertNotIn("export_preserved_rank_history", item.module or "")
                    elif isinstance(item, ast.Import):
                        self.assertTrue(all("export_preserved_rank_history" not in alias.name for alias in item.names))


if __name__ == "__main__":
    unittest.main()
