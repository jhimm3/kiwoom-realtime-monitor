from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from kiwoom_monitor.application.ranking_service import RankingService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        if api_id == "ka00198":
            return {
                "item_inq_rank": [
                    {"bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자", "base_comp_chgr": "1.25", "past_curr_prc": "+72000"},
                    {"bigd_rank": "2", "stk_cd": "000660", "stk_nm": "SK하이닉스", "base_comp_chgr": "-0.50", "cur_prc": "-260000"},
                ]
            }
        raise AssertionError(f"unexpected API request: {api_id}")


class RankingServiceTests(unittest.TestCase):
    def test_direct_fallback_retries_stale_snapshot_until_current(self) -> None:
        class DirectRankingClient(FakeClient):
            def __init__(self) -> None:
                self.requests = 0

            def server_now(self):
                return datetime(2026, 9, 21, 15, 53, 30, 250000)

            def request(self, api_id, path, body):
                self.requests += 1
                clock = "155330" if self.requests >= 5 else "155300"
                return {"item_inq_rank": [{
                    "dt": "20260921", "tm": clock,
                    "bigd_rank": str(index + 1),
                    "stk_cd": f"{index + 1:06d}", "stk_nm": f"종목{index + 1}",
                    "base_comp_chgr": "1.25", "cur_prc": "72000",
                } for index in range(20)]}

        client = DirectRankingClient()
        with patch("kiwoom_monitor.application.ranking_service.time.sleep") as sleeper:
            stocks = RankingService(client, query_type="5").load_top_stocks()

        self.assertEqual(20, len(stocks))
        self.assertEqual(5, client.requests)
        self.assertEqual([0.25, 0.25, 0.5, 0.5], [call.args[0] for call in sleeper.call_args_list])

    def test_nas_stale_snapshot_poll_uses_bounded_adaptive_delays(self) -> None:
        class StoredRankingClient(FakeClient):
            def __init__(self) -> None:
                self.loads = 0

            def server_now(self):
                return datetime(2026, 9, 15, 1, 5, 30, 250000)

            def load_stored_ranking(self, query_type="5"):
                self.loads += 1
                clock = "010530" if self.loads >= 5 else "010500"
                return {"item_inq_rank": [{
                    "dt": "20260915", "tm": clock,
                    "bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자",
                    "base_comp_chgr": "1.25", "cur_prc": "72000",
                }]}

        client = StoredRankingClient()
        with patch("kiwoom_monitor.application.ranking_service.time.sleep") as sleeper:
            stocks = RankingService(client, query_type="5").load_top_stocks()

        self.assertEqual("005930", stocks[0].code)
        self.assertEqual(5, client.loads)
        self.assertEqual([0.25, 0.25, 0.5, 0.5], [call.args[0] for call in sleeper.call_args_list])

    def test_stale_retry_delay_caps_at_three_quarters_of_a_second(self) -> None:
        self.assertEqual(
            [0.25, 0.25, 0.5, 0.5, 0.75, 0.75],
            [RankingService._stale_snapshot_retry_delay(index) for index in range(6)],
        )

    def test_nas_stale_snapshot_is_not_applied_after_retry_limit(self) -> None:
        class StoredRankingClient(FakeClient):
            def server_now(self):
                return datetime(2026, 9, 15, 1, 5, 30, 250000)

            def load_stored_ranking(self, query_type="5"):
                return {"item_inq_rank": [{
                    "dt": "20260915", "tm": "010500",
                    "bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자",
                    "base_comp_chgr": "1.25", "cur_prc": "72000",
                }]}

        with patch("kiwoom_monitor.application.ranking_service.time.sleep"):
            stocks = RankingService(StoredRankingClient(), query_type="5").load_top_stocks()

        self.assertEqual((), stocks)

    def test_nas_partial_snapshot_poll_waits_until_complete_snapshot(self) -> None:
        class StoredRankingClient(FakeClient):
            def __init__(self) -> None:
                self.loads = 0

            def server_now(self):
                return datetime(2026, 9, 15, 1, 5, 32)

            def load_stored_ranking(self, query_type="5"):
                self.loads += 1
                valid_count = 20 if self.loads >= 5 else 3
                rows = []
                for index in range(20):
                    valid = index < valid_count
                    rows.append({
                        "dt": "20260915", "tm": "010530",
                        "bigd_rank": str(index + 1),
                        "stk_cd": f"{index + 1:06d}" if valid else "",
                        "stk_nm": f"종목{index + 1}" if valid else "",
                        "base_comp_chgr": "1.25", "cur_prc": "72000",
                    })
                return {"item_inq_rank": rows}

            def request(self, api_id, path, body):
                raise AssertionError("NAS 재확인 중에는 키움 query API를 호출하면 안 됩니다.")

        client = StoredRankingClient()
        with patch("kiwoom_monitor.application.ranking_service.time.sleep") as sleeper:
            stocks = RankingService(client, query_type="5").load_top_stocks()

        self.assertEqual(20, len(stocks))
        self.assertEqual(5, client.loads)
        self.assertEqual([0.25, 0.25, 0.5, 0.5], [call.args[0] for call in sleeper.call_args_list])

    def test_default_ranking_uses_nas_snapshot_without_ka00198(self) -> None:
        class StoredRankingClient(FakeClient):
            def __init__(self) -> None:
                self.requests = 0

            def load_stored_ranking(self, query_type="5"):
                return {"item_inq_rank": [{
                    "bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자",
                    "base_comp_chgr": "1.25", "cur_prc": "72000",
                }]}

            def request(self, api_id, path, body):
                self.requests += 1
                return super().request(api_id, path, body)

        client = StoredRankingClient()
        stocks = RankingService(client, query_type="5").load_top_stocks()

        self.assertEqual(0, client.requests)
        self.assertEqual("005930", stocks[0].code)

    def test_nondefault_ranking_uses_nas_snapshot_without_ka00198(self) -> None:
        class StoredRankingClient(FakeClient):
            def __init__(self): self.requests = 0
            def load_stored_ranking(self, query_type="5"):
                self.query_type = query_type
                return {"item_inq_rank": [{
                    "bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자",
                    "base_comp_chgr": "1.25", "cur_prc": "72000",
                }]}
            def request(self, api_id, path, body):
                self.requests += 1
                return super().request(api_id, path, body)

        client = StoredRankingClient()
        RankingService(client, query_type="2").load_top_stocks()

        self.assertEqual("2", client.query_type)
        self.assertEqual(0, client.requests)

    def test_rankings_do_not_request_unused_new_high_list(self) -> None:
        service = RankingService(FakeClient())
        stocks = service.load_top_stocks()

        self.assertEqual(2, len(stocks))
        self.assertEqual(frozenset(), stocks[0].new_high_periods)
        self.assertEqual(72_000, stocks[0].current_price)
        self.assertEqual(260_000, stocks[1].current_price)
