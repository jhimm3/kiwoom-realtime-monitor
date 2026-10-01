from __future__ import annotations

import copy
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.central_server.shadow_checkpoint import (
    downgrade_schema, save_frames, split_checkpoint,
)
from kiwoom_monitor.application.research_replay import CandidateUniverseFrame, KrxMinuteBarFrame


def checkpoint(cursor=1, *, bars=None):
    return {"schema_version": 1, "cursor": cursor, "session_profile": "krx-regular/v1",
            "strategy_config": {}, "quality": {"reason": "processed"},
            "strategy_state": {"emitted_candidate_keys": ["preserve-dedup"]},
            "universes": [], "bars": bars if bars is not None else [
                {"code": "B", "observation_key": "late", "revision_id": "B1", "close": 100},
                {"code": "A", "observation_key": "earlier", "revision_id": "A1", "close": 200},
            ]}


def benchmark_checkpoint():
    origin = datetime(2026, 10, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    bars = []
    for index in range(1803):
        code = f"{index % 20:06d}"
        started = origin + timedelta(minutes=index // 20)
        ended = started + timedelta(minutes=1)
        observation_key = f"{started.isoformat()}/{code}"
        bars.append(asdict(KrxMinuteBarFrame(
            revision_id=str(uuid.uuid5(uuid.NAMESPACE_URL, observation_key)),
            observation_key=observation_key,
            code=code,
            bar_start=started.isoformat(),
            bar_end=ended.isoformat(),
            available_at=ended.isoformat(),
            open=10000 + index,
            high=10100 + index,
            low=9900 + index,
            close=10050 + index,
            volume=100000 + index,
            trade_value_million_won=100 + index,
            session_finalized=True,
            capture_quality="complete",
            finalization_source="market_event",
            session_profile="krx-regular/v1",
            research_session="2026-10-01",
            market_session="regular",
            market_phase="continuous",
            schedule_version="krx-v1",
        )))
    codes = tuple(f"{index:06d}" for index in range(20))
    universes = []
    for index in range(11):
        universe = asdict(CandidateUniverseFrame(
            revision_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"universe-{index}")),
            observation_key=f"2026-10-01T08:{index:02d}:00+09:00",
            available_at=f"2026-10-01T08:{index:02d}:00+09:00",
            codes=codes,
            accepted_sequence=index + 1,
        ))
        universe["codes"] = list(universe["codes"])
        universes.append(universe)
    document = checkpoint(100, bars=bars)
    document["universes"] = universes
    return document, bars


class ShadowCheckpointPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not cls.url:
            raise unittest.SkipTest("dedicated PostgreSQL test database not configured")
        if urlsplit(cls.url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing checkpoint test outside dedicated DB")
        cls.store = PostgresQueryStore(cls.url, shadow_checkpoint_frames_enabled=True)
        with cls.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != "kiwoom_monitor_diagnostic_test":
                raise RuntimeError("connected database is not dedicated")
        cls.store.initialize()

    def setUp(self):
        self.ids = []
        self.key = self.new_key()

    def new_key(self):
        key = "diagnostic-checkpoint-" + uuid.uuid4().hex
        self.ids.append(key)
        return key

    def tearDown(self):
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_shadow_monitor_state WHERE monitor_id=ANY(%s)", (self.ids,))
            for table in ("central_shadow_monitor_state", "central_shadow_checkpoint_state", "central_shadow_checkpoint_frames"):
                cursor.execute(f"SELECT count(*) FROM {table} WHERE monitor_id=ANY(%s)", (self.ids,))
                self.assertEqual(0, cursor.fetchone()[0], table)

    def versions(self):
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT code,observation_key,xmin::text FROM central_shadow_checkpoint_frames "
                           "WHERE monitor_id=%s", (self.key,))
            return {(code, key): xmin for code, key, xmin in cursor.fetchall()}

    def test_migration_cursor_only_one_frame_eviction_order_and_inline_recovery(self):
        inline = PostgresQueryStore(self.url)
        initial = checkpoint()
        inline.save_shadow_monitor_state(self.key, initial)
        self.assertEqual(initial, self.store.load_shadow_monitor_state(self.key))
        self.store.save_shadow_monitor_state(self.key, initial)
        old = self.versions()
        next_doc = checkpoint(2)
        self.store.save_shadow_monitor_state(self.key, next_doc)
        self.assertEqual(old, self.versions(), "cursor-only must not UPDATE any frame")
        self.assertEqual(next_doc, inline.load_shadow_monitor_state(self.key))
        next_doc["bars"][0].update(close=101, revision_id="late-correction")
        self.store.save_shadow_monitor_state(self.key, next_doc)
        changed = self.versions()
        self.assertNotEqual(old[("B", "late")], changed[("B", "late")])
        self.assertEqual(old[("A", "earlier")], changed[("A", "earlier")])
        next_doc["bars"].reverse()
        self.store.save_shadow_monitor_state(self.key, next_doc)
        self.assertEqual(changed, self.versions(), "array reordering must not UPDATE frames")
        self.assertEqual(next_doc, self.store.load_shadow_monitor_state(self.key))
        next_doc["bars"].pop()
        self.store.save_shadow_monitor_state(self.key, next_doc)
        self.assertEqual(1, len(self.versions()))
        self.assertEqual(next_doc, self.store.load_shadow_monitor_state(self.key))
        empty = checkpoint(3, bars=[])
        self.store.save_shadow_monitor_state(self.key, empty)
        self.assertEqual({}, self.versions())
        self.assertEqual(empty, self.store.load_shadow_monitor_state(self.key))
        self.store.save_shadow_monitor_state(self.key, {"cursor": 4})
        self.assertEqual({"cursor": 4}, self.store.load_shadow_monitor_state(self.key))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM central_shadow_checkpoint_state WHERE monitor_id=%s", (self.key,))
            self.assertEqual(0, cursor.fetchone()[0])

    def test_frame_failure_rolls_back_header_and_commit_ack_retry_changes_no_frame(self):
        baseline = checkpoint()
        self.store.save_shadow_monitor_state(self.key, baseline)
        before = self.versions()
        update = checkpoint(2)
        update["bars"][0]["close"] = 999

        def failing(*args, **kwargs):
            save_frames(*args, **kwargs)
            raise RuntimeError("after frame write")

        with patch("kiwoom_monitor.central_server.shadow_checkpoint.save_frames", side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, "after frame write"):
                self.store.save_shadow_monitor_state(self.key, update)
        self.assertEqual(baseline, self.store.load_shadow_monitor_state(self.key))
        self.assertEqual(before, self.versions())

        class LostAck:
            def __init__(self, raw): self.raw = raw
            def __enter__(self): self.raw.__enter__(); return self
            def cursor(self): return self.raw.cursor()
            def __exit__(self, kind, value, trace):
                result = self.raw.__exit__(kind, value, trace)
                if kind is None: raise RuntimeError("commit acknowledgement lost")
                return result

        with patch("kiwoom_monitor.central_server.postgres_access.open_observed_connection",
                   side_effect=lambda factory, *_args, **_kwargs: LostAck(factory())):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
                self.store.save_shadow_monitor_state(self.key, update)
        committed = self.versions()
        self.assertEqual(update, self.store.load_shadow_monitor_state(self.key))
        self.store.save_shadow_monitor_state(self.key, update)
        self.assertEqual(committed, self.versions())
        self.assertEqual(update, self.store.load_shadow_monitor_state(self.key))

    def test_same_monitor_serializes_snapshots_while_peer_commits_independently(self):
        baseline = checkpoint(0)
        self.store.save_shadow_monitor_state(self.key, baseline)
        peer = self.new_key()
        first, second = checkpoint(1), checkpoint(2)
        first["bars"][0]["close"] = 111
        second["bars"][1]["close"] = 222
        first_held, second_started, second_held = (threading.Event() for _ in range(3))
        release_first, release_second = threading.Event(), threading.Event()

        def gated(cursor, monitor_id, encoded, *args):
            value = json.loads(encoded)
            if monitor_id == self.key and value["cursor"] == 2:
                second_started.set()
            result = save_frames(cursor, monitor_id, encoded, *args)
            if monitor_id == self.key:
                held, release = (first_held, release_first) if value["cursor"] == 1 else (second_held, release_second)
                held.set()
                if not release.wait(15): raise RuntimeError("test gate timed out")
            return result

        with ThreadPoolExecutor(3) as pool, patch("kiwoom_monitor.central_server.shadow_checkpoint.save_frames", side_effect=gated):
            try:
                a = pool.submit(self.store.save_shadow_monitor_state, self.key, first)
                self.assertTrue(first_held.wait(10))
                b = pool.submit(self.store.save_shadow_monitor_state, self.key, second)
                self.assertTrue(second_started.wait(10))
                pool.submit(self.store.save_shadow_monitor_state, peer, checkpoint(7)).result(10)
                self.assertFalse(b.done())
                self.assertEqual(baseline, self.store.load_shadow_monitor_state(self.key))
                release_first.set(); a.result(10)
                self.assertTrue(second_held.wait(10))
                self.assertEqual(first, self.store.load_shadow_monitor_state(self.key))
                release_second.set(); b.result(10)
                self.assertEqual(second, self.store.load_shadow_monitor_state(self.key))
            finally:
                release_first.set(); release_second.set()

    def test_missing_or_unknown_v2_is_not_silently_replaced_by_legacy(self):
        self.store.save_shadow_monitor_state(self.key, checkpoint())
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_shadow_checkpoint_frames WHERE monitor_id=%s AND code='B'", (self.key,))
        with self.assertRaisesRegex(RuntimeError, "missing"):
            self.store.load_shadow_monitor_state(self.key)
        self.store.save_shadow_monitor_state(self.key, checkpoint())
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE central_shadow_checkpoint_state SET storage_version=3 WHERE monitor_id=%s", (self.key,))
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            self.store.load_shadow_monitor_state(self.key)

    def test_offline_downgrade_commits_latest_document_and_restores_old_schema_contract(self):
        import psycopg
        from psycopg import sql
        schema = "diagnostic_checkpoint_" + uuid.uuid4().hex
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        local = PostgresQueryStore(self.url, shadow_checkpoint_frames_enabled=True)
        local._connect = lambda: psycopg.connect(self.url, options=f"-c search_path={schema}")
        try:
            with local._connect() as connection, connection.cursor() as cursor:
                CentralSchemaMigrationRunner(cursor, "postgres").apply(central_schema_migrations()[:-1])
                cursor.execute("INSERT INTO central_shadow_monitor_state VALUES(%s,now(),%s)", (self.key, json.dumps(checkpoint(0))))
            local.initialize()
            latest = checkpoint(5)
            local.save_shadow_monitor_state(self.key, latest)
            # Fail after materialization/DDL inside the transaction: all layout
            # and marker changes must return to v2, not just the header rows.
            with self.assertRaisesRegex(RuntimeError, "offline fault"):
                with local._connect() as connection, connection.cursor() as cursor:
                    self.assertEqual(1, downgrade_schema(cursor))
                    raise RuntimeError("offline fault")
            self.assertEqual(latest, local.load_shadow_monitor_state(self.key))
            with local._connect() as connection, connection.cursor() as cursor:
                self.assertEqual(1, downgrade_schema(cursor))
            with local._connect() as connection, connection.cursor() as cursor:
                CentralSchemaMigrationRunner(cursor, "postgres").apply(central_schema_migrations()[:-1])
                cursor.execute("SELECT document_json FROM central_shadow_monitor_state WHERE monitor_id=%s", (self.key,))
                self.assertEqual(latest, cursor.fetchone()[0])
                cursor.execute("SELECT MAX(version) FROM central_schema_migrations")
                self.assertEqual(20, cursor.fetchone()[0])
            local.initialize()
            self.assertEqual(latest, local.load_shadow_monitor_state(self.key))
        finally:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    def test_1803_frame_single_change_compares_real_dml_wal_and_buffers(self):
        """Run actual table DML under EXPLAIN, then rollback the measured writes."""
        import psycopg

        initial, bars = benchmark_checkpoint()
        changed = copy.deepcopy(initial)
        changed["cursor"] = 101
        changed["bars"][901]["close"] += 1
        encoded_initial = json.dumps(initial, ensure_ascii=False)
        encoded_changed = json.dumps(changed, ensure_ascii=False)
        self.assertGreater(len(encoded_initial.encode("utf-8")), 1_000_000)

        inline_key, frames_key = self.key, self.new_key()
        inline = PostgresQueryStore(self.url, shadow_checkpoint_frames_enabled=False)
        inline.save_shadow_monitor_state(inline_key, initial)
        self.store.save_shadow_monitor_state(frames_key, initial)

        def plan_metrics(plan_row):
            value = plan_row[0]
            if isinstance(value, str):
                value = json.loads(value)
            root = value[0]
            plan = root["Plan"]
            wal = plan.get("WAL", {})
            buffers = (
                plan.get("Shared Hit Blocks", 0),
                plan.get("Shared Read Blocks", 0),
                plan.get("Shared Dirtied Blocks", 0),
                plan.get("Shared Written Blocks", 0),
            )
            return {
                "wal_records": wal.get("Records", plan.get("WAL Records", 0)),
                "wal_fpi": wal.get("FPI", plan.get("WAL FPI", 0)),
                "wal_bytes": wal.get("Bytes", plan.get("WAL Bytes", 0)),
                "shared_hit_blocks": buffers[0],
                "shared_read_blocks": buffers[1],
                "shared_dirtied_blocks": buffers[2],
                "shared_written_blocks": buffers[3],
                "execution_ms": root.get("Execution Time", 0),
            }

        explain_prefix = "EXPLAIN (ANALYZE, WAL, BUFFERS, FORMAT JSON) "
        inline_sql = (
            "INSERT INTO central_shadow_monitor_state VALUES(%s,%s,%s) "
            "ON CONFLICT(monitor_id) DO UPDATE SET updated_at=EXCLUDED.updated_at,"
            "document_json=EXCLUDED.document_json"
        )
        with psycopg.connect(self.url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(explain_prefix + inline_sql, (
                    inline_key, datetime.now(timezone.utc), encoded_changed,
                ))
                inline_metrics = plan_metrics(cursor.fetchone())
            connection.rollback()
        self.assertEqual(initial, inline.load_shadow_monitor_state(inline_key))
        self.assertGreater(inline_metrics["wal_bytes"], 0, "EXPLAIN WAL was not decoded for the inline write")

        frame_metrics = []

        class ExplainDMLCursor:
            def __init__(self, raw):
                self.raw = raw

            def execute(self, statement, params=None):
                if statement.startswith((
                    "INSERT INTO central_shadow_checkpoint_state",
                    "WITH incoming AS",
                    "WITH retained AS",
                )):
                    self.raw.execute(explain_prefix + statement, params)
                    frame_metrics.append(plan_metrics(self.raw.fetchone()))
                    return self
                self.raw.execute(statement, params)
                return self

            def fetchone(self):
                return self.raw.fetchone()

            @property
            def rowcount(self):
                return self.raw.rowcount

        with psycopg.connect(self.url) as connection:
            with connection.cursor() as raw_cursor:
                save_frames(
                    ExplainDMLCursor(raw_cursor), frames_key, encoded_changed,
                    split_checkpoint(changed), datetime.now(timezone.utc),
                )
            connection.rollback()
        self.assertEqual(initial, self.store.load_shadow_monitor_state(frames_key))
        self.assertEqual(3, len(frame_metrics), "header, changed frame upsert, and stale-frame delete DML")

        totals = {
            name: sum(sample[name] for sample in frame_metrics)
            for name in ("wal_records", "wal_fpi", "wal_bytes", "shared_hit_blocks",
                         "shared_read_blocks", "shared_dirtied_blocks", "shared_written_blocks")
        }
        self.assertGreater(totals["wal_bytes"], 0, "EXPLAIN WAL was not decoded for normalized frame DML")
        print("shadow_checkpoint_dml_benchmark=" + json.dumps({
            "database": "kiwoom_monitor_diagnostic_test",
            "fixture_frames": len(bars),
            "fixture_bytes": len(encoded_initial.encode("utf-8")),
            "changed_frames": 1,
            "legacy_inline_upsert": inline_metrics,
            "normalized_frame_dml": {"statements": frame_metrics, "totals": totals},
            "scope_note": "EXPLAIN ANALYZE WAL BUFFERS of real DML; measured mutations rolled back; setup and COMMIT WAL excluded",
        }, ensure_ascii=False, sort_keys=True))

    def test_1803_frame_change_sizes_compare_committed_sql_and_footprint(self):
        """Compare the public writer for cursor-only, one, and many frame changes."""
        from statistics import median

        initial, bars = benchmark_checkpoint()
        encoded_bytes = len(json.dumps(initial, ensure_ascii=False).encode("utf-8"))
        self.assertGreater(encoded_bytes, 1_000_000)
        inline = PostgresQueryStore(self.url, shadow_checkpoint_frames_enabled=False)
        stores = {"inline": inline, "frames": self.store}
        cases = {"cursor_only": 0, "one_frame": 1, "many_frames": 50}
        keys = {
            (case, mode): self.key if case == "cursor_only" and mode == "inline" else self.new_key()
            for case in cases for mode in stores
        }
        for key in keys.values():
            stores["inline"].save_shadow_monitor_state(key, initial)
        # Seed the normalized stores with their own committed equivalent baseline.
        for case in cases:
            self.store.save_shadow_monitor_state(keys[(case, "frames")], initial)

        samples = {case: {mode: [] for mode in stores} for case in cases}
        captured = []

        def collect(record, *, capture_token):
            captured.append(dict(record))

        def versions(monitor_id):
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT code,observation_key,xmin::text FROM central_shadow_checkpoint_frames "
                    "WHERE monitor_id=%s", (monitor_id,),
                )
                return {(code, observation_key): xmin for code, observation_key, xmin in cursor.fetchall()}

        for round_index in range(3):
            for case, changed_count in cases.items():
                document = copy.deepcopy(initial)
                document["cursor"] = 101 + round_index
                for bar in document["bars"][:changed_count]:
                    bar["close"] += round_index + 1
                order = ("inline", "frames") if round_index % 2 == 0 else ("frames", "inline")
                for mode in order:
                    key = keys[(case, mode)]
                    before_versions = versions(key) if mode == "frames" else {}
                    with patch(
                        "kiwoom_monitor.central_server.diagnostic_metrics.capture_session_token",
                        return_value=(True, "shadow-checkpoint-benchmark"),
                    ), patch(
                        "kiwoom_monitor.central_server.diagnostic_metrics.record_db_call",
                        side_effect=collect,
                    ):
                        stores[mode].save_shadow_monitor_state(key, document)
                    self.assertEqual(document, stores[mode].load_shadow_monitor_state(key))
                    call = captured[-1]
                    self.assertEqual("committed", call["outcome"])
                    self.assertEqual(1, call["commits"])
                    self.assertEqual("candidate.shadow_checkpoint", call["writer_family"])
                    self.assertEqual("shadow_monitor_state", call["writer_kind"])
                    self.assertEqual(2 if mode == "inline" else 5, call["sql_calls"])
                    after_versions = versions(key) if mode == "frames" else {}
                    actual_changed = (
                        sum(before_versions.get(frame_key) != after_xmin
                            for frame_key, after_xmin in after_versions.items())
                        if mode == "frames" else None
                    )
                    if mode == "frames":
                        self.assertEqual(changed_count, actual_changed)
                    samples[case][mode].append({
                        "round": round_index + 1,
                        "input_bytes": encoded_bytes,
                        "changed_frames": actual_changed if mode == "frames" else changed_count,
                        "sql_calls": call["sql_calls"],
                        "execute_ms": call["execute_ms"],
                        "commit_ms": call["commit_ms"],
                        "total_ms": call["total_ms"],
                    })

        for case in cases:
            self.assertEqual(3, len(samples[case]["inline"]))
            self.assertEqual(3, len(samples[case]["frames"]))
        self.assertEqual(18, len(captured))

        physical_bytes = {}
        for case in cases:
            inline_key_for_case = keys[(case, "inline")]
            frame_key_for_case = keys[(case, "frames")]
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT pg_column_size(document_json) FROM central_shadow_monitor_state WHERE monitor_id=%s",
                               (inline_key_for_case,))
                physical_bytes.setdefault(case, {})["inline_document"] = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT pg_column_size(legacy.document_json), "
                    "pg_column_size(header.document_json)+pg_column_size(header.bar_order)+"
                    "(SELECT COALESCE(sum(pg_column_size(frame_json)),0) FROM central_shadow_checkpoint_frames "
                    "WHERE monitor_id=header.monitor_id) "
                    "FROM central_shadow_checkpoint_state header "
                    "JOIN central_shadow_monitor_state legacy USING(monitor_id) "
                    "WHERE header.monitor_id=%s", (frame_key_for_case,),
                )
                fallback_bytes, normalized_bytes = cursor.fetchone()
                physical_bytes[case]["legacy_fallback_document"] = fallback_bytes
                physical_bytes[case]["normalized_payload"] = normalized_bytes
                physical_bytes[case]["normalized_total_column_payload"] = fallback_bytes + normalized_bytes

        def summarize(values):
            return {
                field: {
                    "samples": [sample[field] for sample in values],
                    "median": median(sample[field] for sample in values),
                    "min": min(sample[field] for sample in values),
                    "max": max(sample[field] for sample in values),
                }
                for field in ("sql_calls", "execute_ms", "commit_ms", "total_ms")
            }

        report = {
            case: {
                "requested_changed_frames": changed_count,
                "inline": summarize(samples[case]["inline"]),
                "frames": summarize(samples[case]["frames"]),
                "physical_payload_bytes": physical_bytes[case],
            }
            for case, changed_count in cases.items()
        }
        self.assertTrue(all(row["inline"]["commit_ms"]["median"] >= 0 for row in report.values()))
        self.assertTrue(all(row["frames"]["commit_ms"]["median"] >= 0 for row in report.values()))
        print("shadow_checkpoint_committed_benchmark=" + json.dumps({
            "database": "kiwoom_monitor_diagnostic_test",
            "fixture_frames": len(bars),
            "fixture_bytes": encoded_bytes,
            "rounds": 3,
            "cases": report,
            "scope_note": "public writer commits on dedicated DB; order alternates per round; rows are temporary diagnostics and removed in tearDown; column payload includes the retained legacy fallback but excludes indexes and relation overhead",
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
