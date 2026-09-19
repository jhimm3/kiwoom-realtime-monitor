from __future__ import annotations

import unittest

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_account_balance_changes, parse_market_index_ticks, parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_ticks,
    parse_vi_events,
)


class RealtimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_scope = AccountScope(
            "kiwoom", AccountEnvironment.REAL,
            "11111111-1111-1111-1111-111111111111",
        )

    def test_real_message_with_null_data_is_an_empty_event_batch(self) -> None:
        message = {"trnm": "REAL", "data": None}

        self.assertEqual((), parse_trade_ticks(message))
        self.assertEqual((), parse_order_executions(message))
        self.assertEqual((), parse_account_balance_changes(message))
        self.assertEqual((), parse_market_index_ticks(message))
        self.assertEqual((), parse_program_trade_ticks(message))
        self.assertEqual((), parse_stock_price_references(message))
        self.assertEqual((), parse_vi_events(message))

    def test_reads_0g_price_limit_and_reference_as_one_basis(self) -> None:
        values = parse_stock_price_references({
            "trnm": "REAL",
            "data": [{"type": "0g", "item": "005930", "values": {
                "305": "+91900", "306": "-49500", "307": "70700",
            }}],
        })

        self.assertEqual(1, len(values))
        self.assertEqual(
            ("005930", 91_900, 49_500, 70_700),
            (values[0].code, values[0].upper_limit_price,
             values[0].lower_limit_price, values[0].base_price),
        )
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

    def test_preserves_sor_source_while_normalizing_integrated_code(self) -> None:
        ticks = parse_trade_ticks({
            "trnm": "REAL",
            "data": [{"type": "0B", "item": "005930_AL", "values": {"10": "+100"}}],
        })
        programs = parse_program_trade_ticks({
            "trnm": "REAL",
            "data": [{"type": "0w", "item": "005930_AL", "values": {"20": "100001"}}],
        })

        self.assertEqual(("005930", "SOR"), (ticks[0].code, ticks[0].market))
        self.assertEqual(("005930", "SOR"), (programs[0].code, programs[0].market))

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

    def test_prefers_unit_fill_and_preserves_cumulative_quantities(self) -> None:
        values = parse_order_executions({
            "trnm": "REAL", "data": [{"type": "00", "values": {
                "9203": "18", "909": "fill-2", "9001": "A005930", "913": "체결",
                "905": "+매수", "908": "094022", "900": "3", "902": "1",
                "910": "60700", "911": "2", "914": "60800", "915": "1",
            }}],
        })
        self.assertEqual((60_800, 1), (values[0].price, values[0].quantity))
        self.assertEqual((3, 1, 2), (
            values[0].ordered_quantity,
            values[0].remaining_quantity,
            values[0].cumulative_filled_quantity,
        ))

    def test_ignores_order_receipt_without_fill(self) -> None:
        message = {"trnm": "REAL", "data": [{"type": "00", "values": {"913": "접수", "910": "", "911": ""}}]}
        self.assertEqual((), parse_order_executions(message))

    def test_verified_00_scope_is_attached_and_wrong_account_is_dropped(self) -> None:
        message = {"trnm": "REAL", "data": [{"type": "00", "values": {
            "9201": "12345678", "9203": "18", "909": "7", "9001": "A005930",
            "913": "체결", "905": "+매수", "908": "094022", "910": "60700", "911": "2",
        }}]}
        accepted = parse_order_executions(
            message, lambda raw: self.real_scope if raw == "12345678" else None,
        )
        rejected = parse_order_executions(message, lambda _raw: None)
        self.assertEqual(self.real_scope, accepted[0].origin_scope)
        self.assertEqual((), rejected)
        self.assertNotIn("12345678", repr(accepted[0]))

    def test_reads_04_balance_without_exposing_account_number(self) -> None:
        values = parse_account_balance_changes({
            "trnm": "REAL", "data": [{"type": "04", "item": "A005930", "values": {
                "9201": "secret-account", "9001": "A005930", "302": "삼성전자",
                "930": "3", "931": "+70000", "932": "210000", "933": "2",
                "951": "500000", "10": "+71000",
            }}],
        })
        self.assertEqual(1, len(values))
        self.assertEqual(("005930", 3, 2, 500_000), (
            values[0].code, values[0].position_quantity,
            values[0].orderable_quantity, values[0].deposit_won,
        ))
        self.assertNotIn("account", values[0].__dict__)

    def test_rejects_negative_04_position_quantity(self) -> None:
        values = parse_account_balance_changes({
            "trnm": "REAL", "data": [{"type": "04", "item": "005930", "values": {"930": "-1"}}],
        })
        self.assertEqual((), values)

    def test_04_without_verified_9201_is_dropped(self) -> None:
        message = {"trnm": "REAL", "data": [{"type": "04", "values": {
            "9201": "87654321", "9001": "A005930", "930": "1",
        }}]}
        self.assertEqual((), parse_account_balance_changes(message, lambda _raw: None))

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
