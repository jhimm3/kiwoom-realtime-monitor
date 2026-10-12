from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock,patch
from pathlib import Path
import tempfile

from scripts.export_historical_news_seed import SeedExport


def _fixture() -> dict[str, list[dict]]:
    return {
        "central_news_article_revisions": [
            {"article_revision_id": "a0", "collection_scope": "watchlist", "revision_of": None},
            {"article_revision_id": "a1", "collection_scope": "historical_backfill", "revision_of": "a0"},
            {"article_revision_id": "a2", "collection_scope": "watchlist", "revision_of": None},
        ],
        "central_news_body_revisions": [
            {"body_revision_id": "b1", "article_revision_id": "a1"},
            {"body_revision_id": "b2", "article_revision_id": "a2"},
        ],
        "central_news_event_revisions": [
            {"event_revision_id": "e1", "event_id": "E", "article_revision_id": "a1",
             "body_revision_id": "b1", "revision_of": None},
            {"event_revision_id": "e2", "event_id": "E", "article_revision_id": "a2",
             "body_revision_id": "b2", "revision_of": "e1"},
        ],
        "central_news_event_membership_revisions": [
            {"membership_revision_id": "m1", "event_id": "E", "event_revision_id": "e1",
             "article_revision_id": "a1", "body_revision_id": "b1"},
            {"membership_revision_id": "m2", "event_id": "E", "event_revision_id": "e2",
             "article_revision_id": "a2", "body_revision_id": "b2"},
        ],
        "central_news_source_observations": [
            {"observation_id": "o1", "run_id": "r1", "article_revision_id": "a1"},
        ],
        "central_news_source_runs": [
            {"run_revision_id": "rr1", "run_id": "r1"},
        ],
        "central_news_article_target_revisions": [
            {"target_revision_id": "t1", "article_revision_id": "a1", "revision_of": None},
        ],
        "central_news_ai_revisions": [],
        "central_news_jobs": [],
        "central_documents": [
            {"collection": "news_assessment", "owner": "005930", "document_key": "a1"},
            {"collection": "account_secret", "owner": "005930", "document_key": "a1"},
        ],
    }


class FixtureExport(SeedExport):
    def __init__(self, destination: sqlite3.Connection, data: dict, *, max_rows: int = 100) -> None:
        super().__init__(None, destination, max_rows=max_rows)
        self.data = data

    def query(self, table, column, values, *, batch_size=500):
        selected = set(values)
        yield from (dict(row) for row in self.data.get(table, ()) if row.get(column) in selected)


class HistoricalSeedExportTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE seed_rows(table_name TEXT,row_key TEXT,payload_json TEXT,"
                        "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")

    def tearDown(self):
        self.db.close()

    def test_export_preserves_owned_ids_and_event_support_closure(self):
        report = FixtureExport(self.db, _fixture()).run()
        self.assertEqual((1, 2), (report["owned_articles"], report["support_articles"]))
        articles = self.db.execute("SELECT row_key,role FROM seed_rows "
                                   "WHERE table_name='central_news_article_revisions' ORDER BY row_key").fetchall()
        self.assertEqual([('["a0"]', 'support_only'), ('["a1"]', 'historical'),
                          ('["a2"]', 'support_only')], articles)
        self.assertEqual(1, report["tables"]["central_news_source_runs"])
        self.assertEqual(1, report["tables"]["central_documents"])

    def test_missing_body_reference_blocks_export(self):
        data = _fixture()
        data["central_news_body_revisions"] = data["central_news_body_revisions"][:1]
        with self.assertRaisesRegex(ValueError, "body_revision_id"):
            FixtureExport(self.db, data).run()

    def test_missing_source_run_blocks_export(self):
        data = _fixture()
        data["central_news_source_runs"] = []
        with self.assertRaisesRegex(ValueError, "source run references missing"):
            FixtureExport(self.db, data).run()

    def test_closure_limit_fails_without_truncating(self):
        with self.assertRaisesRegex(ValueError, "max_rows"):
            FixtureExport(self.db, _fixture(), max_rows=2).run()

    def test_query_failure_reports_stage_without_reference_ids(self):
        source=MagicMock()
        source.cursor.return_value.__enter__.return_value.fetchmany.side_effect=RuntimeError('timeout')
        with self.assertRaisesRegex(RuntimeError,'central_news_jobs') as caught:
            list(SeedExport(source,self.db,max_rows=100).query('central_news_jobs','article_revision_id',{'private-reference-id'}))
        self.assertNotIn('private-reference-id',str(caught.exception))
        self.assertIn('"keys": 1',str(caught.exception))

    def test_export_timeout_is_connection_local_and_failed_partial_removed(self):
        from scripts.export_historical_news_seed import export_seed
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'seed.sqlite3'
            with patch('psycopg.connect',side_effect=RuntimeError('connection unavailable')) as connect:
                with self.assertRaisesRegex(RuntimeError,'unavailable'):
                    export_seed('test-only',output,statement_timeout_ms=300000)
            self.assertEqual('-c statement_timeout=300000',connect.call_args.kwargs['options'])
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name(output.name+'.partial').exists())


if __name__ == "__main__":
    unittest.main()
