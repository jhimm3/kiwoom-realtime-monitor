"""Actual owned PostgreSQL rank-import transactions and allocator recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import import_preserved_rank_history as importer
from scripts import export_preserved_rank_history as exporter
from kiwoom_monitor.central_server import database_rank_import as native
from kiwoom_monitor.central_server.database import PostgresQueryStore, SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from kiwoom_monitor.domain.time_basis import as_kst
from tests.unit.test_import_preserved_rank_history import (
    make_artifact, arguments, sqlite_rows, NUMERIC_JSON, assert_public_rank_document_boundary,
)
from tests.unit.test_export_preserved_rank_history import NOW, seed_rank_fixture
from tests.integration.postgres_test_support import isolated_observation_schema


class ImportPreservedRankHistoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        raw = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not raw:
            raise RuntimeError("required native rank import needs a dedicated loopback PostgreSQL fixture")
        cls.url = explicit_dedicated_url(raw)
        if urlsplit(cls.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("owned loopback database only")

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="rank-import-pg-"))
        self.directory = Path(self.temporary)
        self.source, self.artifact, self.seed = make_artifact(self.directory)
        self.targets = []
        self.addCleanup(self.drop_targets)
        self.args = self.new_arguments()

    def new_arguments(self):
        result = arguments(self.artifact)
        self.targets.append("rank_import_" + result["destination_uuid"])
        return result

    def scoped(self, args=None):
        from psycopg.conninfo import make_conninfo
        return make_conninfo(self.url, options="-c search_path=rank_import_" + (args or self.args)["destination_uuid"] + " -c timezone=Asia/Seoul")

    def drop_targets(self):
        from psycopg import sql
        with self.pg.connect(self.url, autocommit=True) as connection:
            for schema in self.targets:
                self.assertRegex(schema, r"\Arank_import_[0-9a-f]{32}\Z")
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))

    def accept(self, *, create=True, args=None, clock=lambda: NOW):
        return importer.import_artifact(self.artifact, dsn=self.url, create=create, now_provider=clock, **(args or self.args))

    def prepare(self, args=None):
        values = args or self.args
        plan, digest, rows, unchanged = importer.freeze_artifact(self.artifact, postgres=True, **values)
        target = importer.prepare_target(self.artifact, plan=plan, plan_sha256=digest, dsn=self.url, create=True, now_provider=lambda: NOW)
        return plan, digest, target

    def test_v23_sqlite_v1_archive_accepts_on_pg24_without_inventing_history(self):
        from contextlib import closing
        import sqlite3
        from kiwoom_monitor.central_server.central_schema import central_schema_migrations
        from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
        clock = NOW + timedelta(hours=2)
        earlier, later = "2026-10-24T10:00:00+09:00", "2026-10-24T02:00:00+00:00"
        expected_slots = {earlier: "2026-10-24T10:00:00.000000+09:00",
                          later: "2026-10-24T11:00:00.000000+09:00", "unknown-slot": None}
        source = self.directory / "old-v23-pg-bridge.sqlite3"
        with closing(sqlite3.connect(source)) as connection:
            CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations("sqlite")[:23])
            for subject, key, payload in (("5", earlier, '{"items":[10]}'),
                                         ("5", later, '{"items":[11]}'), ("legacy", "unknown-slot", NUMERIC_JSON)):
                connection.execute("INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) VALUES('ranking',?,?,?,?)",
                                   (subject, key, clock.timestamp(), payload))
            for key in (earlier, later):
                connection.execute("INSERT INTO central_market_data_observation_meta(dataset_kind,subject,observation_key,effective_at,available_at,venue,unit,value_kind,completeness,origin,source,candidate_universe) VALUES('candidate_set','5',?,?,?,'COMBINED','count','actual','partial','query','owned-v23-inline','ranking_top20')",
                                   (key, key, clock.isoformat()))
            connection.commit()
            self.assertEqual(23, connection.execute("SELECT MAX(version) FROM central_schema_migrations").fetchone()[0])
            old_columns = {row[1] for row in connection.execute("PRAGMA table_info(central_dataset_snapshots)")}
            self.assertFalse({"revision_id", "slot_order_key"} & old_columns)
        source_before = source.read_bytes()
        artifact = self.directory / "old-v1-to-native-pg24"
        manifest = exporter.export_rank_history(source_instance="rank-import-test-source", database=source,
            output=artifact, artifact_now=clock, sqlite_frozen=True)
        self.assertEqual("preserved-rank-history/v1", manifest["format"])
        self.assertEqual([0, 3, 2], [stream["rows"] for stream in manifest["streams"]])
        artifact_before = {path.name: path.read_bytes() for path in artifact.iterdir() if path.is_file()}
        args = arguments(artifact)
        target_schema = "rank_import_" + args["destination_uuid"]
        self.targets.append(target_schema)
        self.enterContext(patch.object(importer, "central_schema_migrations",
                                      return_value=central_schema_migrations("postgres")[:24]))
        first = importer.import_artifact(artifact, dsn=self.url, create=True, now_provider=lambda: clock, **args)
        self.assertFalse(first["replayed"])
        self.assertEqual("COMPLETE", first["receipt"]["state"])
        self.assertEqual(clock.isoformat(), first["receipt"]["accepted_at"])
        self.assertEqual("preserved-rank-history/v1", first["receipt"]["plan"]["artifact_format"])
        self.assertEqual({"central_observation_revisions": 0, "central_dataset_snapshots": 3,
                          "central_market_data_observation_meta": 2}, first["receipt"]["native_content"]["counts"])
        with self.pg.connect(self.scoped(args)) as connection:
            self.assertEqual(24, connection.execute("SELECT MAX(version) FROM central_schema_migrations").fetchone()[0])
            snapshots = list(connection.execute("SELECT snapshot_key,saved_at,revision_id,slot_order_key,payload_json::text FROM central_dataset_snapshots"))
            self.assertEqual(3, len(snapshots))
            self.assertEqual(expected_slots, {row[0]: row[3] for row in snapshots})
            self.assertTrue(all(row[1] == clock.timestamp() and row[2] is None for row in snapshots))
            numeric = json.loads(next(row[4] for row in snapshots if row[0] == "unknown-slot"), parse_float=Decimal)
            self.assertEqual(json.loads(NUMERIC_JSON, parse_float=Decimal), numeric)
            self.assertEqual(Decimal("0.123456789012345678901234567890"), numeric["decimal"])
            self.assertEqual(10 ** 40 + 1, numeric["huge"])
            self.assertEqual([int, Decimal, bool, type(None)], [type(value) for value in numeric["typed"]])
            metadata = dict((row[0], row[1:]) for row in connection.execute(
                "SELECT observation_key,effective_at,available_at,venue,unit,value_kind,completeness,origin,source,candidate_universe,revision_id FROM central_market_data_observation_meta"))
            self.assertEqual({key: (as_kst(datetime.fromisoformat(key)), clock, "COMBINED", "count", "actual", "partial",
                                   "query", "owned-v23-inline", "ranking_top20", None) for key in (earlier, later)}, metadata)
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM central_observation_revisions").fetchone()[0])
        store = PostgresQueryStore(self.scoped(args), now_provider=lambda: clock)
        self.addCleanup(store.close)
        latest = store.load_dataset_snapshots("ranking", "5", 1)[0]
        self.assertEqual(later, latest["snapshot_key"])
        self.assertEqual({"items": [11]}, latest["payload"])
        self.assertEqual("unknown-slot", store.load_dataset_snapshots("ranking", "legacy", 1)[0]["snapshot_key"])

        def receipt_identity():
            with self.pg.connect(self.scoped(args)) as connection:
                return connection.execute("SELECT xmin::text,document_json FROM central_documents WHERE collection='data_transition_receipts' AND owner=%s AND document_key=%s",
                    (args["destination_uuid"], args["operation_id"])).fetchone()
        original_receipt = receipt_identity()
        statements = []
        def observed(query, connection):
            statements.append(query.as_string(connection) if hasattr(query, "as_string") else
                              query.decode("utf-8") if isinstance(query, bytes) else str(query))
        pg = self.pg
        class TracedCursor(pg.Cursor):
            def execute(cursor, query, *values, **options):
                observed(query, cursor.connection)
                return super().execute(query, *values, **options)
            def executemany(cursor, query, *values, **options):
                observed(query, cursor.connection)
                return super().executemany(query, *values, **options)
        class TracedServerCursor(pg.ServerCursor):
            def execute(cursor, query, *values, **options):
                observed(query, cursor.connection)
                return super().execute(query, *values, **options)
        actual_connect = pg.connect
        def traced_connect(*values, **options):
            self.assertIn("search_path=" + target_schema, options.get("options", ""))
            options["cursor_factory"] = TracedCursor
            connection = actual_connect(*values, **options)
            connection.server_cursor_factory = TracedServerCursor
            return connection
        def no_clock():
            raise AssertionError("v1 replay must not sample a new first receipt clock")
        def replay():
            statements.clear()
            with patch.object(pg, "connect", side_effect=traced_connect):
                return importer.import_artifact(artifact, dsn=self.url, create=False, now_provider=no_clock, **args)
        def assert_read_only_replay():
            self.assertTrue(statements)
            self.assertTrue(any("central_dataset_snapshots" in query for query in statements))
            self.assertFalse(any(re.match(r"^\s*(?:INSERT|UPDATE|DELETE|MERGE|COPY|CREATE|ALTER|DROP|TRUNCATE)\b", query, re.I) for query in statements))
            self.assertFalse(any(re.search(r"\b(?:nextval|setval)\s*\(", query, re.I) for query in statements))
        repeated = replay()
        self.assertTrue(repeated["replayed"])
        self.assertEqual(first["receipt"], repeated["receipt"])
        assert_read_only_replay()
        self.assertEqual(original_receipt, receipt_identity())
        with self.pg.connect(self.scoped(args)) as connection:
            connection.execute("UPDATE central_dataset_snapshots SET slot_order_key=NULL WHERE kind='ranking' AND subject='5' AND snapshot_key=%s", (later,))
        with self.assertRaises(native.PreservedRankImportConflict):
            replay()
        assert_read_only_replay()
        self.assertEqual(original_receipt, receipt_identity())
        self.assertEqual(source_before, source.read_bytes())
        self.assertEqual(artifact_before, {path.name: path.read_bytes() for path in artifact.iterdir() if path.is_file()})

    def test_native_jsonb_decimal_identity_clock_readers_and_allocator_are_preserved(self):
        before_source = self.source.read_bytes()
        before_artifact = {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()}
        expected = sqlite_rows(self.source)
        result = self.accept()
        self.assertEqual(7, result["receipt"]["allocator_floor"])
        self.assertEqual([6, 4, 3], list(result["receipt"]["native_content"]["counts"].values()))
        actual_rows = []
        with self.pg.connect(self.scoped()) as connection:
            for table, order in zip(native.PRESERVED_RANK_COLUMNS,
                    ("accepted_sequence", 'kind COLLATE "C",subject COLLATE "C",snapshot_key COLLATE "C"',
                     'subject COLLATE "C",observation_key COLLATE "C"')):
                actual_rows.append([json.loads(row[0], parse_float=Decimal) for row in connection.execute(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY {order}").fetchall()])
        for source_rows, target_rows in zip(expected, actual_rows):
            self.assertEqual(len(source_rows), len(target_rows))
            for original, target in zip(source_rows, target_rows):
                self.assertEqual(set(original), set(target))
                for key, value in original.items():
                    if key in {"payload_json", "source_ref_json", "quality_flags_json"} and value is not None:
                        self.assertEqual(json.loads(value, parse_float=Decimal), target[key])
                    elif key in {"effective_at", "available_at", "received_at"} and value is not None:
                        self.assertEqual(as_kst(datetime.fromisoformat(value)), as_kst(datetime.fromisoformat(target[key])))
                    elif key == "saved_at":
                        self.assertEqual(value, float(target[key]))
                    else:
                        self.assertEqual(value, target[key])
        legacy = next(row["payload_json"] for row in actual_rows[1] if row["subject"] == "legacy")
        self.assertEqual(Decimal("0.123456789012345678901234567890"), legacy["decimal"])
        self.assertEqual(10 ** 40 + 1, legacy["huge"])
        self.assertIs(type(legacy["typed"][0]), int)
        self.assertIs(type(legacy["typed"][1]), Decimal)
        self.assertIs(type(legacy["typed"][2]), bool)
        replay = self.accept(create=False, clock=lambda: (_ for _ in ()).throw(AssertionError("no new first clock")))
        self.assertEqual(result["receipt"], replay["receipt"])
        self.assertTrue(replay["replayed"])
        store = PostgresQueryStore(self.scoped(), now_provider=lambda: NOW)
        self.addCleanup(store.close)
        assert_public_rank_document_boundary(self, store, result["receipt"], postgres=True)
        self.assertEqual(result["receipt"], self.accept(create=False)["receipt"])
        page = store.load_observation_revision_page(0, ("ranking", "top20_membership"))
        self.assertTrue(page.ready)
        self.assertEqual([row["revision_id"] for row in expected[0]], [row["revision_id"] for row in page.rows])
        self.assertEqual(2, len(store.load_dataset_snapshots("ranking", "5", 5)))
        store.save_dataset_snapshot("ranking", "future", "new", {"items": []},
            observation=ranking_observation("future", "new", {"items": []}, NOW, source="kiwoom-ka00198"))
        with self.pg.connect(self.scoped()) as connection:
            self.assertEqual(8, connection.execute("SELECT MAX(accepted_sequence) FROM central_observation_revisions").fetchone()[0])
        self.assertEqual(before_source, self.source.read_bytes())
        self.assertEqual(before_artifact, {path.name: path.read_bytes() for path in self.artifact.iterdir() if path.is_file()})

    def test_receipt_failure_rolls_back_rows_but_never_rewinds_nontransactional_sequence(self):
        plan, digest, target = self.prepare()
        with self.pg.connect(self.scoped()) as connection:
            connection.execute("CREATE FUNCTION reject_rank_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.collection='data_transition_receipts' THEN RAISE EXCEPTION 'owned receipt fixture fault'; END IF; RETURN NEW; END $$")
            connection.execute("CREATE TRIGGER reject_rank_receipt BEFORE INSERT ON central_documents FOR EACH ROW EXECUTE FUNCTION reject_rank_receipt()")
        with self.assertRaises(self.pg.errors.RaiseException):
            self.accept(create=False)
        with self.pg.connect(self.scoped()) as connection:
            self.assertEqual([0, 0, 0], [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in native.PRESERVED_RANK_COLUMNS])
            self.assertIsNone(connection.execute("SELECT document_json FROM central_documents WHERE collection='data_transition_receipts'").fetchone())
            last, called = connection.execute("SELECT last_value,is_called FROM central_observation_revisions_accepted_sequence_seq").fetchone()
            self.assertTrue(called)
            self.assertGreaterEqual(last, 7)
            with self.assertRaises(native.PreservedRankImportConflict):
                native.preserved_rank_receipt(connection.cursor(), postgres=True, destination_uuid=plan["destination_uuid"],
                    operation_id=plan["operation_id"], plan_sha256=digest)
            connection.execute("DROP TRIGGER reject_rank_receipt ON central_documents")
            connection.execute("DROP FUNCTION reject_rank_receipt()")
        accepted = self.accept(create=False)
        self.assertGreaterEqual(accepted["receipt"]["allocator_floor"], last)
        self.assertEqual(6, accepted["receipt"]["native_content"]["counts"]["central_observation_revisions"])

    def test_native_postgres_source_artifact_bridges_both_empty_native_targets_losslessly(self):
        """Required NAS direction: actual PG source, then source-free PG/SQLite import."""
        from contextlib import closing, contextmanager
        import hashlib
        from psycopg.conninfo import conninfo_to_dict
        source_dsn = self.enterContext(isolated_observation_schema(self.url))
        source = PostgresQueryStore(source_dsn, now_provider=lambda: NOW)
        self.addCleanup(source.close)
        source.initialize()
        seed = seed_rank_fixture(source)
        with source._connect() as connection:
            # Owned legacy JSONB fixture, preserving native decimal tokens rather
            # than inventing provider authenticity or routing them through float.
            connection.execute("UPDATE central_dataset_snapshots SET payload_json=%s::jsonb WHERE kind='ranking' AND subject='legacy'", (NUMERIC_JSON,))
            self.assertEqual(8, connection.execute("SELECT nextval(pg_get_serial_sequence('central_observation_revisions','accepted_sequence'))").fetchone()[0])
        source.save_dataset_snapshot("ranking", "bridge", "after-gap", {"items": [], "gap": True},
            observation=ranking_observation("bridge", "after-gap", {"items": [], "gap": True}, NOW, source="kiwoom-ka00198"))
        selections = ("kind IN ('ranking','top20_membership')", "kind IN ('ranking','top20_membership')", "dataset_kind='candidate_set'")
        ordering = ("accepted_sequence", 'kind COLLATE "C",subject COLLATE "C",snapshot_key COLLATE "C"', 'subject COLLATE "C",observation_key COLLATE "C"')
        def source_state():
            with source._connect() as connection:
                selected, identity = [], {}
                for table, predicate, order in zip(native.PRESERVED_RANK_COLUMNS, selections, ordering):
                    selected.append([json.loads(row[0], parse_float=Decimal) for row in connection.execute(
                        f"SELECT row_to_json(t)::text FROM {table} t WHERE {predicate} ORDER BY {order}").fetchall()])
                    # xmin also detects an UPDATE that writes identical values.
                    identity[table] = list(connection.execute(f"SELECT xmin::text,row_to_json(t)::text FROM {table} t ORDER BY {order}"))
                identity["private_documents"] = list(connection.execute("SELECT xmin::text,row_to_json(t)::text FROM central_documents t ORDER BY collection,owner,document_key"))
                identity["allocator"] = connection.execute("SELECT last_value,is_called FROM central_observation_revisions_accepted_sequence_seq").fetchone()
                return selected, identity
        expected, original_identity = source_state()
        artifact = self.directory / "native-pg-source-artifact"
        snapshot_probe = []
        actual_source = exporter._source
        @contextmanager
        def observed_source(*args, **kwargs):
            with actual_source(*args, **kwargs) as connection:
                snapshot_probe.append(connection.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')").fetchone())
                yield connection
        with patch.object(exporter, "_source", side_effect=observed_source):
            manifest = exporter.export_rank_history(source_instance="owned-native-pg-source-bridge", postgres_dsn=source_dsn,
                output=artifact, artifact_now=NOW)
        self.assertEqual([("on", "repeatable read")], snapshot_probe)
        self.assertEqual(9, manifest["global_visible_accepted_highwater"])
        self.assertEqual([7, 5, 4], [stream["rows"] for stream in manifest["streams"]])
        self.assertEqual(2, manifest["verification"]["sequence"]["unselected_or_unallocated_positions"])
        self.assertEqual(2, manifest["verification"]["relationships"]["absent_in_source"])
        self.assertEqual(0, manifest["metrics"]["sql_commits"])
        artifact_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact.iterdir() if p.is_file()}
        for ordinal, filename in enumerate(exporter.FILES):
            self.assertEqual(expected[ordinal], [json.loads(line, parse_float=Decimal)["row"] for line in (artifact / filename).read_text(encoding="utf-8").splitlines()])
        source_schema_option = conninfo_to_dict(source_dsn)["options"]
        matrix = []
        for dialect in ("postgres", "sqlite"):
            with self.subTest(destination=dialect):
                args = arguments(artifact)
                args["source_instance"] = "owned-native-pg-source-bridge"
                target_schema = "rank_import_" + args["destination_uuid"]
                target_directory = self.directory / "new-sqlite-native-pg-source"
                if dialect == "postgres":
                    self.targets.append(target_schema)
                target = {"dsn": self.url} if dialect == "postgres" else {"directory": target_directory}
                connections = []
                actual_connect = self.pg.connect
                def target_only_connect(*args, **kwargs):
                    options = conninfo_to_dict(args[0]).get("options", "") + kwargs.get("options", "")
                    self.assertNotIn(source_schema_option, options)
                    self.assertIn(target_schema, options)
                    connections.append("owned-target-only")
                    return actual_connect(*args, **kwargs)
                with patch.object(self.pg, "connect", side_effect=target_only_connect):
                    first = importer.import_artifact(artifact, create=True, now_provider=lambda: NOW, **target, **args)
                    again = importer.import_artifact(artifact, create=False, now_provider=lambda: (_ for _ in ()).throw(AssertionError("first receipt clock must not change")), **target, **args)
                self.assertEqual(first["receipt"], again["receipt"])
                self.assertFalse(first["replayed"])
                self.assertTrue(again["replayed"])
                self.assertEqual(9, first["receipt"]["allocator_floor"])
                self.assertEqual([7, 5, 4], list(first["receipt"]["native_content"]["counts"].values()))
                if dialect == "postgres":
                    with self.pg.connect(self.scoped(args)) as connection:
                        actual = [[json.loads(row[0], parse_float=Decimal) for row in connection.execute(
                            f"SELECT row_to_json(t)::text FROM {table} t ORDER BY {order}").fetchall()] for table, order in zip(native.PRESERVED_RANK_COLUMNS, ordering)]
                    target_store = PostgresQueryStore(self.scoped(args), now_provider=lambda: NOW)
                else:
                    actual = sqlite_rows(target_directory / "rank.sqlite3")
                    target_store = SQLiteQueryStore(target_directory / "rank.sqlite3", now_provider=lambda: NOW)
                with closing(target_store):
                    for originals, copies in zip(expected, actual):
                        self.assertEqual(len(originals), len(copies))
                        for original, copy in zip(originals, copies):
                            self.assertEqual(set(original), set(copy))
                            for key, value in original.items():
                                other = copy[key]
                                if key in {"payload_json", "source_ref_json", "quality_flags_json"} and value is not None:
                                    self.assertEqual(value, json.loads(other, parse_float=Decimal) if dialect == "sqlite" else other)
                                elif key in {"effective_at", "available_at", "received_at"} and value is not None:
                                    if dialect == "sqlite":
                                        self.assertEqual(value, other)  # Original artifact representation retained.
                                    self.assertEqual(as_kst(datetime.fromisoformat(value)), as_kst(datetime.fromisoformat(other)))
                                elif key == "saved_at":
                                    self.assertEqual(float(value), float(other))
                                else:
                                    self.assertIs(type(value), type(other))
                                    self.assertEqual(value, other)
                    legacy = next(row["payload_json"] for row in actual[1] if row["subject"] == "legacy")
                    legacy = json.loads(legacy, parse_float=Decimal) if dialect == "sqlite" else legacy
                    self.assertEqual(Decimal("0.123456789012345678901234567890"), legacy["decimal"])
                    self.assertEqual(10 ** 40 + 1, legacy["huge"])
                    self.assertEqual([int, Decimal, bool, type(None)], [type(value) for value in legacy["typed"]])
                    page = target_store.load_observation_revision_page(0, ("ranking", "top20_membership"))
                    self.assertTrue(page.ready)
                    self.assertEqual([row["revision_id"] for row in expected[0]], [row["revision_id"] for row in page.rows])
                    self.assertEqual([1, 2, 3, 4, 5, 6, 9], [row["accepted_sequence"] for row in page.rows])
                    native_payload = next(row["payload"] for row in target_store.load_dataset_snapshots("ranking", "5") if row["snapshot_key"] == seed["old"])
                    self.assertEqual(seed["b"], native_payload)
                    target_store.save_dataset_snapshot("ranking", "new", "next", {"items": []},
                        observation=ranking_observation("new", "next", {"items": []}, NOW, source="kiwoom-ka00198"))
                    next_page = target_store.load_observation_revision_page(9, ("ranking",))
                    self.assertEqual([10], [row["accepted_sequence"] for row in next_page.rows])
                matrix.append({"destination": dialect, "counts": [len(rows) for rows in actual], "global_visible_highwater": 9,
                    "selected_sequences": [1,2,3,4,5,6,9], "next_native_sequence": 10, "first_receipt_same_on_resume": True,
                    "origin_connections_during_import": 0, "PG_target_connections_create_plus_resume": len(connections), "native_reader": "PASS", "numeric_precision": "lossless raw native JSON readback; existing normal readers retain their own codec"})
        self.assertEqual(original_identity, source_state()[1])
        self.assertEqual(artifact_before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact.iterdir() if p.is_file()})
        if output := os.environ.get("RANK_IMPORT_BRIDGE_EVIDENCE"):
            Path(output).write_text(json.dumps({"state": "actual_owned_PG_source_bridge_PASS", "source_readonly_probe": snapshot_probe,
                "source_export_metrics": manifest["metrics"], "snapshot": manifest["source"]["snapshot"], "source_global_highwater": 9,
                "reference_meanings": manifest["verification"], "source_row_xmin_and_values_allocator_unchanged": True,
                "artifact_digests_unchanged": True, "matrix": matrix}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_concurrent_same_operation_has_one_native_first_receipt_and_no_duplicate_rows(self):
        self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda offset: self.accept(create=False, clock=lambda: NOW + timedelta(seconds=offset)), (0, 1)))
        self.assertEqual(results[0]["receipt"], results[1]["receipt"])
        self.assertEqual([False, True], sorted(result["replayed"] for result in results))
        with self.pg.connect(self.scoped()) as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM central_documents WHERE collection='data_transition_receipts'").fetchone()[0])
            self.assertEqual([6, 4, 3], [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in native.PRESERVED_RANK_COLUMNS])
        changed = {**self.args, "operation_id": "competing-operation"}
        with self.assertRaises(native.PreservedRankImportConflict):
            self.accept(create=False, args=changed)

    def test_real_commit_ack_loss_and_cancel_after_reservation_keep_native_recovery(self):
        lost = False
        events = []
        pg = self.pg
        class LostACK(pg.Connection):
            def commit(connection):
                nonlocal lost
                pending = False
                if connection.info.transaction_status.name == "INTRANS":
                    pending = connection.execute("SELECT 1 FROM central_documents WHERE collection='data_transition_receipts' LIMIT 1").fetchone() is not None
                super().commit()
                if pending and not lost:
                    lost = True
                    events.append("ACK_LOST_AFTER_COMMIT")
                    connection.close()  # Native commit survives; rollback cannot run.
                    raise OSError("owned actual commit ACK fixture")
            def close(connection):
                events.append("OWNER_CLOSED")
                super().close()
        def connect(*args, **kwargs):
            events.append("OPEN")
            return LostACK.connect(*args, **kwargs)
        with patch.object(pg, "connect", side_effect=connect):
            result = self.accept()
        self.assertTrue(result["recovered_after_ack_loss"])
        position = events.index("ACK_LOST_AFTER_COMMIT")
        self.assertLess(events.index("OWNER_CLOSED", position), events.index("OPEN", position))
        self.assertEqual(result["receipt"], self.accept(create=False)["receipt"])
        self.args = self.new_arguments()
        self.prepare()
        original = native._rank_insert_document
        def cancelled(cursor, pg, collection, owner, key, value, at):
            if collection == "data_transition_receipts":
                raise KeyboardInterrupt()
            return original(cursor, pg, collection, owner, key, value, at)
        with patch.object(native, "_rank_insert_document", side_effect=cancelled):
            with self.assertRaises(KeyboardInterrupt):
                self.accept(create=False)
        with pg.connect(self.scoped()) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM central_observation_revisions").fetchone()[0])
            self.assertIsNone(connection.execute("SELECT document_json FROM central_documents WHERE collection='data_transition_receipts'").fetchone())
            self.assertGreaterEqual(connection.execute("SELECT last_value FROM central_observation_revisions_accepted_sequence_seq").fetchone()[0], 7)
        self.assertEqual("COMPLETE", self.accept(create=False)["receipt"]["state"])


if __name__ == "__main__":
    unittest.main()
