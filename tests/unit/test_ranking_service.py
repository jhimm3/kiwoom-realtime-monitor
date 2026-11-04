from __future__ import annotations

import unittest
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.application.ranking_service import RankingService
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository


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
    def _native_ranking_fixture(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "query-role.sqlite3"
        Database(path).initialize_query_role(role="query")
        original_at = "2020-01-02T10:02:03+09:00"
        with closing(sqlite3.connect(path)) as connection:
            connection.executemany("INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,?)", (
                ("030200", "케이티", "KOSPI", original_at),
                ("005930", "원래종목명", "KOSPI", original_at),
            ))
            # Independent persisted user inputs, including one literal also present
            # in the legacy candidate list. They are current lookup data, not PIT proof.
            connection.executemany("INSERT INTO stock_aliases(alias,stock_code) VALUES(?,?)", (
                ("KT", "030200"), ("개인조회이름", "030200"),
            ))
            connection.commit()
        now = datetime(2031, 1, 2, 10, tzinfo=timezone(timedelta(hours=9)))
        clock_calls = []
        def clock():
            clock_calls.append(now)
            return now
        records = [{"bigd_rank": str(index + 1), "stk_cd": code, "stk_nm": name,
                    "base_comp_chgr": "1", "cur_prc": "100", "dt": "20310102", "tm": "100000"}
                   for index, (code, name) in enumerate([("005930", "삼성전자")] +
                       [(f"200{index:03d}", f"순위종목{index}") for index in range(1, 20)])]
        def forbidden_request(*args, **kwargs):
            raise AssertionError("stored ranking must not request direct transport")
        client = SimpleNamespace(load_stored_ranking=lambda query_type: {"item_inq_rank": records},
                                 server_now=lambda: now.replace(tzinfo=None), request=forbidden_request)
        return path, StockRepository(path, now_provider=clock), client, clock_calls, now

    def test_real_ranking_cache_preserves_aliases_and_records_only_observed_rename(self):
        path, repository, client, clock_calls, now = self._native_ranking_fixture()
        empty = SimpleNamespace(load_stored_ranking=lambda query_type: {"item_inq_rank": []},
                                server_now=lambda: now.replace(tzinfo=None))
        with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect",
                   side_effect=AssertionError("empty ranking opened a database")):
            self.assertEqual((), RankingService(empty, stocks=repository).load_top_stocks())
        self.assertEqual([], clock_calls)
        native_connect, opened, statements = sqlite3.connect, [], []
        def connect(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            opened.append(connection)
            return connection
        with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect", side_effect=connect):
            ranked = RankingService(client, stocks=repository).load_top_stocks()
        self.assertEqual(20, len(ranked))
        self.assertEqual([now], clock_calls)
        self.assertEqual(1, len(opened))
        self.assertEqual(1, sum(sql.startswith("BEGIN") for sql in statements))
        self.assertEqual(1, sum(sql == "COMMIT" for sql in statements))
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")
        with closing(native_connect(path)) as connection:
            self.assertEqual([("KT", "030200"), ("개인조회이름", "030200")], connection.execute(
                "SELECT alias,stock_code FROM stock_aliases ORDER BY alias").fetchall())
            self.assertEqual(("케이티", "KOSPI", "2020-01-02T10:02:03+09:00"), connection.execute(
                "SELECT name,market,updated_at FROM stocks WHERE code='030200'").fetchone())
            self.assertEqual(("삼성전자", "KOSPI", now.isoformat()), connection.execute(
                "SELECT name,market,updated_at FROM stocks WHERE code='005930'").fetchone())
            self.assertEqual(("순위종목19", "", now.isoformat()), connection.execute(
                "SELECT name,market,updated_at FROM stocks WHERE code='200019'").fetchone())
            self.assertEqual((21,), connection.execute("SELECT COUNT(*) FROM stocks").fetchone())
            self.assertEqual([("005930", "원래종목명", "삼성전자", "KRX", "pending", now.isoformat())], connection.execute(
                "SELECT stock_code,old_name,new_name,source,decision,changed_at FROM stock_name_history").fetchall())
        self.assertEqual("030200", repository.find_code_by_name("KT"))
        self.assertEqual({"KT": "030200", "개인조회이름": "030200", "원래종목명": None},
                         repository.find_codes_by_names(("KT", "개인조회이름", "원래종목명")))

    def test_real_ranking_cache_native_failure_rolls_back_closes_and_retries(self):
        path, repository, client, clock_calls, now = self._native_ranking_fixture()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TRIGGER fail_ranking BEFORE INSERT ON stocks WHEN NEW.code='200010' BEGIN SELECT RAISE(ABORT,'ranking cache rejected'); END")
            connection.commit()
        native_connect, opened, statements = sqlite3.connect, [], []
        def connect(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            opened.append(connection)
            return connection
        with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect", side_effect=connect):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "ranking cache rejected"):
                RankingService(client, stocks=repository).load_top_stocks()
        self.assertEqual([now], clock_calls)
        self.assertEqual(1, len(opened))
        self.assertEqual(0, sum(sql == "COMMIT" for sql in statements))
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")
        with closing(native_connect(path)) as connection:
            self.assertEqual([("005930", "원래종목명", "KOSPI", "2020-01-02T10:02:03+09:00"),
                              ("030200", "케이티", "KOSPI", "2020-01-02T10:02:03+09:00")], connection.execute(
                "SELECT code,name,market,updated_at FROM stocks ORDER BY code").fetchall())
            self.assertEqual([], connection.execute("SELECT * FROM stock_name_history").fetchall())
            self.assertEqual([("KT", "030200"), ("개인조회이름", "030200")], connection.execute(
                "SELECT alias,stock_code FROM stock_aliases ORDER BY alias").fetchall())
            connection.execute("DROP TRIGGER fail_ranking")
            connection.commit()
        reopened = StockRepository(path, now_provider=lambda: now)
        self.assertEqual(20, len(RankingService(client, stocks=reopened).load_top_stocks()))
        self.assertEqual({"KT": "030200", "개인조회이름": "030200"},
                         reopened.find_codes_by_names(("KT", "개인조회이름")))
        self.assertEqual((("005930", "원래종목명", "삼성전자", "KRX"),), reopened.pending_name_changes())

    def test_public_query_type_validator_reuses_setter_values_without_changing_legacy_constructor(self):
        service = RankingService(FakeClient(), query_type='invalid')
        self.assertEqual('5', service._query_type)
        for value in ('1','2','3','4','5'):
            self.assertIsNone(RankingService.validate_query_type(value))
            service.set_query_type(value)
            self.assertEqual(value, service._query_type)
        for value in ('invalid','5 ',None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '순위 조회 기준'):
                    RankingService.validate_query_type(value)
                with self.assertRaisesRegex(ValueError, '순위 조회 기준'):
                    service.set_query_type(value)
                self.assertEqual('5', service._query_type)

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
