"""Required native PG preservation contract; dedicated owned loopback DB only."""
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import export_preserved_rank_history as export
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from tests.integration.postgres_test_support import isolated_observation_schema
from tests.unit.test_export_preserved_rank_history import NOW, seed_rank_fixture


class PreservedRankHistoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        raw = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not raw:
            raise RuntimeError("required PostgreSQL preservation contract needs an explicit dedicated loopback fixture")
        cls.url = explicit_dedicated_url(raw)
        if urlsplit(cls.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("test-owned loopback PostgreSQL required")

    def setUp(self):
        self.dsn = self.enterContext(isolated_observation_schema(self.url))
        self.store = PostgresQueryStore(self.dsn, now_provider=lambda: NOW)
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.seed = seed_rank_fixture(self.store)
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="preserved-rank-pg-"))
        self.output = Path(self.temporary) / "artifact"

    def preserve(self, output=None):
        return export.export_rank_history(source_instance="fixture-native-pg", postgres_dsn=self.dsn,
                                          output=output or self.output, artifact_now=NOW)

    def test_current_heads_serialize_normalized_source_identity_without_global_hash_reuse(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        gate = Barrier(2)
        key, subject = "2026-10-24T09:40:00", "head-concurrent"
        first, second = {"items": ["X"]}, {"items": ["Y"]}
        observation = ranking_observation(subject, key, first, NOW, source="A")
        def accept(raw_key):
            peer = PostgresQueryStore(self.dsn, now_provider=lambda: NOW)
            try:
                gate.wait()
                peer.save_dataset_snapshot("ranking", subject, raw_key, first, observation=observation)
            finally:
                peer.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept, raw) for raw in (key, " " + key + " ")]
            for future in futures:
                future.result()
        original = self.store.load_observation_revisions("ranking", subject)
        self.assertEqual(1, len(original))
        heads = self.store.load_dataset_snapshots("ranking", subject)
        self.assertEqual({key, " " + key + " "}, {row["snapshot_key"] for row in heads})
        self.assertEqual([first, first], [row["payload"] for row in heads])
        for source, value in (("B", second), ("A", first)):
            self.store.save_dataset_snapshot("ranking", subject, key, value,
                observation=ranking_observation(subject, key, value, NOW, source=source))
        rows = self.store.load_observation_revisions("ranking", subject)
        self.assertEqual(2, len(rows))
        self.assertEqual(original[0], rows[1])
        for value in (second, first):
            self.store.save_dataset_snapshot("ranking", subject, key, value,
                observation=ranking_observation(subject, key, value, NOW, source="A"))
        rows = self.store.load_observation_revisions("ranking", subject)
        self.assertEqual(4, len(rows))
        self.assertNotEqual(rows[0]["revision_id"], original[0]["revision_id"])
        self.assertEqual(rows[1]["revision_id"], rows[0]["revision_of"])
        self.assertEqual(first, self.store.load_dataset_snapshots("ranking", subject, 1)[0]["payload"])

    def test_native_rows_times_jsonb_precision_and_original_reference_states_are_preserved(self):
        expected = []
        with self.store._connect() as connection:
            for table, predicate in zip(export.TABLES, (
                "WHERE kind IN ('ranking','top20_membership') ORDER BY accepted_sequence",
                "WHERE kind IN ('ranking','top20_membership') ORDER BY kind,subject,snapshot_key",
                "WHERE dataset_kind='candidate_set' ORDER BY subject,observation_key",
            )):
                expected.append([json.loads(row[0]) for row in connection.execute(f"SELECT row_to_json(t)::text FROM {table} t {predicate}").fetchall()])
        report = self.preserve()
        for ordinal, filename in enumerate(export.FILES):
            actual = [json.loads(line)["row"] for line in (self.output / filename).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(expected[ordinal], actual)
        self.assertEqual([6, 4, 3], [stream["rows"] for stream in report["streams"]])
        self.assertEqual(7, report["global_visible_accepted_highwater"])
        self.assertEqual(6, report["metrics"]["selects"])
        self.assertEqual(1, report["metrics"]["connections"])
        self.assertEqual(1, report["metrics"]["rollback"])
        self.assertEqual(0, report["metrics"]["sql_commits"])
        self.assertEqual("rollback_then_idle_context_exit", report["metrics"]["native_owner_exit"])
        rows = [json.loads(line)["row"] for line in (self.output / "revisions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(10 ** 40 + 1, rows[0]["payload_json"]["items"][0]["large"])
        self.assertEqual("01", rows[0]["payload_json"]["items"][0]["bigd_rank"])
        self.assertEqual(2, report["verification"]["relationships"]["absent_in_source"])
        self.assertEqual("verified_artifact_only", export.verify_artifact(self.output)["state"])
        text = (self.output / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(self.dsn, text)
        self.assertNotIn("MUST_NOT_EXPORT", "".join((self.output / name).read_text(encoding="utf-8") for name in export.FILES))
        with self.store._connect() as connection:
            for ordinal, (table, predicate) in enumerate(zip(export.TABLES, (
                "WHERE kind IN ('ranking','top20_membership') ORDER BY accepted_sequence",
                "WHERE kind IN ('ranking','top20_membership') ORDER BY kind,subject,snapshot_key",
                "WHERE dataset_kind='candidate_set' ORDER BY subject,observation_key",
            ))):
                self.assertEqual(expected[ordinal], [json.loads(row[0]) for row in connection.execute(f"SELECT row_to_json(t)::text FROM {table} t {predicate}").fetchall()])

    def test_repeatable_native_cut_omits_later_peer_commit_then_fresh_export_sees_it(self):
        original = export._write_stream
        committed = False
        def peer(path, rows, relation):
            nonlocal committed
            if not committed:
                committed = True
                self.store.save_dataset_snapshot("ranking", "peer", "late", {"items": []})
            return original(path, rows, relation)
        with patch.object(export, "_write_stream", side_effect=peer):
            first = self.preserve()
        snapshots = [json.loads(line)["row"] for line in (self.output / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(any(row["subject"] == "peer" for row in snapshots))
        second_output = Path(self.temporary) / "restart"
        second = self.preserve(second_output)
        snapshots = [json.loads(line)["row"] for line in (second_output / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(row["subject"] == "peer" for row in snapshots))
        self.assertEqual(first["streams"][1]["rows"] + 1, second["streams"][1]["rows"])
        self.assertEqual("read_only_repeatable_read", first["source"]["snapshot"]["protocol"])
        subjects = ["Z", "a", "가", "Å"]
        for subject in subjects:
            payload = {"items": []}
            self.store.save_dataset_snapshot("ranking", subject, "mixed", payload,
                observation=ranking_observation(subject, "mixed", payload, NOW, source="kiwoom-ka00198"))
        queries = []
        original_select = export._select
        def recorded(connection, sql, metrics, **kwargs):
            if kwargs.get("data"):
                queries.append(sql)
            yield from original_select(connection, sql, metrics, **kwargs)
        mixed = Path(self.temporary) / "mixed"
        with patch.object(export, "_select", side_effect=recorded):
            self.preserve(mixed)
        for filename in ("snapshots.jsonl", "metadata.jsonl"):
            rows = [json.loads(line)["row"] for line in (mixed / filename).read_text(encoding="utf-8").splitlines()]
            selected = [row["subject"] for row in rows if row["subject"] in subjects]
            self.assertEqual(sorted(subjects), selected)
        self.assertTrue(all('COLLATE "C"' in query.rsplit("ORDER BY", 1)[-1] for query in queries[1:]))

    def test_native_read_only_snapshot_rejects_source_dml_and_closes_after_read_error(self):
        metrics = {}
        with self.assertRaises(self.pg.errors.ReadOnlySqlTransaction):
            with export._source(None, self.dsn, metrics, frozen=False) as connection:
                row = connection.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')").fetchone()
                self.assertEqual(("on", "repeatable read"), row)
                connection.execute("UPDATE central_dataset_snapshots SET saved_at=0")
        self.assertEqual(1, metrics["rollback"])
        before = self.store.load_dataset_snapshots("ranking", "5")
        original = export._select
        def failed(connection, sql, metrics, **kwargs):
            if kwargs.get("data"):
                # Actual native SQL failure, without executing any source write.
                connection.execute("SELECT missing_rank_export_fixture_column")
            yield from original(connection, sql, metrics, **kwargs)
        with patch.object(export, "_select", side_effect=failed):
            with self.assertRaises(self.pg.errors.UndefinedColumn):
                self.preserve()
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue(list(self.output.glob(".staging-*/failure.json")))
        self.assertEqual(before, self.store.load_dataset_snapshots("ranking", "5"))
        self.preserve(Path(self.temporary) / "retry")


if __name__ == "__main__":
    unittest.main()
