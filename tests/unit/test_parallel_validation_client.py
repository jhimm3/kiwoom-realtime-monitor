from __future__ import annotations

import json
import queue
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import RealtimeValidationRecorder
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def request_with_continuation(self, *args, **kwargs):
        self.calls += 1
        return self.result

    def server_now(self):
        return None

    def get_access_token(self):
        return "token"


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.wall = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def now(self) -> datetime:
        return self.wall + timedelta(seconds=self.value - 100.0)

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ParallelValidationClientTests(unittest.TestCase):
    def test_order_api_never_enters_parallel_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            central = FakeClient(({}, False, ""))
            local = FakeClient(({}, False, ""))
            client = ParallelValidationClient(central, local, Path(directory) / "report.jsonl")
            with self.assertRaisesRegex(ValueError, "order APIs"):
                client.request("kt10000", "/api/dostk/ordr", {})
        self.assertEqual(0, central.calls)
        self.assertEqual(0, local.calls)
    def test_returns_central_immediately_and_records_background_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "comparison.jsonl"
            central = FakeClient(({"price": 100, "return_msg": "ok"}, False, ""))
            local = FakeClient(({"price": 101, "return_msg": "different text"}, False, ""))
            client = ParallelValidationClient(central, local, report)
            result = client.request_with_continuation("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"})
            self.assertEqual(100, result[0]["price"])
            client._jobs.join()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertFalse(row["equal"])
        self.assertEqual("NOT_COMPARABLE", row["status"])
        self.assertEqual(["price"], row["different_fields"])
        self.assertEqual(1, local.calls)

    def test_query_value_mismatch_requires_shared_immutable_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "comparison.jsonl"
            central = FakeClient(({"revision_id": "r1", "price": 100}, False, ""))
            local = FakeClient(({"revision_id": "r1", "price": 101}, False, ""))
            client = ParallelValidationClient(central, local, report)
            client.request_with_continuation(
                "ka10080", "/api/dostk/chart", {"stk_cd": "005930"},
            )
            client._jobs.join()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("VALUE_MISMATCH", row["status"])
        self.assertEqual(["r1"], row["source_refs"]["shared"])

    def test_full_query_queue_is_counted_without_replacing_primary_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            central = FakeClient(({"price": 100}, False, ""))
            client = ParallelValidationClient(
                central, FakeClient(({"price": 100}, False, "")),
                Path(directory) / "comparison.jsonl",
            )
            with patch.object(client._jobs, "put_nowait", side_effect=queue.Full):
                result = client.request_with_continuation(
                    "ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"},
                )
            summary = client.validation_summary()
        self.assertEqual(100, result[0]["price"])
        self.assertEqual(1, summary["queue_skip_count"])
        self.assertEqual(1, summary["status_counts"]["MISSING"])

    def test_realtime_events_are_paired_by_stock_time_and_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            first = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            second = TradeTick("005930", 70100, 120, 8400000, 20, 70200, "101010")
            recorder.observe_central("trade", first)
            recorder.observe_central("trade", second)
            recorder.observe_local("trade", first)
            recorder.observe_local("trade", second)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["equal"] for row in rows))
        self.assertEqual(["MATCH", "NOT_COMPARABLE"], [row["status"] for row in rows])

    def test_unmatched_event_expires_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, match_timeout_seconds=2, monotonic_provider=clock.monotonic,
                now_provider=clock.now,
            )
            tick = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            recorder.observe_central("trade", tick)
            clock.advance(3)
            self.assertEqual(1, recorder.flush_expired())
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("MISSING", row["status"])
        self.assertEqual("direct", row["missing_side"])
        self.assertEqual(1, row["metrics"]["unmatched_count"])

    def test_pending_limit_eviction_is_counted_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report, pending_limit=100)
            for index in range(101):
                recorder.observe_central("trade", TradeTick(
                    f"{index:06d}", 70000, 100, 7000000, 10, 70100, "101010",
                ))
            summary = recorder.validation_summary()
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("pending_limit_eviction", row["reason"])
        self.assertEqual(1, summary["eviction_count"])
        self.assertEqual(100, summary["pending_nas_count"])

    def test_full_report_queue_is_counted_without_blocking_observer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RealtimeValidationRecorder(
                Path(directory) / "realtime.jsonl", report_queue_limit=1,
            )
            with patch.object(recorder._report_jobs, "put_nowait", side_effect=queue.Full):
                recorder.observe_central("trade", {"code": "005930"})
            summary = recorder.validation_summary()
        self.assertEqual(1, summary["queue_skip_count"])
        self.assertEqual(1, summary["status_counts"]["NOT_COMPARABLE"])
        self.assertEqual(1, summary["status_counts"]["MISSING"])

    def test_late_and_out_of_order_are_separate_from_value_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, late_threshold_ms=500, monotonic_provider=clock.monotonic,
                now_provider=clock.now,
            )
            current = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            earlier = TradeTick("005930", 69900, 90, 6200000, 5, 70100, "101009")
            recorder.observe_central("trade", current)
            clock.advance(0.8)
            recorder.observe_local("trade", current)
            recorder.observe_central("trade", earlier)
            recorder.observe_local("trade", earlier)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual("LATE", rows[0]["status"])
        self.assertEqual("OUT_OF_ORDER", rows[1]["status"])
        self.assertEqual(800.0, rows[1]["metrics"]["arrival_gap_ms"]["max"])

    def test_source_clock_skew_and_revision_refs_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, monotonic_provider=clock.monotonic, now_provider=clock.now,
            )
            tick = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            recorder.observe_central("trade", tick, metadata={
                "revision_id": "nas-r1", "received_at": "2026-09-14T09:00:00+00:00",
            })
            recorder.observe_local("trade", tick, metadata={
                "received_at": "2026-09-14T09:00:00.250000+00:00",
            })
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual(["nas-r1"], row["source_refs"]["nas"])
        self.assertEqual(250.0, row["timing"]["source_clock_skew_ms"])

    def test_repeated_identical_provider_event_is_duplicate(self) -> None:
        event = {
            "execution_no": "exec-1", "code": "005930", "name": "삼성전자",
            "side": "매수", "price": 70000, "quantity": 1,
            "trade_time": "101010", "market": "KRX",
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["MATCH", "DUPLICATE"], [row["status"] for row in rows])

    def test_duplicate_provider_event_before_counterpart_does_not_replace_pending(self) -> None:
        event = {
            "order_no": "order-1", "execution_no": "exec-1", "code": "005930",
            "name": "삼성전자", "side": "매수", "price": 70000, "quantity": 1,
            "trade_time": "101010", "market": "KRX",
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            recorder.observe_central("order_execution", event)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["DUPLICATE", "MATCH"], [row["status"] for row in rows])
        self.assertEqual(0, recorder.validation_summary()["pending_nas_count"])


if __name__ == "__main__":
    unittest.main()
