from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.investor_flow_service import InvestorFlowService


class FakeClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        self.calls.append((api_id, path, body))
        return self.response


class InvestorFlowServiceTests(unittest.TestCase):
    def test_marks_same_day_nonzero_values_as_pending_before_close(self) -> None:
        client = FakeClient({"stk_orgn_trde_trnsn": [{
            "dt": "20260831", "for_daly_nettrde_qty": "+1,200",
            "orgn_daly_nettrde_qty": "-300", "for_dt_acc": "1500", "orgn_dt_acc": "200",
        }]})
        value = InvestorFlowService(client).load("005930", datetime(2026, 8, 31, 10, 20))
        self.assertIsNone(value["foreign_net_buy_quantity"])
        self.assertIsNone(value["institution_net_buy_quantity"])
        self.assertEqual("pending_close", value["status"])
        self.assertFalse(value["available"])
        self.assertEqual("ka10045", client.calls[0][0])
        self.assertEqual("005930_AL", client.calls[0][2]["stk_cd"])
        self.assertEqual("20260831", client.calls[0][2]["strt_dt"])

    def test_marks_same_day_zero_values_as_pending_before_close(self) -> None:
        client = FakeClient({"stk_orgn_trde_trnsn": [{
            "dt": "20260901", "for_daly_nettrde_qty": "0", "orgn_daly_nettrde_qty": "0",
            "for_dt_acc": "-539568", "orgn_dt_acc": "-69508",
        }]})
        value = InvestorFlowService(client).load("025980", datetime(2026, 9, 1, 14, 19))
        self.assertEqual("pending_close", value["status"])
        self.assertFalse(value["available"])
        self.assertIsNone(value["foreign_net_buy_quantity"])
        self.assertIsNone(value["institution_net_buy_quantity"])

    def test_keeps_confirmed_zero_values_after_close(self) -> None:
        client = FakeClient({"stk_orgn_trde_trnsn": [{
            "dt": "20260901", "for_daly_nettrde_qty": "0", "orgn_daly_nettrde_qty": "0",
        }]})
        value = InvestorFlowService(client).load("025980", datetime(2026, 9, 1, 20, 5))
        self.assertEqual("confirmed", value["status"])
        self.assertTrue(value["available"])
        self.assertEqual(0, value["foreign_net_buy_quantity"])

    def test_loads_confirmed_nonzero_values_after_close(self) -> None:
        client = FakeClient({"stk_orgn_trde_trnsn": [{
            "dt": "20260831", "for_daly_nettrde_qty": "+1,200",
            "orgn_daly_nettrde_qty": "-300",
        }]})
        value = InvestorFlowService(client).load("005930", datetime(2026, 8, 31, 20, 5))
        self.assertEqual(1_200, value["foreign_net_buy_quantity"])
        self.assertEqual(-300, value["institution_net_buy_quantity"])
        self.assertEqual("confirmed", value["status"])

    def test_retries_krx_when_sor_has_no_same_day_row(self) -> None:
        class SorThenKrxClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                self.calls.append(body)
                if str(body["stk_cd"]).endswith("_AL"):
                    return {"stk_orgn_trde_trnsn": [{"dt": "", "for_daly_nettrde_qty": "", "orgn_daly_nettrde_qty": ""}]}
                return {"stk_orgn_trde_trnsn": [{
                    "dt": "20260904", "for_daly_nettrde_qty": "120", "orgn_daly_nettrde_qty": "-30",
                }]}

        client = SorThenKrxClient()
        value = InvestorFlowService(client).load("386380", datetime(2026, 9, 4, 20, 5))

        self.assertEqual(["386380_AL", "386380"], [call["stk_cd"] for call in client.calls])
        self.assertEqual("KRX", value["market_basis"])
        self.assertEqual(120, value["foreign_net_buy_quantity"])
        self.assertEqual(-30, value["institution_net_buy_quantity"])

    def test_does_not_confirm_a_row_from_another_date(self) -> None:
        client = FakeClient({"stk_orgn_trde_trnsn": [{
            "dt": "20260903", "for_daly_nettrde_qty": "120", "orgn_daly_nettrde_qty": "-30",
        }]})
        value = InvestorFlowService(client).load("386380", datetime(2026, 9, 4, 20, 5))
        self.assertEqual("unavailable", value["status"])
        self.assertFalse(value["available"])


if __name__ == "__main__":
    unittest.main()
