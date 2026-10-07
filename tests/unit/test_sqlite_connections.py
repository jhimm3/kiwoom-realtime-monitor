from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.sqlite_connections import (
    sqlite_read_connection,
    sqlite_transaction,
)


class SQLiteConnectionsTests(unittest.TestCase):
    def test_transaction_commits_and_closes_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with sqlite_transaction(path) as connection:
                connection.execute("CREATE TABLE values_table(value TEXT)")
                connection.execute("INSERT INTO values_table VALUES('kept')")
            with sqlite_read_connection(path) as connection:
                rows = connection.execute("SELECT value FROM values_table").fetchall()
            renamed = path.with_name("renamed.sqlite3")
            path.rename(renamed)

        self.assertEqual([("kept",)], rows)

    def test_transaction_rolls_back_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with sqlite_transaction(path) as connection:
                connection.execute("CREATE TABLE values_table(value TEXT)")
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with sqlite_transaction(path) as connection:
                    connection.execute("INSERT INTO values_table VALUES('discarded')")
                    raise RuntimeError("stop")
            with sqlite_read_connection(path) as connection:
                rows = connection.execute("SELECT value FROM values_table").fetchall()

        self.assertEqual([], rows)

    def test_read_connection_closes_after_query_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                with sqlite_read_connection(path) as connection:
                    connection.execute("SELECT * FROM missing_table")
            path.unlink()


if __name__ == "__main__":
    unittest.main()
