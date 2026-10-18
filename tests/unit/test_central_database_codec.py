from __future__ import annotations

import unittest

from kiwoom_monitor.central_server.database_codec import (
    DAILY_BAR_COLUMNS,
    MINUTE_BAR_COLUMNS,
    bar_result_rows,
    bar_value_rows,
    bounded_limit,
    bounded_offset,
    dataset_snapshot_result_rows,
    document_result_rows,
    document_select_query,
    document_value_rows,
)


class CentralDatabaseCodecTests(unittest.TestCase):
    def test_minute_bar_round_trip_preserves_contract_column_order(self) -> None:
        expected_columns = (
            "trading_date", "minute", "code", "market", "open", "high", "low", "close",
            "volume", "trade_value_million_won", "updated_at",
        )
        value = dict(zip(expected_columns, range(11), strict=True))

        row = bar_value_rows([value], minute=True)[0]
        restored = bar_result_rows([row], minute=True)[0]

        self.assertEqual(expected_columns, MINUTE_BAR_COLUMNS)
        self.assertEqual(tuple(range(11)), row)
        self.assertEqual(value, restored)

    def test_daily_bar_round_trip_uses_daily_columns(self) -> None:
        expected_columns = (
            "trading_date", "code", "market", "open", "high", "low", "close", "volume",
            "trade_value_million_won", "updated_at",
        )
        value = dict(zip(expected_columns, range(10), strict=True))

        stored = bar_value_rows([value], minute=False)
        restored = bar_result_rows(stored, minute=False)

        self.assertEqual(expected_columns, DAILY_BAR_COLUMNS)
        self.assertEqual([tuple(range(10))], stored)
        self.assertEqual([value], restored)

    def test_limit_and_offset_are_bounded_for_both_databases(self) -> None:
        self.assertEqual(1, bounded_limit(0, 5000))
        self.assertEqual(5000, bounded_limit(9999, 5000))
        self.assertEqual(25, bounded_limit(25, 5000))
        self.assertEqual(0, bounded_offset(-3))
        self.assertEqual(7, bounded_offset(7))

    def test_document_rows_round_trip_for_text_and_postgres_json_object(self) -> None:
        value = {"owner": "사용자", "key": "설정", "document": {"모델": "gemini"}}
        stored = document_value_rows("settings", [value], 10.5)[0]

        from_text = document_result_rows([(stored[1], stored[2], stored[3], stored[4])])
        from_mapping = document_result_rows([("사용자", "설정", 10.5, {"모델": "gemini"})])

        expected = [{**value, "updated_at": 10.5}]
        self.assertEqual(expected, from_text)
        self.assertEqual(expected, from_mapping)

    def test_document_query_differs_only_by_database_placeholder(self) -> None:
        sqlite_sql, sqlite_parameters = document_select_query(
            "news", "005930", 20_000, -1, 3.5, placeholder="?",
        )
        postgres_sql, postgres_parameters = document_select_query(
            "news", "005930", 20_000, -1, 3.5, placeholder="%s",
        )

        self.assertEqual(sqlite_sql.replace("?", "%s"), postgres_sql)
        self.assertEqual(["news", "005930", 3.5, 10_000, 0], sqlite_parameters)
        self.assertEqual(sqlite_parameters, postgres_parameters)

    def test_dataset_snapshot_result_supports_text_and_json_object(self) -> None:
        rows = [
            ("005930", "2026-09-08T09:00", 10.5, '{"rank":1}'),
            ("000660", "2026-09-08T09:01", 11.5, {"rank": 2}),
        ]

        self.assertEqual(
            [
                {"subject": "005930", "snapshot_key": "2026-09-08T09:00", "saved_at": 10.5, "payload": {"rank": 1}},
                {"subject": "000660", "snapshot_key": "2026-09-08T09:01", "saved_at": 11.5, "payload": {"rank": 2}},
            ],
            dataset_snapshot_result_rows(rows),
        )


if __name__ == "__main__":
    unittest.main()
