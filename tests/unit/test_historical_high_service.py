from __future__ import annotations

import unittest
from datetime import date

from kiwoom_monitor.application.historical_high_service import HistoricalHighCache, HistoricalHighEvidence, HistoricalHighService, HistoricalHighTarget


class HistoricalHighServiceTests(unittest.TestCase):
    def test_reads_all_continuation_pages_using_adjusted_yearly_chart(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.calls.append((api_id, cont_yn, next_key))
                if cont_yn == "N":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20260000", "high_pric": "100"}, {"dt": "19970000", "high_pric": "250"}]}, True, "older"
                return {"stk_yr_pole_chart_qry": [{"dt": "19850000", "high_pric": "400"}]}, False, ""

        client = Client()
        target = HistoricalHighService(client).load("000050")

        self.assertEqual(400, target.price)
        self.assertEqual(1985, target.first_year)
        self.assertEqual(2026, target.last_year)
        self.assertEqual([("ka10094", "N", ""), ("ka10094", "Y", "older"), ("ka10083", "N", ""), ("ka10083", "Y", "older")], client.calls)

    def test_combines_nxt_high_when_it_is_higher(self) -> None:
        class Client:
            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                high = "300" if str(body["stk_cd"]).endswith("_NX") else "200"
                return {"stk_yr_pole_chart_qry": [{"dt": "20250000", "high_pric": high}]}, False, ""

        self.assertEqual(300, HistoricalHighService(Client(), include_nxt=True).load("005930").price)

    def test_refines_adjustment_year_to_month_and_event_month_to_day(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.calls.append(api_id)
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [
                        {"dt": "20260000", "high_pric": "60000", "upd_stkpc_tp": "8", "upd_rt": "-80.00"},
                        {"dt": "20250000", "high_pric": "15000"},
                    ]}, False, ""
                if api_id == "ka10083":
                    return {"stk_mth_pole_chart_qry": [
                        {"dt": "20260401", "high_pric": "60000", "upd_stkpc_tp": "8", "upd_rt": "-80.00"},
                        {"dt": "20260501", "high_pric": "17000"},
                    ]}, False, ""
                return {"stk_dt_pole_chart_qry": [
                    {"dt": "20260410", "high_pric": "12000", "upd_stkpc_tp": "8", "upd_rt": "-80.00"},
                    {"dt": "20260420", "high_pric": "13000"},
                ]}, False, ""

        client = Client()
        target = HistoricalHighService(client).load("003350")

        self.assertEqual(17000, target.price)
        self.assertEqual("20260501", target.occurred_on)
        self.assertEqual(["ka10094", "ka10083", "ka10081"], client.calls)
        self.assertNotIn(60000, [item.high_price for item in target.evidence])

    def test_incremental_split_adjusts_saved_history_and_compares_new_high(self) -> None:
        cached = HistoricalHighCache(
            HistoricalHighTarget(1_200_000, 2024, 2025, "20250102", (HistoricalHighEvidence("month", "20250102", 1_200_000),)),
            "2025-08-25",
        )

        class Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.calls.append(api_id)
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20260000", "high_pric": "250000", "upd_stkpc_tp": "8", "upd_rt": "-83.333333"}]}, False, ""
                if api_id == "ka10083":
                    return {"stk_mth_pole_chart_qry": [{"dt": "20260201", "high_pric": "250000", "upd_stkpc_tp": "8", "upd_rt": "-83.333333"}]}, False, ""
                return {"stk_dt_pole_chart_qry": [
                    {"dt": "20260210", "high_pric": "190000", "upd_stkpc_tp": "8", "upd_rt": "-83.333333"},
                    {"dt": "20260220", "high_pric": "250000"},
                ]}, False, ""

        client = Client()
        target = HistoricalHighService(client, cache_loader=lambda code: cached).load("000001")

        self.assertEqual(250_000, target.price)
        self.assertEqual("20260220", target.occurred_on)
        self.assertEqual(["ka10094", "ka10083", "ka10081"], client.calls)
        self.assertIn(200_000, [item.high_price for item in target.evidence])

    def test_stops_after_yearly_chart_when_250_day_high_is_already_higher(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.calls.append(api_id)
                return {"stk_yr_pole_chart_qry": [
                    {"dt": "20260000", "high_pric": "198400"},
                    {"dt": "20250000", "high_pric": "150000"},
                ]}, False, ""

        client = Client()
        target = HistoricalHighService(client, high_250_loader=lambda code: 198_400).load("000720")

        self.assertEqual(198_400, target.price)
        self.assertEqual(["ka10094"], client.calls)

    def test_stops_before_daily_chart_when_refined_months_are_below_250_day_high(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.calls.append(api_id)
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20160000", "high_pric": "60000", "upd_stkpc_tp": "8"}]}, False, ""
                return {"stk_mth_pole_chart_qry": [{"dt": "20160301", "high_pric": "5000", "upd_stkpc_tp": "8"}]}, False, ""

        client = Client()
        target = HistoricalHighService(client, high_250_loader=lambda code: 5_960).load("047770")

        self.assertEqual(5_960, target.price)
        self.assertEqual(["ka10094", "ka10083"], client.calls)

    def test_refines_old_year_using_today_as_adjustment_base(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.bodies: list[tuple[str, dict[str, object]]] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.bodies.append((api_id, body))
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20240000", "high_pric": "17880"}]}, False, ""
                return {"stk_mth_pole_chart_qry": [{"dt": "20241002", "high_pric": "17880"}]}, False, ""

        client = Client()
        target = HistoricalHighService(client).load("003350")

        self.assertEqual(17_880, target.price)
        monthly_body = next(body for api_id, body in client.bodies if api_id == "ka10083")
        self.assertEqual(date.today().strftime("%Y%m%d"), monthly_body["base_dt"])
