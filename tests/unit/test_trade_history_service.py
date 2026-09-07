from __future__ import annotations

import unittest
from datetime import date

from kiwoom_monitor.application.trade_history_service import TradeHistoryService


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((cont_yn, next_key))
        if cont_yn == "N":
            return {"acnt_ord_cntr_prps_dtl": [{
                "ord_no": "50", "stk_cd": "A005930", "stk_nm": "삼성전자",
                "io_tp_nm": "+현금매수", "ord_tm": "10:15:03", "cntr_qty": "0000000010",
                "cntr_uv": "0000070000", "trde_tp": "시장가", "dmst_stex_tp": "KRX",
            }]}, True, "next"
        return {"acnt_ord_cntr_prps_dtl": [{
            "ord_no": "51", "stk_cd": "A005930", "stk_nm": "삼성전자",
            "io_tp_nm": "-현금매도", "ord_tm": "14:25:01", "cntr_qty": "0000000005",
            "cntr_uv": "0000072000", "trde_tp": "보통", "dmst_stex_tp": "NXT",
        }]}, False, ""


class TradeHistoryServiceTests(unittest.TestCase):
    def test_parses_buy_sell_and_continuation(self) -> None:
        client = FakeClient()
        fills = TradeHistoryService(client).load_day(date(2026, 8, 28))
        self.assertEqual(2, len(fills))
        self.assertEqual(("매도", "매수"), tuple(fill.side for fill in fills))
        self.assertEqual("005930", fills[0].stock_code)
        self.assertEqual("14:25:01", fills[0].filled_at.strftime("%H:%M:%S"))
        self.assertEqual([("N", ""), ("Y", "next")], client.calls)


if __name__ == "__main__":
    unittest.main()
