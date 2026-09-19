from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.minute_chart_service import MinuteChartService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        return {
            "stk_min_pole_chart_qry": [
                {"cntr_tm": "20260814100200", "open_pric": "-110", "high_pric": "120", "low_pric": "100", "cur_prc": "115", "trde_qty": "20"},
                {"cntr_tm": "20260814100100", "open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105", "trde_qty": "10"},
            ]
        }


class MinuteChartServiceTests(unittest.TestCase):
    def test_two_day_chart_uses_recent_central_bars_without_ka10080(self) -> None:
        class StoredRecentClient:
            def __init__(self) -> None:
                self.calls = []
                self.requests = 0

            def load_stored_recent_minute_bars(
                self, code, end_date, market, trading_days,
            ):
                self.calls.append((code, end_date, market, trading_days))
                return (
                    {
                        "trading_date": "2026-08-28", "minute": "15:00",
                        "open": 100, "high": 110, "low": 90, "close": 105,
                        "volume": 10, "trade_value_million_won": 200,
                    },
                    {
                        "trading_date": "2026-08-31", "minute": "10:00",
                        "open": 110, "high": 120, "low": 100, "close": 115,
                        "volume": 20, "trade_value_million_won": 300,
                    },
                ) if market == "COMBINED" else ()

            def request(self, *_args, **_kwargs):
                self.requests += 1
                raise AssertionError("recent central bars must not create ka10080")

        client = StoredRecentClient()
        bars = MinuteChartService(
            client, include_nxt=True,
        ).load_two_trading_days("005930", datetime(2026, 8, 31))

        self.assertEqual(0, client.requests)
        self.assertEqual(["COMBINED"], [call[2] for call in client.calls])
        self.assertEqual(
            ["2026-08-28", "2026-08-31"],
            sorted({bar.minute.date().isoformat() for bar in bars}),
        )

    def test_combined_central_bars_are_loaded_once_without_double_counting(self) -> None:
        class CombinedClient:
            def __init__(self) -> None:
                self.calls = []

            def load_stored_minute_bars(self, code, trading_date, market):
                self.calls.append((code, trading_date, market))
                return ({
                    "trading_date": trading_date, "minute": "10:01",
                    "open": 100, "high": 110, "low": 90, "close": 105,
                    "volume": 10, "trade_value_million_won": 250,
                    "market": "COMBINED", "source_market": "SOR",
                },)

            def request(self, *_args, **_kwargs):
                raise AssertionError("combined central bars must not create ka10080")

        client = CombinedClient()
        bars = MinuteChartService(client, include_nxt=True).load_today(
            "005930", datetime(2026, 9, 14),
        )

        self.assertEqual([("005930", "2026-09-14", "COMBINED")], client.calls)
        self.assertEqual(2.5, bars[0].trade_value_eok)

    def test_stored_today_returns_explicit_central_completion(self) -> None:
        class CoverageClient:
            def load_stored_minute_bars_with_coverage(self, code, trading_date, market):
                self.call = (code, trading_date, market)
                return (({
                    "trading_date": trading_date, "minute": "19:59",
                    "open": 100, "high": 100, "low": 100, "close": 100,
                    "volume": 1, "trade_value_million_won": 1,
                },), True)

        client = CoverageClient()
        bars, complete = MinuteChartService(client, include_nxt=True).load_today_with_completion(
            "001210", datetime(2026, 9, 14),
        )

        self.assertEqual(("001210", "2026-09-14", "COMBINED"), client.call)
        self.assertEqual(1, len(bars))
        self.assertTrue(complete)

    def test_uses_stored_central_bars_without_ka10080(self) -> None:
        class StoredClient:
            def __init__(self) -> None:
                self.requests = 0

            def load_stored_minute_bars(self, code, trading_date, market):
                self.assertions = (code, trading_date, market)
                return ({
                    "trading_date": "2026-08-14", "minute": "10:01",
                    "open": 100, "high": 110, "low": 90, "close": 105,
                    "volume": 10, "trade_value_million_won": 250,
                },)

            def request(self, *_args, **_kwargs):
                self.requests += 1
                raise AssertionError("stored bars must not create a Kiwoom query")

        client = StoredClient()
        bars = MinuteChartService(client).load_today("005930", datetime(2026, 8, 14))

        self.assertEqual(("005930", "2026-08-14", "KRX"), client.assertions)
        self.assertEqual(0, client.requests)
        self.assertEqual(2.5, bars[0].trade_value_eok)

    def test_empty_central_bars_do_not_create_an_app_ka10080_request(self) -> None:
        class EmptyStoredClient(FakeClient):
            def __init__(self) -> None:
                self.requests = 0

            def load_stored_minute_bars(self, *_args):
                return ()

            def request(self, api_id, path, body):
                self.requests += 1
                return super().request(api_id, path, body)

        client = EmptyStoredClient()
        bars = MinuteChartService(client).load_today("005930", datetime(2026, 8, 14))

        self.assertEqual(0, client.requests)
        self.assertEqual(0, len(bars))

    def test_converts_and_sorts_minute_chart_rows(self) -> None:
        bars = MinuteChartService(FakeClient()).load_today("005930", datetime(2026, 8, 14))

        self.assertEqual(2, len(bars))
        self.assertEqual(1, bars[0].minute.minute)
        self.assertEqual(10, bars[0].minute.hour)
        self.assertEqual(110, bars[1].open_price)

    def test_loads_a_second_page_when_ka10080_has_more_rows(self) -> None:
        class ContinuationClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def request_with_continuation(self, _api_id: str, _path: str, _body: dict[str, object], *, cont_yn: str, next_key: str):
                self.calls.append((cont_yn, next_key))
                if cont_yn == "N":
                    return {"stk_min_pole_chart_qry": [{"cntr_tm": "20260814100100", "open_pric": "100", "high_pric": "100", "low_pric": "100", "cur_prc": "100", "trde_qty": "1"}]}, True, "next"
                return {"stk_min_pole_chart_qry": [{"cntr_tm": "20260814100000", "open_pric": "90", "high_pric": "90", "low_pric": "90", "cur_prc": "90", "trde_qty": "1"}]}, False, ""

        client = ContinuationClient()
        bars = MinuteChartService(client).load_today("005930", datetime(2026, 8, 14))
        self.assertEqual([("N", ""), ("Y", "next")], client.calls)
        self.assertEqual(2, len(bars))

    def test_sums_krx_and_nxt_trade_value_for_same_minute(self) -> None:
        class MarketClient:
            def request(self, _api_id: str, _path: str, body: dict[str, object]) -> dict[str, object]:
                is_nxt = str(body["stk_cd"]).endswith("_NX")
                return {
                    "stk_min_pole_chart_qry": [
                        {
                            "cntr_tm": "20260814100100",
                            "open_pric": "200" if is_nxt else "100",
                            "high_pric": "220" if is_nxt else "120",
                            "low_pric": "190" if is_nxt else "90",
                            "cur_prc": "210" if is_nxt else "110",
                            "trde_qty": "20" if is_nxt else "10",
                        }
                    ]
                }

        bars = MinuteChartService(MarketClient(), include_nxt=True).load_today("005930", datetime(2026, 8, 14))
        self.assertEqual(1, len(bars))
        self.assertEqual(30, bars[0].volume)
        self.assertAlmostEqual(0.0000515, bars[0].trade_value_eok)

    def test_two_day_chart_uses_previous_available_trading_day(self) -> None:
        class TwoDayClient:
            def request(self, _api_id: str, _path: str, _body: dict[str, object]) -> dict[str, object]:
                return {"stk_min_pole_chart_qry": [
                    {"cntr_tm": "20260831100000", "open_pric": "110", "high_pric": "120", "low_pric": "100", "cur_prc": "115", "trde_qty": "20"},
                    {"cntr_tm": "20260828150000", "open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105", "trde_qty": "10"},
                    {"cntr_tm": "20260827150000", "open_pric": "90", "high_pric": "100", "low_pric": "80", "cur_prc": "95", "trde_qty": "10"},
                ]}

        bars = MinuteChartService(TwoDayClient()).load_two_trading_days("005930", datetime(2026, 8, 31))
        self.assertEqual(["2026-08-28", "2026-08-31"], sorted({bar.minute.date().isoformat() for bar in bars}))
