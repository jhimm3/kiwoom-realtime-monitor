from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_market_index_ticks, parse_order_executions, parse_trade_ticks,
)


class RealtimeTests(unittest.TestCase):
    def test_reads_0b_market_cap_in_eok(self) -> None:
        ticks = parse_trade_ticks(
            {
                "trnm": "REAL",
                "data": [{"type": "0B", "item": "005930", "values": {"10": "+100", "311": "1,234,567"}}],
            }
        )

        self.assertEqual(1, len(ticks))
        self.assertEqual(1_234_567, ticks[0].market_cap_eok)

    def test_reads_execution_strength_and_session_type(self) -> None:
        ticks = parse_trade_ticks({
            "trnm": "REAL",
            "data": [{"type": "0B", "item": "005930", "values": {"228": "103.25", "290": "2", "15": "-82"}}],
        })
        self.assertEqual(103.25, ticks[0].execution_strength)
        self.assertEqual("2", ticks[0].session_type)
        self.assertEqual(-82, ticks[0].trade_volume)

    def test_preserves_market_while_normalizing_nxt_code(self) -> None:
        ticks = parse_trade_ticks(
            {
                "trnm": "REAL",
                "data": [{"type": "0B", "item": "005930_NX", "values": {"10": "+100", "14": "123"}}],
            }
        )

        self.assertEqual("005930", ticks[0].code)
        self.assertEqual("NXT", ticks[0].market)

    def test_reads_only_completed_order_execution(self) -> None:
        message = {
            "trnm": "REAL",
            "data": [{"type": "00", "item": "005930", "values": {
                "9203": "18", "909": "7", "9001": "A005930", "302": "삼성전자",
                "913": "체결", "905": "+매수", "908": "094022", "910": "+60700",
                "911": "2", "2135": "KRX",
            }}],
        }
        values = parse_order_executions(message)
        self.assertEqual(1, len(values))
        self.assertEqual(("005930", "매수", 60_700, 2), (values[0].code, values[0].side, values[0].price, values[0].quantity))

    def test_ignores_order_receipt_without_fill(self) -> None:
        message = {"trnm": "REAL", "data": [{"type": "00", "values": {"913": "접수", "910": "", "911": ""}}]}
        self.assertEqual((), parse_order_executions(message))

    def test_reads_realtime_market_index_and_breadth(self) -> None:
        values = parse_market_index_ticks({"trnm": "REAL", "data": [
            {"type": "0J", "item": "001", "values": {
                "20": "101530", "10": "+2812.34", "12": "+1.25", "14": "1,234,500",
            }},
            {"type": "0U", "item": "101", "values": {
                "10": "902.11", "12": "-0.35", "252": "720", "255": "810", "253": "33",
            }},
        ]})
        self.assertEqual(2, len(values))
        self.assertEqual(("kospi", 2812.34, 1.25, 1_234_500), (
            values[0].market, values[0].index_value, values[0].change_rate,
            values[0].cumulative_trade_value_million_won,
        ))
        self.assertEqual(("kosdaq", 720, 810, 33), (
            values[1].market, values[1].advancing_count,
            values[1].declining_count, values[1].flat_count,
        ))


if __name__ == "__main__":
    unittest.main()
