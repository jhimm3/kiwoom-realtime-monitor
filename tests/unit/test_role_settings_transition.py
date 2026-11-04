from __future__ import annotations

from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import preserve_current_settings as operator
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.database import (
    Database, DEFAULT_COLUMNS, DEFAULT_SETTINGS, ROLE_SETTINGS_TABLES, QUERY_STATE_TABLES, _role_settings_seed,
)
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from tests.unit import test_preserve_current_settings as existing_preservation


NOW = datetime(2026, 10, 25, 10, 0, tzinfo=KST)


class RoleSettingsSeedTests(unittest.TestCase):
    def test_fingerprint_retains_primitive_identity_null_and_dynamic_keys(self):
        rows = {table: [] for table in ROLE_SETTINGS_TABLES}
        rows["settings"] = [("dynamic:key", None), ("blob", b"\x00raw\xff")]
        frozen, fingerprint = _role_settings_seed(rows)
        rows["settings"].append(("later", "changed"))
        self.assertEqual(2, len(frozen["settings"]))
        self.assertEqual(fingerprint, _role_settings_seed({**rows, "settings": list(frozen["settings"])})[1])
        for left, right in ((0, "0"), (1, 1.0), (None, "None"), (b"raw", "raw"), (-0.0, 0.0)):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(_role_settings_seed({**rows, "settings": [("key", left)]})[1],
                                    _role_settings_seed({**rows, "settings": [("key", right)]})[1])
        for value in (True, float("nan"), 2**63, {"value": "object"}):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                _role_settings_seed({**rows, "settings": [("key", value)]})


class RoleSettingsTransitionTests(unittest.TestCase):
    def fixture(self, *, unconsumable=False):
        fixture = existing_preservation.PreservedCurrentSettingsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if unconsumable:
            with closing(sqlite3.connect(fixture.source)) as connection, connection:
                connection.execute("UPDATE column_settings SET width=NULL WHERE column_name='rank'")
        fixture.capture(sqlite_frozen=True)
        selection = fixture.selection(include_orphans=True)
        fixture.accept(selection)
        return fixture, selection, fixture.root / "role.sqlite3"

    def install(self, fixture, selection, path):
        return operator.install_settings(artifact=fixture.artifact, selection=selection,
                                         accepted_target=fixture.target, target=path)

    def rows(self, path):
        with closing(sqlite3.connect(path)) as connection:
            return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                    for table in ROLE_SETTINGS_TABLES}

    def test_fresh_role_schema_has_only_settings_and_uses_existing_normal_writers(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "query.sqlite3"
        clock = Mock(return_value=NOW)
        database = Database(path, now_provider=clock)
        first = database.initialize(settings_role="fixture-query")
        self.assertFalse(first["replayed"])
        self.assertFalse(first["order_authority"])
        self.assertFalse(first["applied"])
        self.assertFalse(first["role_ready"])
        self.assertEqual("OFFLINE_UNBOUND", first["runtime_state"])
        self.assertEqual("30", database.settings.get("refresh_interval_seconds"))
        self.assertEqual(tuple(ColumnSetting(name, bool(v), p, w) for name, v, p, w in DEFAULT_COLUMNS),
                         ColumnSettingsRepository(path).list())
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual({*ROLE_SETTINGS_TABLES, "role_settings_schema"},
                             {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual([], connection.execute("SELECT * FROM central_setting_versions").fetchall())
        clock.assert_not_called()
        database.settings.set_many({"rank_query_type": "2", "dynamic:future-key": "raw-json: { }"})
        self.assertEqual("2", database.settings.get("rank_query_type"))
        clock.assert_called_once_with()
        reopened = Database(path)
        self.assertTrue(reopened.initialize(settings_role="fixture-query")["replayed"])
        self.assertEqual("2", reopened.settings.get("rank_query_type"))

    def test_selected_install_preserves_original_primitives_versions_and_no_default_inference(self):
        fixture, selection, path = self.fixture()
        source_bytes = fixture.source.read_bytes()
        carrier_bytes = (fixture.target / "offline.sqlite3").read_bytes()
        accepted = self.rows(fixture.target / "offline.sqlite3")
        first = self.install(fixture, selection, path)
        self.assertEqual(accepted, self.rows(path))
        self.assertEqual("SELECTED", first["seed_kind"])
        self.assertFalse(first["applied"])
        self.assertEqual(source_bytes, fixture.source.read_bytes())
        self.assertEqual(carrier_bytes, (fixture.target / "offline.sqlite3").read_bytes())
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(("blob",), connection.execute("SELECT typeof(value) FROM settings WHERE key='dynamic:not-in-defaults'").fetchone())
            self.assertEqual(("null",), connection.execute("SELECT typeof(value) FROM settings WHERE key='null-value'").fetchone())
            self.assertEqual(("integer",), connection.execute("SELECT typeof(value) FROM settings WHERE key='integer-value'").fetchone())
            self.assertEqual(("text", "real"), connection.execute("SELECT typeof(visible),typeof(width) FROM column_settings WHERE column_name='unknown-column'").fetchone())
            self.assertIsNone(connection.execute("SELECT * FROM settings WHERE key='refresh_interval_seconds'").fetchone())
            self.assertEqual([(None,)], connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='orphan-column'").fetchall())
        self.assertEqual(Database(fixture.source).settings.get("null-value"), Database(path).settings.get("null-value"))
        self.assertEqual(ColumnSettingsRepository(fixture.source).list(), ColumnSettingsRepository(path).list())

    def test_normal_set_save_reset_and_restart_keep_unedited_raw_state_and_original_receipt(self):
        fixture, selection, path = self.fixture()
        self.install(fixture, selection, path)
        original = self.rows(path)
        database = Database(path, now_provider=lambda: NOW)
        database.initialize(settings_role="query-local")
        database.settings.set_many({"rank_query_type": "2", "new:dynamic": "selected-edit"})
        columns = ColumnSettingsRepository(path, now_provider=lambda: NOW)
        columns.save((ColumnSetting("rank", False, 7, 222),))
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual((NOW.isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='rank_query_type'").fetchone())
            self.assertIsNone(connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='null-value'").fetchone())
            self.assertEqual(("0", 1, 137.5), connection.execute("SELECT visible,position,width FROM column_settings WHERE column_name='unknown-column'").fetchone())
            self.assertEqual(("2026-10-20T12:00:00+09:00",), original["central_column_setting_versions"][0][1:])
            receipt_before = connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0]
        before_retry = self.rows(path)
        self.assertTrue(self.install(fixture, selection, path)["replayed"])
        self.assertEqual(before_retry, self.rows(path))
        columns.reset()  # Existing explicit reset changes known default names only.
        self.assertEqual(ColumnSetting("rank", True, 0, 32), next(row for row in columns.list() if row.name == "rank"))
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(("0", 1, 137.5), connection.execute("SELECT visible,position,width FROM column_settings WHERE column_name='unknown-column'").fetchone())
            self.assertEqual(receipt_before, connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0])
        reopened = Database(path)
        reopened.initialize(settings_role="query-local")
        self.assertEqual("2", reopened.settings.get("rank_query_type"))
        self.assertEqual("selected-edit", reopened.settings.get("new:dynamic"))

    def test_wrong_role_legacy_initialize_and_foreign_target_do_not_adopt_or_overwrite(self):
        fixture, selection, path = self.fixture()
        self.install(fixture, selection, path)
        before = self.rows(path)
        with self.assertRaises(ValueError):
            Database(path).initialize(settings_role="different-role")
        with self.assertRaises(ValueError):
            Database(path).initialize()
        self.assertEqual(before, self.rows(path))
        foreign = fixture.root / "foreign.sqlite3"
        Database(foreign).initialize()
        with self.assertRaises(ValueError):
            self.install(fixture, selection, foreign)

    def test_install_failure_rolls_back_schema_values_and_receipt_then_retries(self):
        fixture, selection, path = self.fixture()
        native = sqlite3.connect
        class Failed(sqlite3.Connection):
            def executemany(connection, sql, values):
                if "central_setting_versions" in sql:
                    raise sqlite3.OperationalError("controlled role seed failure")
                return super().executemany(sql, values)
        def connect(*args, **kwargs):
            return native(*args, **kwargs, factory=Failed) if Path(args[0]) == path else native(*args, **kwargs)
        with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.install(fixture, selection, path)
        with closing(native(path)) as connection:
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
        self.assertFalse(self.install(fixture, selection, path)["replayed"])

    def test_commit_ack_loss_recovers_same_installation_without_new_clock_or_overwriting_edits(self):
        fixture, selection, path = self.fixture()
        native = sqlite3.connect
        class LostAck(sqlite3.Connection):
            def commit(connection):
                super().commit()
                raise OSError("controlled role ACK loss")
        def connect(*args, **kwargs):
            return native(*args, **kwargs, factory=LostAck) if Path(args[0]) == path else native(*args, **kwargs)
        with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
            with self.assertRaises(OSError):
                self.install(fixture, selection, path)
        Database(path, now_provider=lambda: NOW).settings.set("rank_query_type", "4")
        with patch.object(operator, "_clock", side_effect=AssertionError("installation must not issue a clock")):
            self.assertTrue(self.install(fixture, selection, path)["replayed"])
        self.assertEqual("4", Database(path).settings.get("rank_query_type"))

    def test_carrier_tampering_blocks_before_target_creation_and_cli_omits_private_values(self):
        fixture, selection, path = self.fixture()
        with closing(sqlite3.connect(fixture.target / "offline.sqlite3")) as connection, connection:
            connection.execute("UPDATE settings SET value='PRIVATE-TAMPER' WHERE key='rank_query_type'")
        selection_path = fixture.root / "selection.json"
        selection_path.write_text(json.dumps(selection), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = operator.main(["install", "--artifact", str(fixture.artifact), "--selection", str(selection_path),
                                  "--accepted-target", str(fixture.target), "--target", str(path)])
        self.assertEqual(1, code)
        self.assertFalse(path.exists())
        self.assertEqual({"state": "failed", "error_type": "SettingsPreservationError"}, json.loads(output.getvalue()))

    def test_unreadable_column_primitive_stays_preserved_without_claiming_runtime_readiness(self):
        fixture, selection, path = self.fixture(unconsumable=True)
        first = self.install(fixture, selection, path)
        self.assertFalse(first["applied"])
        self.assertFalse(first["role_ready"])
        self.assertEqual("NOT_EVALUATED", first["consumer_status"])
        self.assertEqual(self.rows(fixture.target / "offline.sqlite3"), self.rows(path))
        for source in (fixture.source, path):
            with self.subTest(source=source.name), self.assertRaises(TypeError):
                ColumnSettingsRepository(source).list()


class QueryRoleStoreTests(unittest.TestCase):
    def root(self):
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_fresh_query_role_has_one_exact_owned_layout_no_legacy_tables_and_reopens(self):
        path = self.root() / "query-role.sqlite3"
        first = Database(path).initialize_query_role(role="fixture-query")
        self.assertEqual("FRESH", first["seed_kind"])
        self.assertFalse(first["applied"])
        self.assertFalse(first["role_ready"])
        with closing(sqlite3.connect(path)) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(29, len(names))
            self.assertTrue({"settings", "column_settings", "stocks", "minute_bars", "daily_bars", "profile_themes", "role_settings_schema"} <= names)
            self.assertFalse({"query_cache_schema", "themes", "stock_themes", "schema_migrations", "legacy_news_transfer_migrations"} & names)
            self.assertEqual([(2,)], connection.execute("SELECT schema_version FROM role_settings_schema").fetchall())
            self.assertEqual([(DEFAULT_SETTINGS["theme_active_profile"],)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
        before = path.read_bytes()
        self.assertTrue(Database(path).initialize_query_role(role="fixture-query")["replayed"])
        self.assertEqual(before, path.read_bytes())
        with self.assertRaises(ValueError):
            Database(path).initialize()
        with self.assertRaises(ValueError):
            Database(path).initialize_role_settings(role="fixture-query")

    def test_query_role_does_not_adopt_legacy_four_only_or_foreign_target(self):
        for kind in ("legacy", "settings-only", "foreign"):
            with self.subTest(kind=kind):
                path = self.root() / "query-role.sqlite3"
                if kind == "legacy":
                    Database(path).initialize()
                elif kind == "settings-only":
                    Database(path).initialize_role_settings(role="fixture-query")
                else:
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute("CREATE TABLE foreign_data(value BLOB)")
                        connection.execute("INSERT INTO foreign_data VALUES(?)", (b"preserved",))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    Database(path).initialize_query_role(role="fixture-query")
                self.assertEqual(before, path.read_bytes())

    def test_query_role_creation_failure_rolls_back_settings_profiles_schema_and_receipt_in_one_tx(self):
        path = self.root() / "query-role.sqlite3"
        native = sqlite3.connect
        calls = []
        class Traced(sqlite3.Connection):
            def execute(connection, sql, *args):
                calls.append(sql)
                if sql.startswith("INSERT INTO role_settings_schema"):
                    raise sqlite3.OperationalError("controlled query receipt failure")
                return super().execute(sql, *args)
            def executescript(connection, *args):
                raise AssertionError("executescript would split the native initialization transaction")
            def commit(connection):
                calls.append("COMMIT")
                return super().commit()
        with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=lambda *a, **k: native(*a, **k, factory=Traced)):
            with self.assertRaisesRegex(sqlite3.OperationalError, "^controlled query receipt failure$"):
                Database(path).initialize_query_role(role="fixture-query")
        self.assertEqual(1, calls.count("BEGIN IMMEDIATE"))
        self.assertEqual(0, calls.count("COMMIT"))
        with closing(native(path)) as connection:
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
        Database(path).initialize_query_role(role="fixture-query")

    def test_selected_query_install_uses_explicit_cli_layout_and_seeds_no_default_or_named_theme(self):
        for active in (DEFAULT_SETTINGS["theme_active_profile"], "내 프로필"):
            with self.subTest(active=active):
                fixture = existing_preservation.PreservedCurrentSettingsTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                with closing(sqlite3.connect(fixture.source)) as connection, connection:
                    connection.execute("INSERT INTO settings VALUES('theme_active_profile',?)", (active,))
                fixture.capture(sqlite_frozen=True)
                selection = fixture.selection(include_orphans=True)
                fixture.accept(selection)
                path = fixture.root / 'query-role.sqlite3'
                selected_path = fixture.root / 'selection.json'
                selected_path.write_text(json.dumps(selection), encoding='utf-8')
                with redirect_stdout(io.StringIO()):
                    code = operator.main(['install', '--artifact', str(fixture.artifact), '--selection', str(selected_path),
                        '--accepted-target', str(fixture.target), '--target', str(path), '--layout', 'query'])
                self.assertEqual(0, code)
                before = path.read_bytes()
                self.assertTrue(Database(path).initialize_query_role(role="query-local")["replayed"])
                self.assertEqual(before, path.read_bytes())
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual([], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
                    self.assertEqual((active,), connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())
                helper = RoleSettingsTransitionTests()
                self.assertEqual(helper.rows(fixture.target / "offline.sqlite3"), helper.rows(path))

    def test_explicit_role_reads_only_known_columns_and_rejects_consumed_raw_types_without_writes(self):
        path = self.root() / "query-settings.sqlite3"
        Database(path).initialize(settings_role="fixture-query")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO settings VALUES('unrelated:null',NULL)")
            connection.execute("INSERT INTO column_settings VALUES('unknown-column','0',1,NULL)")
            connection.execute("UPDATE settings SET value=NULL WHERE key='rank_query_type'")
        before = path.read_bytes()
        strict = SettingsRepository(path, strict_text=True)
        with self.assertRaisesRegex(ValueError, "rank_query_type"):
            strict.get("rank_query_type")
        with self.assertRaises(KeyError):
            strict.get("not-selected")
        self.assertEqual("None", Database(path).settings.get("rank_query_type"))
        self.assertEqual("30", strict.get("refresh_interval_seconds"))
        columns = ColumnSettingsRepository(path, column_names=("rank",), strict_values=True)
        self.assertEqual((ColumnSetting("rank", True, 0, 32),), columns.list())
        self.assertEqual(before, path.read_bytes())
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE column_settings SET width=NULL WHERE column_name='rank'")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "rank"):
            columns.list()
        self.assertEqual(before, path.read_bytes())

    def test_existing_repositories_can_bind_the_current_provider_after_api_replacement(self):
        path = self.root() / "query-settings.sqlite3"
        Database(path).initialize(settings_role="fixture-query")
        settings, columns = SettingsRepository(path), ColumnSettingsRepository(path)
        current = [NOW]
        settings.set_now_provider(lambda: current[0])
        columns.set_now_provider(lambda: current[0])
        settings.set("rank_query_type", "2")
        current[0] = NOW.replace(day=26)
        settings.set("rank_query_type", "3")
        columns.save((ColumnSetting("rank", True, 0, 91),))
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([(current[0].isoformat(),)], connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='rank_query_type'").fetchall())
            self.assertEqual([(current[0].isoformat(),)], connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='rank'").fetchall())
            self.assertEqual([(None,)], connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchall())


class QueryRoleCurrentStateTests(unittest.TestCase):
    def fixture(self, *, ordinary=False):
        fixture = existing_preservation.PreservedCurrentStateArtifactTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if ordinary:
            with closing(sqlite3.connect(fixture.source)) as connection, connection:
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=7")
                connection.execute("DELETE FROM profile_themes WHERE profile_id=7")
                connection.execute("INSERT INTO profile_themes VALUES(7,'AI','#123456')")
                connection.execute("INSERT INTO profile_stock_themes VALUES(7,'000001','AI',NULL)")
                connection.execute("UPDATE profile_theme_name_decisions SET source_name='SOURCE',target_name='AI',decision_source='user' WHERE profile_id=7")
                connection.execute("UPDATE profile_theme_suggestions SET raw_theme_name='AI',confidence=88,body_hash='original-hash' WHERE profile_id=7")
                connection.execute("UPDATE central_setting_versions SET updated_at='2020-01-02T10:02:03+09:00' WHERE setting_key='theme_active_profile'")
                connection.execute("UPDATE column_settings SET width=137")
                connection.execute("UPDATE stock_aliases SET alias='LegacyAlias' WHERE stock_code='000001'")
                connection.execute("UPDATE stocks SET name='원종목' WHERE code='000001'")
        fixture.capture()
        selection = fixture.selection()
        return fixture, selection, fixture.root / "query-current.sqlite3"

    def install(self, fixture, selection, path, **kwargs):
        return operator.install_current_state(artifact=fixture.artifact, selection=selection,
            target=path, operation_id="fixture-install", target_id="fixture-target", receipt_now=NOW, **kwargs)

    def selected(self, fixture, selection):
        state = operator.select_current_state_rows(fixture.artifact, selection)
        state.update(target_operation_id="fixture-install", target_id="fixture-target")
        return state

    def rows(self, path):
        with closing(sqlite3.connect(path)) as connection:
            return {table: connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid").fetchall()
                    for table, columns in QUERY_STATE_TABLES.items()}

    def test_single_cut_native_seed_preserves_all_eleven_relations_types_active_and_first_receipt(self):
        fixture, selection, path = self.fixture()
        selected = self.selected(fixture, selection)
        source_before = fixture.source.read_bytes()
        native, calls, closed = sqlite3.connect, [], []
        class Traced(sqlite3.Connection):
            def execute(connection, sql, *args):
                calls.append(sql)
                return super().execute(sql, *args)
            def executescript(connection, *args):
                raise AssertionError("initial seed must remain in its native transaction")
            def commit(connection):
                calls.append("COMMIT")
                return super().commit()
            def close(connection):
                closed.append(True)
                return super().close()
        def connect(*args, **kwargs):
            self.assertEqual(path, Path(args[0]))
            calls.append("CONNECT")
            return native(*args, **kwargs, factory=Traced)
        with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
            result = self.install(fixture, selection, path)
        self.assertEqual(1, calls.count("CONNECT"))
        self.assertEqual(1, calls.count("BEGIN IMMEDIATE"))
        self.assertEqual(1, calls.count("COMMIT"))
        self.assertEqual([True], closed)
        self.assertEqual(source_before, fixture.source.read_bytes())
        self.assertEqual(selected["rows"], self.rows(path))
        self.assertEqual("SELECTED_USER_STATE", result["seed_kind"])
        self.assertEqual(NOW.isoformat(), result["receipt"]["accepted_at"])
        self.assertEqual(_role_settings_seed(selected["rows"], tables=QUERY_STATE_TABLES)[1], result["seed_sha256"])
        self.assertEqual(selected["binding"], result["receipt"]["plan"]["binding"])
        self.assertEqual(selected["source_layouts"], result["receipt"]["plan"]["source_layouts"])
        self.assertEqual({t: len(r) for t, r in selected["rows"].items()}, result["receipt"]["plan"]["relation_counts"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["role_ready"])
        self.assertFalse(result["order_authority"])
        self.assertEqual("NOT_EVALUATED", result["consumer_status"])
        with closing(native(path)) as connection:
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual([(7,)], connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=(SELECT value FROM settings WHERE key='theme_active_profile') COLLATE NOCASE").fetchall())
            self.assertEqual([(88.5, 'real')], connection.execute("SELECT confidence,typeof(confidence) FROM profile_theme_suggestions").fetchall())
            self.assertEqual([(b"original-version-\x00", 'blob')], connection.execute("SELECT updated_at,typeof(updated_at) FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchall())
            self.assertEqual(29, connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
            self.assertEqual(result["receipt"], json.loads(connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0]))
        raw = path.read_bytes()
        self.assertEqual(result["receipt"], Database(path).initialize_query_role(role="query-local")["receipt"])
        self.assertEqual(raw, path.read_bytes())

    def test_explicit_installer_rejects_binding_open_selection_and_private_cli_output(self):
        fixture, selection, path = self.fixture()
        for mutate in (lambda s: s.update(manifest_sha256="0" * 64),
                       lambda s: s.update(operation_id="another-cut"),
                       lambda s: s["records"][0].update(placement="RETAIN_SOURCE")):
            broken = deepcopy(selection)
            mutate(broken)
            with self.assertRaises(ValueError):
                self.install(fixture, broken, path)
            self.assertFalse(path.exists())
        selected_path = fixture.root / "selection.json"
        selected_path.write_text(json.dumps(selection), encoding="utf-8")
        args = ["install-state", "--artifact", str(fixture.artifact), "--selection", str(selected_path),
                "--target", str(path), "--operation-id", "fixture-install", "--target-id", "fixture-target"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, operator.main(args))
        first = json.loads(output.getvalue())
        for marker in ("PRIVATE-SETTING", "PRIVATE-EVIDENCE", "PRIVATE-ALIAS", "original-version", str(fixture.root)):
            self.assertNotIn(marker, output.getvalue())
        for private in ("receipt", "plan", "source_layouts", "source_relations", "profile_id", "last_price",
                        "fixture-source", "fixture-owner", "fixture-cut", "fixture-install", "fixture-target"):
            self.assertNotIn(private, output.getvalue())
        self.assertEqual({"state", "seed_kind", "seed_sha256", "replayed", "runtime_state", "applied",
            "role_ready", "order_authority", "consumer_status", "source_authenticity", "selected", "relations",
            "manifest_sha256", "selection_sha256"}, set(first))
        self.assertEqual(16, first["selected"])
        self.assertEqual(11, first["relations"])
        self.assertEqual(self.selected(fixture, selection)["binding"]["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual("SELECTED_USER_STATE", first["seed_kind"])
        self.assertFalse(first["replayed"])
        self.assertFalse(first["applied"])
        with patch.object(operator, "_clock", side_effect=AssertionError("reentry must keep first clock")), redirect_stdout(io.StringIO()):
            self.assertEqual(0, operator.main(args))
        self.assertEqual("UNVERIFIED", first["source_authenticity"])
        self.assertEqual("NOT_EVALUATED", first["consumer_status"])

    def test_state_input_conflicts_and_unsupported_primitives_layouts_precede_target_creation(self):
        fixture, selection, path = self.fixture()
        selected = self.selected(fixture, selection)
        for mutate in (lambda s: s["binding"].update(target_role="another-role"),
                       lambda s: s["binding"].update(selected=999),
                       lambda s: s["rows"]["settings"].append(("unsupported", True)),
                       lambda s: s["source_layouts"]["theme_profiles"].update(profile_id_type="TEXT"),
                       lambda s: s["source_relations"].update(theme_profiles=False)):
            broken = deepcopy(selected)
            mutate(broken)
            with self.assertRaises(ValueError):
                Database(path).initialize_query_role(role="query-local", preserved_state=broken, receipt_at=lambda: NOW.isoformat())
            self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            Database(path).initialize_query_role(role="query-local", preserved_state=selected, preserved_rows=selected["rows"], receipt_at=lambda: NOW.isoformat())
        self.assertFalse(path.exists())

    def test_native_seed_reference_affinity_and_receipt_failures_roll_back_every_relation(self):
        fixture, selection, path = self.fixture()
        selected = self.selected(fixture, selection)
        native = sqlite3.connect
        for failure in ("foreign_key", "catalog", "active", "affinity", "receipt", "clock", "cancel"):
            with self.subTest(failure=failure):
                target = path.with_name(failure + ".sqlite3")
                state = deepcopy(selected)
                if failure == "foreign_key":
                    row = state["rows"]["profile_themes"][0]
                    state["rows"]["profile_themes"][0] = (900, *row[1:])
                elif failure == "catalog":
                    row = state["rows"]["profile_theme_suggestions"][0]
                    state["rows"]["profile_theme_suggestions"][0] = (row[0], "missing", *row[2:])
                elif failure == "active":
                    state["rows"]["settings"][0] = ("theme_active_profile", "missing")
                elif failure == "affinity":
                    row = state["rows"]["profile_theme_suggestions"][0]
                    state["rows"]["profile_theme_suggestions"][0] = (*row[:5], "88", *row[6:])
                class Failing(sqlite3.Connection):
                    def execute(connection, sql, *args):
                        if sql.startswith("INSERT INTO role_settings_schema") and failure in {"receipt", "cancel"}:
                            if failure == "cancel":
                                raise KeyboardInterrupt("controlled seed cancellation")
                            raise sqlite3.OperationalError("controlled first receipt failure")
                        return super().execute(sql, *args)
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=lambda *a, **k: native(*a, **k, factory=Failing)):
                    with self.assertRaises((ValueError, sqlite3.IntegrityError, sqlite3.OperationalError, KeyboardInterrupt)):
                        Database(target).initialize_query_role(role="query-local", preserved_state=state,
                            receipt_at=lambda: "2026-10-25T01:00:00+00:00" if failure == "clock" else NOW.isoformat())
                with closing(native(target)) as connection:
                    self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
                self.assertFalse(self.install(fixture, selection, target)["replayed"])

    def test_same_plan_ack_and_close_loss_recover_first_receipt_after_normal_edits_without_reseed(self):
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        for phase in ("commit", "close"):
            with self.subTest(phase=phase):
                fixture, selection, path = self.fixture(ordinary=True)
                native, owner_closed = sqlite3.connect, []
                class LostAck(sqlite3.Connection):
                    def commit(connection):
                        super().commit()
                        if phase == "commit":
                            raise OSError("controlled state COMMIT ACK loss")
                    def close(connection):
                        super().close()
                        owner_closed.append(True)
                        if phase == "close":
                            raise OSError("controlled state close ACK loss")
                def connect(*args, **kwargs):
                    return native(*args, **kwargs, factory=LostAck) if Path(args[0]) == path else native(*args, **kwargs)
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
                    with self.assertRaises(OSError):
                        self.install(fixture, selection, path)
                self.assertEqual([True], owner_closed)
                with closing(native(path)) as connection:
                    original = json.loads(connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0])
                db = Database(path, now_provider=lambda: NOW)
                themes = ThemeRepository.open_existing(path, "User", now_provider=lambda: NOW)
                themes.rename_profile("User", "Edited", settings=db.settings)
                db.settings.set("rank_query_type", "4")
                before = self.rows(path)
                with patch.object(operator, "_clock", side_effect=AssertionError("no new first acceptance clock")):
                    retry = self.install(fixture, selection, path)
                self.assertTrue(retry["replayed"])
                self.assertEqual(original, retry["receipt"])
                self.assertEqual(before, self.rows(path))
                self.assertEqual("Edited", db.settings.get("theme_active_profile"))
                self.assertEqual("4", db.settings.get("rank_query_type"))

    def test_foreign_existing_wrong_role_conflicting_plan_and_unknown_kind_do_not_mutate(self):
        fixture, selection, path = self.fixture()
        for kind in ("foreign", "fresh", "selected", "wrong-role", "plan", "unknown-kind"):
            with self.subTest(kind=kind):
                target = path.with_name(kind + ".sqlite3")
                if kind == "foreign":
                    with closing(sqlite3.connect(target)) as connection, connection:
                        connection.execute("CREATE TABLE foreign_data(value)")
                        connection.execute("INSERT INTO foreign_data VALUES('keep')")
                elif kind == "fresh":
                    Database(target).initialize_query_role(role="query-local")
                elif kind == "selected":
                    prior, chosen, _ = RoleSettingsTransitionTests.fixture(self)
                    operator.install_settings(artifact=prior.artifact, selection=chosen, accepted_target=prior.target, target=target, layout="query")
                else:
                    self.install(fixture, selection, target)
                    if kind == "unknown-kind":
                        with closing(sqlite3.connect(target)) as connection, connection:
                            connection.execute("UPDATE role_settings_schema SET seed_kind='FUTURE_UNKNOWN'")
                before = target.read_bytes()
                with self.assertRaises(ValueError):
                    if kind == "wrong-role":
                        Database(target).initialize_query_role(role="different-role")
                    elif kind == "plan":
                        operator.install_current_state(artifact=fixture.artifact, selection=selection, target=target,
                            operation_id="different-install", target_id="fixture-target", receipt_now=NOW)
                    else:
                        self.install(fixture, selection, target)
                self.assertEqual(before, target.read_bytes())

    def test_ordinary_selected_rows_reopen_through_actual_settings_columns_theme_and_stock_consumers(self):
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        fixture, selection, path = self.fixture(ordinary=True)
        first = self.install(fixture, selection, path)
        expected = self.rows(path)
        before = path.read_bytes()
        database = Database(path, now_provider=lambda: NOW)
        self.assertTrue(database.initialize_query_role(role="query-local")["replayed"])
        strict = SettingsRepository(path, strict_text=True)
        missing_defaults = (
            ("daily_high_adjusted_basis_version", "0"),
            ("historical_high_adjusted_basis_version", "0"),
            ("krx_stock_catalog_date", ""),
            ("kind_name_history_sync_date", ""),
        )
        for key, value in missing_defaults:
            with self.subTest(key=key):
                with self.assertRaises(KeyError):
                    strict.get(key)
                self.assertEqual(value, strict.get(key, value))
                with self.assertRaises(KeyError):
                    strict.get(key)
        self.assertEqual("5", database.settings.get("rank_query_type"))
        self.assertEqual("uSeR", database.settings.get("theme_active_profile"))
        columns = ColumnSettingsRepository(path, now_provider=lambda: NOW)
        self.assertEqual((ColumnSetting("rank", True, 0, 137),), columns.list())
        themes = ThemeRepository.open_existing(path, database.settings.get("theme_active_profile"), now_provider=lambda: NOW)
        self.assertIsNotNone(themes)
        self.assertEqual((("AI", "#123456"),), themes.list_themes())
        self.assertEqual({"원종목": "AI"}, themes.all_by_name())
        self.assertEqual("#123456", themes.color_for_stock_theme("000001", "AI"))
        self.assertEqual("000001", StockRepository(path).find_code_by_name("LegacyAlias"))
        self.assertEqual(expected, self.rows(path))
        self.assertEqual(first["receipt"], database.initialize_query_role(role="query-local")["receipt"])
        self.assertFalse(first["role_ready"])
        restarted = SettingsRepository(path, strict_text=True)
        for key, value in missing_defaults:
            self.assertEqual(value, restarted.get(key, value))
        self.assertEqual(expected, self.rows(path))
        self.assertEqual(before, path.read_bytes())


class FullRoleCurrentStateTests(unittest.TestCase):
    # Reuse source/selection helpers without inheriting their discovered tests.
    rows = QueryRoleCurrentStateTests.rows

    def fixture(self, *, ordinary=False, drive_markers=False):
        fixture, _, _ = QueryRoleCurrentStateTests.fixture(self, ordinary=ordinary)
        if drive_markers:
            with closing(sqlite3.connect(fixture.source)) as connection, connection:
                for key, value in (
                    ("google_drive_unsynced_changes", "1"),
                    ("google_drive_local_changed_at", "2020-01-02T01:02:03Z"),
                    ("google_drive_last_upload_success_at", "2020-01-01T09:00:00+09:00"),
                ):
                    connection.execute("INSERT INTO settings VALUES(?,?)", (key, value))
                    connection.execute("INSERT INTO central_setting_versions VALUES(?,?)", (key, b"old-drive-version-\x00"))
            fixture.artifact = fixture.root / "full-current-state-artifact"
            fixture.capture()
        selection = fixture.selection()
        selection["target_role"] = "full-local"
        _, source_records, _ = operator.verified_current_state_rows(fixture.artifact)
        for record in selection["records"]:
            source_record = source_records[record["record_id"]]
            if (drive_markers and source_record["relation"] in {"settings", "central_setting_versions"}
                    and operator.decode_value(source_record["row"][0]) in {
                        "google_drive_unsynced_changes", "google_drive_local_changed_at",
                        "google_drive_last_upload_success_at"}):
                record["placement"] = "RETAIN_SOURCE"
                record.pop("target_role", None)
            if record["placement"] == "TRANSFER":
                record["target_role"] = "full-local"
        state = operator.select_current_state_rows(fixture.artifact, selection)
        state.update(target_operation_id="fixture-full-install", target_id="fixture-full-target", target_layout="full")
        return fixture, state, fixture.root / "full-current.sqlite3"

    def install(self, state, path, *, receipt_at=None):
        return Database(path).initialize_full_role(role="full-local", preserved_state=state,
            receipt_at=receipt_at if receipt_at is not None else lambda: NOW.isoformat())

    def test_selected_full_seed_preserves_raw_eleven_then_adds_only_missing_defaults_and_public_consumers(self):
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        for ordinary in (False, True):
            with self.subTest(ordinary_consumers=ordinary):
                fixture, state, path = self.fixture(ordinary=ordinary, drive_markers=True)
                source_before = fixture.source.read_bytes()
                native, calls, closed = sqlite3.connect, [], []
                class Traced(sqlite3.Connection):
                    def executescript(connection, *args):
                        raise AssertionError("full seed/defaults/first receipt must share one native transaction")
                    def close(connection):
                        closed.append(True)
                        return super().close()
                def connect(*args, **kwargs):
                    self.assertEqual(path, Path(args[0]))
                    calls.append("CONNECT")
                    connection = native(*args, **kwargs, factory=Traced)
                    connection.set_trace_callback(calls.append)
                    return connection
                clock = Mock(return_value=NOW.isoformat())
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
                    result = self.install(state, path, receipt_at=clock)
                clock.assert_called_once_with()
                self.assertEqual((1, 1, 1), tuple(calls.count(sql) for sql in ("CONNECT", "BEGIN IMMEDIATE", "COMMIT")))
                self.assertEqual([True], closed)
                self.assertEqual(source_before, fixture.source.read_bytes())
                stored = self.rows(path)
                expected = deepcopy(state["rows"])
                selected_keys = {row[0] for row in expected["settings"]}
                selected_columns = {row[0] for row in expected["column_settings"]}
                expected["settings"].extend((key, value) for key, value in DEFAULT_SETTINGS.items() if key not in selected_keys)
                expected["column_settings"].extend(row for row in DEFAULT_COLUMNS if row[0] not in selected_columns)
                self.assertEqual(expected, stored)
                self.assertEqual(_role_settings_seed(state["rows"], tables=QUERY_STATE_TABLES)[1], result["seed_sha256"])
                self.assertEqual("full", result["layout"])
                self.assertEqual("full_role_initialized_unbound", result["state"])
                self.assertEqual("SELECTED_USER_STATE", result["seed_kind"])
                self.assertFalse(result["replayed"])
                self.assertEqual("OFFLINE_UNBOUND", result["runtime_state"])
                for key in ("applied", "role_ready", "order_authority"):
                    self.assertIs(False, result[key])
                receipt = result["receipt"]
                plan = receipt["plan"]
                self.assertEqual("full-role-current-state-receipt/v1", receipt["format"])
                self.assertEqual("full-role-current-state-plan/v1", plan["format"])
                self.assertEqual(NOW.isoformat(), receipt["accepted_at"])
                for key in ("binding", "source_relations", "source_layouts", "target_id", "target_operation_id"):
                    self.assertEqual(state[key], plan[key])
                self.assertEqual({table: len(rows) for table, rows in state["rows"].items()}, plan["relation_counts"])
                self.assertEqual(result["seed_sha256"], plan["seed_sha256"])
                self.assertRegex(plan["fresh_defaults_sha256"], r"^[0-9a-f]{64}$")
                self.assertNotEqual(plan["seed_sha256"], plan["fresh_defaults_sha256"])
                with closing(native(path)) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertEqual(32, len(tables))
                    self.assertTrue({"themes", "stock_themes", "legacy_news_transfer_migrations"} <= tables)
                    self.assertFalse({"schema_migrations", "query_cache_schema"} & tables)
                    self.assertEqual([(1, "full")], connection.execute("SELECT schema_version,store_kind FROM role_settings_schema").fetchall())
                    self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
                    for table in ("themes", "stock_themes", "legacy_news_transfer_migrations"):
                        self.assertEqual([], connection.execute(f"SELECT * FROM {table}").fetchall())
                    self.assertEqual(receipt, json.loads(connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0]))
                    self.assertEqual(state["rows"]["central_setting_versions"], connection.execute("SELECT setting_key,updated_at FROM central_setting_versions ORDER BY rowid").fetchall())
                    for key, value in (("google_drive_unsynced_changes", "0"),
                                       ("google_drive_local_changed_at", ""),
                                       ("google_drive_last_upload_success_at", "")):
                        self.assertEqual((value,), connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone())
                        self.assertIsNone(connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key=?", (key,)).fetchone())
                    if not ordinary:
                        self.assertEqual([(b"original-version-\x00", "blob")], connection.execute("SELECT updated_at,typeof(updated_at) FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchall())
                        self.assertEqual([(88.5, "real", b"raw-body-hash", "2020-01-02T01:02:03Z")], connection.execute("SELECT confidence,typeof(confidence),body_hash,analyzed_at FROM profile_theme_suggestions").fetchall())
                    self.assertEqual([("2020-01-02T01:02:03Z",)], connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='rank_query_type'").fetchall())
                    self.assertEqual([("2020-01-02T10:02:03+09:00",)], connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='rank'").fetchall())
                before_read = path.read_bytes()
                database = Database(path)
                self.assertEqual("5", database.settings.get("rank_query_type"))
                self.assertEqual("uSeR", database.settings.get("theme_active_profile"))
                self.assertEqual("30", SettingsRepository(path, strict_text=True).get("refresh_interval_seconds"))
                self.assertEqual(ColumnSetting("rank", True, 0, 137), next(row for row in ColumnSettingsRepository(path).list() if row.name == "rank"))
                if ordinary:
                    themes = ThemeRepository.open_existing(path, database.settings.get("theme_active_profile"))
                    self.assertIsNotNone(themes)
                    self.assertEqual((("AI", "#123456"),), themes.list_themes())
                    self.assertEqual({"원종목": "AI"}, themes.all_by_name())
                    self.assertEqual("000001", StockRepository(path).find_code_by_name("LegacyAlias"))
                self.assertEqual(before_read, path.read_bytes())
                self.assertEqual(source_before, fixture.source.read_bytes())

    def test_fresh_plain_and_same_plan_reentry_keep_normal_edits_without_clock_defaults_or_dml(self):
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        for selected in (False, True):
            with self.subTest(selected=selected):
                fixture, state, path = self.fixture(ordinary=True)
                first = self.install(state, path) if selected else Database(path).initialize_full_role(role="full-local")
                if not selected:
                    self.assertEqual("FRESH", first["seed_kind"])
                    self.assertEqual(dict(DEFAULT_SETTINGS), dict(self.rows(path)["settings"]))
                    self.assertEqual(list(DEFAULT_COLUMNS), self.rows(path)["column_settings"])
                    self.assertEqual([(1, DEFAULT_SETTINGS["theme_active_profile"])], self.rows(path)["theme_profiles"])
                database = Database(path, now_provider=lambda: NOW)
                themes = ThemeRepository.open_existing(path, database.settings.get("theme_active_profile"), now_provider=lambda: NOW)
                themes.rename_profile(themes.active_profile, "EditedFull", settings=database.settings)
                database.settings.set("rank_query_type", "4")
                ColumnSettingsRepository(path, now_provider=lambda: NOW).save((ColumnSetting("rank", False, 7, 222),))
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("DELETE FROM settings WHERE key='refresh_interval_seconds'")
                    original = connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0]
                expected, before = self.rows(path), path.read_bytes()
                native, calls, closed = sqlite3.connect, [], []
                class Traced(sqlite3.Connection):
                    def close(connection):
                        closed.append(True)
                        return super().close()
                def connect(*args, **kwargs):
                    self.assertEqual(path, Path(args[0]))
                    connection = native(*args, **kwargs, factory=Traced)
                    connection.set_trace_callback(calls.append)
                    return connection
                forbidden_clock = Mock(side_effect=AssertionError("reentry must retain the original acceptance"))
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
                    plain = Database(path, now_provider=forbidden_clock).initialize()
                    replay = self.install(state, path, receipt_at=forbidden_clock) if selected else Database(path, now_provider=forbidden_clock).initialize_full_role(role="full-local")
                forbidden_clock.assert_not_called()
                self.assertTrue(plain["replayed"])
                self.assertTrue(replay["replayed"])
                self.assertTrue(closed)
                self.assertEqual([], [sql for sql in calls if sql.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "COMMIT"}])
                self.assertEqual(expected, self.rows(path))
                self.assertEqual(before, path.read_bytes())
                self.assertEqual("EditedFull", Database(path).settings.get("theme_active_profile"))
                self.assertEqual("4", Database(path).settings.get("rank_query_type"))
                with closing(native(path)) as connection:
                    self.assertEqual(original, connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0])
                    self.assertIsNone(connection.execute("SELECT value FROM settings WHERE key='refresh_interval_seconds'").fetchone())
                if selected:
                    self.assertEqual(first["receipt"], plain["receipt"])
                    self.assertEqual(first["receipt"], replay["receipt"])

    def test_unaccepted_full_installations_never_adopt_targets_or_commit_partial_state(self):
        fixture, state, path = self.fixture()
        for kind, mutate in (
            ("missing-layout", lambda value: value.pop("target_layout")),
            ("query-layout", lambda value: value.update(target_layout="query")),
            ("wrong-binding", lambda value: value["binding"].update(target_role="other-role")),
            ("missing-target", lambda value: value.pop("target_id")),
            ("invalid-primitive", lambda value: value["rows"]["settings"].append(("invalid", True))),
            ("invalid-source-layout", lambda value: value["source_layouts"]["theme_profiles"].update(profile_id_type="TEXT")),
        ):
            with self.subTest(rejected_input=kind):
                broken, target = deepcopy(state), path.with_name(kind + ".sqlite3")
                mutate(broken)
                clock = Mock(side_effect=AssertionError("rejected input has no acceptance"))
                with self.assertRaises(ValueError):
                    self.install(broken, target, receipt_at=clock)
                clock.assert_not_called()
                self.assertFalse(target.exists())
        for kind in ("query", "legacy-full", "fresh-full", "settings-only", "foreign", "wrong-role", "target-conflict", "unknown-kind"):
            with self.subTest(rejected_existing=kind):
                target = path.with_name(kind + ".sqlite3")
                if kind == "query":
                    Database(target).initialize_query_role(role="full-local")
                elif kind == "legacy-full":
                    Database(target).initialize()
                elif kind == "fresh-full":
                    Database(target).initialize_full_role(role="full-local")
                elif kind == "settings-only":
                    Database(target).initialize_role_settings(role="full-local")
                elif kind == "foreign":
                    with closing(sqlite3.connect(target)) as connection, connection:
                        connection.execute("CREATE TABLE foreign_data(value BLOB)")
                        connection.execute("INSERT INTO foreign_data VALUES(?)", (b"keep-full-source",))
                else:
                    self.install(state, target)
                    if kind == "unknown-kind":
                        with closing(sqlite3.connect(target)) as connection, connection:
                            connection.execute("UPDATE role_settings_schema SET seed_kind='UNKNOWN_FUTURE'")
                before = target.read_bytes()
                with self.assertRaises(ValueError):
                    if kind == "wrong-role":
                        Database(target).initialize_full_role(role="other-role")
                    elif kind == "target-conflict":
                        broken = deepcopy(state)
                        broken["target_id"] = "different-full-target"
                        self.install(broken, target)
                    else:
                        self.install(state, target)
                self.assertEqual(before, target.read_bytes())
                if kind == "query":
                    with self.assertRaises(ValueError):
                        Database(target).initialize()
                    self.assertEqual(before, target.read_bytes())
                elif kind == "target-conflict":
                    with self.assertRaises(ValueError):
                        Database(target).initialize_query_role(role="full-local")
                    self.assertEqual(before, target.read_bytes())
        native = sqlite3.connect
        for failure in ("foreign-key", "affinity", "receipt", "clock", "cancel"):
            with self.subTest(rolled_back=failure):
                target, broken, calls = path.with_name(failure + ".sqlite3"), deepcopy(state), []
                if failure == "foreign-key":
                    row = broken["rows"]["profile_themes"][0]
                    broken["rows"]["profile_themes"][0] = (900, *row[1:])
                elif failure == "affinity":
                    row = broken["rows"]["profile_theme_suggestions"][0]
                    broken["rows"]["profile_theme_suggestions"][0] = (*row[:5], "88", *row[6:])
                class Failing(sqlite3.Connection):
                    def execute(connection, sql, *args):
                        if sql.startswith("INSERT INTO role_settings_schema") and failure in {"receipt", "cancel"}:
                            if failure == "cancel":
                                raise KeyboardInterrupt("controlled full first receipt cancellation")
                            raise sqlite3.OperationalError("controlled full first receipt failure")
                        return super().execute(sql, *args)
                    def executescript(connection, *args):
                        raise AssertionError("full initialization must not split its transaction")
                def connect(*args, **kwargs):
                    connection = native(*args, **kwargs, factory=Failing)
                    connection.set_trace_callback(calls.append)
                    return connection
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
                    with self.assertRaises((ValueError, sqlite3.IntegrityError, sqlite3.OperationalError, KeyboardInterrupt)):
                        self.install(broken, target, receipt_at=lambda: "2026-10-25T01:00:00+00:00" if failure == "clock" else NOW.isoformat())
                self.assertEqual(0, calls.count("COMMIT"))
                with closing(native(target)) as connection:
                    self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
                self.assertFalse(self.install(state, target)["replayed"])

    def test_full_commit_and_close_ack_loss_recover_first_receipt_after_public_edits(self):
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        for phase in ("commit", "close"):
            with self.subTest(phase=phase):
                fixture, state, path = self.fixture(ordinary=True)
                native, closed = sqlite3.connect, []
                class LostAck(sqlite3.Connection):
                    def commit(connection):
                        super().commit()
                        if phase == "commit":
                            raise OSError("controlled full COMMIT ACK loss")
                    def close(connection):
                        super().close()
                        closed.append(True)
                        if phase == "close":
                            raise OSError("controlled full close ACK loss")
                def connect(*args, **kwargs):
                    self.assertEqual(path, Path(args[0]))
                    return native(*args, **kwargs, factory=LostAck)
                clock = Mock(return_value=NOW.isoformat())
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=connect):
                    with self.assertRaisesRegex(OSError, "^controlled full (COMMIT|close) ACK loss$"):
                        self.install(state, path, receipt_at=clock)
                clock.assert_called_once_with()
                self.assertEqual([True], closed)
                with closing(native(path)) as connection:
                    original = json.loads(connection.execute("SELECT preservation_receipt_json FROM role_settings_schema").fetchone()[0])
                database = Database(path, now_provider=lambda: NOW)
                themes = ThemeRepository.open_existing(path, "User", now_provider=lambda: NOW)
                themes.rename_profile("User", "EditedFull", settings=database.settings)
                database.settings.set("rank_query_type", "4")
                before, calls = self.rows(path), []
                def traced(*args, **kwargs):
                    connection = native(*args, **kwargs)
                    connection.set_trace_callback(calls.append)
                    return connection
                forbidden_clock = Mock(side_effect=AssertionError("ACK recovery keeps first receipt clock"))
                with patch("kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect", side_effect=traced):
                    retry = self.install(state, path, receipt_at=forbidden_clock)
                    plain = Database(path, now_provider=forbidden_clock).initialize()
                forbidden_clock.assert_not_called()
                self.assertTrue(retry["replayed"])
                self.assertTrue(plain["replayed"])
                self.assertEqual(original, retry["receipt"])
                self.assertEqual(original, plain["receipt"])
                self.assertEqual(NOW.isoformat(), original["accepted_at"])
                self.assertEqual(before, self.rows(path))
                self.assertEqual([], [sql for sql in calls if sql.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "COMMIT"}])
                self.assertEqual("EditedFull", Database(path).settings.get("theme_active_profile"))
                self.assertEqual("4", Database(path).settings.get("rank_query_type"))


class FullRoleRestoreValidationTests(unittest.TestCase):
    fixture = FullRoleCurrentStateTests.fixture
    install = FullRoleCurrentStateTests.install
    rows = FullRoleCurrentStateTests.rows

    def test_existing_only_full_validation_rejects_missing_empty_legacy_query_and_wrong_role_without_creation_or_writes(self):
        for kind in ('missing', 'empty', 'legacy', 'settings-only', 'query', 'wrong-role', 'foreign'):
            with self.subTest(kind=kind):
                root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                path = root / 'monitor.sqlite3'
                if kind == 'empty':
                    path.touch()
                elif kind == 'legacy':
                    Database(path).initialize()
                elif kind == 'settings-only':
                    Database(path).initialize_role_settings(role='full-local')
                elif kind == 'query':
                    Database(path).initialize_query_role(role='full-local')
                elif kind == 'wrong-role':
                    Database(path).initialize_full_role(role='another-full-role')
                elif kind == 'foreign':
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute('CREATE TABLE user_original(value BLOB)')
                        connection.execute('INSERT INTO user_original VALUES(?)', (b'owned-original-\x00',))
                before = path.read_bytes() if path.exists() else None
                native, statements, opened, closed = sqlite3.connect, [], [], []

                class Traced(sqlite3.Connection):
                    def commit(connection):
                        raise AssertionError('restore validation attempted COMMIT')

                    def close(connection):
                        closed.append(True)
                        return super().close()

                def connect(*args, **kwargs):
                    self.assertEqual(path.as_uri() + '?mode=rw', kwargs.get('database', args[0] if args else None))
                    self.assertIs(True, kwargs.get('uri'))
                    connection = native(*args, **kwargs, factory=Traced)
                    opened.append(True)
                    connection.set_trace_callback(statements.append)
                    return connection

                clock = Mock(side_effect=AssertionError('restore validation sampled a new business clock'))
                with patch('kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect', side_effect=connect):
                    with self.assertRaises((ValueError, sqlite3.OperationalError)):
                        Database(path, now_provider=clock).initialize_full_role(role='full-local', allow_create=False)
                clock.assert_not_called()
                self.assertEqual(opened, closed)
                self.assertEqual([], [sql for sql in statements if sql.lstrip().split(' ', 1)[0].upper()
                                      in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'ALTER', 'DROP', 'COMMIT'}])
                if before is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(before, path.read_bytes())
                self.assertEqual({'monitor.sqlite3'} if before is not None else set(),
                                 {item.name for item in root.iterdir()})

    def test_existing_only_full_validation_replays_native_fresh_or_selected_target_without_defaults_clock_or_receipt_rewrite(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                fixture, state, path = self.fixture(ordinary=True)
                source_before = fixture.source.read_bytes()
                first = self.install(state, path) if selected else Database(path).initialize_full_role(role='full-local')
                Database(path, now_provider=lambda: NOW).settings.set('rank_query_type', '4')
                ColumnSettingsRepository(path, now_provider=lambda: NOW).save((ColumnSetting('rank', False, 7, 222),))
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("DELETE FROM settings WHERE key='refresh_interval_seconds'")
                    receipt_before = connection.execute('SELECT preservation_receipt_json FROM role_settings_schema').fetchone()[0]
                expected, before = self.rows(path), path.read_bytes()
                native, statements, opened, closed = sqlite3.connect, [], [], []

                class Traced(sqlite3.Connection):
                    def commit(connection):
                        raise AssertionError('existing-only reentry attempted COMMIT')

                    def close(connection):
                        closed.append(True)
                        return super().close()

                def connect(*args, **kwargs):
                    self.assertEqual(path.as_uri() + '?mode=rw', kwargs.get('database', args[0] if args else None))
                    self.assertIs(True, kwargs.get('uri'))
                    connection = native(*args, **kwargs, factory=Traced)
                    opened.append(True)
                    connection.set_trace_callback(statements.append)
                    return connection

                clock = Mock(side_effect=AssertionError('existing-only reentry sampled a new business clock'))
                with patch('kiwoom_monitor.infrastructure.persistence.database.sqlite3.connect', side_effect=connect):
                    database = Database(path, now_provider=clock)
                    replays = [database.initialize_full_role(role='full-local', allow_create=False) for _ in range(2)]
                clock.assert_not_called()
                self.assertEqual([True, True], opened)
                self.assertEqual(opened, closed)
                self.assertTrue(all(result['replayed'] for result in replays))
                self.assertTrue(all(result['seed_sha256'] == first['seed_sha256'] for result in replays))
                self.assertEqual([], [sql for sql in statements if sql.lstrip().split(' ', 1)[0].upper()
                                      in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'ALTER', 'DROP', 'COMMIT'}])
                self.assertEqual(expected, self.rows(path))
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(source_before, fixture.source.read_bytes())
                with closing(native(path)) as connection:
                    self.assertEqual(receipt_before, connection.execute('SELECT preservation_receipt_json FROM role_settings_schema').fetchone()[0])
                    self.assertIsNone(connection.execute("SELECT value FROM settings WHERE key='refresh_interval_seconds'").fetchone())
                if selected:
                    self.assertEqual([first['receipt'], first['receipt']], [result['receipt'] for result in replays])


if __name__ == "__main__":
    unittest.main()
