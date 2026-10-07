"""Selection and safety bounds for recorded DB-call replay."""
from __future__ import annotations

import unittest
import json
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.diagnostic_replay import (
    ReplayCall, ReplayProfile, compile_replay_profile, compile_trace_replay_profile,
    dedicated_database_url, require_after_hours, run_replay,
)


def _report() -> dict:
    started = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
    def call(kind: str, offset: float, call_id: str, sql_calls: int = 2) -> dict:
        return {"writer_kind": kind, "started_at": started + offset,
                "call_id": call_id, "access_mode": "write",
                "outcome": "committed", "commits": 1, "sql_calls": sql_calls}

    return {"state": "completed", "kind": "measure", "run_id": "source-1",
            "result": {"phase": {
                "state": "complete", "elapsed_seconds": 30,
                "started_at_utc": "2026-10-01T00:00:00+00:00",
                "db_calls": {"state": "complete"},
                "db_calls_raw": {"coverage": "opt_in_observed_calls_only",
                                 "dropped": 0, "raw_truncated": False,
                                 "truncated": False,
                                 "calls": [call("news_job_claim", 2, "claim-1"),
                                           call("shadow_monitor_state", 12, "shadow-1"),
                                           call("news_job_claim", 14, "claim-2"),
                                           call("realtime_minute", 18, "minute-1")]},
                "market_bar_saves": {"writer_transactions": {
                    "shadow_monitor_state": {"call_samples": [
                        {"db_call_id": "shadow-1", "payload_bytes_estimated": 1_115_991}
                    ]}}}}}}


class ReplayProfileTests(unittest.TestCase):
    def test_recorded_minute_counts_require_complete_supported_shape_before_db_access(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_replay import _recorded_query_minute_shape
        counts = {"bar_shape_version": 1, "bar_changed_rows": 2, "observations": 7,
                  "metadata_suppressed_rows": 0, "revision_insert_rows": 1,
                  "revision_history_enabled": 1, "duplicate_input_keys": 2}
        def call(shape):
            return ReplayCall(0, "query_minute", rows_attempted=7,
                              source_call_id="minute-1", observed_domain_counts=tuple(shape.items()))
        self.assertEqual(counts, _recorded_query_minute_shape(call(counts)))
        invalid = [{key: value for key, value in counts.items() if key != "observations"}]
        invalid += [{**counts, key: value} for key, value in (
            ("bar_shape_version", 2), ("bar_changed_rows", -1), ("bar_changed_rows", 6),
            ("bar_changed_rows", True), ("revision_insert_rows", 6),
            ("observations", 6), ("metadata_suppressed_rows", 7),
            ("duplicate_input_keys", 7), ("duplicate_input_keys", -1),
            ("revision_history_enabled", 0),
        )]
        for shape in invalid:
            with self.subTest(shape=shape), patch("psycopg.connect") as connect:
                profile = ReplayProfile("trace", 1, 1, 1, (call(shape),), {}, 0, 1,
                                        ("query_minute",), (), query_minute_scenario="recorded_counts")
                with self.assertRaisesRegex(ValueError, "recorded_shape"):
                    run_replay(profile, "unused-url", "invalid-shape", threading.Event(),
                               threading.Event(), threading.Event(), threading.Event())
                connect.assert_not_called()

    def test_recorded_minute_trace_selection_uses_counters_and_rejects_missing_domain(self) -> None:
        counts = {"bar_shape_version": 1, "bar_changed_rows": 2, "observations": 7,
                  "metadata_suppressed_rows": 0, "revision_insert_rows": 1,
                  "revision_history_enabled": 1, "duplicate_input_keys": 2}
        events = [
            {"seq": 1, "event_type": "call_start", "call_id": "minute-1", "wall_ns": int(1001e9),
             "writer_kind": "query_minute", "writer_family": "rest.market_bars.minute",
             "operation": "replace_minute_bars", "access_mode": "write", "rows_attempted": 7},
            {"seq": 2, "event_type": "call_end", "call_id": "minute-1",
             "outcome": "committed", "commits": 1, "sql_calls": 42},
            {"seq": 3, "event_type": "domain", "call_id": "minute-1", "domain_counts": counts},
        ]
        manifest = {"state": "complete", "known_dropped": 0, "accepted": 3, "written": 3,
                    "last_seq": 3, "started_at": 1000, "finished_at": 1060,
                    "chunks": [{"name": "000001.jsonl", "count": 3, "first_seq": 1, "last_seq": 3}]}
        with patch("kiwoom_monitor.central_server.diagnostic_trace.status", return_value=manifest), \
                patch("kiwoom_monitor.central_server.diagnostic_trace.chunk_bytes",
                      side_effect=lambda *args: b"\n".join(json.dumps(row).encode() for row in events)):
            kwargs = dict(window_start_seconds=0, window_end_seconds=10,
                          include_writer_kinds=("query_minute",), query_minute_scenario="recorded_counts")
            plan = compile_trace_replay_profile("trace", 15, **kwargs)
            self.assertEqual(counts, dict(plan.supported_calls[0].observed_domain_counts))
            self.assertEqual("minute-1", plan.supported_calls[0].source_call_id)
            events[2]["domain_counts"] = {**counts, "bar_changed_rows": -1}
            with self.assertRaisesRegex(ValueError, "recorded_shape_unsupported"):
                compile_trace_replay_profile("trace", 15, **kwargs)
            events.pop()
            manifest.update(accepted=2, written=2, last_seq=2)
            manifest["chunks"][0].update(count=2, last_seq=2)
            with self.assertRaisesRegex(ValueError, "recorded_shape_missing"):
                compile_trace_replay_profile("trace", 15, **kwargs)

    def test_query_minute_requires_explicit_scenario_and_bounded_rows(self) -> None:
        events = [
            {"seq": 1, "event_type": "call_start", "call_id": "minute-1",
             "writer_family": "rest.market_bars.minute", "writer_kind": "query_minute",
             "operation": "replace_minute_bars", "access_mode": "write",
             "rows_attempted": 900, "wall_ns": int(1001 * 1e9)},
            {"seq": 2, "event_type": "call_end", "call_id": "minute-1",
             "outcome": "committed", "commits": 1, "sql_calls": 37},
            {"seq": 3, "event_type": "domain", "call_id": "minute-1",
             "domain_counts": {"bar_shape_version": 1, "bar_changed_rows": 0,
                               "revision_insert_rows": 0, "untrusted": "not-numeric"}},
        ]
        manifest = {"state": "complete", "known_dropped": 0, "accepted": 3,
                    "written": 3, "last_seq": 3, "started_at": 1000, "finished_at": 1060,
                    "chunks": [{"name": "000001.jsonl", "count": 3,
                                "first_seq": 1, "last_seq": 3}]}
        def content(*args):
            return b"\n".join(json.dumps(row).encode() for row in events)
        kwargs = dict(window_start_seconds=0, window_end_seconds=10,
                      include_writer_kinds=("query_minute",))
        with patch("kiwoom_monitor.central_server.diagnostic_trace.status", return_value=manifest), \
                patch("kiwoom_monitor.central_server.diagnostic_trace.chunk_bytes", side_effect=content):
            with self.assertRaisesRegex(ValueError, "scenario_required_or_invalid"):
                compile_trace_replay_profile("trace", 15, **kwargs)
            plan = compile_trace_replay_profile("trace", 15, **kwargs,
                                                query_minute_scenario="unchanged_page")
            self.assertEqual("unchanged_page", plan.query_minute_scenario)
            self.assertEqual(900, plan.supported_calls[0].rows_attempted)
            self.assertEqual({"bar_shape_version": 1, "bar_changed_rows": 0, "revision_insert_rows": 0},
                             dict(plan.supported_calls[0].observed_domain_counts))
            # Diagnostic SQL count is not the adapter's compatibility version.
            events[1]["sql_calls"] = 42
            events[2]["domain_counts"] = {}
            self.assertEqual((), compile_trace_replay_profile("trace", 15, **kwargs,
                             query_minute_scenario="fresh_page").supported_calls[0].observed_domain_counts)
            events[0]["rows_attempted"] = 1001
            with self.assertRaisesRegex(ValueError, "shape_unsupported"):
                compile_trace_replay_profile("trace", 15, **kwargs, query_minute_scenario="fresh_page")

    def test_trace_window_verifies_pairs_and_preserves_batch_rows(self) -> None:
        events = []
        for kind, offset, rows, sql in [("realtime_latest", 31, 25, 1),
                                        ("dataset:program_flow", 32, 2, 3),
                                        ("realtime_second_bar", 40, 30, 1)]:
            call_id = f"call-{kind}"
            events.extend([
                {"event_type": "call_start", "call_id": call_id,
                 "writer_kind": kind, "access_mode": "write", "rows_attempted": rows,
                 "wall_ns": int((1000 + offset) * 1e9)},
                {"event_type": "call_end", "call_id": call_id,
                 "outcome": "committed", "commits": 1, "sql_calls": sql},
            ])
        for seq, row in enumerate(events, 1):
            row["seq"] = seq
        manifest = {"state": "complete", "known_dropped": 0, "accepted": 6,
                    "written": 6, "last_seq": 6, "started_at": 1000, "finished_at": 4600,
                    "chunks": [{"name": "000001.jsonl", "count": 6,
                                "first_seq": 1, "last_seq": 6}]}
        content = b"\n".join(json.dumps(row).encode() for row in events)
        with patch("kiwoom_monitor.central_server.diagnostic_trace.status", return_value=manifest), \
                patch("kiwoom_monitor.central_server.diagnostic_trace.chunk_bytes", return_value=content):
            plan = compile_trace_replay_profile(
                "trace", 15, window_start_seconds=30, window_end_seconds=40,
                include_writer_kinds=("realtime_latest", "dataset:program_flow", "realtime_second_bar"))
            self.assertEqual([1, 2], [call.offset_seconds for call in plan.supported_calls])
            self.assertEqual([25, 2], [call.rows_attempted for call in plan.supported_calls])
            # A call exactly at end is excluded by the half-open window.
            self.assertEqual(2, plan.window_calls)
            events[3]["call_id"] = "missing-end"
            broken = b"\n".join(json.dumps(row).encode() for row in events)
            with patch("kiwoom_monitor.central_server.diagnostic_trace.chunk_bytes", return_value=broken):
                with self.assertRaisesRegex(ValueError, "unsupported_or_incomplete"):
                    compile_trace_replay_profile(
                        "trace", 15, window_start_seconds=30, window_end_seconds=40,
                        include_writer_kinds=("dataset:program_flow",))

    def test_window_and_writer_selection_preserve_relative_timing(self) -> None:
        plan = compile_replay_profile(
            _report(), 17, window_start_seconds=10, window_end_seconds=22,
            include_writer_kinds=("news_job_claim", "shadow_monitor_state"))
        self.assertEqual(4, plan.source_calls)
        self.assertEqual(2, plan.window_calls)
        self.assertEqual([2, 4], [call.offset_seconds for call in plan.supported_calls])
        self.assertEqual(["shadow_monitor_state", "news_job_claim"],
                         [call.kind for call in plan.supported_calls])
        self.assertEqual(1_115_991, plan.supported_calls[0].payload_bytes)
        self.assertEqual({}, plan.omitted_kinds)

    def test_unsupported_selected_writer_fails_instead_of_silent_omission(self) -> None:
        with self.assertRaisesRegex(ValueError, "replay_selected_calls_unsupported"):
            compile_replay_profile(
                _report(), 17, window_start_seconds=10, window_end_seconds=22,
                include_writer_kinds=("realtime_minute",))

    def test_incomplete_capture_and_overlong_window_are_rejected(self) -> None:
        source = _report()
        source["result"]["phase"]["db_calls_raw"]["dropped"] = 1
        with self.assertRaisesRegex(ValueError, "replay_profile_capture_incomplete"):
            compile_replay_profile(source, 35)
        with self.assertRaisesRegex(ValueError, "replay_profile_duration_out_of_bounds"):
            compile_replay_profile(_report(), 10, window_start_seconds=10,
                                   window_end_seconds=22)

    def test_database_url_cannot_target_operational_database(self) -> None:
        live = "postgresql://user:password@database/kiwoom_monitor"
        self.assertTrue(dedicated_database_url(live).endswith(
            "/kiwoom_monitor_diagnostic_test"))
        with self.assertRaises((RuntimeError, ValueError)):
            dedicated_database_url(live, "postgresql://user:password@database/kiwoom_monitor")

    def test_market_hours_guard(self) -> None:
        kst = ZoneInfo("Asia/Seoul")
        with self.assertRaisesRegex(ValueError, "after_market_hours_only"):
            require_after_hours(datetime(2026, 10, 2, 9, tzinfo=kst))
        with self.assertRaisesRegex(ValueError, "after_market_hours_only"):
            require_after_hours(datetime(2026, 10, 6, 9, tzinfo=kst))
        with self.assertRaisesRegex(ValueError, "after_market_hours_only"):
            require_after_hours(datetime(2027, 10, 5, 9, tzinfo=kst))
        with patch.dict("os.environ", {"KIWOOM_ENVIRONMENT": "real"}):
            require_after_hours(datetime(2026, 10, 5, 9, tzinfo=kst))
            require_after_hours(datetime(2026, 10, 9, 14, tzinfo=kst))
        with patch.dict("os.environ", {"KIWOOM_ENVIRONMENT": "mock"}):
            with self.assertRaisesRegex(ValueError, "after_market_hours_only"):
                require_after_hours(datetime(2026, 10, 5, 9, tzinfo=kst))
        require_after_hours(datetime(2026, 10, 2, 20, 30, tzinfo=kst))
        require_after_hours(datetime(2026, 10, 3, 9, tzinfo=kst))


class ReplayExecutionTests(unittest.TestCase):
    def test_realtime_lane_waits_for_commit_while_program_peer_is_independent(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_replay import TEST_DATABASE_NAME
        peer = threading.Event()
        latest_done = threading.Event()
        footprint = {"central_realtime_latest": set(), "central_second_trade_bars": set(),
                     "central_dataset_snapshots": set()}

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, parameters=()):
                if sql == "SELECT current_database()": self.result = (TEST_DATABASE_NAME,)
                elif sql.startswith("SELECT to_regclass"): self.result = (parameters[0],)
                else:
                    table = next(name for name in footprint if name in sql)
                    if sql.startswith("DELETE"): footprint[table].clear()
                    self.result = (len(footprint[table]),)
            def fetchone(self): return self.result

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def cursor(self): return Cursor()

        class Store:
            def _connect(self): return Connection()
            def save_realtime_snapshots(self, values):
                if not peer.wait(2): raise RuntimeError("peer did not commit independently")
                footprint["central_realtime_latest"].update(v["item_key"] for v in values)
                latest_done.set()
            def save_second_trade_bars(self, values):
                if not latest_done.is_set(): raise RuntimeError("realtime lane order lost")
                footprint["central_second_trade_bars"].update(
                    (v["code"], v["trade_second"]) for v in values)
            def save_dataset_snapshots(self, values):
                footprint["central_dataset_snapshots"].update((v[1], v[2]) for v in values)
                peer.set()

        profile = ReplayProfile("trace", 1, 3, 3, (
            ReplayCall(0, "realtime_latest", rows_attempted=2),
            ReplayCall(0, "realtime_second_bar", rows_attempted=3),
            ReplayCall(0, "dataset:program_flow", rows_attempted=1),
        ), {}, 0, 1, (), ())
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        with patch("psycopg.connect", return_value=Connection()), \
                patch("kiwoom_monitor.central_server.diagnostic_replay._ReplayStore", return_value=Store()):
            result = run_replay(profile, "test-url", "unit-lanes", threading.Event(),
                                threading.Event(), gate, done)
        self.assertEqual("complete", result["state"])
        self.assertEqual(3, sum(result["completed_calls"].values()))
        self.assertTrue(all(v["expected_rows"] == v["actual_rows"]
                            for v in result["fixture_verification"].values()))
        self.assertTrue(all(not rows for rows in footprint.values()))

    def test_busy_lane_does_not_drop_recorded_calls(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_replay import TEST_DATABASE_NAME
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, parameters=()):
                self.result = ((TEST_DATABASE_NAME,) if sql == "SELECT current_database()" else
                               ("central_news_jobs",) if sql.startswith("SELECT to_regclass") else None)
            def fetchone(self): return self.result
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def cursor(self): return Cursor()
        class Store:
            def _connect(self): return Connection()
            def claim_news_jobs(self, limit):
                time.sleep(0.03)
                return []
        profile = ReplayProfile("trace", 1, 8, 8,
                                tuple(ReplayCall(0, "news_job_claim") for _ in range(8)),
                                {}, 0, 1, (), ())
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        with patch("psycopg.connect", return_value=Connection()), \
                patch("kiwoom_monitor.central_server.diagnostic_replay._ReplayStore", return_value=Store()):
            result = run_replay(profile, "test-url", "unit-busy", threading.Event(),
                                threading.Event(), gate, done)
        self.assertEqual(8, result["completed_calls"]["news_job_claim"])
        self.assertEqual(0, result["unsubmitted_calls"])
        self.assertEqual(0, result["skipped_backpressure"]["news_job_claim"])
        self.assertGreater(result["schedule_lag_ms"]["max"], 20)


if __name__ == "__main__":
    unittest.main()
