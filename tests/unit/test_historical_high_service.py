from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from kiwoom_monitor.application.historical_high_service import HistoricalHighCache, HistoricalHighEvidence, HistoricalHighService, HistoricalHighTarget


class HistoricalHighServiceTests(unittest.TestCase):
    def test_uses_stored_nas_target_without_chart_tr(self) -> None:
        class Client:
            def load_stored_historical_high(self, code):
                return {
                    "price": 90000, "first_year": 2020, "last_year": 2026,
                    "occurred_on": "20260914", "evidence": [{
                        "period": "day", "trade_date": "20260914", "high_price": 90000,
                    }],
                }
            def request_with_continuation(self, *args, **kwargs):
                raise AssertionError("chart TR must not run")

        target = HistoricalHighService(Client()).load("005930")

        self.assertEqual(90000, target.price)
        self.assertEqual("20260914", target.evidence[0].trade_date)

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

    def test_incremental_split_discards_old_basis_and_reloads_today_adjusted_history(self) -> None:
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
        self.assertEqual(["ka10094", "ka10083", "ka10081", "ka10094", "ka10083", "ka10081"], client.calls)
        self.assertNotIn(1_200_000, [item.high_price for item in target.evidence])
        self.assertNotIn(200_000, [item.high_price for item in target.evidence])

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
        with patch('kiwoom_monitor.application.historical_high_service.kst_today', return_value=date(2030,1,2)):
            target = HistoricalHighService(client).load("003350")

        self.assertEqual(17_880, target.price)
        monthly_body = next(body for api_id, body in client.bodies if api_id == "ka10083")
        self.assertEqual("20300102", monthly_body["base_dt"])

    def test_source_as_of_is_used_for_adjustment_chart_requests(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.bodies: list[tuple[str, dict[str, object]]] = []

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.bodies.append((api_id, body))
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20240000", "high_pric": "150", "upd_stkpc_tp": "8"}]}, False, ""
                if api_id == "ka10083":
                    return {"stk_mth_pole_chart_qry": [{"dt": "20241000", "high_pric": "140", "upd_stkpc_tp": "8"}]}, False, ""
                return {"stk_dt_pole_chart_qry": [{"dt": "20241002", "high_pric": "130"}]}, False, ""

        source_day = date(2001, 4, 3)
        client = Client()
        target = HistoricalHighService(client).load("003350", as_of=source_day)

        self.assertEqual(130, target.price)
        self.assertEqual(["20010403"] * 3, [body["base_dt"] for _, body in client.bodies])

    def test_source_as_of_is_used_for_250_day_evidence(self) -> None:
        class Client:
            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                return {"stk_yr_pole_chart_qry": [{"dt": "20240000", "high_pric": "150"}]}, False, ""

        target = HistoricalHighService(
            Client(), high_250_loader=lambda _code: 200,
        ).load("003350", as_of=date(2001, 4, 3))

        self.assertEqual("20010403", target.evidence[-1].trade_date)

    def test_incremental_historical_high_uses_source_as_of_date(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.bodies: list[dict[str, object]] = []

            def load_stored_historical_high(self, code):
                return None

            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                self.bodies.append(body)
                return {"stk_yr_pole_chart_qry": [{"dt": "20250000", "high_pric": "100"}]}, False, ""

        client = Client()
        cache = HistoricalHighCache(
            HistoricalHighTarget(100, 2025, 2025, "20250000", (
                HistoricalHighEvidence("year", "20250000", 100),
            )),
            "2025-01-01",
        )
        service = HistoricalHighService(
            client, cache_loader=lambda _code: cache,
            high_250_loader=lambda _code: 90,
        )

        target = service.load("003350", as_of=date(2001, 4, 3))

        self.assertEqual(["20010403"], [body["base_dt"] for body in client.bodies])
        self.assertEqual("20010403", target.evidence[-1].trade_date)

    def test_keeps_current_adjusted_price_after_share_consolidations(self) -> None:
        class Client:
            def request_with_continuation(self, api_id: str, path: str, body: dict[str, object], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, object], bool, str]:
                if api_id == "ka10094":
                    return {"stk_yr_pole_chart_qry": [{"dt": "20160000", "high_pric": "3450000"}]}, False, ""
                return {"stk_mth_pole_chart_qry": [{"dt": "20161104", "high_pric": "3450000"}]}, False, ""

        target = HistoricalHighService(Client()).load("900300")

        self.assertEqual(3_450_000, target.price)
        self.assertEqual("20161104", target.occurred_on)


class _MarketCharts:
    KEYS = {"ka10094": "stk_yr_pole_chart_qry", "ka10083": "stk_mth_pole_chart_qry", "ka10081": "stk_dt_pole_chart_qry"}

    def __init__(self, *, krx=100, nxt=200):
        self.rows = {(code, api): [{"dt": day, "high_pric": str(high)}]
                     for code, high in (("005930", krx), ("005930_NX", nxt))
                     for api, day in (("ka10094", "20260000"), ("ka10083", "20261001"))}
        self.failures = {}
        self.pages = {}
        self.calls = []

    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((api_id, path, dict(body), cont_yn, next_key))
        key = (body["stk_cd"], api_id)
        if key in self.failures:
            raise self.failures[key]
        if key in self.pages:
            index = int(next_key) if cont_yn == "Y" else 0
            pages = self.pages[key]
            more = index + 1 < len(pages)
            return {self.KEYS[api_id]: pages[index]}, more, str(index + 1) if more else ""
        return {self.KEYS[api_id]: self.rows.get(key, [])}, False, ""


class HistoricalHighMarketCacheTests(unittest.TestCase):
    DAY = date(2026, 10, 20)

    @staticmethod
    def cache(price=100, day="20260901"):
        return HistoricalHighCache(HistoricalHighTarget(price, 2026, 2026, day,
            (HistoricalHighEvidence("month", day, price),)), "2026-10-19")

    def load(self, client, cache=None, *, include_nxt=True, high_250=None):
        return HistoricalHighService(client, include_nxt=include_nxt, cache_loader=lambda _: cache,
            high_250_loader=lambda _: high_250).load("005930", as_of=self.DAY)

    def test_cached_nxt_only_increase_matches_fresh_without_reloading_krx_months(self):
        client, fresh_client = _MarketCharts(), _MarketCharts()
        cached = self.load(client, self.cache())
        fresh = self.load(fresh_client)
        self.assertEqual((200, "20261001"), (cached.price, cached.occurred_on))
        self.assertEqual((fresh.price, fresh.occurred_on), (cached.price, cached.occurred_on))
        self.assertEqual([("ka10094", "005930"), ("ka10094", "005930_NX"), ("ka10083", "005930_NX")],
                         [(api, body["stk_cd"]) for api, _, body, _, _ in client.calls])
        self.assertEqual(4, len(fresh_client.calls))
        self.assertTrue(all(body["base_dt"] == "20261020" and body["upd_stkpc_tp"] == "1"
                            for _, _, body, _, _ in client.calls + fresh_client.calls))

    def test_prior_nxt_year_and_earlier_same_year_month_are_not_certified_by_krx_cache(self):
        for year, month in (("20250000", "20250501"), ("20260000", "20260501")):
            with self.subTest(month=month):
                client = _MarketCharts(nxt=80)
                client.rows[("005930_NX", "ka10094")] = [{"dt": year, "high_pric": "300"}]
                client.rows[("005930_NX", "ka10083")] = [{"dt": month, "high_pric": "300"}]
                cached = self.load(client, self.cache())
                self.assertEqual((300, month), (cached.price, cached.occurred_on))
                self.assertEqual((300, month), (self.load(client).price, self.load(client).occurred_on))

    def test_nxt_annual_continuation_keeps_old_max_while_krx_checked_cut_stays_bounded(self):
        client = _MarketCharts(nxt=80)
        client.pages[("005930", "ka10094")] = [
            [{"dt": "20260000", "high_pric": "100"}, {"dt": "20250000", "high_pric": "100"}],
            [{"dt": "20100000", "high_pric": "90"}],
        ]
        client.pages[("005930_NX", "ka10094")] = [
            [{"dt": "20260000", "high_pric": "80"}], [{"dt": "20250000", "high_pric": "300"}],
        ]
        client.rows[("005930_NX", "ka10083")] = [{"dt": "20250501", "high_pric": "300"}]
        target = self.load(client, self.cache())
        self.assertEqual(300, target.price)
        self.assertEqual([("005930", "N", ""), ("005930_NX", "N", ""), ("005930_NX", "Y", "1")],
            [(body["stk_cd"], cont, key) for api, _, body, cont, key in client.calls if api == "ka10094"])

    def test_cached_krx_increase_and_krx_only_selection_keep_their_market_source(self):
        client = _MarketCharts(krx=250, nxt=90)
        target = self.load(client, self.cache())
        self.assertEqual(250, target.price)
        self.assertEqual([("ka10094", "005930"), ("ka10083", "005930"), ("ka10094", "005930_NX")],
            [(api, body["stk_cd"]) for api, _, body, _, _ in client.calls])
        client = _MarketCharts()
        client.failures[("005930_NX", "ka10094")] = AssertionError("KRX-only queried NXT")
        self.assertEqual(100, self.load(client, self.cache(), include_nxt=False).price)
        self.assertEqual(1, len(client.calls))

    def test_fresh_canonical_evidence_keeps_krx_max_and_original_first_tie_metadata(self):
        for nxt in (200, 300):
            with self.subTest(nxt=nxt):
                client = _MarketCharts(krx=300, nxt=nxt)
                client.rows[("005930", "ka10083")][0]["upd_rt"] = "0"
                client.rows[("005930_NX", "ka10083")][0]["upd_rt"] = "0.0"
                target = self.load(client)
                self.assertEqual((300, "20261001"), (target.price, target.occurred_on))
                self.assertEqual((HistoricalHighEvidence("month", "20261001", 300, adjustment_rate="0"),), target.evidence)
        client = _MarketCharts(krx=300, nxt=300)
        client.rows[("005930", "ka10083")] = [
            {"dt": "20260901", "high_pric": "200"},
            {"dt": "20261001", "high_pric": "300"},
        ]
        client.rows[("005930_NX", "ka10083")] = [{"dt": "20260901", "high_pric": "300"}]
        target = self.load(client)
        self.assertEqual((300, "20261001"), (target.price, target.occurred_on))
        self.assertEqual([("20260901", 300), ("20261001", 300)],
            [(item.trade_date, item.high_price) for item in target.evidence])

    def test_cached_nxt_winner_is_not_overwritten_by_lower_same_key_krx_refinement(self):
        client = _MarketCharts(krx=250, nxt=300)
        client.rows[("005930", "ka10094")] = [{"dt": "20260000", "high_pric": "400"}]
        target = self.load(client, self.cache(300, "20261001"))
        self.assertEqual(300, target.price)
        self.assertEqual((HistoricalHighEvidence("month", "20261001", 300),), target.evidence)
        self.assertEqual(3, len(client.calls))

    def test_nxt_new_adjustment_discards_old_shared_basis_and_uses_existing_fresh_path(self):
        client = _MarketCharts()
        client.rows[("005930_NX", "ka10094")][0]["upd_stkpc_tp"] = "8"
        client.rows[("005930_NX", "ka10083")][0]["upd_stkpc_tp"] = "8"
        client.rows[("005930_NX", "ka10081")] = [{"dt": "20261020", "high_pric": "200", "upd_stkpc_tp": "8", "upd_rt": "-80"}]
        target = self.load(client, self.cache(1200))
        self.assertEqual((200, "20261020"), (target.price, target.occurred_on))
        self.assertNotIn(1200, [item.high_price for item in target.evidence])
        self.assertEqual(9, len(client.calls))
        self.assertEqual(["005930", "005930_NX", "005930", "005930_NX"],
            [body["stk_cd"] for api, _, body, _, _ in client.calls if api == "ka10094"])
        self.assertTrue(all(body["base_dt"] == "20261020" for _, _, body, _, _ in client.calls))

    def test_optional_nxt_failures_keep_existing_fresh_and_cached_partial_fallback(self):
        for api in ("ka10094", "ka10083", "ka10081"):
            with self.subTest(api=api):
                client = _MarketCharts()
                if api == "ka10081":
                    client.rows[("005930_NX", "ka10083")][0]["upd_stkpc_tp"] = "8"
                client.failures[("005930_NX", api)] = OSError("controlled optional NXT failure")
                cached = self.load(client, self.cache())
                fresh = self.load(client)
                self.assertEqual(100, cached.price)
                self.assertEqual(100, fresh.price)
                self.assertEqual((HistoricalHighEvidence("month", "20260901", 100),), cached.evidence)

    def test_krx_error_is_not_swallowed_as_optional_market_failure(self):
        client = _MarketCharts()
        failure = OSError("required KRX failed")
        client.failures[("005930", "ka10094")] = failure
        with self.assertRaises(OSError) as caught:
            self.load(client, self.cache())
        self.assertIs(failure, caught.exception)
        self.assertEqual(1, len(client.calls))

    def test_250_day_auxiliary_survives_new_market_checks_and_adjustment_refinement(self):
        client = _MarketCharts(nxt=150)
        target = self.load(client, self.cache(), high_250=200)
        self.assertEqual((200, "20261020"), (target.price, target.occurred_on))
        self.assertEqual(2, len(client.calls))
        client = _MarketCharts(nxt=150)
        client.rows[("005930", "ka10094")][0]["upd_stkpc_tp"] = "8"
        client.rows[("005930", "ka10083")][0]["upd_stkpc_tp"] = "8"
        client.rows[("005930", "ka10081")] = [{"dt": "20261001", "high_pric": "180", "upd_stkpc_tp": "8"}]
        target = self.load(client, self.cache(), high_250=200)
        self.assertEqual((200, "20261020"), (target.price, target.occurred_on))
        self.assertEqual(HistoricalHighEvidence("250day", "20261020", 200), target.evidence[-1])

    def test_nas_authoritative_target_still_bypasses_cache_auxiliary_and_chart_requests(self):
        client = _MarketCharts()
        source = {"price": 900, "first_year": 2020, "last_year": 2026, "occurred_on": "20201001",
                  "evidence": [{"period": "month", "trade_date": "20201001", "high_price": 900}]}
        client.load_stored_historical_high = lambda code: source
        def forbidden(_):
            raise AssertionError("authoritative NAS result touched local cache/auxiliary")
        target = HistoricalHighService(client, include_nxt=True, cache_loader=forbidden, high_250_loader=forbidden).load("005930", as_of=self.DAY)
        self.assertEqual((900, "20201001"), (target.price, target.occurred_on))
        self.assertEqual((), tuple(client.calls))
