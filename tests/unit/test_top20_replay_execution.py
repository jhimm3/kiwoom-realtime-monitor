"""Controlled local lifecycle checks; no PostgreSQL/performance acceptance."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_payload
from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
    ReplayRuntimeScope,
)
from kiwoom_monitor.central_server.diagnostic_rest_input import RequestTapeClient, read_request_tape
from kiwoom_monitor.central_server.diagnostic_top20_execution import (
    build_top20_session, execute_top20_session, prepare_top20_source_inputs,
)
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import read_realtime_tape, SubscriptionTape
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from tests.top20_native_fixture import capture_native_top20_fixture


ORIGIN = datetime.fromisoformat("2026-09-10T09:30:01+09:00")


class Top20ReplayExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stores = []
        self.addCleanup(lambda: [store.close() for store in self.stores])

    def store(self, name):
        store = SQLiteQueryStore(Path(self.directory.name) / f"{name}.sqlite3")
        store.initialize()
        self.stores.append(store)
        return store

    async def source_fixture(self):
        return await capture_native_top20_fixture(self.store("source"), ORIGIN)

    def inputs(self, fixture):
        values = []
        for row in fixture["events"]:
            if (row.get("event_type") == "collector_input" and row.get("input_kind") == "message"
                    or row.get("event_type") == "top20_realtime_input" and row.get("input_kind") == "market_operation"):
                values.append({"input_id": row["input_id"], "kind": row["input_kind"],
                    "entered_mono_ns": row.get("entered_mono_ns", row["mono_ns"]), "seq": row["seq"],
                    "payload": thaw_payload(row["payload"])})
        return sorted(values, key=lambda item: (item["entered_mono_ns"], item["seq"]))

    async def test_native_loops_repeat_three_times_and_exclusion_never_injects_past_membership(self):
        fixture = await self.source_fixture()
        memberships, stages = [], []
        for run, enabled in enumerate((True, True, True, False)):
            client = RequestTapeClient(read_request_tape(fixture["events"]), preserve_transport_delay=False)
            bindings = client.task_bindings(fixture["roles"])
            clock, store = Top20FixtureClock(ORIGIN), self.store(f"replay-{run}")
            store._query_cache_wall_time = clock.wall_time
            service, broker, collector = build_top20_session(store, clock, client, bindings,
                outbox_path=None, minute_backfill_enabled=False)
            # Distinct inputs reproduce newly generated native subscriptions;
            # they do not inject source ACK/READY controls into the hub.
            inputs = prepare_top20_source_inputs(self.inputs(fixture), clock=clock,
                started_mono_ns=fixture["started"], end_seconds=20)
            with patch.object(trace, "input_token", return_value=None), \
                    patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", 0):
                report = await execute_top20_session(service, broker, collector, clock=clock,
                    runtime=ReplayRuntimeScope(), bindings=bindings, subscription_tape=SubscriptionTape(
                        read_realtime_tape(fixture["events"])), source_hub_component=fixture["hub"],
                    source_component=fixture["component"], prepared_inputs=inputs,
                    started_mono_ns=fixture["started"], end_seconds=20, include_top20=enabled, drain_timeout=30)
            self.assertTrue(report["execution_succeeded"], report)
            self.assertFalse(report["full_experiment_acceptance"])
            self.assertFalse(report["historical_outputs_injected"])
            self.assertEqual(0, report["runtime"]["pending_threads"])
            self.assertEqual(0, report["runtime"]["pending_tasks"])
            stored = store.load_dataset_snapshots("top20_membership", ORIGIN.date().isoformat())
            if enabled:
                self.assertEqual(1, len(stored))
                memberships.append(stored[0]["payload"])
                stages.append(report["native_stage_ready"])
                self.assertTrue(any(item["native_ready"] for item in report["subscription"]["subscriptions"]))
            else:
                self.assertEqual([], stored)
                self.assertIsNone(report["native_membership"])
                self.assertEqual([], report["subscription"]["subscriptions"])
                self.assertEqual(0, report["rest"]["used_inputs"])
                self.assertTrue(all(item["delivered_rows"] == 0 for item in report["source_inputs"] if item["kind"] == "message"))
        self.assertEqual([memberships[0]] * 3, memberships)
        self.assertEqual([stages[0]] * 3, stages)

    def test_unknown_ready_and_invalid_clock_fail_before_any_execution(self):
        clock = Top20FixtureClock(ORIGIN)
        item = {"input_id": "unsafe", "kind": "upstream_ready", "seq": 1,
            "entered_mono_ns": 1, "payload": {"source_time": ORIGIN}}
        with self.assertRaisesRegex(ValueError, "historical_effect"):
            prepare_top20_source_inputs([item], clock=clock, started_mono_ns=1, end_seconds=1)
        item["kind"] = "message"
        item["payload"] = {"source_time": ORIGIN + timedelta(days=1), "message": {"trnm": "REAL", "data": []}}
        with self.assertRaisesRegex(ValueError, "clock_mismatch"):
            prepare_top20_source_inputs([item], clock=clock, started_mono_ns=1, end_seconds=1)
