from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_trade_ticks


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


if __name__ == "__main__":
    unittest.main()
