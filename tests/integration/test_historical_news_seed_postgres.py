"""Run only against kiwoom_monitor_diagnostic_test, never the live database."""

from __future__ import annotations

import os
import sqlite3
import unittest
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import IsolationLevel

from scripts.export_historical_news_seed import SeedExport, TABLE_KEYS


def _test_url() -> str:
    explicit = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL")
    if explicit:
        return explicit
    live = os.environ.get("KIWOOM_SERVER_DATABASE_URL", "")
    if not live:
        raise unittest.SkipTest("dedicated PostgreSQL test URL is unavailable")
    parsed = urlsplit(live)
    return urlunsplit((parsed.scheme, parsed.netloc,
                       "/kiwoom_monitor_diagnostic_test", parsed.query, parsed.fragment))


class HistoricalNewsSeedPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url = _test_url()
        self.admin = psycopg.connect(self.url, connect_timeout=5, autocommit=True)
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual("kiwoom_monitor_diagnostic_test", cursor.fetchone()[0])
        self.schema = "news_seed_" + uuid4().hex[:12]
        with self.admin.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {self.schema}")
            self.addCleanup(self._drop_schema)
            for table, keys in TABLE_KEYS.items():
                columns = set(keys)
                columns.update(("article_revision_id", "revision_of"))
                if table == "central_news_article_revisions":
                    columns.add("collection_scope")
                if table in {"central_news_event_revisions", "central_news_event_membership_revisions"}:
                    columns.update(("event_id", "body_revision_id", "event_revision_id"))
                if table == "central_news_source_observations":
                    columns.add("run_id")
                if table == "central_news_source_runs":
                    columns.add("run_id")
                if table == "central_news_jobs":
                    columns.update(("stage", "state", "output_ref"))
                definition = ",".join(f"{name} TEXT" for name in sorted(columns))
                cursor.execute(f"CREATE TABLE {self.schema}.{table}({definition})")
            cursor.execute(f"INSERT INTO {self.schema}.central_news_article_revisions "
                           "(article_revision_id,collection_scope) VALUES('a1','historical_backfill')")
            cursor.execute(f"INSERT INTO {self.schema}.central_news_body_revisions "
                           "(body_revision_id,article_revision_id) VALUES('b1','a1')")
            cursor.execute(f"INSERT INTO {self.schema}.central_news_jobs "
                           "(job_key,article_revision_id,stage,state,output_ref) "
                           "VALUES('j1','a1','BODY','COMPLETED','b1')")

    def _drop_schema(self) -> None:
        with self.admin.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def test_read_only_snapshot_exports_original_ids(self) -> None:
        destination = sqlite3.connect(":memory:")
        destination.execute("CREATE TABLE seed_rows(table_name TEXT,row_key TEXT,"
                            "payload_json TEXT,payload_hash TEXT,role TEXT,"
                            "PRIMARY KEY(table_name,row_key))")
        try:
            with psycopg.connect(self.url, connect_timeout=5,
                                 options=f"-c search_path={self.schema}") as source:
                source.read_only = True
                source.isolation_level = IsolationLevel.REPEATABLE_READ
                with source.cursor() as cursor:
                    cursor.execute("SHOW transaction_read_only")
                    self.assertEqual("on", cursor.fetchone()[0])
                report = SeedExport(source, destination, max_rows=100).run()
                self.assertEqual(1, report["owned_articles"])
                self.assertEqual(1, report["tables"]["central_news_body_revisions"])
                self.assertEqual(1, report["tables"]["central_news_jobs"])
                self.assertEqual(("[\"a1\"]", "historical"), destination.execute(
                    "SELECT row_key,role FROM seed_rows "
                    "WHERE table_name='central_news_article_revisions'").fetchone())
        finally:
            destination.close()


if __name__ == "__main__":
    unittest.main()
