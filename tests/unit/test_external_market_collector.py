from __future__ import annotations

import unittest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from typing import Any

from kiwoom_monitor.central_server.external_market_collector import YahooDelayedMarketCollector, parse_yahoo_chart
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import KST
from kiwoom_monitor.central_server.futures_roll import (
    change_percent,
    evaluate_roll,
    latest_bar_date,
    latest_session_volume,
    next_futures_contract,
    trailing_volume,
)


class ExternalMarketCollectorTests(unittest.TestCase):
    def test_parses_yahoo_chart_and_skips_missing_close(self) -> None:
        document = {"chart": {"result": [{
            "timestamp": [0, 300],
            "indicators": {"quote": [{
                "open": [100, None], "high": [110, None], "low": [90, None],
                "close": [105, None], "volume": [10, None],
            }]},
        }], "error": None}}
        rows = parse_yahoo_chart(document, "NASDAQ_FUTURES", "MNQU26.CME", "5m")
        self.assertEqual(1, len(rows))
        self.assertEqual("1970-01-01T00:00:00Z", rows[0]["bar_time"])
        self.assertEqual(105.0, rows[0]["close"])

    def test_missing_result_is_an_error_not_a_zero_bar(self) -> None:
        with self.assertRaises(ValueError):
            parse_yahoo_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "WTI_FUTURES", "CLV26.NYM", "5m")

    def test_next_contract_uses_monthly_and_quarterly_cycles(self) -> None:
        self.assertEqual("CLX26.NYM", next_futures_contract("WTI_FUTURES", "CLV26.NYM"))
        self.assertEqual("CLF27.NYM", next_futures_contract("WTI_FUTURES", "CLZ26.NYM"))
        self.assertEqual("MNQZ26.CME", next_futures_contract("NASDAQ_FUTURES", "MNQU26.CME"))
        self.assertEqual("MNQH27.CME", next_futures_contract("NASDAQ_FUTURES", "MNQZ26.CME"))

    def test_roll_requires_consecutive_volume_confirmations(self) -> None:
        first = evaluate_roll("CLV26.NYM", "CLX26.NYM", 100, 120, 0, 2)
        self.assertFalse(first.rolled)
        self.assertEqual(1, first.confirmation_count)
        second = evaluate_roll("CLV26.NYM", "CLX26.NYM", 100, 130, first.confirmation_count, 2)
        self.assertTrue(second.rolled)
        self.assertEqual("CLX26.NYM", second.active_contract)
        reset = evaluate_roll("CLV26.NYM", "CLX26.NYM", 120, 100, first.confirmation_count, 2)
        self.assertEqual(0, reset.confirmation_count)

    def test_volume_and_return_are_contract_local(self) -> None:
        rows = [
            {"bar_time": "2026-09-09T23:55:00Z", "volume": 99},
            {"bar_time": "2026-09-10T00:00:00Z", "volume": 10},
            {"bar_time": "2026-09-10T00:05:00Z", "volume": 20},
        ]
        self.assertEqual(30.0, latest_session_volume(rows))
        self.assertEqual("2026-09-10", latest_bar_date(rows))
        self.assertEqual(0.0, latest_session_volume(rows[:1], "2026-09-10"))
        self.assertEqual(129.0, trailing_volume(rows, "2026-09-10T00:05:00Z"))
        self.assertAlmostEqual(0.7821229, change_percent(90.20, 89.50) or 0.0, places=6)
        self.assertAlmostEqual(0.4950495, change_percent(91.35, 90.90) or 0.0, places=6)

    def test_rolls_when_only_the_next_contract_has_current_session_volume(self) -> None:
        result = evaluate_roll("CLV26.NYM", "CLX26.NYM", 0, 100, 1, 2)
        self.assertTrue(result.rolled)

    def test_trailing_volume_preserves_unknown_real_zero_partial_sum_and_window(self) -> None:
        end = "2026-09-10T00:05:00Z"
        for rows, cut in (
            ([], end), ([{"bar_time": end, "volume": None}], end),
            ([{"bar_time": "2026-09-09T00:05:00Z", "volume": 100}], end),
            ([{"bar_time": "invalid", "volume": 100}], end),
            ([{"bar_time": end, "volume": 0}], "invalid"),
        ):
            with self.subTest(rows=rows, cut=cut):
                self.assertIsNone(trailing_volume(rows, cut))
        self.assertEqual(0.0, trailing_volume([{"bar_time": end, "volume": 0}], end))
        self.assertEqual(7.0, trailing_volume([
            {"bar_time": "2026-09-09T00:05:00Z", "volume": 100},
            {"bar_time": "2026-09-09T00:06:00Z", "volume": 3},
            {"bar_time": "2026-09-09T23:59:00Z", "volume": None},
            {"bar_time": end, "volume": 4},
            {"bar_time": "2026-09-10T00:06:00Z", "volume": 100},
        ], end))


class _MemoryStore:
    def __init__(self) -> None:
        self.bars: list[dict[str, Any]] = []
        self.documents: dict[tuple[str, str], dict[str, Any]] = {}

    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        self.bars.extend(values)

    def load_documents(self, collection: str, owner: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        document = self.documents.get((collection, owner))
        return [{"document": document}] if document is not None else []

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        for value in values:
            self.documents[(collection, str(value["owner"]))] = dict(value["document"])


class _SyntheticCollector(YahooDelayedMarketCollector):
    def _fetch(self, instrument: str, contract: str, interval: str, range_value: str) -> list[dict[str, Any]]:
        del range_value
        is_next = contract in {"CLX26.NYM", "MNQZ26.CME"}
        if interval == "1d":
            return [
                _bar(instrument, contract, interval, "2026-09-09T00:00:00Z", 90.0, 100),
                _bar(instrument, contract, interval, "2026-09-10T00:00:00Z", 91.0, 100),
            ]
        return [_bar(
            instrument, contract, interval, "2026-09-10T01:00:00Z",
            90.5 if not is_next else 91.5, 200 if not is_next else 300,
        )]


def _bar(
    instrument: str, contract: str, timeframe: str, bar_time: str, close: float, volume: float,
) -> dict[str, Any]:
    return {
        "provider": "yahoo_delayed", "instrument": instrument, "contract": contract,
        "timeframe": timeframe, "bar_time": bar_time, "open": close, "high": close,
        "low": close, "close": close, "volume": volume, "updated_at": 1.0,
    }


class ExternalMarketAutomaticRollTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_volume_keeps_native_roll_state_until_recovery_and_restart(self) -> None:
        stages = ("active_error", "next_error", "both_error", "active_empty", "both_empty", "next_old", "active_null")
        for stage in stages:
            for has_state in (False, True):
                with self.subTest(stage=stage, has_state=has_state), tempfile.TemporaryDirectory() as directory:
                    now = datetime(2026, 9, 10, 10, tzinfo=KST)
                    store = SQLiteQueryStore(Path(directory) / "central.sqlite3", now_provider=lambda: now)
                    store.initialize()
                    previous = {
                        "provider": "yahoo_delayed", "instrument": "WTI_FUTURES",
                        "active_contract": "CLV26.NYM", "next_contract": "CLX26.NYM",
                        "confirmation_count": 1, "required_confirmations": 2,
                        "active_volume": 100, "candidate_volume": 150,
                        "current_price": 90.0, "previous_daily_close": 89.0,
                        "change_pct": 1.123, "rolled_at": "2026-09-01T10:00:00+09:00",
                        "previous_contract": "CLU26.NYM", "updated_at": "2026-09-10T09:00:00+09:00",
                    }
                    if has_state:
                        store.upsert_documents("external_market_roll_state", [{
                            "owner": "WTI_FUTURES", "key": "current", "document": previous,
                        }])
                    initial = store.load_documents("external_market_roll_state", "WTI_FUTURES", 1)
                    collector = _SyntheticCollector(store, {"WTI_FUTURES": "CLV26.NYM"}, now_provider=lambda: now)
                    fetch = collector._fetch
                    calls = []

                    def incomplete(instrument, contract, interval, range_value):
                        calls.append((contract, interval))
                        if interval == "5m":
                            active = contract == "CLV26.NYM"
                            affected = stage.startswith("both") or stage.startswith("active") == active
                            if affected:
                                if stage.endswith("error"):
                                    raise OSError("061 provider source unavailable")
                                if stage.endswith("empty"):
                                    return []
                                rows = fetch(instrument, contract, interval, range_value)
                                if stage.endswith("old"):
                                    return [{**row, "bar_time": "2026-09-08T00:00:00Z"} for row in rows]
                                return [{**row, "volume": None} for row in rows]
                        return fetch(instrument, contract, interval, range_value)

                    restarted = None
                    try:
                        with patch.object(collector, "_fetch", side_effect=incomplete):
                            for _ in range(2):
                                await collector.collect_once(include_daily=True)
                                self.assertEqual(initial, store.load_documents("external_market_roll_state", "WTI_FUTURES", 1))
                                status = store.load_documents("external_market_collection_status", "WTI_FUTURES", 1)[0]["document"]
                                self.assertEqual("failed", status["status"])
                                self.assertEqual("CLV26.NYM", status["contract"])
                                if stage.endswith("error"):
                                    self.assertIn("061 provider source unavailable", status["error"])
                        # A successful contract's daily work is not lost because comparison data is missing.
                        for contract in ("CLV26.NYM", "CLX26.NYM"):
                            successful = ("WTI_FUTURES", contract) in collector._daily_completed_contracts
                            if successful:
                                self.assertEqual(1, calls.count((contract, "1d")))
                                self.assertEqual(90.0, collector._daily_reference_closes[("WTI_FUTURES", contract)])
                        if collector._daily_completed_contracts:
                            self.assertTrue(store.load_external_bars("WTI_FUTURES", "1d"))
                        restarted = _SyntheticCollector(store, {"WTI_FUTURES": "CLV26.NYM"}, now_provider=lambda: now)
                        await restarted.collect_once(include_daily=True)
                        recovered = store.load_documents("external_market_roll_state", "WTI_FUTURES", 1)[0]["document"]
                        self.assertEqual("CLX26.NYM" if has_state else "CLV26.NYM", recovered["active_contract"])
                        self.assertEqual(0 if has_state else 1, recovered["confirmation_count"])
                        self.assertEqual(90.0, recovered["previous_daily_close"])
                        self.assertEqual("ok", store.load_documents("external_market_collection_status", "WTI_FUTURES", 1)[0]["document"]["status"])
                        if not has_state:
                            await restarted.collect_once(include_daily=False)
                            self.assertEqual("CLX26.NYM", store.load_documents("external_market_roll_state", "WTI_FUTURES", 1)[0]["document"]["active_contract"])
                    finally:
                        await collector.close()
                        if restarted is not None:
                            await restarted.close()
                        store.close()

    async def test_real_zero_and_old_shared_window_keep_existing_roll_rules(self) -> None:
        for active_volume, next_volume, old_window, expected, auto_roll in (
            (0, 100, False, "CLX26.NYM", True),
            (100, 0, False, "CLV26.NYM", True),
            (0, 0, False, "CLV26.NYM", True),
            (100, 150, True, "CLX26.NYM", True),
            (100, 150, False, "CLV26.NYM", False),
        ):
            with self.subTest(active=active_volume, next=next_volume, old_window=old_window), tempfile.TemporaryDirectory() as directory:
                now = datetime(2026, 9, 10, 10, tzinfo=KST)
                store = SQLiteQueryStore(Path(directory) / "central.sqlite3", now_provider=lambda: now)
                store.initialize()
                collector = _SyntheticCollector(store, {"WTI_FUTURES": "CLV26.NYM"}, now_provider=lambda: now,
                                                auto_roll_enabled=auto_roll)
                fetch = collector._fetch
                calls = []

                def rows(instrument, contract, interval, range_value):
                    calls.append(contract)
                    values = fetch(instrument, contract, interval, range_value)
                    if interval == "5m":
                        return [{**value, "volume": active_volume if contract == "CLV26.NYM" else next_volume,
                                 "bar_time": "2026-09-08T00:00:00Z" if old_window else value["bar_time"]} for value in values]
                    return values

                try:
                    with patch.object(collector, "_fetch", side_effect=rows):
                        await collector.collect_once(include_daily=True)
                        await collector.collect_once(include_daily=False)
                    state = store.load_documents("external_market_roll_state", "WTI_FUTURES", 1)[0]["document"]
                    self.assertEqual(expected, state["active_contract"])
                    self.assertEqual(0, state["confirmation_count"])
                    if not auto_roll:
                        self.assertIsNone(state["candidate_volume"])
                        self.assertEqual({"CLV26.NYM"}, set(calls))
                    self.assertEqual("ok", store.load_documents("external_market_collection_status", "WTI_FUTURES", 1)[0]["document"]["status"])
                finally:
                    await collector.close()
                    store.close()

    async def test_diagnostic_status_reports_live_collection_activity(self) -> None:
        collector = _SyntheticCollector(
            _MemoryStore(), {"WTI_FUTURES": "CLV26.NYM"}, poll_seconds=60,
        )
        before = collector.diagnostic_status()
        self.assertTrue(before["configured"])
        self.assertFalse(before["operational_enabled"])
        self.assertFalse(before["running"])
        self.assertEqual(60, before["poll_seconds"])
        self.assertEqual(0, before["collection_attempts"])

        await collector.collect_once(include_daily=False)
        after = collector.diagnostic_status()
        self.assertEqual(1, after["collection_attempts"])
        self.assertEqual(1, after["collection_completions"])
        self.assertIsNotNone(after["last_collection_started_at"])
        self.assertIsNotNone(after["last_collection_completed_at"])
        self.assertGreater(int(after["collection_saved_rows_total"]), 0)
        self.assertGreater(int(after["last_collection_saved_rows"]), 0)
        self.assertIsNone(after["last_collection_error"])

    async def test_collects_both_contracts_and_persists_forward_roll(self) -> None:
        store = _MemoryStore()
        collector = _SyntheticCollector(
            store, {"WTI_FUTURES": "CLV26.NYM"}, roll_confirmations=2,
        )
        await collector.collect_once(include_daily=True)
        first = store.documents[("external_market_roll_state", "WTI_FUTURES")]
        self.assertEqual("CLV26.NYM", first["active_contract"])
        self.assertEqual(1, first["confirmation_count"])
        await collector.collect_once(include_daily=False)
        second = store.documents[("external_market_roll_state", "WTI_FUTURES")]
        self.assertEqual("CLX26.NYM", second["active_contract"])
        self.assertEqual("CLV26.NYM", second["previous_contract"])
        self.assertEqual("previous_daily_close", second["change_basis"])
        self.assertAlmostEqual((91.5 / 90.0 - 1.0) * 100.0, second["change_pct"])
        self.assertEqual({"CLV26.NYM", "CLX26.NYM"}, {row["contract"] for row in store.bars})


if __name__ == "__main__":
    unittest.main()
