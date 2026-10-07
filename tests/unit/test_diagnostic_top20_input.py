from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    capture_owner, captured_workload, freeze_payload, operation_identity, thaw_payload,
)
from kiwoom_monitor.central_server.diagnostic_top20_input import (
    VERSION, ranking_cycle, replay_ranking_decisions,
)
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult


NOW = datetime.fromisoformat("2026-10-07T09:00:00+09:00")


def rows(clock="090000"):
    return [{"dt": "20261007", "tm": clock, "stk_cd": f"{100000 + rank:06d}",
             "stk_nm": f"종목{rank}", "bigd_rank": str(rank)} for rank in range(1, 21)]


class Broker:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def request_unrecorded(self, api_id, path, body):
        assert (api_id, path, body) == ("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return BrokerResult({"item_inq_rank": outcome, "ignored_network_field": "not retained"},
                            False, "", cache_hit=self.calls == 2)

    request = request_unrecorded


class Top20InputTests(unittest.IsolatedAsyncioTestCase):
    def recording(self, events):
        def emit(token, event_type, fields, value):
            events.append({"seq": len(events) + 1, "event_type": event_type,
                           **fields, "payload": freeze_payload(value).value})
            return True
        return patch.object(trace, "emit_payload", side_effect=emit)

    async def run_cycle(self, broker, directory):
        store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
        store.initialize()
        service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=lambda: NOW,
            catalog_loader=lambda: tuple((row["stk_cd"], row["stk_nm"], "KOSPI") for row in rows()))
        try:
            codes = await service.refresh_ranking_once()
            if service._fundamentals_tasks:
                await asyncio.gather(*tuple(service._fundamentals_tasks))
            return codes, store.load_dataset_snapshots("top20_membership", "2026-10-07", 1)
        finally:
            # The fixture owns catalog/subscription tasks too; let their real
            # to_thread calls finish before deleting the temporary SQLite file.
            if service._fundamentals_tasks:
                await asyncio.gather(*tuple(service._fundamentals_tasks), return_exceptions=True)
            await service.close()
            store.close()

    async def test_off_on_native_outputs_match_and_retry_decisions_replay(self):
        events = []
        outcomes = [rows("085930"), rows()[:19], rows()]
        with tempfile.TemporaryDirectory() as off, tempfile.TemporaryDirectory() as on:
            with patch.object(trace, "input_token", return_value=None), \
                    patch.object(trace, "emit_payload") as emit:
                before = await self.run_cycle(Broker(outcomes), off)
                emit.assert_not_called()
            with patch.object(trace, "input_token", side_effect=lambda key: "trace" if key == "top20_inputs" else None), \
                    self.recording(events):
                after = await self.run_cycle(Broker(outcomes), on)
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1][0]["payload"], after[1][0]["payload"])
        ranking_events = [event for event in events if event.get("market_input_version") == VERSION]
        self.assertEqual(["cycle_start", "attempt", "attempt", "attempt", "cycle_end"],
                         [event["input_kind"] for event in ranking_events])
        self.assertNotIn("ignored_network_field", thaw_payload(ranking_events[1]["payload"])["payload"])
        result = await replay_ranking_decisions(events, events[0]["input_id"])
        self.assertTrue(result["selection"]["accepted"])
        self.assertEqual(rows(), result["selection"]["items"])
        self.assertEqual(3, result["attempts"])
        self.assertFalse(result["downstream_replay_supported"])
        self.assertFalse(result["timing_preserved"])

    async def capture_failure(self, failure):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trace, "input_token", side_effect=lambda key: "trace" if key == "top20_inputs" else None), self.recording(events):
                with self.assertRaises(type(failure)):
                    await self.run_cycle(Broker([failure]), directory)
        return events

    async def test_transport_error_is_captured_without_message_and_replayed(self):
        events = await self.capture_failure(OSError("SECRET transport detail"))
        self.assertNotIn("SECRET", str(events))
        result = await replay_ranking_decisions(events, events[0]["input_id"])
        self.assertEqual("OSError", result["error_type"])
        self.assertIsNone(result["selection"])

    async def test_gap_rejection_bad_version_and_missing_attempt_fail_preflight(self):
        events = await self.capture_failure(OSError("request failed"))
        identifier = events[0]["input_id"]
        for mutation in ("gap", "rejected", "version", "attempt"):
            candidate = copy.deepcopy(events)
            if mutation == "gap":
                candidate[1]["seq"] = 9
            elif mutation == "rejected":
                candidate.append({"seq": 4, "event_type": "input_rejected", "input_id": identifier})
            elif mutation == "version":
                candidate[1]["market_input_version"] = "unknown"
            else:
                candidate.pop(1)
                candidate[-1]["seq"] = 2
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                ranking_cycle(candidate, identifier)

    async def test_observer_exception_does_not_change_native_result(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trace, "input_token", side_effect=lambda key: "trace" if key == "top20_inputs" else None), \
                    patch.object(trace, "emit_payload", side_effect=OSError("recorder failed")), \
                    patch.object(trace, "reject_input") as rejected:
                codes, snapshots = await self.run_cycle(Broker([rows()]), directory)
        self.assertEqual(20, len(codes))
        self.assertEqual(1, len(snapshots))
        self.assertEqual(3, sum(call.args[1].get("reason") == "ranking_capture_error"
                                for call in rejected.call_args_list))
        self.assertEqual(2, sum(call.args[1].get("reason") == "market_request_observer_error"
                                for call in rejected.call_args_list))

    async def test_same_component_child_inherits_cause_peer_does_not(self):
        class Owner:
            @captured_workload("top20", "autonomous_top20")
            async def child(self):
                return operation_identity()
        first, peer = Owner(), Owner()
        with patch.object(trace, "input_token", return_value=None):
            with capture_owner("top20", f"autonomous_top20:{id(first):x}", "root", cause_input_id="cause"):
                child = await asyncio.create_task(first.child())
                other = await asyncio.create_task(peer.child())
        self.assertEqual("cause", child["cause_input_id"])
        self.assertEqual("", other["cause_input_id"])

    async def test_replay_rejects_unused_response_and_does_not_execute_descendants(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trace, "input_token", side_effect=lambda key: "trace" if key == "top20_inputs" else None), \
                    self.recording(events):
                await self.run_cycle(Broker([rows("085930"), rows()]), directory)
        identifier = events[0]["input_id"]
        events.append({"seq": len(events) + 1, "event_type": "operation_start",
                       "operation_id": "same-cause-write", "cause_input_id": identifier})
        events.append({"seq": len(events) + 1, "event_type": "operation_start",
                       "operation_id": "independent-peer", "cause_input_id": "other"})
        result = await replay_ranking_decisions(events, identifier)
        self.assertEqual(["same-cause-write"], result["historical_descendant_operations_not_executed"])
        changed = thaw_payload(events[1]["payload"])
        changed["payload"]["item_inq_rank"] = rows()
        events[1]["payload"] = freeze_payload(changed).value
        with self.assertRaisesRegex(ValueError, "response_unused"):
            await replay_ranking_decisions(events, identifier)

    async def test_cancellation_is_incomplete_for_replay(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trace, "input_token", side_effect=lambda key: "trace" if key == "top20_inputs" else None), \
                    self.recording(events):
                broker = Broker([])
                broker.request_unrecorded = AsyncMock(side_effect=asyncio.CancelledError)
                with self.assertRaises(asyncio.CancelledError):
                    await self.run_cycle(broker, directory)
        ending = next(row for row in events if row.get("market_input_version") == VERSION
                      and row["input_kind"] == "cycle_end")
        self.assertEqual("cancelled", thaw_payload(ending["payload"])["outcome"])
        with self.assertRaisesRegex(ValueError, "cycle_invalid"):
            ranking_cycle(events, events[0]["input_id"])

    async def test_schema_three_round_trip_leaves_legacy_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                    patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path", return_value=control):
                parent = _set_tool(control, True, 300)
                _set_trace(control, True, 120, expected_session=parent["diagnostic_tool"]["session_id"])
                started = trace.start(seconds=60, top20_inputs=True)
                try:
                    with tempfile.TemporaryDirectory() as db:
                        await self.run_cycle(Broker([rows()]), db)
                finally:
                    completed = trace.stop()
                    _set_tool(control, False)
                self.assertEqual(3, completed["schema_version"])
                self.assertEqual(0, completed["known_dropped"])
                self.assertEqual(completed["accepted"], completed["written"])
                manifest, events = trace.recorded_events(started["trace_id"])
                market = [event for event in events if event["event_type"] == "market_input"]
                self.assertEqual(3, len(market))
                replay = await replay_ranking_decisions(events, market[0]["input_id"])
                self.assertTrue(replay["selection"]["accepted"])
