from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_trade_ticks


class RealtimeParserTests(unittest.TestCase):
    def test_extracts_display_values_from_0b_message(self) -> None:
        ticks = parse_trade_ticks(
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "item": "005930",
                        "values": {"10": "-71000", "12": "+1.23", "13": "1,234", "14": "987654", "15": "12", "17": "72000", "20": "101530"},
                    }
                ],
            }
        )

        self.assertEqual(1, len(ticks))
        self.assertEqual("005930", ticks[0].code)
        self.assertEqual(71000, ticks[0].current_price)
        self.assertEqual(1234, ticks[0].cumulative_volume)
        self.assertEqual(12, ticks[0].trade_volume)
        self.assertEqual(72000, ticks[0].high_price)
        self.assertEqual(1.23, ticks[0].change_rate)

    def test_normalizes_nxt_code_to_base_stock_code(self) -> None:
        ticks = parse_trade_ticks({"trnm": "REAL", "data": [{"type": "0B", "item": "005930_NX", "values": {"10": "70000"}}]})
        self.assertEqual("005930", ticks[0].code)

    def test_ignores_non_trade_messages(self) -> None:
        self.assertEqual((), parse_trade_ticks({"trnm": "PING"}))
