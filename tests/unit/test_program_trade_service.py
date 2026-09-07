from __future__ import annotations

import unittest
from datetime import date

from kiwoom_monitor.application.program_trade_service import ProgramTradeService
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_program_trade_ticks


class _Client:
    def __init__(self) -> None:
        self.calls = []

    def request(self, api_id, path, body):
        self.calls.append((api_id, path, body))
        return {"stk_tm_prm_trde_trnsn": [{
            "tm": "101530", "prm_netprps_amt": "1,234", "prm_netprps_amt_irds": "20",
            "prm_netprps_qty": "5,678", "prm_netprps_qty_irds": "30", "stex_tp": "통합",
        }]}


class ProgramTradeTests(unittest.TestCase):
    def test_reads_realtime_0w(self):
        ticks = parse_program_trade_ticks({"trnm": "REAL", "data": [{
            "type": "0w", "item": "005930_AL", "values": {
                "20": "101530", "210": "5678", "211": "30", "212": "1234", "213": "20",
            },
        }]})
        self.assertEqual(1, len(ticks))
        self.assertEqual("005930", ticks[0].code)
        self.assertEqual(1234, ticks[0].net_buy_amount_million_won)
        self.assertEqual(30, ticks[0].net_buy_quantity_change)

    def test_after_close_request_uses_ka90008_once_per_code(self):
        client = _Client()
        rows = ProgramTradeService(client).load_day("005930", date(2026, 8, 31))
        self.assertEqual(1, len(rows))
        self.assertEqual("ka90008", client.calls[0][0])
        self.assertEqual("005930_AL", client.calls[0][2]["stk_cd"])
        self.assertEqual(1234, rows[0]["net_buy_amount_million_won"])


if __name__ == "__main__":
    unittest.main()
