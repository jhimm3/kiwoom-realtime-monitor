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
    _EphemeralVaultMetadata,
    _a4b_content_documents,
    _exercise_a4b_content_api,
    _exercise_shadow_transaction,
)

class CheckPostgresIntegrationTests(unittest.TestCase):
    def test_temporary_vault_metadata_does_not_read_or_write_live_fence(self) -> None:
        class LiveStore:
            def __init__(self):
                self.upserts = []

            def load_documents(self, collection, owner="", limit=1000, offset=0):
                if collection == "credential_vault_state":
                    return [{"owner": "live", "key": "state", "document": {"revision": 9}}]
                return [{"collection": collection, "owner": owner}]

            def upsert_documents(self, collection, values):
                self.upserts.append((collection, values))

            def load_credential_activations(self, profile_id):
                return [profile_id]

        live = LiveStore()
        isolated = _EphemeralVaultMetadata(live)

        self.assertEqual([], isolated.load_documents("credential_vault_state"))
        isolated.upsert_documents("credential_vault_state", [{
            "owner": "temporary", "key": "state", "document": {"revision": 1},
        }])
        self.assertEqual(
            [{"owner": "temporary", "key": "state", "document": {"revision": 1}}],
            isolated.load_documents("credential_vault_state", "temporary"),
        )
        self.assertEqual([], live.upserts)
        self.assertEqual(["profile"], isolated.load_credential_activations("profile"))

    def test_shadow_check_rolls_back_before_other_connections_can_observe_it(self) -> None:
        class Cursor:
            def __init__(self, connection):
                self.connection = connection
                self.result = (0,)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _parameters=()):
                if "INSERT INTO central_shadow_monitor_state" in sql:
                    self.connection.pending_state = 1
                elif "INSERT INTO central_shadow_decisions" in sql:
                    pass
                elif "INSERT INTO central_shadow_candidate_events" in sql:
                    self.connection.pending_candidate = 1
                elif "SELECT document_json FROM central_shadow" in sql:
                    self.result = None
                elif "central_shadow_monitor_state" in sql:
                    self.result = (self.connection.pending_state,)
                elif "central_shadow_candidate_events" in sql:
                    self.result = (self.connection.pending_candidate,)

            def fetchone(self):
                return self.result

        class Connection:
            def __init__(self, committed):
                self.committed = committed
                self.pending_state = committed[0]
                self.pending_candidate = committed[1]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor(self)

            def rollback(self):
                self.pending_state, self.pending_candidate = self.committed

        class Store:
            def __init__(self):
                self.committed = (0, 0)

            def _connect(self):
                return Connection(self.committed)

        self.assertTrue(_exercise_shadow_transaction(
            Store(), "integration-test", datetime(2026, 9, 21, tzinfo=UTC),
        ))

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
