from __future__ import annotations

import unittest

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    parse_account_balance_changes, parse_market_index_ticks, parse_market_operation_ticks,
    OrderBookLevel, parse_order_book_ticks, parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_quote_observations, parse_trade_ticks,
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
        self.assertEqual((), parse_market_operation_ticks(message))
        self.assertEqual((), parse_program_trade_ticks(message))
        self.assertEqual((), parse_stock_price_references(message))
        self.assertEqual((), parse_vi_events(message))
        self.assertEqual((), parse_trade_quote_observations(message))
        self.assertEqual((), parse_order_book_ticks(message))

    def test_reads_0s_market_operation_code_without_reinterpreting_it(self) -> None:
        ticks = parse_market_operation_ticks({
            "trnm": "REAL",
            "data": [{"type": "0s", "item": "", "values": {
                "215": "3", "20": "090000", "214": "000000",
            }}],
        })

        self.assertEqual(1, len(ticks))
        self.assertEqual(("3", "090000", "000000"), (
            ticks[0].status_code, ticks[0].trade_time, ticks[0].remaining_time,
        ))

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

    def test_0b_quote_and_reported_venue_do_not_change_legacy_trade_payload(self) -> None:
        values = {"10": "+20800", "15": "+82", "20": "165208"}
        source = {"type": "0B", "item": "005930_AL"}
        old = {"trnm": "REAL", "data": [source | {"values": values}]}
        message = {"trnm": "REAL", "data": [source | {"values": values | {
            "27": "-20800", "28": "-20700", "9081": "NXT",
        }}]}
        quote, = parse_trade_quote_observations(message)
        self.assertEqual(("005930", "SOR", "NXT", "NXT", "165208", "second", 20_800, 20_700), (
            quote.code, quote.subscription_market, quote.reported_venue, quote.raw_reported_venue,
            quote.trade_time, quote.time_precision, quote.best_ask_won, quote.best_bid_won,
        ))
        self.assertEqual(parse_trade_ticks(old), parse_trade_ticks(message))
        self.assertEqual("SOR", parse_trade_ticks(message)[0].market)
        missing, = parse_trade_quote_observations(old)
        self.assertEqual((None, None, None), (missing.best_ask_won, missing.best_bid_won, missing.reported_venue))

    def test_0b_quote_preserves_unknown_reported_venue_without_guessing_missing_values(self) -> None:
        quote, = parse_trade_quote_observations({"trnm": "REAL", "data": [
            {"type": "0C", "item": "005930", "values": {"27": "20800", "28": "20700"}},
            {"type": "0B", "item": "005930", "values": {"20": "091211123", "27": "0", "28": "+1_2", "9081": "9"}},
        ]})
        self.assertEqual((None, None, None, None, "9"), (
            quote.trade_time, quote.best_ask_won, quote.best_bid_won, quote.reported_venue, quote.raw_reported_venue,
        ))

    def test_0d_preserves_level_sides_sizes_and_indicative_fields_separately(self) -> None:
        fields = {
            "21": "165207", "41": "-20800", "61": "82", "50": "+21350", "70": "19",
            "51": "-20700", "71": "23847", "60": "-20250", "80": "25",
            "6044": "4", "6053": "5", "6054": "6", "6063": "7",
            "6066": "8", "6075": "9", "6076": "10", "6085": "11",
            "121": "12622527", "125": "14453430", "23": "20850", "24": "332941", "291": "20860", "292": "20",
        }
        message = {"trnm": "REAL", "data": [{"type": "0D", "item": "005930", "values": fields}]}
        book, = parse_order_book_ticks(message, subscription_item="005930")
        self.assertEqual(("005930", "005930", "KRX", "165207", "second"), (
            book.code, book.item, book.subscription_market, book.quote_time, book.time_precision,
        ))
        self.assertEqual((10, 10), (len(book.asks), len(book.bids)))
        self.assertEqual((OrderBookLevel(20_800, 82), OrderBookLevel(21_350, 19)), (book.asks[0], book.asks[9]))
        self.assertEqual((OrderBookLevel(20_700, 23_847), OrderBookLevel(20_250, 25)), (book.bids[0], book.bids[9]))
        venue_quantities = (book.krx_ask_quantities, book.krx_bid_quantities, book.nxt_ask_quantities, book.nxt_bid_quantities)
        self.assertEqual([(4, 5), (6, 7), (8, 9), (10, 11)], [(levels[0], levels[9]) for levels in venue_quantities])
        self.assertEqual((12_622_527, 14_453_430, 20_850, 332_941, 20_860, 20), (
            book.total_ask_quantity, book.total_bid_quantity, book.indicative_price_won, book.indicative_quantity,
            book.auction_indicative_price_won, book.auction_indicative_quantity,
        ))
        self.assertIsNone(parse_order_book_ticks(message)[0].subscription_market)
        self.assertIsNone(parse_order_book_ticks(message, subscription_item="005930_NX")[0].subscription_market)

    def test_0d_missing_and_invalid_sizes_do_not_replace_reported_zero_or_infer_time(self) -> None:
        fields = {"21": "256100", "20": "091211", "41": "20800", "61": "0", "42": "20850", "62": "-2",
                  "51": "20700", "71": True, "52": "20650", "72": "", "23": "0", "24": "0"}
        message = {"trnm": "REAL", "data": [
            {"type": "0H", "item": "005930", "values": fields},
            {"type": "0D", "item": "005930_NX", "values": fields},
            {"type": "0D", "item": "005930_AL", "values": fields},
            {"type": "0D", "item": "005930_XX", "values": fields},
        ]}
        nxt, sor = parse_order_book_ticks(message)
        self.assertEqual(("005930", "NXT", "SOR"), (nxt.code, nxt.subscription_market, sor.subscription_market))
        self.assertEqual((None, None, 0), (nxt.quote_time, nxt.indicative_price_won, nxt.indicative_quantity))
        self.assertEqual((OrderBookLevel(20_800, 0), OrderBookLevel(20_850, None)), nxt.asks[:2])
        self.assertEqual((OrderBookLevel(20_700, None), OrderBookLevel(20_650, None)), nxt.bids[:2])
        self.assertEqual((), parse_order_book_ticks(message | {"trnm": "REG"}))

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
