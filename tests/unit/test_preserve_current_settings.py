from __future__ import annotations

from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import preserve_current_settings as operator
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupError
from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import SQLiteSnapshotContractError

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=KST)


class PreservedCurrentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite3"
        self.artifact = self.root / "artifact"
        self.target = self.root / "target"
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.executescript(
                "CREATE TABLE settings(key PRIMARY KEY,value);"
                "CREATE TABLE column_settings(column_name PRIMARY KEY,visible,position,width);"
                "CREATE TABLE central_setting_versions(setting_key PRIMARY KEY,updated_at);"
                "CREATE TABLE central_column_setting_versions(column_name PRIMARY KEY,updated_at);"
            )
            connection.executemany("INSERT INTO settings VALUES(?,?)", [
                ("rank_query_type", "5"), ("dynamic:not-in-defaults", b"\x00raw\xff"),
                ("null-value", None), ("integer-value", 9223372036854775807),
                ("json-original", '{ "value": 2, "enabled":true }'),
                ("pending-theme", "PINNED-NOT-TRANSFERRED"),
                ("unclassified-secret", "PRIVATE-NOT-IN-OUTPUT"),
            ])
            connection.executemany("INSERT INTO column_settings VALUES(?,?,?,?)", [
                ("rank", 1, 0, 0), ("unknown-column", "0", 1, 137.5)])
            connection.executemany("INSERT INTO central_setting_versions VALUES(?,?)", [
                ("rank_query_type", "2026-10-20T03:00:00Z"), ("orphan", b"original-version")])
            connection.executemany("INSERT INTO central_column_setting_versions VALUES(?,?)", [
                ("rank", "2026-10-20T12:00:00+09:00"), ("orphan-column", None)])

    def capture(self, **kwargs):
        return operator.capture_settings(database=self.source, source_instance="fixture-source",
                                         source_owner="original-desktop", output=self.artifact,
                                         artifact_now=NOW, **kwargs)

    def selection(self, *, include_orphans=False):
        manifest, records, digest = operator.verified_settings_rows(self.artifact)
        entries = []
        for record_id, record in records.items():
            name = operator.decode_value(record["row"][0])
            relation = record["relation"]
            if name == "pending-theme":
                entries.append(dict(record_id=record_id, placement="REFERENCE_PENDING"))
            elif name == "unclassified-secret":
                entries.append(dict(record_id=record_id, placement="UNRESOLVED"))
            elif relation in ("settings", "column_settings") or include_orphans:
                entries.append(dict(record_id=record_id, placement="TRANSFER", target_role="query-local"))
        return dict(format="current-settings-selection/v1", manifest_sha256=digest,
                    source_instance=manifest["source_instance"], target_role="query-local", records=entries)

    def accept(self, selection=None, **kwargs):
        return operator.accept_settings(artifact=self.artifact, selection=selection or self.selection(),
                                        target=self.target, operation_id="fixture-operation",
                                        target_id="fixture-target", receipt_now=NOW, **kwargs)

    def test_lossless_raw_capture_selection_and_actual_repository_consumers_after_reopen(self):
        before = self.source.read_bytes()
        with patch("kiwoom_monitor.infrastructure.persistence.database.Database.initialize", side_effect=AssertionError("source initialization")), patch("kiwoom_monitor.infrastructure.persistence.theme_repository.ThemeRepository.__init__", side_effect=AssertionError("source theme mutation")):
            report = self.capture(sqlite_frozen=True)
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual((13, 7, 2), (report["records"], report["missing_versions"], report["orphan_versions"]))
        with patch.object(operator, "_clock", return_value=NOW.astimezone(timezone.utc).isoformat()):
            result = self.accept()
        self.assertEqual(NOW.isoformat(), result["receipt"]["accepted_at"])
        self.assertFalse(result["overall_P2_complete"])
        self.assertFalse(result["order_authority"])
        self.assertEqual("OFFLINE_UNBOUND", result["runtime_state"])
        self.assertEqual(dict(captured=13, selected=9, unselected=4, unresolved=4, reference_pending=1), result["receipt"]["counts"])
        path = self.target / "offline.sqlite3"
        source_reader, target_reader = SettingsRepository(self.source), SettingsRepository(path)
        for key in ("rank_query_type", "dynamic:not-in-defaults", "null-value", "integer-value", "json-original"):
            self.assertEqual(source_reader.get(key), target_reader.get(key))
        self.assertEqual(ColumnSettingsRepository(self.source).list(), ColumnSettingsRepository(path).list())
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([( "rank_query_type", "2026-10-20T03:00:00Z")],
                             connection.execute("SELECT * FROM central_setting_versions").fetchall())
            self.assertEqual([], connection.execute("SELECT * FROM settings WHERE key='pending-theme'").fetchall())
            self.assertEqual(("blob",), connection.execute("SELECT typeof(value) FROM settings WHERE key='dynamic:not-in-defaults'").fetchone())
            self.assertEqual(("null",), connection.execute("SELECT typeof(value) FROM settings WHERE key='null-value'").fetchone())
            self.assertEqual(("integer",), connection.execute("SELECT typeof(value) FROM settings WHERE key='integer-value'").fetchone())
        self.assertFalse((self.target / operator.OWNER_FILE).exists())

    def test_missing_and_orphan_versions_are_not_created_or_silently_dropped(self):
        self.capture()
        result = self.accept(self.selection(include_orphans=True))
        with closing(sqlite3.connect(self.target / "offline.sqlite3")) as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM central_setting_versions").fetchone()[0])
            self.assertEqual([(None,)], connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='orphan-column'").fetchall())
            self.assertEqual([], connection.execute("SELECT * FROM central_setting_versions WHERE setting_key='null-value'").fetchall())
        self.assertEqual(11, result["receipt"]["counts"]["selected"])

    def test_identical_retry_keeps_first_receipt_without_clock_or_native_writes_and_conflicts_preserve_it(self):
        self.capture()
        first = self.accept()
        with patch.object(operator, "_clock", side_effect=AssertionError("retry must not sample clock")):
            repeated = self.accept()
        self.assertTrue(repeated["replayed"])
        self.assertEqual(first["receipt"], repeated["receipt"])
        self.assertEqual((0, 0, 1), (repeated["metrics"]["data_insert_rows"], repeated["metrics"]["commit"], repeated["metrics"]["rollback"]))
        changed = self.selection(include_orphans=True)
        with self.assertRaises(SettingsBackupError):
            self.accept(changed)
        with closing(sqlite3.connect(self.target / "offline.sqlite3")) as connection:
            self.assertEqual(first["receipt"], json.loads(connection.execute("SELECT receipt_json FROM settings_transition_receipts").fetchone()[0]))

    def test_commit_ack_loss_recovers_original_receipt_and_same_operation(self):
        self.capture()
        native = sqlite3.connect
        class LostAck(sqlite3.Connection):
            def commit(connection):
                super().commit()
                raise OSError("native acknowledgment lost")
        with patch("kiwoom_monitor.infrastructure.persistence.settings_backup.sqlite3.connect",
                   side_effect=lambda *a, **k: native(*a, **k, factory=LostAck)):
            with self.assertRaises(OSError):
                self.accept()
        with closing(native(self.target / "offline.sqlite3")) as connection:
            original = json.loads(connection.execute("SELECT receipt_json FROM settings_transition_receipts").fetchone()[0])
        with patch.object(operator, "_clock", side_effect=AssertionError("no new receipt")):
            result = self.accept()
        self.assertTrue(result["replayed"])
        self.assertEqual(original, result["receipt"])
        self.assertFalse((self.target / operator.OWNER_FILE).exists())

    def test_native_failure_and_cancellation_roll_back_data_schema_and_receipt_then_resume_owned_target(self):
        self.capture()
        native = sqlite3.connect
        for error in (sqlite3.OperationalError("injected write failure"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                class FailedWrite(sqlite3.Connection):
                    def executemany(connection, sql, values):
                        if "central_setting_versions" in sql:
                            raise error
                        return super().executemany(sql, values)
                with patch("kiwoom_monitor.infrastructure.persistence.settings_backup.sqlite3.connect",
                           side_effect=lambda *a, **k: native(*a, **k, factory=FailedWrite)):
                    with self.assertRaises(type(error)):
                        self.accept()
                with closing(native(self.target / "offline.sqlite3")) as connection:
                    self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
                self.assertTrue((self.target / operator.OWNER_FILE).is_file())
        self.assertFalse(self.accept()["replayed"])

    def test_foreign_target_source_mismatch_tampering_and_version_placement_never_adopt_or_accept(self):
        self.capture()
        self.target.mkdir()
        path = self.target / "offline.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE foreign_data(value)")
        before = path.read_bytes()
        with self.assertRaises(SettingsBackupError):
            self.accept()
        self.assertEqual(before, path.read_bytes())
        selection = self.selection()
        selection["source_instance"] = "another-source"
        with self.assertRaises(operator.SettingsPreservationError):
            operator.select_rows(self.artifact, selection)
        selection = self.selection()
        selection["records"].append(dict(record_id="central_setting_versions:0", placement="UNRESOLVED"))
        with self.assertRaises(operator.SettingsPreservationError):
            operator.select_rows(self.artifact, selection)
        stream = self.artifact / "settings.jsonl"
        stream.write_bytes(stream.read_bytes() + b"{}\n")
        with self.assertRaises(operator.SettingsPreservationError):
            operator.verify_artifact(self.artifact)

    def test_live_wal_fixed_snapshot_readonly_SQL_counts_and_source_side_effects(self):
        native = sqlite3.connect
        peer = native(self.source)
        self.addCleanup(peer.close)
        peer.execute("PRAGMA journal_mode=WAL")
        peer.execute("UPDATE settings SET value='5' WHERE key='rank_query_type'")
        peer.commit()
        files = [self.source, Path(str(self.source)+"-wal"), Path(str(self.source)+"-shm")]
        before = [path.read_bytes() for path in files]
        statements = []
        class Trace(sqlite3.Connection):
            def __init__(connection, *a, **k):
                super().__init__(*a, **k)
                connection.set_trace_callback(statements.append)
        with patch("kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot.sqlite3.connect",
                   side_effect=lambda *a, **k: native(*a, **k, factory=Trace)):
            result = self.capture()
        self.assertEqual(before, [path.read_bytes() for path in files])
        self.assertEqual((1, 5, 4, 1, 1, 0), tuple(result["metrics"][key] for key in
                         ("connections", "selects", "data_selects", "begin", "rollback", "checkpoints")))
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "COMMIT")) for sql in statements))
        self.assertEqual(1, sum(sql == "BEGIN" for sql in statements))
        self.assertEqual(1, sum(sql == "ROLLBACK" for sql in statements))

    def test_peer_commit_after_source_snapshot_and_partial_cancel_are_not_published_as_complete(self):
        peer = sqlite3.connect(self.source)
        self.addCleanup(peer.close)
        peer.execute("PRAGMA journal_mode=WAL")
        peer.execute("UPDATE settings SET value='5' WHERE key='rank_query_type'")
        peer.commit()
        original = operator.encode_value
        committed = False
        def late(value):
            nonlocal committed
            if not committed:
                peer.execute("INSERT INTO settings VALUES('peer-late','later')")
                peer.commit()
                committed = True
            return original(value)
        with patch.object(operator, "encode_value", side_effect=late):
            self.capture()
        _, records, _ = operator.verified_settings_rows(self.artifact)
        self.assertFalse(any(operator.decode_value(row["row"][0]) == "peer-late" for row in records.values()))
        cancelled = self.root / "cancelled"
        with patch.object(operator, "encode_value", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                operator.capture_settings(database=self.source, source_instance="fixture", source_owner="owner", output=cancelled)
        self.assertFalse(cancelled.exists())
        partial = next(self.root.glob("cancelled.partial-*"))
        self.assertFalse((partial / "manifest.json").exists())
        self.assertEqual("KeyboardInterrupt", json.loads((partial / "failure.json").read_text())["error_type"])

    def test_closed_wal_requires_explicit_frozen_and_native_open_error_type_survives(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        self.assertFalse(Path(str(self.source)+"-wal").exists())
        with self.assertRaises(SQLiteSnapshotContractError):
            self.capture()
        self.assertFalse(self.artifact.exists())
        self.capture(sqlite_frozen=True)
        source = self.source
        with patch("kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot.sqlite3.connect",
                   side_effect=sqlite3.OperationalError("native error")):
            with self.assertRaises(sqlite3.OperationalError):
                operator.capture_settings(database=source, source_instance="fixture", source_owner="owner", output=self.root/"native-error", sqlite_frozen=True)

    def test_empty_missing_tables_primitive_codec_and_cli_summary_do_not_expose_raw_values(self):
        for value in (None, b"\x00\xff", "한글", 2**63-1, -(2**63), -0.0, float("inf")):
            restored = operator.decode_value(operator.encode_value(value))
            self.assertIs(type(value), type(restored))
            self.assertEqual(value.hex() if type(value) is float else value,
                             restored.hex() if type(restored) is float else restored)
        with self.assertRaises(operator.SettingsPreservationError):
            operator.encode_value(float("nan"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, operator.main(["capture", "--database", str(self.source), "--source-instance", "fixture", "--source-owner", "owner", "--output", str(self.artifact)]))
        self.assertNotIn("PRIVATE-NOT-IN-OUTPUT", out.getvalue())
        self.assertNotIn("unclassified-secret", out.getvalue())
        self.assertNotIn(str(self.source), out.getvalue())
        empty = self.root / "empty.sqlite3"
        with closing(sqlite3.connect(empty)):
            pass
        result = operator.capture_settings(database=empty, source_instance="empty", source_owner="owner", output=self.root/"empty-artifact")
        self.assertEqual(0, result["records"])
        self.assertEqual(0, operator.verify_artifact(self.root/"empty-artifact")["records"])

    def test_public_cli_capture_verify_accept_and_retry_exit_zero_without_sensitive_output(self):
        workspace = Path(__file__).resolve().parents[2]
        def run(*arguments):
            result = subprocess.run([sys.executable, "-m", "scripts.preserve_current_settings", *arguments],
                                    cwd=workspace, capture_output=True, text=True, timeout=180)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("PRIVATE-NOT-IN-OUTPUT", result.stdout + result.stderr)
            self.assertNotIn(str(self.source), result.stdout)
            return json.loads(result.stdout)
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        run("capture", "--database", str(self.source), "--source-instance", "fixture-source", "--source-owner", "original-desktop", "--output", str(self.artifact), "--sqlite-frozen")
        self.assertEqual("verified_artifact_only", run("verify", "--artifact", str(self.artifact))["state"])
        selected = self.root / "selection.json"
        selected.write_text(json.dumps(self.selection()), encoding="utf-8")
        arguments = ("accept", "--artifact", str(self.artifact), "--selection", str(selected), "--target", str(self.target), "--operation-id", "fixture-operation", "--target-id", "fixture-target")
        first, retry = run(*arguments), run(*arguments)
        self.assertEqual(first["receipt"], retry["receipt"])
        self.assertTrue(retry["replayed"])
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_absent_version_relations_remain_missing_without_default_or_clock_regeneration(self):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("DROP TABLE central_setting_versions")
            connection.execute("DROP TABLE central_column_setting_versions")
        result = self.capture()
        self.assertEqual((9, 9, 0), (result["records"], result["missing_versions"], result["orphan_versions"]))
        self.accept()
        with closing(sqlite3.connect(self.target / "offline.sqlite3")) as connection:
            self.assertEqual([], connection.execute("SELECT * FROM central_setting_versions").fetchall())
            self.assertEqual([], connection.execute("SELECT * FROM central_column_setting_versions").fetchall())


class PreservedCurrentStateArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "raw-source.sqlite3"
        self.artifact = self.root / "current-state-artifact"
        # Current native PK/NOCASE/FK layout, without normalizing Repository or
        # backup constructors. FK enforcement stays off to exercise old gaps.
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value);
                CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible,position,width);
                CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at);
                CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at);
                CREATE TABLE theme_profiles(profile_id INTEGER PRIMARY KEY,profile_name TEXT NOT NULL COLLATE NOCASE UNIQUE);
                CREATE TABLE profile_themes(profile_id INTEGER NOT NULL,theme_name TEXT NOT NULL COLLATE NOCASE,
                    default_color TEXT NOT NULL,PRIMARY KEY(profile_id,theme_name),
                    FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE);
                CREATE TABLE profile_stock_themes(profile_id INTEGER NOT NULL,stock_code TEXT NOT NULL,
                    theme_name TEXT NOT NULL COLLATE NOCASE,custom_color TEXT,PRIMARY KEY(profile_id,stock_code,theme_name),
                    FOREIGN KEY(profile_id,theme_name) REFERENCES profile_themes(profile_id,theme_name) ON DELETE CASCADE);
                CREATE TABLE profile_theme_name_decisions(profile_id INTEGER NOT NULL,decision_kind TEXT NOT NULL,
                    source_name TEXT NOT NULL COLLATE NOCASE,target_name TEXT NOT NULL COLLATE NOCASE,
                    decision_source TEXT NOT NULL,updated_at TEXT NOT NULL,
                    PRIMARY KEY(profile_id,decision_kind,source_name,target_name),
                    FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE);
                CREATE TABLE profile_theme_suggestions(profile_id INTEGER NOT NULL,stock_code TEXT NOT NULL,
                    news_identity TEXT NOT NULL,raw_theme_name TEXT NOT NULL COLLATE NOCASE,evidence TEXT NOT NULL,
                    confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 100),provider TEXT NOT NULL,model TEXT NOT NULL,
                    body_hash TEXT NOT NULL,analyzed_at TEXT NOT NULL,status TEXT NOT NULL,reviewed_theme_names TEXT NOT NULL,
                    reviewed_at TEXT,article_title TEXT NOT NULL,article_published_at TEXT NOT NULL,article_url TEXT NOT NULL,
                    PRIMARY KEY(profile_id,stock_code,news_identity,raw_theme_name),
                    FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE);
                CREATE TABLE stocks(code TEXT PRIMARY KEY,name TEXT NOT NULL,market TEXT NOT NULL,updated_at TEXT NOT NULL,last_price REAL);
                CREATE TABLE stock_aliases(alias TEXT PRIMARY KEY,stock_code TEXT NOT NULL,
                    FOREIGN KEY(stock_code) REFERENCES stocks(code));
            """)
            connection.executemany("INSERT INTO settings VALUES(?,?)", [
                ("theme_active_profile", "uSeR"), ("unrelated-secret", "PRIVATE-SETTING-RAW"),
                ("central_theme_sync_v1", '{"prepared":"UNFINISHED-RAW-NOT-CLOSED"}'),
                ("rank_query_type", "5")])
            connection.execute("INSERT INTO column_settings VALUES(?,?,?,?)", ("rank", 1, 0, 137.5))
            connection.executemany("INSERT INTO central_setting_versions VALUES(?,?)", [
                ("theme_active_profile", b"original-version-\x00"), ("rank_query_type", "2020-01-02T01:02:03Z")])
            connection.execute("INSERT INTO central_column_setting_versions VALUES(?,?)",
                               ("rank", "2020-01-02T10:02:03+09:00"))
            connection.executemany("INSERT INTO theme_profiles VALUES(?,?)", [(7, "User"), (9, "Other")])
            connection.executemany("INSERT INTO profile_themes VALUES(?,?,?)", [
                (7, " AI ", b"\x00raw-color\xff"), (7, "ß", "#123456"), (7, "SS", "#654321"), (9, "Other", "#000000")])
            connection.executemany("INSERT INTO profile_stock_themes VALUES(?,?,?,?)", [
                (7, "000001", " ai ", None), (7, "000001", "ss", ""), (9, "000002", "Other", "#111111")])
            connection.execute("INSERT INTO profile_theme_name_decisions VALUES(?,?,?,?,?,?)",
                               (7, "alias", " SOURCE ", " AI ", b"original-decision", "2020-01-02T01:02:03Z"))
            connection.execute("INSERT INTO profile_theme_suggestions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (7, "000001", "PRIVATE-NEWS-ID", " AI ", " PRIVATE-EVIDENCE ", 88.5,
                                "provider", "model", b"raw-body-hash", "2020-01-02T01:02:03Z", "approved",
                                '[ " AI " ]', None, "원 기사", "2020-01-02T10:02:00+09:00", "https://example.invalid/original"))
            connection.executemany("INSERT INTO stocks VALUES(?,?,?,?,?)", [
                ("000001", "원 종목", "KOSPI", "2020-01-01T00:00:00Z", 1.25),
                ("000002", "다른 종목", "KOSDAQ", "2020-01-01T09:00:00+09:00", 2.5),
                ("999999", "UNREFERENCED-CACHE", "", "2020-01-01", 999)])
            connection.executemany("INSERT INTO stock_aliases VALUES(?,?)", [
                (" PRIVATE-ALIAS ", "000001"), ("OtherAlias", "000002")])

    def capture(self, **kwargs):
        frozen = kwargs.pop("sqlite_frozen", True)
        return operator.capture_current_state(database=self.source, source_instance="fixture-source",
            source_owner="fixture-owner", operation_id="fixture-cut", output=self.artifact,
            sqlite_frozen=frozen, artifact_now=NOW, **kwargs)

    def selection(self):
        manifest, records, digest = operator.verified_current_state_rows(self.artifact)
        entries = []
        for key, item in records.items():
            table = item["relation"]
            row = tuple(operator.decode_value(value) for value in item["row"])
            chosen = (row[0] == 7 if table.startswith("profile_") or table == "theme_profiles"
                      else row[0] == "000001" if table == "stocks"
                      else row[0] in {"theme_active_profile", "rank_query_type"} if table == "settings"
                      else row[1] == "000001" if table == "stock_aliases" else True)
            placement = "TRANSFER" if chosen else "REFERENCE_PENDING" if table == "settings" and row[0] == "central_theme_sync_v1" else "RETAIN_SOURCE"
            entries.append(dict(record_id=key, placement=placement,
                                **({"target_role": "query-local"} if chosen else {})))
        return dict(format="current-state-selection/v1", source_instance="fixture-source",
                    source_owner="fixture-owner", operation_id="fixture-cut", manifest_sha256=digest,
                    target_role="query-local", records=entries)

    def test_raw_cut_selection_preserves_native_identity_text_types_and_minimum_catalog_without_target(self):
        before = self.source.read_bytes()
        original = operator.readonly_sqlite_snapshot
        with patch.object(operator, "readonly_sqlite_snapshot", wraps=original) as snapshot, \
             patch("kiwoom_monitor.infrastructure.persistence.theme_backup.ThemeBackupService.export_document",
                   side_effect=AssertionError("normalized export is not raw")):
            report = self.capture()
        self.assertEqual(1, snapshot.call_count)
        self.assertEqual(before, self.source.read_bytes())
        manifest, records, digest = operator.verified_current_state_rows(self.artifact)
        self.assertEqual(NOW.isoformat(), manifest["artifact_at"])
        self.assertEqual((1, 1, 1, 0, 0), tuple(manifest["metrics"][key] for key in (
            "connections", "begin", "rollback", "source_dml", "sql_commits")))
        self.assertEqual(11, manifest["metrics"]["data_selects"])
        result = operator.select_current_state_rows(self.artifact, self.selection())
        self.assertEqual("CLOSED", result["closure"]["state"])
        self.assertEqual([(7, "User")], result["rows"]["theme_profiles"])
        self.assertEqual([("theme_active_profile", "uSeR"), ("rank_query_type", "5")], result["rows"]["settings"])
        self.assertEqual([("rank", 1, 0, 137.5)], result["rows"]["column_settings"])
        self.assertEqual([("theme_active_profile", b"original-version-\x00"), ("rank_query_type", "2020-01-02T01:02:03Z")],
                         result["rows"]["central_setting_versions"])
        self.assertEqual([("rank", "2020-01-02T10:02:03+09:00")], result["rows"]["central_column_setting_versions"])
        self.assertEqual([(7, " AI ", b"\x00raw-color\xff"), (7, "ß", "#123456"), (7, "SS", "#654321")],
                         result["rows"]["profile_themes"])
        self.assertEqual([(7, "000001", " ai ", None), (7, "000001", "ss", "")], result["rows"]["profile_stock_themes"])
        self.assertEqual(88.5, result["rows"]["profile_theme_suggestions"][0][5])
        self.assertEqual('[ " AI " ]', result["rows"]["profile_theme_suggestions"][0][11])
        self.assertIsNone(result["rows"]["profile_theme_suggestions"][0][12])
        self.assertEqual([("000001", "원 종목", "KOSPI", "2020-01-01T00:00:00Z")], result["rows"]["stocks"])
        self.assertEqual([(" PRIVATE-ALIAS ", "000001")], result["rows"]["stock_aliases"])
        raw = b"".join(path.read_bytes() for path in self.artifact.glob("*.jsonl"))
        self.assertIn(b"PRIVATE-SETTING-RAW", raw)
        self.assertIn(b"UNFINISHED-RAW-NOT-CLOSED", raw)
        self.assertNotIn(b"UNREFERENCED-CACHE", raw)
        self.assertEqual(1, result["binding"]["reference_pending"])
        self.assertFalse(result["installed"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["order_authority"])
        self.assertEqual("UNVERIFIED", result["source_authenticity"])
        self.assertEqual("NOT_EVALUATED", result["consumer_status"])
        self.assertEqual(digest, result["binding"]["manifest_sha256"])
        # ASCII SQLite NOCASE matches ss to SS; Unicode casefold must not match ß.
        ss = next(key for key, item in records.items() if item["relation"] == "profile_themes" and item["row"][1] == ["text", "SS"])
        member = next(key for key, item in records.items() if item["relation"] == "profile_stock_themes" and item["row"][2] == ["text", "ss"])
        self.assertEqual([ss], next(ref["candidates"] for ref in result["closure"]["references"]
                                    if ref["record_id"] == member and ref["reference"] == "theme"))
        self.assertEqual("verified_current_state_artifact_only", report["state"])
        self.assertEqual({"settings", "column_settings", "central_setting_versions", "central_column_setting_versions"}, set(operator.TABLES))

    def test_single_snapshot_settings_and_theme_cut_excludes_peer_commit_without_duplicate_active_read(self):
        native = sqlite3.connect
        statements, changed = [], []
        with closing(native(self.source)) as keeper, closing(native(self.source)) as peer:
            keeper.execute("PRAGMA journal_mode=WAL")
            keeper.execute("INSERT INTO settings VALUES('barrier-anchor','private')")
            keeper.commit()
            class Barrier(sqlite3.Connection):
                def execute(connection, sql, parameters=()):
                    cursor = super().execute(sql, parameters)
                    statements.append(sql)
                    if sql.startswith("SELECT profile_id,profile_name FROM theme_profiles") and not changed:
                        peer.execute("UPDATE settings SET value='4' WHERE key='rank_query_type'")
                        peer.execute("UPDATE settings SET value='Later' WHERE key='theme_active_profile'")
                        peer.execute("UPDATE column_settings SET width=999.5 WHERE column_name='rank'")
                        peer.execute("UPDATE central_setting_versions SET updated_at='later-version'")
                        peer.execute("UPDATE theme_profiles SET profile_name='Later' WHERE profile_id=7")
                        peer.execute("UPDATE profile_themes SET default_color='#peer' WHERE profile_id=7 AND theme_name=' AI '")
                        peer.commit()
                        changed.append(True)
                    return cursor
            with patch("kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot.sqlite3.connect",
                       side_effect=lambda *args, **kwargs: native(*args, **kwargs, factory=Barrier)):
                self.capture(sqlite_frozen=False)
            self.assertEqual([True], changed)
            result = operator.select_current_state_rows(self.artifact, self.selection())
            self.assertEqual("CLOSED", result["closure"]["state"])
            self.assertEqual([("theme_active_profile", "uSeR"), ("rank_query_type", "5")], result["rows"]["settings"])
            self.assertEqual([("rank", 1, 0, 137.5)], result["rows"]["column_settings"])
            self.assertEqual([(7, "User")], result["rows"]["theme_profiles"])
            self.assertEqual(b"\x00raw-color\xff", result["rows"]["profile_themes"][0][2])
            self.assertEqual(b"original-version-\x00", result["rows"]["central_setting_versions"][0][1])
            self.assertEqual(("4",), keeper.execute("SELECT value FROM settings WHERE key='rank_query_type'").fetchone())
            self.assertEqual(("Later",), keeper.execute("SELECT profile_name FROM theme_profiles WHERE profile_id=7").fetchone())
        self.assertEqual(1, sum(sql.startswith("SELECT key,value FROM settings ") for sql in statements))
        self.assertEqual(1, sum(sql.startswith("SELECT setting_key,updated_at FROM central_setting_versions ") for sql in statements))
        self.assertEqual(1, sum(sql.startswith("SELECT profile_id,profile_name FROM theme_profiles ") for sql in statements))
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for sql in statements))

    def test_selection_requires_explicit_parents_catalog_and_existing_original_version_without_selecting_all_profiles(self):
        self.capture()
        selection = self.selection()
        for prefix in ("theme_profiles:", "profile_themes:", "stocks:", "central_setting_versions:", "central_column_setting_versions:"):
            attempt = json.loads(json.dumps(selection))
            for entry in attempt["records"]:
                if entry["record_id"].startswith(prefix):
                    entry["placement"] = "REFERENCE_PENDING"
            result = operator.select_current_state_rows(self.artifact, attempt)
            self.assertEqual("INCOMPLETE", result["closure"]["state"])
            self.assertGreater(result["closure"]["summary"]["states"]["unselected_reference"], 0)
        result = operator.select_current_state_rows(self.artifact, selection)
        self.assertEqual("CLOSED", result["closure"]["state"])
        self.assertEqual([(7, "User")], result["rows"]["theme_profiles"])
        self.assertGreater(result["binding"]["unselected"], 0)

    def test_source_missing_parent_absent_relation_unknown_types_and_original_missing_version_are_distinct(self):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("DELETE FROM central_setting_versions")
            connection.execute("DELETE FROM profile_themes WHERE profile_id=7 AND theme_name=' AI '")
            connection.execute("UPDATE settings SET value=? WHERE key='theme_active_profile'", (b"User",))
            connection.execute("DROP TABLE theme_profiles")
        self.capture()
        result = operator.select_current_state_rows(self.artifact, self.selection())
        states = result["closure"]["summary"]["states"]
        for state in ("source_relation_absent", "missing_in_source", "unknown_reference_type"):
            self.assertGreater(states[state], 0)
        self.assertEqual("INCOMPLETE", result["closure"]["state"])
        original = next(item for item in result["closure"]["references"] if item["reference"] == "original_version")
        self.assertEqual("missing_in_source", original["state"])
        self.assertFalse(original["blocking"])
        self.assertEqual([], result["rows"]["central_setting_versions"])

    def test_absent_original_version_stays_missing_without_default_or_new_clock(self):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("DROP TABLE central_setting_versions")
        self.capture()
        with patch.object(operator, "_clock", side_effect=AssertionError("selection cannot sample time")):
            result = operator.select_current_state_rows(self.artifact, self.selection())
        self.assertEqual("CLOSED", result["closure"]["state"])
        self.assertEqual([], result["rows"]["central_setting_versions"])
        version = next(item for item in result["closure"]["references"] if item["reference"] == "original_version")
        self.assertEqual("source_relation_absent", version["state"])
        self.assertFalse(version["blocking"])

    def test_reopen_exact_operation_is_source_free_and_conflicts_never_rewrite_first_cut(self):
        first = self.capture()
        before = {path.name: path.read_bytes() for path in self.artifact.iterdir()}
        with patch.object(operator, "readonly_sqlite_snapshot", side_effect=AssertionError("no source on replay")), \
             patch.object(operator, "_clock", side_effect=AssertionError("no new clock")):
            second = operator.capture_current_state(database=self.root / "does-not-exist", source_instance="fixture-source",
                source_owner="fixture-owner", operation_id="fixture-cut", output=self.artifact)
            self.assertTrue(second["replayed"])
            self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
            with self.assertRaises(operator.SettingsPreservationError):
                operator.capture_current_state(database=self.source, source_instance="wrong-source",
                    source_owner="fixture-owner", operation_id="fixture-cut", output=self.artifact)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.artifact.iterdir()})

    def test_selection_wrong_binding_duplicate_or_unknown_records_and_wrong_role_never_return_target_ready(self):
        self.capture()
        original = self.selection()
        for field in ("format", "manifest_sha256", "source_instance", "source_owner", "operation_id"):
            attempt = json.loads(json.dumps(original))
            attempt[field] = "wrong"
            with self.subTest(field=field), self.assertRaises(operator.SettingsPreservationError):
                operator.select_current_state_rows(self.artifact, attempt)
        for records in ([original["records"][0], original["records"][0]],
                        [dict(record_id="missing:0", placement="TRANSFER", target_role="query-local")],
                        [dict(record_id=original["records"][0]["record_id"], placement="TRANSFER", target_role="another-role")]):
            with self.assertRaises(operator.SettingsPreservationError):
                operator.select_current_state_rows(self.artifact, {**original, "records": records})

    def test_stream_tampering_unknown_layout_and_settings_v1_cannot_be_misread_as_current_state(self):
        self.capture()
        copied = self.root / "tampered"
        shutil.copytree(self.artifact, copied)
        with (copied / "profile_themes.jsonl").open("ab") as stream:
            stream.write(b'{}\n')
        with self.assertRaises(operator.SettingsPreservationError):
            operator.verify_current_state_artifact(copied)
        with self.assertRaises(operator.SettingsPreservationError):
            operator.verify_artifact(self.artifact)
        settings_only = self.root / "settings-only"
        operator.capture_settings(database=self.source, source_instance="fixture-source", source_owner="fixture-owner",
                                  output=settings_only, sqlite_frozen=True, artifact_now=NOW)
        with self.assertRaises(operator.SettingsPreservationError):
            operator.verify_current_state_artifact(settings_only)
        wrong_contract = self.root / "old-raw9-contract"
        shutil.copytree(self.artifact, wrong_contract)
        manifest_path = wrong_contract / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["format"] = "preserved-current-theme-state/v1"
        manifest_path.unlink()  # Break only this owned copied hard link before changing the fixture.
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(operator.SettingsPreservationError):
            operator.verify_current_state_artifact(wrong_contract)
        before = self.source.read_bytes()
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("ALTER TABLE profile_themes ADD COLUMN future_material TEXT")
        with self.assertRaises(operator.SettingsPreservationError):
            operator.capture_current_state(database=self.source, source_instance="fixture-source", source_owner="fixture-owner",
                operation_id="unknown-layout", output=self.root / "unknown-layout", sqlite_frozen=True, artifact_now=NOW)
        self.assertFalse((self.root / "unknown-layout" / "manifest.json").exists())
        self.assertNotEqual(before, self.source.read_bytes())  # Only explicit fixture DDL changed it.

    def test_interruption_and_publication_failure_preserve_incomplete_cut_without_false_complete_or_overwrite(self):
        native = operator.encode_value
        calls = 0
        def interrupted(value):
            nonlocal calls
            calls += 1
            if calls == 12:
                raise KeyboardInterrupt()
            return native(value)
        before = self.source.read_bytes()
        with patch.object(operator, "encode_value", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.capture()
        partial = next(self.root.glob("current-state-artifact.partial-*"))
        self.assertFalse((partial / "manifest.json").exists())
        self.assertEqual("KeyboardInterrupt", json.loads((partial / "failure.json").read_text())["error_type"])
        self.capture()
        self.assertTrue(partial.is_dir())
        other = self.root / "publication-failed"
        with patch.object(operator.os, "link", side_effect=OSError("fixture publication failure")), self.assertRaises(OSError):
            operator.capture_current_state(database=self.source, source_instance="fixture-source", source_owner="fixture-owner",
                operation_id="failed-cut", output=other, sqlite_frozen=True, artifact_now=NOW)
        failed = {path.name: path.read_bytes() for path in other.iterdir()}
        self.assertNotIn("manifest.json", failed)
        with self.assertRaises(FileNotFoundError):
            operator.capture_current_state(database=self.source, source_instance="fixture-source", source_owner="fixture-owner",
                operation_id="failed-cut", output=other, sqlite_frozen=True, artifact_now=NOW)
        self.assertEqual(failed, {path.name: path.read_bytes() for path in other.iterdir()})
        self.assertEqual(before, self.source.read_bytes())

    def test_public_current_state_cli_emits_only_safe_summary_and_never_accepts_or_installs(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, operator.main(["capture-state", "--database", str(self.source),
                "--source-instance", "fixture-source", "--source-owner", "fixture-owner", "--operation-id", "fixture-cut",
                "--output", str(self.artifact), "--sqlite-frozen"]))
            self.assertEqual(0, operator.main(["verify-state", "--artifact", str(self.artifact)]))
            selection = self.root / "current-state-selection.json"
            selection.write_text(json.dumps(self.selection()), encoding="utf-8")
            self.assertEqual(0, operator.main(["select-state", "--artifact", str(self.artifact), "--selection", str(selection)]))
        reports = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual("CLOSED", reports[-1]["closure_state"])
        self.assertFalse(reports[-1]["installed"])
        self.assertFalse(reports[-1]["applied"])
        self.assertEqual("UNVERIFIED", reports[-1]["source_authenticity"])
        self.assertEqual("NOT_EVALUATED", reports[-1]["consumer_status"])
        for private in ("PRIVATE-NEWS-ID", "PRIVATE-EVIDENCE", "PRIVATE-ALIAS", "PRIVATE-SETTING-RAW", "UNFINISHED-RAW-NOT-CLOSED", str(self.source)):
            self.assertNotIn(private, out.getvalue())

    def test_explicit_full_installer_preserves_raw_cut_consumers_and_first_receipt_on_same_plan_replay(self):
        from kiwoom_monitor.infrastructure.persistence.database import Database

        self.capture()
        original_selection = self.selection()
        original_plan = json.dumps(original_selection, sort_keys=True)
        full_selection = {**original_selection, "target_layout": "full"}
        selected = operator.select_current_state_rows(self.artifact, full_selection)
        self.assertEqual("full", selected["target_layout"])
        self.assertEqual("CLOSED", selected["closure"]["state"])
        selection_sha256 = hashlib.sha256(json.dumps(full_selection, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(selection_sha256, selected["binding"]["selection_sha256"])
        source_before = self.source.read_bytes()
        artifact_before = {path.name: path.read_bytes() for path in self.artifact.iterdir()}
        target = self.root / "full-current.sqlite3"
        arguments = dict(artifact=self.artifact, selection=full_selection, target=target,
                         operation_id="fixture-full-install", target_id="fixture-full-target", layout="full")
        with patch.object(Database, "initialize_query_role", side_effect=AssertionError("full must use its native owner")):
            first = operator.install_current_state(**arguments, receipt_now=NOW)
        self.assertEqual("full_role_initialized_unbound", first["state"])
        self.assertEqual("full", first["layout"])
        self.assertEqual("SELECTED_USER_STATE", first["seed_kind"])
        self.assertEqual("OFFLINE_UNBOUND", first["runtime_state"])
        self.assertEqual("UNVERIFIED", first["source_authenticity"])
        self.assertEqual("NOT_EVALUATED", first["consumer_status"])
        self.assertFalse(first["replayed"])
        for field in ("applied", "role_ready", "order_authority"):
            self.assertFalse(first[field])
        receipt = first["receipt"]
        self.assertEqual("full-role-current-state-receipt/v1", receipt["format"])
        self.assertEqual(NOW.isoformat(), receipt["accepted_at"])
        self.assertEqual(selected["binding"], receipt["plan"]["binding"])
        self.assertEqual(selected["source_layouts"], receipt["plan"]["source_layouts"])
        self.assertEqual(selected["source_relations"], receipt["plan"]["source_relations"])
        self.assertEqual({table: len(rows) for table, rows in selected["rows"].items()},
                         receipt["plan"]["relation_counts"])
        self.assertEqual(first["seed_sha256"], receipt["plan"]["seed_sha256"])
        self.assertRegex(receipt["plan"]["fresh_defaults_sha256"], r"^[0-9a-f]{64}$")
        with closing(sqlite3.connect(target)) as connection:
            for table, columns in operator.CURRENT_STATE_TABLES.items():
                actual = connection.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
                typed_actual = [tuple((type(value), value) for value in row) for row in actual]
                for row in selected["rows"][table]:
                    with self.subTest(relation=table):
                        self.assertIn(tuple((type(value), value) for value in row), typed_actual)
            self.assertEqual([(88.5, "real", b"raw-body-hash", "blob", None, "null")],
                connection.execute("SELECT confidence,typeof(confidence),body_hash,typeof(body_hash),"
                                   "reviewed_at,typeof(reviewed_at) FROM profile_theme_suggestions").fetchall())
            self.assertEqual([(b"original-version-\x00", "blob")], connection.execute(
                "SELECT updated_at,typeof(updated_at) FROM central_setting_versions "
                "WHERE setting_key='theme_active_profile'").fetchall())
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
        reader = SettingsRepository(target, strict_text=True)
        self.assertEqual("uSeR", reader.get("theme_active_profile"))
        self.assertEqual("5", reader.get("rank_query_type"))
        self.assertEqual(ColumnSettingsRepository(self.source, column_names=("rank",)).list(),
                         ColumnSettingsRepository(target, column_names=("rank",)).list())
        # Preserving a real width never claims that the strict integer consumer applied it.
        with self.assertRaises(ValueError):
            ColumnSettingsRepository(target, column_names=("rank",), strict_values=True).list()
        target_before = target.read_bytes()
        native, statements = sqlite3.connect, []

        class ReplayTrace(sqlite3.Connection):
            def __init__(connection, *args, **kwargs):
                super().__init__(*args, **kwargs)
                connection.set_trace_callback(statements.append)

        with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect",
                   side_effect=lambda *args, **kwargs: native(*args, **kwargs, factory=ReplayTrace)), \
             patch.object(operator, "_clock", side_effect=AssertionError("same plan keeps the first receipt clock")):
            repeated = operator.install_current_state(**arguments)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(receipt, repeated["receipt"])
        self.assertEqual(first["seed_sha256"], repeated["seed_sha256"])
        self.assertTrue(statements)
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE",
            "CREATE", "ALTER", "DROP", "VACUUM", "REINDEX")) for sql in statements))
        self.assertEqual(target_before, target.read_bytes())
        self.assertEqual(source_before, self.source.read_bytes())
        self.assertEqual(artifact_before, {path.name: path.read_bytes() for path in self.artifact.iterdir()})
        self.assertEqual(original_plan, json.dumps(original_selection, sort_keys=True))

    def test_public_install_state_cli_full_opt_in_query_default_and_layout_mismatch_are_private(self):
        self.capture()
        original_selection = self.selection()
        full_selection = {**original_selection, "target_layout": "full"}
        query_plan, full_plan = self.root / "query-selection.json", self.root / "full-selection.json"
        query_plan.write_text(json.dumps(original_selection), encoding="utf-8")
        full_plan.write_text(json.dumps(full_selection), encoding="utf-8")
        source_before = self.source.read_bytes()
        artifact_before = {path.name: path.read_bytes() for path in self.artifact.iterdir()}
        plans_before = {path: path.read_bytes() for path in (query_plan, full_plan)}
        public_keys = {"state", "seed_kind", "seed_sha256", "replayed", "runtime_state", "applied",
                       "role_ready", "order_authority", "consumer_status", "source_authenticity",
                       "selected", "relations", "manifest_sha256", "selection_sha256"}

        def run(selection, target, *, layout=None, expected_code=0):
            arguments = ["install-state", "--artifact", str(self.artifact), "--selection", str(selection),
                         "--target", str(target), "--operation-id", "fixture-cli-install",
                         "--target-id", "fixture-cli-target"]
            if layout is not None:
                arguments.extend(("--layout", layout))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(expected_code, operator.main(arguments))
            report = json.loads(output.getvalue())
            for private in ("PRIVATE-NEWS-ID", "PRIVATE-EVIDENCE", "PRIVATE-ALIAS", "PRIVATE-SETTING-RAW",
                            "UNFINISHED-RAW-NOT-CLOSED", "original-version", "raw-body-hash", str(self.root),
                            "fixture-source", "fixture-owner", "fixture-cut", "fixture-cli-install",
                            "fixture-cli-target", "source_layouts", "source_relations", "profile_id", "receipt"):
                self.assertNotIn(private, output.getvalue())
            if expected_code == 0:
                self.assertEqual(public_keys, set(report))
                for field in ("seed_sha256", "manifest_sha256", "selection_sha256"):
                    self.assertRegex(report[field], r"^[0-9a-f]{64}$")
                self.assertEqual((16, 11), (report["selected"], report["relations"]))
                self.assertEqual("SELECTED_USER_STATE", report["seed_kind"])
                self.assertEqual("OFFLINE_UNBOUND", report["runtime_state"])
                self.assertEqual("UNVERIFIED", report["source_authenticity"])
                self.assertEqual("NOT_EVALUATED", report["consumer_status"])
                for field in ("applied", "role_ready", "order_authority"):
                    self.assertFalse(report[field])
            else:
                self.assertEqual({"state", "error_type"}, set(report))
                self.assertEqual("failed", report["state"])
            return report

        full_target, query_target = self.root / "cli-full.sqlite3", self.root / "cli-query.sqlite3"
        with patch.object(operator, "_clock", return_value=NOW.isoformat()):
            first = run(full_plan, full_target, layout="full")
            query = run(query_plan, query_target)
        self.assertEqual("full_role_initialized_unbound", first["state"])
        self.assertEqual("query_role_initialized_unbound", query["state"])
        self.assertFalse(first["replayed"])
        self.assertFalse(query["replayed"])
        self.assertEqual(original_selection["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual(hashlib.sha256(json.dumps(full_selection, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest(), first["selection_sha256"])
        self.assertNotEqual(first["selection_sha256"], query["selection_sha256"])
        self.assertEqual(first["manifest_sha256"], query["manifest_sha256"])
        target_before = full_target.read_bytes()
        with patch.object(operator, "_clock", side_effect=AssertionError("CLI replay cannot replace first receipt")):
            repeated = run(full_plan, full_target, layout="full")
            for index, (plan, layout) in enumerate(((query_plan, "full"), (full_plan, None), (full_plan, "query"))):
                rejected = self.root / f"layout-mismatch-{index}" / "new.sqlite3"
                run(plan, rejected, layout=layout, expected_code=1)
                self.assertFalse(rejected.parent.exists())
        self.assertEqual({**first, "replayed": True}, repeated)
        self.assertEqual(target_before, full_target.read_bytes())
        self.assertEqual(source_before, self.source.read_bytes())
        self.assertEqual(artifact_before, {path.name: path.read_bytes() for path in self.artifact.iterdir()})
        self.assertEqual(plans_before, {path: path.read_bytes() for path in (query_plan, full_plan)})


    def _journal_full_fixture(self):
        from kiwoom_monitor.infrastructure.app_paths import AppPaths
        self.capture()
        selection = {**self.selection(), "target_layout": "full"}
        paths = AppPaths.for_full_role(self.root / "selected-full-root")
        monitor = operator.install_current_state(
            artifact=self.artifact, selection=selection, target=paths.database_path,
            operation_id="fixture-full-install", target_id="fixture-full-target", receipt_now=NOW, layout="full")
        rows = [
            ("personal_trade_rules", '[ "PRIVATE-FIXTURE-RULE" ]', "2026-10-30T09:02:00+09:00"),
            ("personal_trade_rules_structured", "[]", "2026-10-30T00:03:00Z"),
            ("strategy_packs", '[{"pack_id":"mimosa_v1","name":"PRIVATE-FIXTURE-PACK","version":7,"enabled":false,"priority":4,"setup_types":["돌파"],"required_data":["1분봉"],"source_files":["fixture-unopened.docx"]}]', "original-non-normalized-time"),
        ]
        payload = dict(purpose="journal3-once", source_instance="fixture-original-journal",
                       operation_id="fixture-original-cut", rows=[[operator.encode_value(value) for value in row] for row in rows],
                       missing_keys=[])
        artifact = self.root / "fixed3.private.json"
        artifact.write_text(operator._json(dict(payload=payload,
            sha256=hashlib.sha256(operator._json(payload).encode("utf-8")).hexdigest())), encoding="utf-8")
        arguments = dict(artifact=artifact, full_data_dir=paths.data_dir, full_settings_role=selection["target_role"],
                         operation_id="fixture-journal-install", target_id="fixture-journal-target")
        return paths, arguments, rows, monitor["receipt"]

    def test_selected_journal_installer_uses_existing_full_owner_exact_path_exclusive_lease_and_native_noop(self):
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease, StrictRestoreError
        paths, arguments, rows, original_monitor_receipt = self._journal_full_fixture()
        before_monitor, before_artifact = paths.database_path.read_bytes(), arguments["artifact"].read_bytes()
        before_source = self.source.read_bytes()
        native_accept = JournalRepository.accept_preserved_current_settings
        native, observations = sqlite3.connect, {}

        def observe_path(path, **kwargs):
            self.assertEqual(paths.journal_database_path, path)
            with self.assertRaises(StrictRestoreError):
                AppDataLease(paths.data_dir, exclusive=False, timeout=0)
            return native_accept(path, **kwargs)

        for phase in ("first", "retry"):
            connections, statements = [], []
            class TraceConnection(sqlite3.Connection):
                def __init__(connection, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    connection.set_trace_callback(statements.append)
                    connections.append(connection)
            with patch.object(sqlite3, "connect", side_effect=lambda *args, **kwargs: native(*args, **kwargs, factory=TraceConnection)), \
                    patch.object(JournalRepository, "accept_preserved_current_settings", side_effect=observe_path), \
                    patch.object(operator, "_clock", side_effect=(lambda *_: NOW.isoformat()) if phase == "first"
                                 else (lambda *_: self.fail("no clock on exact journal replay"))) as clock:
                if phase == "first":
                    first = operator.install_journal_settings(**arguments)
                    self.assertEqual("ACCEPTED", first["state"])
                    self.assertEqual(1, clock.call_count)
                else:
                    journal_before = paths.journal_database_path.read_bytes()
                    repeated = operator.install_journal_settings(**arguments)
                    self.assertEqual("EXACT_NOOP", repeated["state"])
                    self.assertEqual(first["receipt"], repeated["receipt"])
                    self.assertEqual(0, clock.call_count)
                    self.assertEqual(journal_before, paths.journal_database_path.read_bytes())
                    self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")) for sql in statements))
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.in_transaction
            observations[phase] = dict(connections=len(connections), statements=len(statements),
                selects=sum(sql.lstrip().upper().startswith("SELECT") for sql in statements),
                ddl_dml=sum(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")) for sql in statements),
                clock=clock.call_count)
        print("JOURNAL_SELECTED_FULL_CALLER_COST " + json.dumps(observations, sort_keys=True))
        with closing(sqlite3.connect(paths.journal_database_path)) as connection:
            self.assertEqual(rows, connection.execute("SELECT setting_key,value_json,updated_at FROM journal_settings ORDER BY setting_key").fetchall())
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0])
        with closing(sqlite3.connect(paths.database_path)) as connection:
            self.assertEqual(original_monitor_receipt, json.loads(connection.execute(
                "SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0]))
            self.assertEqual(32, connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0])
        self.assertEqual(before_monitor, paths.database_path.read_bytes())
        self.assertEqual(before_artifact, arguments["artifact"].read_bytes())
        self.assertEqual(before_source, self.source.read_bytes())
        self.assertFalse((paths.data_dir / "owner.json").exists())
        reader = JournalRepository(paths.journal_database_path)
        self.assertEqual(("PRIVATE-FIXTURE-RULE",), reader.load_personal_rules())
        self.assertEqual(7, reader.load_strategy_packs()[0].version)

    def test_selected_journal_installer_rejects_foreign_root_database_binding_artifact_and_busy_owner(self):
        from kiwoom_monitor.infrastructure.app_paths import AppPaths
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease, StrictRestoreError
        paths, arguments, _, _ = self._journal_full_fixture()
        monitor_before = paths.database_path.read_bytes()
        with self.assertRaises(ValueError):
            operator.install_journal_settings(**{**arguments, "full_settings_role": "different-role"}, receipt_now=NOW)
        self.assertFalse(paths.journal_database_path.exists())
        # The existing coordinator owns admission: this command cannot enter a
        # full root while a current desktop or child still holds its lease.
        lease_type = AppDataLease
        with AppDataLease(paths.data_dir, exclusive=False):
            with patch("kiwoom_monitor.infrastructure.persistence.strict_restore.AppDataLease",
                       side_effect=lambda root, **kwargs: lease_type(root, **{**kwargs, "timeout": 0})):
                with self.assertRaises(StrictRestoreError):
                    operator.install_journal_settings(**arguments, receipt_now=NOW)
        self.assertFalse(paths.journal_database_path.exists())
        JournalRepository(paths.journal_database_path)
        journal_before = paths.journal_database_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "no matching"):
            operator.install_journal_settings(**arguments, receipt_now=NOW)
        self.assertEqual(journal_before, paths.journal_database_path.read_bytes())
        self.assertEqual(monitor_before, paths.database_path.read_bytes())
        query = AppPaths.for_full_role(self.root / "foreign-query-root")
        Database(query.database_path).initialize_query_role(role=arguments["full_settings_role"])
        query_before = query.database_path.read_bytes()
        with self.assertRaises(ValueError):
            operator.install_journal_settings(**{**arguments, "full_data_dir": query.data_dir}, receipt_now=NOW)
        self.assertFalse(query.journal_database_path.exists())
        self.assertEqual(query_before, query.database_path.read_bytes())
        invalid = self.root / "invalid-fixed3.json"
        document = json.loads(arguments["artifact"].read_bytes())
        document["payload"]["rows"].append([["text", "unselected_history"], ["text", "private"], ["text", "original"]])
        document["sha256"] = hashlib.sha256(operator._json(document["payload"]).encode("utf-8")).hexdigest()
        invalid.write_text(operator._json(document), encoding="utf-8")
        with patch.object(AppPaths, "for_full_role", side_effect=AssertionError("verify before target path creation")):
            with self.assertRaises(ValueError):
                operator.install_journal_settings(**{**arguments, "artifact": invalid})

    def test_public_install_journal_settings_cli_keeps_raw_rows_and_first_receipt_private(self):
        paths, arguments, _, _ = self._journal_full_fixture()
        command = ["install-journal-settings", "--artifact", str(arguments["artifact"]),
                   "--full-data-dir", str(paths.data_dir), "--full-settings-role", arguments["full_settings_role"],
                   "--operation-id", arguments["operation_id"], "--target-id", arguments["target_id"]]
        reports = []
        for phase in ("first", "retry", "conflict"):
            output = io.StringIO()
            selected = command if phase != "conflict" else [value if value != arguments["target_id"] else "another-journal-target" for value in command]
            with redirect_stdout(output), patch.object(operator, "_clock", side_effect=(lambda *_: NOW.isoformat())
                    if phase == "first" else (lambda *_: self.fail("CLI exact retry cannot sample a new clock"))):
                code = operator.main(selected)
            report = json.loads(output.getvalue())
            self.assertEqual(1 if phase == "conflict" else 0, code)
            for private in ("PRIVATE-FIXTURE-RULE", "PRIVATE-FIXTURE-PACK", "fixture-unopened.docx", "original-non-normalized-time", "accepted_at", "monitor_seed_sha256"):
                self.assertNotIn(private, output.getvalue())
            if phase == "conflict":
                self.assertEqual({"state", "error_type"}, set(report))
            else:
                self.assertEqual({"state", "rows", "missing_keys", "artifact_sha256", "first_receipt_sha256", "runtime_state", "applied", "order_authority"}, set(report))
                self.assertEqual((3, 0, "OFFLINE_UNBOUND", False, False), tuple(report[key]
                    for key in ("rows", "missing_keys", "runtime_state", "applied", "order_authority")))
                reports.append(report)
        self.assertEqual("ACCEPTED", reports[0]["state"])
        self.assertEqual({**reports[0], "state": "EXACT_NOOP"}, reports[1])

    def test_public_install_journal_settings_cli_requires_nonblank_raw_root_and_preserves_explicit_dot(self):
        paths, arguments, _, _ = self._journal_full_fixture()
        monitor_before = paths.database_path.read_bytes()
        fixed3_before = arguments["artifact"].read_bytes()
        source_before = self.source.read_bytes()
        current_artifact_before = {item.name: item.read_bytes() for item in self.artifact.iterdir()}
        root_files_before = {item.relative_to(paths.data_dir).as_posix() for item in paths.data_dir.rglob("*") if item.is_file()}
        original_cwd = Path.cwd()
        def command(raw_root):
            return ["install-journal-settings", "--artifact", str(arguments["artifact"]),
                    "--full-data-dir", raw_root, "--full-settings-role", arguments["full_settings_role"],
                    "--operation-id", arguments["operation_id"], "--target-id", arguments["target_id"]]
        try:
            os.chdir(paths.data_dir)
            for label, raw_root in (("empty", ""), ("whitespace", "   ")):
                with self.subTest(raw_root=label):
                    output = io.StringIO()
                    with redirect_stdout(output), patch.object(operator, "install_journal_settings",
                            side_effect=AssertionError("invalid raw root must not enter the installer")) as install:
                        self.assertEqual(1, operator.main(command(raw_root)))
                    install.assert_not_called()
                    self.assertEqual({"state": "failed", "error_type": "SettingsPreservationError"}, json.loads(output.getvalue()))
                    self.assertFalse(paths.journal_database_path.exists())
                    self.assertFalse((paths.data_dir / "journal-selected-settings-owner.json").exists())
                    self.assertEqual(root_files_before, {item.relative_to(paths.data_dir).as_posix() for item in paths.data_dir.rglob("*") if item.is_file()})
                    self.assertEqual(monitor_before, paths.database_path.read_bytes())
                    self.assertEqual(fixed3_before, arguments["artifact"].read_bytes())
                    self.assertEqual(source_before, self.source.read_bytes())
                    self.assertEqual(current_artifact_before, {item.name: item.read_bytes() for item in self.artifact.iterdir()})
            output = io.StringIO()
            with redirect_stdout(output), patch.object(operator, "_clock", return_value=NOW.isoformat()):
                self.assertEqual(0, operator.main(command(".")))
            self.assertEqual("ACCEPTED", json.loads(output.getvalue())["state"])
            self.assertTrue(paths.journal_database_path.is_file())
            self.assertEqual(monitor_before, paths.database_path.read_bytes())
            self.assertEqual(fixed3_before, arguments["artifact"].read_bytes())
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
