"""Offline single-artifact native acceptance; no operating source or startup."""
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from scripts import export_preserved_rank_history as export
from scripts import import_preserved_rank_history as importer
from kiwoom_monitor.central_server import database_rank_import as native
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
from tests.unit.test_export_preserved_rank_history import NOW, seed_rank_fixture

ROOT = Path(__file__).resolve().parents[2]
NUMERIC_JSON = '{"items":[],"decimal":0.123456789012345678901234567890,"huge":10000000000000000000000000000000000000001,"typed":[1,1.0,true,null]}'


def make_artifact(directory, *, many=False):
    source = directory / "source.sqlite3"
    store = SQLiteQueryStore(source, now_provider=lambda: NOW)
    store.initialize()
    seed = seed_rank_fixture(store)
    later = SQLiteQueryStore(source, now_provider=lambda: NOW + timedelta(minutes=1))
    later.save_dataset_snapshot("ranking", "5", seed["old"], seed["b"], observation=ranking_observation(
        "5", seed["old"], seed["b"], NOW + timedelta(minutes=1), source="kiwoom-ka00198"))
    # Test-owned legacy TEXT evidence has exact decimal tokens that a Python
    # float JSON codec cannot reproduce. No provider authenticity is inferred.
    with store._connection() as connection:
        connection.execute("UPDATE central_dataset_snapshots SET payload_json=? WHERE kind='ranking' AND subject='legacy'", (NUMERIC_JSON,))
    if many:
        values = []
        for ordinal in range(260):
            key = f"batch-{ordinal:04}"
            payload = {"items": [], "ordinal": ordinal}
            values.append(("ranking", "batch", key, payload,
                ranking_observation("batch", key, payload, NOW, source="kiwoom-ka00198")))
        store.save_dataset_snapshots(values)
    artifact = directory / "artifact"
    export.export_rank_history(source_instance="rank-import-test-source", database=source,
                               output=artifact, artifact_now=NOW, sqlite_frozen=True)
    later.close()
    store.close()
    return source, artifact, seed


def arguments(artifact, destination_uuid=None):
    return {"source_instance": "rank-import-test-source",
            "manifest_sha256": hashlib.sha256((artifact / "manifest.json").read_bytes()).hexdigest(),
            "destination_uuid": destination_uuid or uuid.uuid4().hex, "operation_id": "rank-import-operation"}


def sqlite_rows(path):
    values = []
    with closing(sqlite3.connect(path)) as connection:
        for table, columns in native.PRESERVED_RANK_COLUMNS_V3.items():
            order = ("accepted_sequence" if table == "central_observation_revisions" else
                     "kind COLLATE BINARY,subject COLLATE BINARY,snapshot_key COLLATE BINARY" if table == "central_dataset_snapshots" else
                     "subject COLLATE BINARY,observation_key COLLATE BINARY")
            selection = ("dataset_kind='candidate_set'" if table == "central_market_data_observation_meta"
                         else "kind IN ('ranking','top20_membership')")
            values.append([dict(zip(columns, row)) for row in connection.execute(
                f"SELECT {','.join(columns)} FROM {table} WHERE {selection} ORDER BY {order}")])
    return values


def assert_public_rank_document_boundary(test, store, first_receipt, *, postgres=False):
    """One oracle reused with both real completed native target fixtures."""
    owner, key = first_receipt["destination_uuid"], first_receipt["operation_id"]
    before_receipt = store.load_document("data_transition_receipts", owner, key)["document"]
    before_target = store.load_document("preserved_rank_targets", owner, "target")["document"]
    test.assertEqual(first_receipt, before_receipt)  # Generic reads remain public.
    acquisition = "_connect" if postgres else "_connection"
    changed = {**first_receipt, "accepted_at": (NOW + timedelta(seconds=1)).isoformat()}
    for collection in ("preserved_rank_targets", "data_transition_receipts"):
        values = [{"owner": owner, "key": key, "document": changed}]
        # Fail on actual acquisition if validation attempts I/O for either backend.
        with patch.object(store, acquisition, side_effect=AssertionError("reserved namespace must reject before I/O")):
            for operation, args in ((store.upsert_documents, (collection, values)),
                                    (store.replace_documents, (collection, values)),
                                    (store.insert_immutable_document, (collection, owner, "new-key", changed))):
                with test.assertRaises(ValueError):
                    operation(*args)
    ordinary = "rank_import_ordinary_document_fixture"
    store.upsert_documents(ordinary, [{"owner": owner, "key": "mutable", "document": {"value": 1}}])
    store.replace_documents(ordinary, [{"owner": owner, "key": "mutable", "document": {"value": 2}}])
    test.assertTrue(store.insert_immutable_document(ordinary, owner, "immutable", {"value": 3}))
    test.assertEqual({"value": 2}, store.load_document(ordinary, owner, "mutable")["document"])
    test.assertEqual({"value": 3}, store.load_document(ordinary, owner, "immutable")["document"])
    test.assertEqual(before_receipt, store.load_document("data_transition_receipts", owner, key)["document"])
    test.assertEqual(before_target, store.load_document("preserved_rank_targets", owner, "target")["document"])


class ImportPreservedRankHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="rank-import-unit-"))
        self.directory = Path(self.temporary)
        self.source, self.artifact, self.seed = make_artifact(self.directory)
        self.args = arguments(self.artifact)
        self.target = self.directory / "new-offline-target"

    def accept(self, *, create=True, now_provider=lambda: NOW):
        return importer.import_artifact(self.artifact, directory=self.target, create=create,
                                        now_provider=now_provider, **self.args)

    def prepared(self):
        plan, digest, rows, unchanged = importer.freeze_artifact(self.artifact, postgres=False, **self.args)
        target = importer.prepare_target(self.artifact, plan=plan, plan_sha256=digest,
                                         directory=self.target, create=True, now_provider=lambda: NOW)
        return plan, digest, target

    def test_v23_inline_original_still_exports_v1_and_imports_without_fake_history(self):
        from kiwoom_monitor.central_server.central_schema import central_schema_migrations
        from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
        source = self.directory / "old-v23.sqlite3"
        earlier, later = "2026-10-24T10:00:00+09:00", "2026-10-24T02:00:00+00:00"
        expected_slots = {earlier: "2026-10-24T10:00:00.000000+09:00", later: "2026-10-24T11:00:00.000000+09:00", "unknown-slot": None}
        with closing(sqlite3.connect(source)) as connection:
            CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations("sqlite")[:23])
            for subject, key, payload in (("legacy", "unknown-slot", NUMERIC_JSON),
                                         ("5", earlier, '{"items":[10]}'), ("5", later, '{"items":[11]}')):
                connection.execute("INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) VALUES('ranking',?,?,1.25,?)", (subject, key, payload))
            connection.commit()
        artifact = self.directory / "old-v1-artifact"
        report = export.export_rank_history(source_instance="rank-import-test-source", database=source,
                                             output=artifact, artifact_now=NOW, sqlite_frozen=True)
        self.assertEqual("preserved-rank-history/v1", report["format"])
        args = arguments(artifact)
        target = self.directory / "old-v1-new-native-target"
        first = importer.import_artifact(artifact, directory=target, create=True, now_provider=lambda: NOW, **args)["receipt"]
        target_path = target / "rank.sqlite3"
        with closing(sqlite3.connect(target_path)) as connection:
            self.assertEqual((NUMERIC_JSON, None), connection.execute("SELECT payload_json,revision_id FROM central_dataset_snapshots WHERE subject='legacy'").fetchone())
            self.assertEqual(expected_slots, dict(connection.execute("SELECT snapshot_key,slot_order_key FROM central_dataset_snapshots")))
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM central_observation_revisions").fetchone()[0])
        with closing(SQLiteQueryStore(target_path)) as store:
            self.assertEqual(later, store.load_dataset_snapshots("ranking", "5", 1)[0]["snapshot_key"])

        statements = []
        actual_connect = sqlite3.connect
        def traced(*args, **kwargs):
            connection = actual_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        def no_clock():
            raise AssertionError("receipt replay must not sample a new first clock")
        def resume():
            statements.clear()
            with patch.object(importer.sqlite3, "connect", side_effect=traced):
                return importer.import_artifact(artifact, directory=target, create=False, now_provider=no_clock, **args)
        replay = resume()
        self.assertTrue(replay["replayed"])
        self.assertEqual(first, replay["receipt"])
        self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for query in statements))
        for key, corrupted in ((later, None), (later, expected_slots[earlier]),
                               ("unknown-slot", expected_slots[later])):
            with self.subTest(key=key, corrupted=corrupted):
                with closing(actual_connect(target_path)) as connection:
                    connection.execute("UPDATE central_dataset_snapshots SET slot_order_key=? WHERE snapshot_key=?", (corrupted, key))
                    connection.commit()
                with self.assertRaises(native.PreservedRankImportConflict):
                    resume()
                self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for query in statements))
                with closing(actual_connect(target_path)) as connection:
                    stored = json.loads(connection.execute("SELECT document_json FROM central_documents WHERE collection='data_transition_receipts'").fetchone()[0])
                    self.assertEqual(first, stored)
                    connection.execute("UPDATE central_dataset_snapshots SET slot_order_key=? WHERE snapshot_key=?", (expected_slots[key], key))
                    connection.commit()

    def test_three_native_relations_exact_first_receipt_and_readers_after_reopen(self):
        before_source = self.source.read_bytes()
        artifact_bytes = {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()}
        expected = sqlite_rows(self.source)
        result = self.accept()
        receipt = result["receipt"]
        self.assertFalse(result["replayed"])
        self.assertEqual("COMPLETE", receipt["state"])
        self.assertFalse(receipt["runtime_admission"])
        self.assertEqual([6, 4, 3], list(receipt["native_content"]["counts"].values()))
        self.assertEqual(7, receipt["allocator_floor"])
        self.assertEqual(expected, sqlite_rows(self.target / "rank.sqlite3"))
        store = SQLiteQueryStore(self.target / "rank.sqlite3", now_provider=lambda: NOW)
        self.addCleanup(store.close)
        page = store.load_observation_revision_page(0, ("ranking", "top20_membership"))
        self.assertEqual([row["revision_id"] for row in expected[0]], [row["revision_id"] for row in page.rows])
        snapshot = store.load_dataset_snapshots("ranking", "5", 5)
        self.assertEqual(2, len(snapshot))
        self.assertEqual(self.seed["b"], next(row["payload"] for row in snapshot if row["snapshot_key"] == self.seed["old"]))
        metadata = store.load_market_data_metadata(MarketDatasetKind.CANDIDATE_SET, "5", self.seed["old"])
        self.assertEqual(NOW + timedelta(minutes=1), metadata.available_at)
        legacy = store.load_dataset_snapshots("ranking", "legacy")[0]
        self.assertEqual(10 ** 40 + 1, legacy["payload"]["huge"])
        self.assertEqual(before_source, self.source.read_bytes())
        self.assertEqual(artifact_bytes, {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()})
        store.save_dataset_snapshot("ranking", "future", "new", {"items": []},
            observation=ranking_observation("future", "new", {"items": []}, NOW, source="kiwoom-ka00198"))
        with closing(sqlite3.connect(self.target / "rank.sqlite3")) as connection:
            self.assertEqual(8, connection.execute("SELECT MAX(accepted_sequence) FROM central_observation_revisions").fetchone()[0])

    def test_identical_resume_has_no_data_writes_and_never_resamples_first_clock(self):
        first = self.accept()["receipt"]
        statements, connections = [], []
        actual_connect = sqlite3.connect
        def traced(*args, **kwargs):
            connections.append(args[0])
            connection = actual_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        def no_clock():
            raise AssertionError("replay must return the original receipt without clock sampling")
        with patch.object(importer.sqlite3, "connect", side_effect=traced):
            replay = self.accept(create=False, now_provider=no_clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first, replay["receipt"])
        self.assertEqual(1, len(connections))
        self.assertTrue(all(str(self.source) not in value for value in connections))
        self.assertFalse(any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for statement in statements))

    def test_public_document_writes_cannot_mutate_native_receipt_or_target_descriptor(self):
        first = self.accept()["receipt"]
        with closing(SQLiteQueryStore(self.target / "rank.sqlite3", now_provider=lambda: NOW)) as store:
            assert_public_rank_document_boundary(self, store, first)
        self.assertEqual(first, self.accept(create=False)["receipt"])

    def test_existing_or_wrong_owner_source_and_plan_are_rejected_before_adoption(self):
        self.target.mkdir()
        sentinel = self.target / "rank.sqlite3"
        sentinel.write_bytes(b"existing user material")
        with self.assertRaises((FileExistsError, native.PreservedRankImportConflict)):
            self.accept()
        with self.assertRaises(native.PreservedRankImportConflict):
            self.accept(create=False)
        self.assertEqual(b"existing user material", sentinel.read_bytes())
        other = {**self.args, "source_instance": "wrong-source"}
        with self.assertRaises(native.PreservedRankImportConflict):
            importer.import_artifact(self.artifact, directory=self.directory / "wrong", create=True, **other)
        self.assertFalse((self.directory / "wrong").exists())
        self.target = self.directory / "owned"
        self.accept()
        changed = {**self.args, "operation_id": "another-operation"}
        with self.assertRaises(native.PreservedRankImportConflict):
            importer.import_artifact(self.artifact, directory=self.target, create=False, **changed)

    def test_native_receipt_failure_rolls_back_all_payload_and_sqlite_sequence_then_resumes(self):
        plan, digest, target = self.prepared()
        with closing(sqlite3.connect(self.target / "rank.sqlite3")) as connection:
            connection.execute("CREATE TRIGGER reject_rank_receipt BEFORE INSERT ON central_documents "
                "WHEN NEW.collection='data_transition_receipts' BEGIN SELECT RAISE(ABORT,'receipt fixture fault'); END")
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.accept(create=False)
        with closing(sqlite3.connect(self.target / "rank.sqlite3")) as connection:
            self.assertEqual([0, 0, 0], [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in native.PRESERVED_RANK_COLUMNS])
            self.assertIsNone(connection.execute("SELECT document_json FROM central_documents WHERE collection='data_transition_receipts'").fetchone())
            self.assertIsNone(connection.execute("SELECT seq FROM sqlite_sequence WHERE name='central_observation_revisions'").fetchone())
            with self.assertRaises(native.PreservedRankImportConflict):
                native.preserved_rank_receipt(connection.cursor(), postgres=False, destination_uuid=plan["destination_uuid"],
                    operation_id=plan["operation_id"], plan_sha256=digest)
            connection.execute("DROP TRIGGER reject_rank_receipt")
            connection.commit()
        result = self.accept(create=False)
        self.assertEqual(7, result["receipt"]["allocator_floor"])

    def test_cancel_after_first_native_batch_leaves_incomplete_empty_target_and_can_resume(self):
        extra = self.directory / "many"
        extra.mkdir()
        _, self.artifact, _ = make_artifact(extra, many=True)
        self.args = arguments(self.artifact)
        self.prepared()
        original = importer.freeze_artifact
        observed = []
        def cancel(*args, **kwargs):
            plan, digest, rows, unchanged = original(*args, **kwargs)
            def partial(table):
                for ordinal, row in enumerate(rows(table)):
                    if table == "central_observation_revisions" and ordinal == 257:
                        observed.append("after-one-256-row-native-batch")
                        raise KeyboardInterrupt()
                    yield row
            return plan, digest, partial, unchanged
        with patch.object(importer, "freeze_artifact", side_effect=cancel):
            with self.assertRaises(KeyboardInterrupt):
                self.accept(create=False)
        self.assertEqual(["after-one-256-row-native-batch"], observed)
        self.assertEqual([[], [], []], sqlite_rows(self.target / "rank.sqlite3"))
        self.assertEqual("COMPLETE", self.accept(create=False)["receipt"]["state"])

    def test_commit_ack_loss_closes_first_owner_then_returns_exact_native_first_receipt(self):
        events = []
        actual_connect = sqlite3.connect
        lost = False
        class LostACK(sqlite3.Connection):
            def commit(connection):
                nonlocal lost
                pending = connection.execute("SELECT COUNT(*) FROM central_documents WHERE collection='data_transition_receipts'").fetchone()[0]
                super().commit()
                if pending and not lost:
                    lost = True
                    events.append("COMMITTED_ACK_LOST")
                    connection.close()  # Actual commit + lost connection; rollback fails.
                    raise OSError("injected ACK loss after real native commit")
            def close(connection):
                events.append("OWNER_CLOSED")
                super().close()
        def connect(*args, **kwargs):
            events.append("OPEN")
            return actual_connect(*args, factory=LostACK, **kwargs)
        with patch.object(importer.sqlite3, "connect", side_effect=connect):
            result = self.accept()
        self.assertTrue(result["recovered_after_ack_loss"])
        self.assertTrue(result["replayed"])
        lost_at = events.index("COMMITTED_ACK_LOST")
        self.assertLess(events.index("OWNER_CLOSED", lost_at), events.index("OPEN", lost_at))
        self.assertEqual(result["receipt"], self.accept(create=False)["receipt"])

    def test_target_mutation_and_artifact_mutation_fail_without_rewriting_first_receipt(self):
        receipt = self.accept()["receipt"]
        with closing(sqlite3.connect(self.target / "rank.sqlite3")) as connection:
            connection.execute("UPDATE central_dataset_snapshots SET saved_at=0 WHERE subject='legacy'")
            connection.commit()
        with self.assertRaises(native.PreservedRankImportConflict):
            self.accept(create=False)
        with closing(sqlite3.connect(self.target / "rank.sqlite3")) as connection:
            original = json.loads(connection.execute("SELECT document_json FROM central_documents WHERE collection='data_transition_receipts'").fetchone()[0])
            self.assertEqual(receipt, original)
        self.target = self.directory / "mutated-input"
        original_freeze = importer.freeze_artifact
        def mutate(*args, **kwargs):
            result = original_freeze(*args, **kwargs)
            with (self.artifact / "snapshots.jsonl").open("ab") as stream:
                stream.write(b"\n")
            return result
        with patch.object(importer, "freeze_artifact", side_effect=mutate):
            with self.assertRaises(native.PreservedRankImportConflict):
                self.accept()
        self.assertEqual([[], [], []], sqlite_rows(self.target / "rank.sqlite3"))

    def test_unknown_extra_source_column_rejects_before_creating_target(self):
        import shutil
        rejection = "v3 current-head schema is incomplete or unsupported"
        original_artifact = {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()}
        # The actual v3 source schema is rejected before any complete artifact
        # or target can be published. Retained staging diagnostics are incomplete.
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("ALTER TABLE central_dataset_snapshots ADD COLUMN unknown_old_column TEXT DEFAULT 'preserve-me'")
            connection.commit()
        source_before = self.source.read_bytes()
        rejected_export = self.directory / "extra-columns-export"
        with self.assertRaisesRegex(export.PreservationError, rejection):
            export.export_rank_history(source_instance="rank-import-test-source", database=self.source,
                                       output=rejected_export, artifact_now=NOW, sqlite_frozen=True)
        self.assertEqual(source_before, self.source.read_bytes())
        for filename in (*export.FILES, "prepared-manifest.json", "manifest.json"):
            self.assertFalse((rejected_export / filename).exists(), filename)
        self.assertFalse(self.target.exists())

        # Independently present the importer with a self-consistent, test-owned
        # raw v3 artifact carrying the unknown column; no exporter/verifier mock.
        artifact = self.directory / "extra-columns-artifact"
        shutil.copytree(self.artifact, artifact)
        self.assertEqual("verified_artifact_only", export.verify_artifact(artifact)["state"])
        manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("preserved-rank-history/v3", manifest["format"])
        schema = manifest["source"]["schema"]
        position = max(index for index, row in enumerate(schema) if row[0] == "central_dataset_snapshots") + 1
        schema.insert(position, ["central_dataset_snapshots", "unknown_old_column", "TEXT", 0, 0])
        manifest["source"]["schema_sha256"] = hashlib.sha256(export._json(schema).encode("utf-8")).hexdigest()
        stream = artifact / "snapshots.jsonl"
        records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
        for record in records:
            record["row"]["unknown_old_column"] = "preserve-me"
        changed = "".join(export._json(record) + "\n" for record in records).encode("utf-8")
        stream.write_bytes(changed)
        manifest["streams"][1].update(rows=len(records), bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest())
        encoded = export._json(manifest)
        for filename in ("prepared-manifest.json", "manifest.json"):
            (artifact / filename).write_text(encoded, encoding="utf-8")
        # These independent digests exclude a checksum/row-shape accident as
        # the rejection oracle. The precise failure must be unsupported schema.
        self.assertEqual(manifest["source"]["schema_sha256"], hashlib.sha256(export._json(schema).encode("utf-8")).hexdigest())
        self.assertEqual(manifest["streams"][1]["sha256"], hashlib.sha256(stream.read_bytes()).hexdigest())
        self.assertEqual(manifest["streams"][1]["rows"], len(records))
        with self.assertRaisesRegex(export.PreservationError, rejection):
            importer.import_artifact(artifact, directory=self.target, create=True, **arguments(artifact))
        self.assertFalse(self.target.exists())
        self.assertEqual(original_artifact, {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()})
        self.assertEqual(source_before, self.source.read_bytes())

    def test_public_cli_create_and_exact_resume_and_decimal_codec(self):
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT)))}
        base = [sys.executable, "-m", "scripts.import_preserved_rank_history", str(self.artifact),
            "--source-instance", self.args["source_instance"], "--manifest-sha256", self.args["manifest_sha256"],
            "--destination-uuid", self.args["destination_uuid"], "--operation-id", self.args["operation_id"],
            "--sqlite-target-directory", str(self.target)]
        first = subprocess.run([*base, "--create-offline-target"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, first.returncode, first.stderr + first.stdout)
        second = subprocess.run([*base, "--resume-owned-target"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, second.returncode, second.stderr + second.stdout)
        self.assertEqual(json.loads(first.stdout)["receipt"], json.loads(second.stdout)["receipt"])
        self.assertTrue(json.loads(second.stdout)["replayed"])
        self.assertEqual('{"n":0.123456789012345678901234567890}', importer._lossless({"n": Decimal("0.123456789012345678901234567890")}))


if __name__ == "__main__":
    unittest.main()
