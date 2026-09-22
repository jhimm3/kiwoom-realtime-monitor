from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import (
    CentralServerUnavailable,
    RemoteKiwoomRestClient,
)


class Response:
    def __init__(self, document: dict[str, object]) -> None:
        self._body = json.dumps(document).encode("utf-8")

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self) -> bytes: return self._body


class RemoteKiwoomRestClientTests(unittest.TestCase):
    def test_top20_ranking_query_uses_kst_trading_date(self) -> None:
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            return Response({"snapshots": []})

        result = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_ranking("5")

        self.assertEqual({"item_inq_rank": []}, result)
        self.assertRegex(captured["url"], r"subject=\d{4}-\d{2}-\d{2}&limit=1$")

    def test_loads_last_nas_0b_market_caps_without_kiwoom_query(self) -> None:
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            return Response({"market_caps": [{
                "code": "005930", "market_cap_eok": 4_321_000,
                "observed_at": "2026-09-21T06:20:00+00:00",
            }]})

        values = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_market_caps(("005930", "000660"))

        self.assertIn("/api/v1/market/latest-market-caps?", captured["url"])
        self.assertNotIn("/api/v1/kiwoom/query", captured["url"])
        self.assertEqual(4_321_000, values["005930"]["market_cap_eok"])

    def test_loads_nas_top20_rows_and_statistics_without_kiwoom_query(self) -> None:
        urls = []

        def opener(request, **_kwargs):
            urls.append(request.full_url)
            if "top20-statistics" in request.full_url:
                return Response({"hourly": [], "comparisons": [{"trade_date": "2026-09-08"}]})
            return Response({"snapshots": [
                {"payload": {"minute": "2026-09-08T09:01", "capture_state": "realtime_complete"}},
                {"payload": {"minute": "2026-09-08T09:00", "capture_state": "realtime_complete"}},
            ]})

        client = RemoteKiwoomRestClient("https://nas.example.test", "secret", opener=opener)
        rows = client.load_stored_top20_index("2026-09-08")
        statistics = client.load_stored_top20_statistics("2026-09-01", "2026-09-08")

        self.assertEqual("2026-09-08T09:00", rows[0]["minute"])
        self.assertEqual("2026-09-08", statistics["comparisons"][0]["trade_date"])
        self.assertTrue(all("/api/v1/kiwoom/query" not in url for url in urls))

    def test_account_discovery_preserves_profile_display_label(self) -> None:
        def opener(_request, **_kwargs):
            return Response({"accounts": [{
                "broker": "kiwoom",
                "environment": "mock",
                "account_ref": "11111111-1111-1111-1111-111111111111",
                "credential_profile_id": "mock-profile",
                "binding_revision": 2,
                "display_label": "연습 계좌",
            }]})

        contexts = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_account_contexts()

        self.assertEqual("연습 계좌", contexts[0].display_label)

    def test_gateway_failure_is_reported_as_central_unavailable_for_failover(self) -> None:
        def opener(request, **_kwargs):
            raise HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(b""))

        client = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        )
        with self.assertRaises(CentralServerUnavailable):
            client.load_stored_ranking("5")
        with self.assertRaises(CentralServerUnavailable):
            client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})

    def test_authentication_failure_does_not_allow_local_failover(self) -> None:
        def opener(request, **_kwargs):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

        client = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        )
        with self.assertRaisesRegex(Exception, "HTTP 401") as raised:
            client.load_stored_ranking("5")
        self.assertNotIsInstance(raised.exception, CentralServerUnavailable)

    def test_loads_recent_stored_minute_bars_without_kiwoom_query(self) -> None:
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            captured["method"] = request.method
            return Response({"bars": [{
                "trading_date": "2026-09-11", "minute": "15:20",
            }]})

        rows = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_recent_minute_bars("005930", "2026-09-14", "KRX", 2)

        self.assertEqual("GET", captured["method"])
        self.assertIn("/api/v1/market/recent-minute-bars?", captured["url"])
        self.assertNotIn("/api/v1/kiwoom/query", captured["url"])
        self.assertEqual("2026-09-11", rows[0]["trading_date"])

    def test_loads_stored_minute_bars_with_get_without_kiwoom_query(self) -> None:
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            captured["method"] = request.method
            return Response({"bars": [{"minute": "10:01"}]})

        rows = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_minute_bars("005930", "2026-09-14", "KRX")

        self.assertEqual("GET", captured["method"])
        self.assertIn("/api/v1/market/minute-bars?", captured["url"])
        self.assertNotIn("/api/v1/kiwoom/query", captured["url"])
        self.assertEqual("10:01", rows[0]["minute"])

    def test_loads_stored_minute_bar_coverage(self) -> None:
        def opener(_request, **_kwargs):
            return Response({"bars": [{"minute": "19:59"}], "coverage": {"complete": True}})

        rows, complete = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_minute_bars_with_coverage("001210", "2026-09-14", "COMBINED")

        self.assertEqual("19:59", rows[0]["minute"])
        self.assertTrue(complete)

    def test_matches_local_client_request_contract(self) -> None:
        captured = {}

        def opener(request, **kwargs):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = kwargs["timeout"]
            return Response({"payload": {"return_code": 0}, "has_next": True, "next_key": "page2"})

        client = RemoteKiwoomRestClient("https://nas.example.test/", "secret", opener=opener)
        result = client.request_with_continuation("ka10080", "/api/dostk/chart", {"stk_cd": "005930"})
        self.assertEqual(({"return_code": 0}, True, "page2"), result)
        self.assertEqual("https://nas.example.test/api/v1/kiwoom/query", captured["url"])
        self.assertEqual("Bearer secret", captured["authorization"])
        self.assertEqual("ka10080", captured["body"]["api_id"])

    def test_loads_stored_candidate_flows_without_kiwoom_query(self) -> None:
        urls = []

        def opener(request, **_kwargs):
            urls.append(request.full_url)
            if "investor_flow" in request.full_url:
                return Response({"snapshots": [{
                    "snapshot_key": "20260914:SOR",
                    "payload": {"rows": [{"dt": "20260914"}]},
                }]})
            return Response({"snapshots": [{
                "snapshot_key": "20260914:REALTIME:100100",
                "payload": {"rows": [{"trade_time": "100100"}]},
            }]})

        client = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        )
        investor = client.load_stored_investor_flow("005930", "20260914")
        program = client.load_stored_program_flow("005930", "20260914")

        self.assertEqual("20260914", investor["stk_orgn_trde_trnsn"][0]["dt"])
        self.assertEqual("100100", program["stk_tm_prm_trde_trnsn"][0]["trade_time"])
        self.assertTrue(all("/api/v1/market/snapshots/" in url for url in urls))
        self.assertTrue(all("/api/v1/kiwoom/query" not in url for url in urls))

    def test_loads_stored_new_high_sets(self) -> None:
        def opener(request, **_kwargs):
            period = "5" if "subject=5" in request.full_url else "20"
            return Response({"snapshots": [{
                "snapshot_key": "latest",
                "payload": {"items": [{"stk_cd": f"0000{period}"}]},
            }]})

        values = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_new_highs((5, 20))

        self.assertEqual({"00005"}, values[5])
        self.assertEqual({"000020"}, values[20])

    def test_loads_stored_historical_high_document(self) -> None:
        def opener(request, **_kwargs):
            return Response({"documents": [{"document": {"target": {
                "price": 90000, "first_year": 2020, "last_year": 2026,
                "occurred_on": "20260914", "evidence": [],
            }}}]})

        value = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).load_stored_historical_high("005930")

        self.assertEqual(90000, value["price"])

    def test_loads_stored_stock_documents_and_market_index(self) -> None:
        def opener(request, **_kwargs):
            if "/content/" in request.full_url:
                payload = {"nxtEnable": "Y"} if "nxt_eligibility" in request.full_url else {"mac": "1000"}
                return Response({"documents": [{"document": {"payload": payload}}]})
            return Response({"snapshots": [{"payload": {"minutes": [], "daily": []}}]})

        client = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        )
        self.assertEqual("1000", client.load_stored_fundamentals("005930")["mac"])
        self.assertEqual("Y", client.load_stored_nxt_eligibility("005930")["nxtEnable"])
        self.assertEqual([], client.load_stored_market_index("kospi", "20260914")["minutes"])

    def test_account_query_uses_v2_context_and_server_cursor(self) -> None:
        requests = []

        def opener(request, **kwargs):
            body = json.loads(request.data.decode("utf-8"))
            requests.append((request.full_url, body))
            page = len(requests) - 1
            return Response({
                "payload": {"page": page}, "batch_id": "batch-1",
                "page_index": page, "complete": page == 1,
                "next_key": "cursor-1" if page == 0 else "",
                "context": {
                    "broker": "kiwoom", "environment": "real",
                    "account_ref": "11111111-1111-1111-1111-111111111111",
                    "credential_profile_id": "nas-real-default", "binding_revision": 2,
                },
            })

        result = RemoteKiwoomRestClient(
            "https://nas.example.test", "secret", opener=opener,
        ).query_account_pages("kt00007", "/api/dostk/acnt", {"ord_dt": "20260913"})
        self.assertEqual(({"page": 0}, {"page": 1}), result.pages)
        self.assertEqual("nas", result.context.transport)
        self.assertEqual("cursor-1", requests[1][1]["next_key"])
        self.assertEqual("https://nas.example.test/api/v2/kiwoom/account-query", requests[0][0])


if __name__ == "__main__":
    unittest.main()
