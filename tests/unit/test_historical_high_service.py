from __future__ import annotations

import unittest

from kiwoom_monitor.application.historical_high_service import HistoricalHighService


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
        self.assertEqual([("ka10094", "N", ""), ("ka10094", "Y", "older")], client.calls)

    def test_combines_nxt_high_when_it_is_higher(self) -> None:
        class Client:
            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                high = "300" if str(body["stk_cd"]).endswith("_NX") else "200"
                return {"stk_yr_pole_chart_qry": [{"dt": "20250000", "high_pric": high}]}, False, ""

        self.assertEqual(300, HistoricalHighService(Client(), include_nxt=True).load("005930").price)
