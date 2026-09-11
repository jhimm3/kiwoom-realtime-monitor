from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


class ParallelValidationClientTests(unittest.TestCase):
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
        self.assertEqual(["price"], row["different_fields"])
        self.assertEqual(1, local.calls)

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
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["equal"] for row in rows))


if __name__ == "__main__":
    unittest.main()
