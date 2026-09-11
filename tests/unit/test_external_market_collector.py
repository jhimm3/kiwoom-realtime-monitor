from __future__ import annotations

import unittest
from typing import Any

from kiwoom_monitor.central_server.external_market_collector import YahooDelayedMarketCollector, parse_yahoo_chart
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
    async def test_collects_both_contracts_and_persists_forward_roll(self) -> None:
        store = _MemoryStore()
        collector = _SyntheticCollector(  # type: ignore[arg-type]
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
