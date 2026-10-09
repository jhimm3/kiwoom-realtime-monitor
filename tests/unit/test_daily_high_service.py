from __future__ import annotations

import unittest

from kiwoom_monitor.application.daily_high_service import DailyHighService


class FullChartClient:
    def request_with_continuation(self, api_id, path, body, **kwargs):
        response = self.request(api_id, path, body)
        for row in response.get("stk_dt_pole_chart_qry", []):
            for key in ("open_pric", "low_pric", "cur_prc"):
                row.setdefault(key, row["high_pric"])
            row.setdefault("trde_qty", "0")
        return response, False, ""


class FakeClient(FullChartClient):
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        self.api_id = api_id
        self.path = path
        self.body = body
        rows = [{"dt": f"202608{day:02d}", "high_pric": str(100 + day)} for day in range(25, 0, -1)]
        return {"stk_dt_pole_chart_qry": rows}


class DailyHighServiceTests(unittest.TestCase):
    def test_nas_wrappers_preserve_verified_highs_without_local_tr(self) -> None:
        import tempfile
        from datetime import timedelta
        from pathlib import Path
        from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable
        from kiwoom_monitor.application.daily_bar_coverage import daily_query_date

        query_date = daily_query_date()
        basis = query_date.isoformat()
        rows = tuple({"trading_date": (query_date - timedelta(days=i)).isoformat(),
                      "high": 2000 + i, "open": 1000, "low": 900, "close": 1500,
                      "volume": 10, "trade_value_million_won": 1} for i in range(250))
        coverage = {"query_basis_date": basis, "window_end": basis, "expected_count": 250,
                    "collection_verified": True, "scope": "initial", "source_exhausted": False,
                    "periods": {str(n): {"status": "ready"} for n in (5, 20, 250)}}

        class Primary:
            error = None
            def load_stored_daily_bars_with_coverage(self, *args):
                if self.error:
                    raise self.error
                return rows, coverage

        class NoTR:
            def request_with_continuation(self, *args, **kwargs):
                raise AssertionError("stored daily restoration must not request Kiwoom TR")

        with tempfile.TemporaryDirectory() as directory:
            for mode in ("failover", "validation", "validation_fallback"):
                with self.subTest(mode=mode):
                    primary = Primary()
                    client = (FailoverKiwoomRestClient(primary, NoTR()) if mode == "failover" else
                              ParallelValidationClient(primary, NoTR(), Path(directory) / "report.jsonl",
                                                       fallback_on_unavailable=mode == "validation_fallback"))
                    target = DailyHighService(client, include_nxt=True).load("005930")
                    self.assertEqual(2249, target.high_250_price)
                    self.assertTrue(target.collection_verified)
                    self.assertEqual("ready", target.period_status("250"))
                    primary.error = CentralServerUnavailable("offline")
                    if mode == "validation":
                        with self.assertRaises(CentralServerUnavailable):
                            client.load_stored_daily_bars_with_coverage("005930", "KRX", 250)
                    else:
                        self.assertEqual(((), {}), client.load_stored_daily_bars_with_coverage("005930", "KRX", 250))
                    if mode == "failover":
                        primary.error = None
                        self.assertEqual(((), {}), client.load_stored_daily_bars_with_coverage("005930", "KRX", 250))

    def test_keeps_latest_250_daily_bars_for_persistence(self) -> None:
        from datetime import date, timedelta

        class ManyBarsClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                base = date(2026, 8, 28)
                return {"stk_dt_pole_chart_qry": [
                    {"dt": (base - timedelta(days=index)).strftime("%Y%m%d"), "high_pric": str(1_000 + index)}
                    for index in range(300)
                ]}

        targets = DailyHighService(ManyBarsClient()).load("005930")
        self.assertEqual(250, len(targets.daily_bars))

    def test_calculates_five_and_twenty_day_highs_from_latest_daily_bars(self) -> None:
        client = FakeClient()
        targets = DailyHighService(client).load("005930")
        self.assertEqual(125, targets.high_5_price)
        self.assertEqual(125, targets.high_20_price)
        self.assertEqual(125, targets.high_250_price)
        self.assertEqual("ready", targets.period_status("250"))
        self.assertEqual("ka10081", client.api_id)
        self.assertEqual("/api/dostk/chart", client.path)

    def test_prefers_direct_daily_trade_value_from_ka10081(self) -> None:
        class DirectValueClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                return {
                    "stk_dt_pole_chart_qry": [
                        {"dt": "20260822", "high_pric": "100", "trde_prica": "98765432100", "cur_prc": "100"},
                        {"dt": "20260821", "high_pric": "90", "trde_prica": "12345678900", "cur_prc": "80"},
                    ]
                }

        targets = DailyHighService(DirectValueClient()).load("005930")
        self.assertAlmostEqual(987_654_321, targets.previous_day_trade_value_eok or 0)
        self.assertEqual(100, targets.previous_day_close_price)

    def test_uses_latest_completed_bar_when_today_bar_is_absent(self) -> None:
        from datetime import date
        from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets

        targets = DailyHighTargets.from_daily_bars(
            (
                DailyBar("20260821", 100, 10.0, 80),
                DailyBar("20260820", 90, 9.0, 70),
            ),
            as_of=date(2026, 8, 23),
        )
        self.assertEqual(10.0, targets.previous_day_trade_value_eok)
        self.assertEqual(80, targets.previous_day_close_price)

    def test_sums_krx_and_nxt_only_for_previous_day_trade_value(self) -> None:
        class CombinedMarketClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                code = str(body["stk_cd"])
                value = "2000" if code.endswith("_NX") else "3000"
                high = "200" if code.endswith("_NX") else "100"
                return {
                    "stk_dt_pole_chart_qry": [
                        {"dt": "20260822", "high_pric": high, "trde_prica": value, "cur_prc": "90"}
                    ]
                }

        targets = DailyHighService(CombinedMarketClient(), include_nxt=True).load("005930")
        self.assertEqual(200, targets.high_5_price)  # Verified source end covers its entire short history
        self.assertEqual(50.0, targets.previous_day_trade_value_eok)  # 30억 + 20억

    def test_keeps_last_combined_250_high_when_nxt_temporarily_fails(self) -> None:
        class NxtFailureClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                if str(body["stk_cd"]).endswith("_NX"):
                    raise RuntimeError("temporary NXT error")
                return {"stk_dt_pole_chart_qry": [{"dt": "20260825", "high_pric": "2987000"}]}

        targets = DailyHighService(
            NxtFailureClient(),
            include_nxt=True,
            cached_high_250_loader=lambda code: 3_002_000,
        ).load("000660")

        self.assertIsNone(targets.high_250_price)
        self.assertEqual(3_002_000, targets.cached_high_250_price)

    def test_keeps_last_combined_250_high_when_nxt_response_is_temporarily_empty(self) -> None:
        class EmptyNxtClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                if str(body["stk_cd"]).endswith("_NX"):
                    return {"stk_dt_pole_chart_qry": []}
                return {"stk_dt_pole_chart_qry": [{"dt": "20260825", "high_pric": "2987000"}]}

        targets = DailyHighService(
            EmptyNxtClient(),
            include_nxt=True,
            cached_high_250_loader=lambda code: 3_007_000,
        ).load("000660")

        self.assertIsNone(targets.high_250_price)
        self.assertEqual(3_007_000, targets.cached_high_250_price)

    def test_successful_nxt_response_replaces_stale_cached_250_high(self) -> None:
        class CombinedClient(FullChartClient):
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                high = "3002000" if str(body["stk_cd"]).endswith("_NX") else "2987000"
                from datetime import date, timedelta
                return {"stk_dt_pole_chart_qry": [{"dt": (date(2026, 8, 25) - timedelta(days=i)).strftime("%Y%m%d"), "high_pric": high} for i in range(250)]}

        targets = DailyHighService(
            CombinedClient(),
            include_nxt=True,
            cached_high_250_loader=lambda code: 3_100_000,
        ).load("000660")

        self.assertEqual(3_002_000, targets.high_250_price)
