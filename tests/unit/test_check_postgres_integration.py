from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts.check_postgres_integration import (
    _A4B_COLLECTIONS,
    _a4b_content_documents,
    _exercise_a4b_content_api,
)

class CheckPostgresIntegrationTests(unittest.TestCase):
    def test_a4b_fixtures_preserve_scope_key_provenance_and_tombstones(self) -> None:
        account_ref = "11111111-1111-4111-8111-111111111111"
        values = _a4b_content_documents(
            "integration-test", account_ref, datetime(2026, 9, 13, tzinfo=UTC),
        )

        self.assertEqual(set(_A4B_COLLECTIONS), set(values))
        for collection, value in values.items():
            document = value["document"]
            self.assertEqual(account_ref, value["owner"])
            self.assertEqual(account_ref, document["origin_account_ref"])
            self.assertEqual(account_ref, document["canonical_account_ref"])
            self.assertEqual(collection, document["source_collection"])
            self.assertEqual(value["key"], document["source_key"])
            self.assertEqual(account_ref, document["source_owner"])
            self.assertEqual(64, len(document["source_content_hash"]))
        news = values["journal_v2_news_links"]
        identity = {
            "origin_scope": {
                "broker": "kiwoom", "environment": "mock", "account_ref": account_ref,
            },
            "group_id": news["document"]["group_id"],
            "stock_code": "005930", "identity": "integration-test",
        }
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(
            "journal-news-link:v2:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            news["key"],
        )
        self.assertTrue(news["document"]["is_deleted"])
        self.assertTrue(values["journal_v2_sync_states"]["document"]["is_deleted"])

    def test_a4b_api_exercise_requires_success_and_observes_scope_rejection(self) -> None:
        account_ref = "11111111-1111-4111-8111-111111111111"
        values = _a4b_content_documents(
            "integration-test", account_ref, datetime(2026, 9, 13, tzinfo=UTC),
        )
        with tempfile.TemporaryDirectory() as directory:
            statuses, mismatch = asyncio.run(
                _exercise_a4b_content_api(
                    f"sqlite:///{Path(directory) / 'central.sqlite3'}", values,
                )
            )

        self.assertEqual({collection: 200 for collection in _A4B_COLLECTIONS}, statuses)
        self.assertEqual(422, mismatch)


if __name__ == "__main__":
    unittest.main()
