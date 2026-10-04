from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from scripts.audit_postgres_access import audit_direct_connections, inventory


class PostgresDirectConnectionAuditTests(unittest.TestCase):
    def test_new_or_stale_callsite_requires_review(self) -> None:
        site = {"file": "src/example.py", "owner": "Store._connect", "call": "psycopg.connect"}

        unreviewed = audit_direct_connections([site], {"approved_sites": []})
        self.assertEqual("review_required", unreviewed["status"])
        self.assertEqual(1, unreviewed["unapproved_sites"][0]["current_count"])
        self.assertEqual([], unreviewed["stale_approvals"])

        stale = audit_direct_connections([], {"approved_sites": [{**site, "count": 1}]})
        self.assertEqual("review_required", stale["status"])
        self.assertEqual([], stale["unapproved_sites"])
        self.assertEqual(1, stale["stale_approvals"][0]["approved_count"])

    def test_repository_direct_connections_match_reviewed_manifest(self) -> None:
        root = Path(__file__).resolve().parents[2]

        result = inventory(root)

        self.assertEqual("pass", result["direct_connection_guard"]["status"])
        self.assertEqual(42, result["direct_connection_guard"]["current_callsite_count"])
        self.assertEqual([], result["direct_connection_guard"]["unapproved_sites"])
        self.assertEqual([], result["direct_connection_guard"]["stale_approvals"])

    def test_news_writer_audit_follows_document_module_helpers(self) -> None:
        result = inventory(Path(__file__).resolve().parents[2])
        rows = {row["function"]: row for row in result["postgres_store_methods"]}

        for method in ("save_news_body_revision", "save_news_source_page",
                       "save_historical_market_news_batch"):
            with self.subTest(method=method):
                row = rows[f"PostgresQueryStore.{method}"]
                self.assertIn("_insert_postgres_news_job", row["reachable_local_helpers"])
                self.assertIn("central_news_jobs", row["reachable_literal_tables_candidates"])

    def test_dataset_writer_audit_follows_observation_helper_chain(self) -> None:
        result = inventory(Path(__file__).resolve().parents[2])
        rows = {row["function"]: row for row in result["postgres_store_methods"]}
        row = rows["PostgresQueryStore.save_dataset_snapshots"]
        self.assertIn("_append_postgres_observation_revision", row["reachable_local_helpers"])
        self.assertIn("_insert_postgres_observation_revision", row["reachable_local_helpers"])
        self.assertIn("_observation_revision_values", row["reachable_local_helpers"])
        selected = result["selected_pg_and_shared_helper_calls"]
        self.assertIn(
            "src/kiwoom_monitor/central_server/database_observation_writes.py",
            {call["file"] for call in selected},
        )

    def test_helper_only_import_chain_is_followed_without_upper_layer_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            central = root / "src/kiwoom_monitor/central_server"
            app = root / "src/kiwoom_monitor/application"
            central.mkdir(parents=True)
            app.mkdir(parents=True)
            (central / "database.py").write_text(
                "from .database_datasets import read_snapshot as _read\n"
                "class QueryStore: pass\n"
                "class SQLiteQueryStore: pass\n"
                "class PostgresQueryStore:\n"
                "    def load_dataset_snapshots(self, cursor): return _read(cursor)\n",
                encoding="utf-8",
            )
            (central / "database_datasets.py").write_text(
                "from .database_observation_writes import read_rows as _read_rows\n"
                "def read_snapshot(cursor): return _read_rows(cursor)\n",
                encoding="utf-8",
            )
            (central / "database_observation_writes.py").write_text(
                "def read_rows(cursor):\n"
                "    cursor.execute('SELECT * FROM central_dataset_snapshots')\n",
                encoding="utf-8",
            )
            (central / "postgres_access.py").write_text("", encoding="utf-8")
            (app / "unrelated.py").write_text(
                "def unrelated(cursor): cursor.execute('DELETE FROM central_unrelated')\n",
                encoding="utf-8",
            )

            result = inventory(root, root / "missing-approvals.json")
            row = next(
                item for item in result["postgres_store_methods"]
                if item["function"] == "PostgresQueryStore.load_dataset_snapshots"
            )
            self.assertIn("read_snapshot", row["reachable_local_helpers"])
            self.assertIn("read_rows", row["reachable_local_helpers"])
            self.assertIn("central_dataset_snapshots", row["reachable_literal_tables_candidates"])
            selected_files = {
                item["file"] for item in result["selected_pg_and_shared_helper_calls"]
            }
            self.assertIn(
                "src/kiwoom_monitor/central_server/database_observation_writes.py",
                selected_files,
            )
            self.assertNotIn(
                "src/kiwoom_monitor/application/unrelated.py",
                selected_files,
            )


if __name__ == "__main__":
    unittest.main()
