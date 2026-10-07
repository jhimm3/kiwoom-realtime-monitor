"""Fixed PostgreSQL diagnostic probes never expose raw query parameters."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from kiwoom_monitor.central_server.diagnostic_sampling import _device_delta, read_postgres_snapshot


class FakeCursor:
    def __init__(self):
        self.statement = ""
        self.executed = []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.executed.append((statement, params))

    def fetchone(self):
        if "current_database" in self.statement:
            return ("kiwoom_monitor", "17.11")
        if "pg_stat_user_tables" in self.statement:
            return ("public", 10, 2, 4, 8, None, datetime.now(timezone.utc))
        raise AssertionError(self.statement)

    def fetchall(self):
        if "pg_stat_activity" in self.statement:
            return [(12, "client backend", datetime.now(timezone.utc), "active",
                     "IO", "DataFileRead", 850.2, 1500.1, [],
                     "SELECT * FROM central_news_jobs WHERE secret='sensitive-token'")]
        if "pg_stat_user_indexes" in self.statement:
            return [("idx_central_news_jobs_ready", 8, 100, 10)]
        raise AssertionError(self.statement)


class FakeConnection:
    def __init__(self): self.cursor_value = FakeCursor()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cursor_value


class DiagnosticSamplingApiTests(unittest.TestCase):
    def test_device_delta_preserves_counters_without_dividing_by_zero(self):
        before = {"dm-4": [0, 0, 0, 0, 3, 0, 10, 20, 0, 30, 40]}
        after = {"dm-4": [0, 0, 0, 0, 5, 0, 14, 26, 0, 34, 48]}

        result = _device_delta(before, after, 0)

        self.assertEqual(2, result["dm-4"]["writes"])
        self.assertEqual(4, result["dm-4"]["write_sectors"])
        self.assertEqual(3.0, result["dm-4"]["write_await_ms"])
        self.assertIsNone(result["dm-4"]["average_queue"])
        self.assertIsNone(result["dm-4"]["busy_percent"])

    def test_device_delta_calculates_rate_fields_for_positive_elapsed(self):
        before = {"dm-4": [0, 0, 0, 0, 3, 0, 10, 20, 0, 30, 40]}
        after = {"dm-4": [0, 0, 0, 0, 5, 0, 14, 26, 0, 34, 48]}

        result = _device_delta(before, after, 0.5)

        self.assertEqual(0.02, result["dm-4"]["average_queue"])
        self.assertEqual(0.8, result["dm-4"]["busy_percent"])

    def test_activity_and_news_stats_are_fixed_read_only_and_redacted(self):
        connection = FakeConnection()
        with patch("psycopg.connect", return_value=connection) as connect:
            result = read_postgres_snapshot("fake-url", sections=frozenset({"activity", "news_jobs"}),
                                            pid=12)
        connect.assert_called_once_with("fake-url", autocommit=True, connect_timeout=5)
        self.assertEqual(12, result["sections"]["activity"]["rows"][0]["pid"])
        self.assertEqual(["pg_backend_pid" in query for query, _ in connection.cursor_value.executed],
                         [False, False, False, True, False, False])
        self.assertIn("SET default_transaction_read_only=on", connection.cursor_value.executed[0][0])
        self.assertNotIn("sensitive-token", json.dumps(result))
        self.assertIsNone(result["sections"]["activity"]["rows"][0]["query_template"])
        self.assertEqual("public", result["sections"]["news_jobs"]["table"]["schema"])
        news_table_query = next(query for query, _ in connection.cursor_value.executed
                                if "pg_stat_user_tables" in query)
        news_index_query = next(query for query, _ in connection.cursor_value.executed
                                if "pg_stat_user_indexes" in query)
        self.assertIn("relid=to_regclass('central_news_jobs')", news_table_query)
        self.assertIn("relid=to_regclass('central_news_jobs')", news_index_query)
        self.assertEqual(100, result["sections"]["news_jobs"]["indexes"][0]["tuples_read"])


if __name__ == "__main__":
    unittest.main()
