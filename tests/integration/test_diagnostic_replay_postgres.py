"""Dedicated PostgreSQL gate for the bounded replay pilot."""
from __future__ import annotations

import os
import threading
import unittest
from uuid import uuid4
from unittest.mock import patch

import psycopg

from kiwoom_monitor.central_server.diagnostic_replay import (
    ReplayCall, ReplayProfile, TEST_DATABASE_NAME, run_replay,
)


class DiagnosticReplayPostgresTests(unittest.TestCase):
    def test_recorded_minute_counts_preserve_partial_changes_duplicates_history_and_cleanup(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        from kiwoom_monitor.central_server.diagnostic_metrics import record_writer_transaction
        cases = [(900, 2, 3, 100, 1), (15, 3, 0, 5, 1),
                 (11, 2, 0, 4, 0), (10, 0, 0, 2, 1)]
        shapes = [{"bar_shape_version": 1, "observations": rows,
                   "bar_changed_rows": changed, "duplicate_input_keys": duplicates,
                   "revision_insert_rows": revisions, "revision_history_enabled": history,
                   "metadata_suppressed_rows": 0}
                  for rows, changed, revisions, duplicates, history in cases]
        calls = tuple(ReplayCall(index * 0.01, "query_minute", rows_attempted=case[0],
                                 source_call_id=f"source-{index}",
                                 observed_domain_counts=tuple(shape.items()))
                      for index, (case, shape) in enumerate(zip(cases, shapes)))
        profile = ReplayProfile("trace", 1, 4, 4, calls, {}, 0, 1,
                                ("query_minute",), (), query_minute_scenario="recorded_counts")
        run_id = uuid4().hex
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        with patch("kiwoom_monitor.central_server.diagnostic_metrics.record_writer_transaction",
                   wraps=record_writer_transaction) as observed:
            result = run_replay(profile, url, run_id, threading.Event(), threading.Event(), gate, done)
        self.assertEqual("complete", result["state"], result)
        measured = [call.kwargs["domain_counts"] for call in observed.call_args_list
                    if call.args[0] == "query_minute"][-len(cases):]
        self.assertEqual(shapes, measured)
        self.assertEqual(shapes, [call["expected_domain_counts"] for call in result["replayed_calls"]])
        self.assertEqual([f"source-{index}" for index in range(4)],
                         [call["source_call_id"] for call in result["replayed_calls"]])
        fixture = result["fixture_verification"]["query_minute"]
        self.assertTrue(fixture["values_match"], fixture)
        self.assertEqual(825, fixture["actual_rows"])
        self.assertEqual(825, fixture["metadata_rows"])
        self.assertEqual(821, fixture["revision_rows"])
        self._assert_minute_scope_empty(url, [f"diagnostic-replay-{run_id}-minute-{index}"
                                             for index in range(len(cases))])

    def test_recorded_minute_failed_call_rolls_back_then_cleans_seeded_scope_and_keeps_peer(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        from kiwoom_monitor.central_server.diagnostic_replay import (
            _ReplayStore, _query_minute_rows, _query_minute_observations,
        )
        # The peer writer and all run writes are gated by the same test-DB preflight.
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on -c statement_timeout=5000") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                self.assertEqual(TEST_DATABASE_NAME, cursor.fetchone()[0])
        store = _ReplayStore(url)
        peer = _query_minute_rows(1, f"diagnostic-peer-{uuid4().hex}", 0, "fresh_page")
        store.replace_minute_bars(peer, observations=_query_minute_observations(peer))
        class FailingStore(_ReplayStore):
            def replace_minute_bars(self, values, *, observations=None):
                if values[0]["code"].endswith("-minute-1") and values[0]["close"] == 10000:
                    observations = [(" ", observations[0][1]), *observations[1:]]
                return super().replace_minute_bars(values, observations=observations)
        shape = {"bar_shape_version": 1, "observations": 5, "bar_changed_rows": 1,
                 "duplicate_input_keys": 2, "revision_insert_rows": 1,
                 "revision_history_enabled": 1, "metadata_suppressed_rows": 0}
        profile = ReplayProfile("trace", 1, 2, 2, tuple(
            ReplayCall(index * 0.01, "query_minute", rows_attempted=5,
                       observed_domain_counts=tuple(shape.items())) for index in range(2)),
            {}, 0, 1, ("query_minute",), (), query_minute_scenario="recorded_counts")
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        run_id = uuid4().hex
        try:
            with patch("kiwoom_monitor.central_server.diagnostic_replay._ReplayStore", FailingStore):
                result = run_replay(profile, url, run_id, threading.Event(), threading.Event(), gate, done)
            self.assertEqual("aborted", result["state"], result)
            self.assertEqual({"query_minute": 1}, result["completed_calls"])
            self.assertEqual(["ValueError"], result["errors"])
            self._assert_minute_scope_empty(url, [f"diagnostic-replay-{run_id}-minute-{index}" for index in range(2)])
            self.assertEqual(1, len(store.load_minute_bars(peer[0]["code"], peer[0]["trading_date"], "KRX")))
        finally:
            with store._connect() as connection, connection.cursor() as cursor:
                for table, column, value in (("central_observation_revisions", "subject", peer[0]["code"] + ":KRX"),
                                             ("central_market_data_observation_meta", "subject", peer[0]["code"] + ":KRX"),
                                             ("central_minute_bars", "code", peer[0]["code"])):
                    cursor.execute(f"DELETE FROM {table} WHERE {column}=%s", (value,))

    def test_query_minute_scenarios_preserve_noop_revision_values_and_clean_all_tables(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        from kiwoom_monitor.central_server.diagnostic_metrics import record_writer_transaction
        for scenario in ("unchanged_page", "one_changed_bar", "fresh_page"):
            with self.subTest(scenario=scenario):
                run_id = uuid4().hex
                profile = ReplayProfile("trace", 1, 2, 2, (
                    ReplayCall(0, "query_minute", rows_attempted=900,
                               source_call_id="source-minute-1"),
                    ReplayCall(0.01, "query_minute", rows_attempted=900,
                               source_call_id="source-minute-2"),
                ), {}, 0, 1, ("query_minute",), (), query_minute_scenario=scenario)
                gate, done = threading.Event(), threading.Event()
                gate.set(); done.set()
                with patch("kiwoom_monitor.central_server.diagnostic_metrics.record_writer_transaction",
                           wraps=record_writer_transaction) as observed:
                    result = run_replay(profile, url, run_id, threading.Event(), threading.Event(), gate, done)
                self.assertEqual("complete", result["state"], result)
                self.assertEqual({"query_minute": 2}, result["completed_calls"])
                replayed = result["replayed_calls"]
                self.assertEqual(["source-minute-1", "source-minute-2"],
                                 [call["source_call_id"] for call in replayed])
                self.assertEqual(2, len({call["replay_call_id"] for call in replayed}))
                self.assertEqual([900, 900], [call["rows_attempted"] for call in replayed])
                self.assertEqual(["committed", "committed"], [call["state"] for call in replayed])
                fixture = result["fixture_verification"]["query_minute"]
                self.assertTrue(fixture["values_match"])
                self.assertEqual(1800 if scenario == "fresh_page" else 900, fixture["actual_rows"])
                self.assertEqual(fixture["actual_rows"], fixture["metadata_rows"])
                self.assertEqual(1800 if scenario == "fresh_page" else 902 if scenario == "one_changed_bar" else 900,
                                 fixture["revision_rows"])
                calls = [call.kwargs["domain_counts"] for call in observed.call_args_list
                         if call.args[0] == "query_minute"][-2:]
                expected_change = 0 if scenario == "unchanged_page" else 1 if scenario == "one_changed_bar" else 900
                self.assertEqual([expected_change] * 2, [call["bar_changed_rows"] for call in calls])
                self.assertEqual([expected_change] * 2, [call["revision_insert_rows"] for call in calls])
                codes = ([f"diagnostic-replay-{run_id}-minute-{index}" for index in range(2)]
                         if scenario == "fresh_page" else [f"diagnostic-replay-{run_id}-minute"])
                self._assert_minute_scope_empty(url, codes)

    def test_query_minute_invalid_observation_rolls_back_and_cleans_only_its_scope(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                self.assertEqual(TEST_DATABASE_NAME, cursor.fetchone()[0])
        from kiwoom_monitor.central_server.diagnostic_replay import _ReplayStore, _query_minute_rows, _query_minute_observations
        peer_code = f"diagnostic-replay-peer-{uuid4().hex}"
        peer = _query_minute_rows(1, peer_code, 0, "fresh_page")
        store = _ReplayStore(url)
        store.replace_minute_bars(peer, observations=_query_minute_observations(peer))
        class FailingStore(_ReplayStore):
            calls = 0
            def replace_minute_bars(self, values, *, observations=None):
                self.calls += 1
                if self.calls == 2:
                    observations = [(" ", observations[0][1]), *observations[1:]]
                return super().replace_minute_bars(values, observations=observations)
        run_id = uuid4().hex
        profile = ReplayProfile("trace", 1, 2, 2, (
            ReplayCall(0, "query_minute", rows_attempted=2),
            ReplayCall(0.01, "query_minute", rows_attempted=2),
        ), {}, 0, 1, ("query_minute",), (), query_minute_scenario="fresh_page")
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        try:
            with patch("kiwoom_monitor.central_server.diagnostic_replay._ReplayStore", FailingStore):
                result = run_replay(profile, url, run_id, threading.Event(), threading.Event(), gate, done)
            self.assertEqual("aborted", result["state"])
            self.assertEqual({"query_minute": 1}, result["completed_calls"])
            self.assertEqual(["ValueError"], result["errors"])
            self._assert_minute_scope_empty(url, [f"diagnostic-replay-{run_id}-minute-{index}" for index in range(2)])
            self.assertEqual(1, len(store.load_minute_bars(peer[0]["code"], peer[0]["trading_date"], "KRX")))
        finally:
            with store._connect() as connection, connection.cursor() as cursor:
                for table, column, value in (("central_observation_revisions", "subject", peer[0]["code"] + ":KRX"),
                                             ("central_market_data_observation_meta", "subject", peer[0]["code"] + ":KRX"),
                                             ("central_minute_bars", "code", peer[0]["code"])):
                    cursor.execute(f"DELETE FROM {table} WHERE {column}=%s", (value,))

    def _assert_minute_scope_empty(self, url, codes):
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on") as connection:
            with connection.cursor() as cursor:
                for table, column, values in (("central_minute_bars", "code", codes),
                                              ("central_market_data_observation_meta", "subject", [code + ":KRX" for code in codes]),
                                              ("central_observation_revisions", "subject", [code + ":KRX" for code in codes])):
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE {column}=ANY(%s)", (values,))
                    self.assertEqual(0, cursor.fetchone()[0], table)

    def test_realtime_and_program_shapes_commit_verify_and_clean_scoped_rows(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        run_id = uuid4().hex
        profile = ReplayProfile("diagnostic-trace", 1, 6, 6, (
            ReplayCall(0.0, "realtime_latest", rows_attempted=3),
            ReplayCall(0.01, "realtime_second_bar", rows_attempted=2),
            ReplayCall(0.02, "dataset:program_flow", rows_attempted=1),
            ReplayCall(0.03, "realtime_latest", rows_attempted=2),
            ReplayCall(0.04, "dataset:program_flow", rows_attempted=2),
            ReplayCall(0.05, "realtime_second_bar", rows_attempted=2),
        ), {}, 0, 1, (), ())
        gate, done = threading.Event(), threading.Event()
        gate.set(); done.set()
        result = run_replay(profile, url, run_id, threading.Event(), threading.Event(), gate, done)
        self.assertEqual("complete", result["state"])
        self.assertEqual({"realtime_latest": 2, "realtime_second_bar": 2, "dataset:program_flow": 2},
                         result["completed_calls"])
        self.assertEqual({"realtime_latest": {"expected_rows": 3, "actual_rows": 3},
                          "realtime_second_bar": {"expected_rows": 2, "actual_rows": 2},
                          "dataset:program_flow": {"expected_rows": 2, "actual_rows": 2}},
                         result["fixture_verification"])
        codes = [f"diagnostic-replay-{run_id}-{item:03d}" for item in range(3)]
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on") as connection:
            with connection.cursor() as cursor:
                for table, column in (("central_realtime_latest", "item_key"),
                                      ("central_second_trade_bars", "code"),
                                      ("central_dataset_snapshots", "subject")):
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE {column}=ANY(%s)", (codes,))
                    self.assertEqual(0, cursor.fetchone()[0], table)

    def test_news_and_shadow_replay_commit_and_remove_only_their_rows(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL URL is required")
        run_id = uuid4().hex
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on") as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                self.assertEqual(TEST_DATABASE_NAME, cursor.fetchone()[0])
        profile = ReplayProfile(
            "diagnostic-test", 1.0, 2, 2,
            (ReplayCall(0.0, "news_job_claim"),
             ReplayCall(0.1, "shadow_monitor_state", 20_000)),
            {}, 0.0, 1.0, (), (),
        )
        stop = threading.Event()
        ready = threading.Event()
        started = threading.Event()
        done = threading.Event()
        started.set()
        done.set()
        result = run_replay(profile, url, run_id, stop, ready, started, done)
        self.assertEqual("complete", result["state"])
        self.assertEqual({"news_job_claim": 1, "shadow_monitor_state": 1},
                         result["completed_calls"])
        self.assertEqual({"news_job_claim": 0, "shadow_monitor_state": 0},
                         result["skipped_backpressure"])
        with psycopg.connect(url, autocommit=True, connect_timeout=5,
                             options="-c default_transaction_read_only=on") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM central_shadow_monitor_state WHERE monitor_id=%s",
                    (f"diagnostic-replay-{run_id}",),
                )
                self.assertEqual(0, cursor.fetchone()[0])


if __name__ == "__main__":
    unittest.main()
