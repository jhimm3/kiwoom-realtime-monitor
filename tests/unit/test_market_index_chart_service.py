from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.market_index_chart_service import MarketIndexChartService


class Client:
    def request(self, api_id, path, body):
        if api_id == "ka20006":
            return {"inds_dt_pole_qry": [{
                "dt": "20260828", "open_pric": "259900", "high_pric": "260120",
                "low_pric": "259850", "cur_prc": "260050", "trde_qty": "123", "trde_prica": "45600",
            }]}
        return {"inds_min_pole_qry": [{
            "cntr_tm": "20260828152900", "open_pric": "+259900",
            "high_pric": "+260120", "low_pric": "-259850", "cur_prc": "+260050",
        }]}


class MarketIndexChartServiceTests(unittest.TestCase):
    def test_reads_sector_minute_values_as_index_points(self):
        rows = MarketIndexChartService(Client()).load("kospi", datetime(2026, 8, 28))
        self.assertEqual((2599.0, 2601.2, 2598.5, 2600.5, None), next(iter(rows.values())))

    def test_reads_sector_daily_trade_value(self):
        rows = MarketIndexChartService(Client()).load_daily("kosdaq", datetime(2026, 8, 28))
        self.assertEqual((2599.0, 2601.2, 2598.5, 2600.5, 123, 456.0), rows[0][1:7])


if __name__ == "__main__": unittest.main()
