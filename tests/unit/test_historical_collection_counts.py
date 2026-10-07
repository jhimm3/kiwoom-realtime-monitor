from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.historical_collection_counts import initialize_counts, read_counts


class IncrementalCountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "counts.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE prepared_news(id INTEGER PRIMARY KEY,state TEXT,body_json TEXT,updated_at REAL)")
        self.connection.executemany("INSERT INTO prepared_news VALUES(?,?,?,?)", [
            (1, "ready", '{"body_status":"fulltext"}', 10),
            (2, "failed", "invalid-json", 11),
        ])
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def snapshot(self):
        return read_counts(self.connection, "prepared_news")

    def test_bootstrap_updates_replay_delete_and_rollback(self):
        initialize_counts(self.path, ["prepared_news"])
        self.assertEqual(self.snapshot()["counts"], {"ready": 1, "failed": 1})
        with self.connection:
            self.connection.execute("INSERT INTO prepared_news VALUES(3,'ready',?,12)", ('{"body_status":"summary_only"}',))
            self.connection.execute("UPDATE prepared_news SET state='ready',body_json=?,updated_at=13 WHERE id=2", ('{"body_status":"fulltext"}',))
        self.assertEqual(self.snapshot()["body_counts"], {"fulltext": 2, "summary_only": 1})
        with self.connection:
            self.connection.execute("INSERT INTO prepared_news VALUES(3,'ready',?,14) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", ('{"body_status":"summary_only"}',))
        self.assertEqual(self.snapshot()["counts"], {"ready": 3})
        self.connection.execute("UPDATE prepared_news SET state='failed',body_json='bad' WHERE id=1")
        self.connection.rollback()
        self.assertEqual(self.snapshot()["counts"], {"ready": 3})
        with self.connection:
            self.connection.execute("DELETE FROM prepared_news WHERE id=1")
            self.connection.execute("UPDATE prepared_news SET body_json='bad' WHERE id=2")
        self.assertEqual(self.snapshot()["body_counts"], {"summary_only": 1})
        self.assertEqual(self.snapshot()["counts"], {"ready": 2})
        self.assertEqual(initialize_counts(self.path, ["prepared_news"])["prepared_news"], "already_initialized")

    def test_concurrent_write_during_bootstrap_is_included_once(self):
        def mutate():
            with closing(sqlite3.connect(self.path)) as other, other:
                other.execute("UPDATE prepared_news SET state='ready',body_json=? WHERE id=2", ('{"body_status":"summary_only"}',))
                other.execute("DELETE FROM prepared_news WHERE id=1")
                other.execute("INSERT INTO prepared_news VALUES(3,'failed','{}',20)")
        initialize_counts(self.path, ["prepared_news"], snapshot_hook=mutate)
        self.assertEqual(self.snapshot()["counts"], {"ready": 1, "failed": 1})
        self.assertEqual(self.snapshot()["body_counts"], {"summary_only": 1})

    def test_interrupted_bootstrap_retry_and_exclusive_initializer(self):
        def fail():
            with self.assertRaises(OSError):
                initialize_counts(self.path, ["prepared_news"])
            with closing(sqlite3.connect(self.path)) as other, other:
                other.execute("INSERT INTO prepared_news VALUES(3,'ready','{}',20)")
            raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            initialize_counts(self.path, ["prepared_news"], snapshot_hook=fail)
        with self.assertRaises(sqlite3.OperationalError):
            self.snapshot()
        initialize_counts(self.path, ["prepared_news"])
        self.assertEqual(self.snapshot()["counts"], {"ready": 2, "failed": 1})

    def test_poll_reads_only_small_summary_tables(self):
        initialize_counts(self.path, ["prepared_news"])
        statements = []
        self.connection.set_trace_callback(statements.append)
        for _ in range(5):
            self.snapshot()
        self.assertTrue(statements)
        self.assertFalse(any("FROM prepared_news" in sql for sql in statements))
        self.assertFalse(any("GROUP BY" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
