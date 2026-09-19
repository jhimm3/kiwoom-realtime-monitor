from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from kiwoom_monitor.domain.order_contract import OrderState
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import (
    KiwoomMockAccountReader,
    merge_order_snapshots,
    parse_account_snapshot,
    parse_filled_orders,
    parse_unfilled_orders,
    snapshot_from_order_execution,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution


NOW = datetime(2026, 9, 14, 1, 2, 3, tzinfo=timezone.utc)


class _Broker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object], str, str]] = []

    async def request(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((api_id, path, body, cont_yn, next_key))
        if api_id == "ka10075" and cont_yn == "N":
            return SimpleNamespace(payload={"oso": [{
                "ord_no": "0001", "stk_cd": "A005930", "ord_stt": "체결",
                "ord_qty": "+3", "ord_pric": "70,000", "oso_qty": "2",
                "io_tp_nm": "+매수", "cntr_no": "fill-1",
                "unit_cntr_pric": "70000", "unit_cntr_qty": "1", "tm": "100203",
            }]}, has_next=True, next_key="page-2")
        if api_id == "ka10075":
            return SimpleNamespace(payload={"oso": [{
                "ord_no": "0002", "stk_cd": "000660", "ord_stt": "접수",
                "ord_qty": "2", "ord_pric": "120000", "oso_qty": "2",
                "io_tp_nm": "-매도",
            }]}, has_next=False, next_key="")
        if api_id == "ka10076":
            return SimpleNamespace(payload={"cntr": [{
                "ord_no": "0001", "stk_cd": "005930", "ord_stt": "체결",
                "ord_qty": "3", "cntr_qty": "1", "oso_qty": "2",
            }]}, has_next=False, next_key="")
        if api_id == "kt00018":
            return SimpleNamespace(payload={"acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A005930", "rmnd_qty": "10",
            }]}, has_next=False, next_key="")
        if api_id == "kt00001":
            return SimpleNamespace(payload={"ord_alow_amt": "500000"}, has_next=False, next_key="")
        raise AssertionError(api_id)


class MockAccountTests(unittest.TestCase):
    def test_reader_uses_four_read_only_queries_and_continuation(self) -> None:
        async def scenario() -> None:
            broker = _Broker()
            recovery = await KiwoomMockAccountReader(
                broker, environment="mock", account_ref="mock-account",
                now_provider=lambda: NOW,
            ).read()

            self.assertEqual(["0001", "0002"], [item.broker_order_id for item in recovery.orders])
            self.assertEqual(OrderState.PARTIALLY_FILLED, recovery.orders[0].state)
            self.assertEqual(("fill-1",), tuple(fill.execution_id for fill in recovery.orders[0].fills))
            self.assertEqual({"005930": 10}, recovery.account.positions)
            self.assertEqual(140_000, recovery.account.reserved_open_buy_won)
            self.assertEqual(640_000, recovery.account.available_cash_won)
            self.assertEqual(
                ["ka10075", "ka10075", "ka10076", "kt00018", "kt00001"],
                [call[0] for call in broker.calls],
            )
            self.assertEqual(("Y", "page-2"), broker.calls[1][3:5])

        asyncio.run(scenario())

    def test_real_environment_is_rejected_before_query(self) -> None:
        with self.assertRaisesRegex(ValueError, "mock credentials"):
            KiwoomMockAccountReader(_Broker(), environment="real", account_ref="account")

    def test_filled_query_keeps_aggregate_without_fabricating_execution_id(self) -> None:
        snapshots = parse_filled_orders({"cntr": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "체결",
            "ord_qty": "3", "cntr_qty": "3", "oso_qty": "0",
        }]}, "account", NOW)
        self.assertEqual(OrderState.FILLED, snapshots[0].state)
        self.assertEqual(3, snapshots[0].filled_quantity)
        self.assertEqual((), snapshots[0].fills)

    def test_unfilled_query_parses_distinct_same_second_execution_ids(self) -> None:
        first = parse_unfilled_orders({"oso": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "체결",
            "ord_qty": "3", "oso_qty": "2", "cntr_no": "fill-a",
            "unit_cntr_qty": "1", "unit_cntr_pric": "70000", "tm": "100203",
        }]}, "account", NOW)
        second = parse_unfilled_orders({"oso": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "체결",
            "ord_qty": "3", "oso_qty": "1", "cntr_no": "fill-b",
            "unit_cntr_qty": "1", "unit_cntr_pric": "70100", "tm": "100203",
        }]}, "account", NOW)
        merged = merge_order_snapshots(first, second)
        self.assertEqual(("fill-a", "fill-b"), tuple(fill.execution_id for fill in merged[0].fills))
        self.assertEqual(2, merged[0].filled_quantity)

    def test_cancelled_partial_order_is_not_mislabeled_filled(self) -> None:
        snapshot = parse_filled_orders({"cntr": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "취소확인",
            "ord_qty": "3", "cntr_qty": "1", "oso_qty": "0",
        }]}, "account", NOW)[0]
        self.assertEqual(OrderState.CANCELLED, snapshot.state)

    def test_filled_snapshot_wins_a_cancel_race_during_merge(self) -> None:
        filled = parse_filled_orders({"cntr": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "체결",
            "ord_qty": "3", "cntr_qty": "3", "oso_qty": "0",
        }]}, "account", NOW)
        cancelled = parse_filled_orders({"cntr": [{
            "ord_no": "7", "stk_cd": "005930", "ord_stt": "취소확인",
            "ord_qty": "3", "cntr_qty": "1", "oso_qty": "0",
        }]}, "account", NOW)
        self.assertEqual(OrderState.FILLED, merge_order_snapshots(filled, cancelled)[0].state)

    def test_account_uses_broker_orderable_amount_after_reservation(self) -> None:
        snapshot = parse_account_snapshot(
            deposit_pages=({"ord_alow_amt": "500000"},),
            balance_pages=({"acnt_evlt_remn_indv_tot": []},),
            unfilled_pages=({"oso": [{
                "ord_no": "1", "stk_cd": "005930", "ord_qty": "2",
                "oso_qty": "2", "ord_pric": "70000", "io_tp_nm": "+매수",
            }]},),
            account_ref="account", as_of=NOW,
        )
        self.assertEqual(500_000, snapshot.available_cash_won - snapshot.reserved_open_buy_won)

    def test_invalid_symbol_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid domestic stock code"):
            parse_filled_orders({"cntr": [{
                "ord_no": "7", "stk_cd": "bad", "ord_stt": "체결",
                "ord_qty": "1", "cntr_qty": "1", "oso_qty": "0",
            }]}, "account", NOW)

    def test_negative_orderable_cash_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "negative ord_alow_amt"):
            parse_account_snapshot(
                deposit_pages=({"ord_alow_amt": "-1"},),
                balance_pages=({"acnt_evlt_remn_indv_tot": []},),
                unfilled_pages=({"oso": []},),
                account_ref="account", as_of=NOW,
            )

    def test_realtime_unit_fill_becomes_detailed_broker_snapshot(self) -> None:
        snapshot = snapshot_from_order_execution(OrderExecution(
            "7", "fill-1", "005930", "삼성전자", "매수", 70_000, 1,
            "100203", "KRX", 3, 2, 1,
        ), account_ref="account", as_of=NOW)
        self.assertEqual(OrderState.PARTIALLY_FILLED, snapshot.state)
        self.assertEqual(1, snapshot.filled_quantity)
        self.assertEqual("fill-1", snapshot.fills[0].execution_id)


if __name__ == "__main__":
    unittest.main()
