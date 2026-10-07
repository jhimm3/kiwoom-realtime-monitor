from __future__ import annotations

import sqlite3
import unittest

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


if __name__ == "__main__":
    unittest.main()
