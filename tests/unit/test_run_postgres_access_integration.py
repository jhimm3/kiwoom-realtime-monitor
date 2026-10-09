from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.run_postgres_access_integration import (
    _dedicated_url,
    _explicit_dedicated_url,
    _install_project_import_paths,
    _resolve_target_url,
    main,
)
from tests.integration.postgres_test_support import isolated_observation_schema


class ObservationSchemaFixtureTests(unittest.TestCase):
    URL = "postgresql://user:secret@localhost/kiwoom_monitor_diagnostic_test?sslmode=require"

    def test_non_diagnostic_url_is_rejected_before_connecting(self) -> None:
        with patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "dedicated diagnostic database"):
                with isolated_observation_schema("postgresql://user:secret@localhost/kiwoom_monitor"):
                    self.fail("production database must not be touched")
            connect.assert_not_called()

    def test_connected_database_is_verified_before_creating_schema(self) -> None:
        with patch("psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("production",)
            with self.assertRaisesRegex(RuntimeError, "non-diagnostic database"):
                with isolated_observation_schema(self.URL):
                    self.fail("wrong connected database must not be touched")
            self.assertEqual([unittest.mock.call("SELECT current_database()")], cursor.execute.call_args_list)

    def test_owned_schema_and_connection_options_survive_body_failure(self) -> None:
        from psycopg.conninfo import conninfo_to_dict

        source = self.URL + "&options=-c%20statement_timeout%3D12000"
        with patch("psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("kiwoom_monitor_diagnostic_test",)
            with self.assertRaisesRegex(OSError, "injected test failure"):
                with isolated_observation_schema(source) as scoped:
                    parameters = conninfo_to_dict(scoped)
                    self.assertEqual("require", parameters["sslmode"])
                    self.assertEqual("kiwoom_monitor_diagnostic_test", parameters["dbname"])
                    self.assertRegex(parameters["options"],
                                     r"^-c statement_timeout=12000 -c search_path=observation_test_[0-9a-f]{32}$")
                    schema = parameters["options"].rsplit("=", 1)[1]
                    raise OSError("injected test failure")
            statements = [args[0].as_string() for args, _ in cursor.execute.call_args_list
                          if not isinstance(args[0], str)]
            self.assertEqual([f'CREATE SCHEMA "{schema}"', f'DROP SCHEMA "{schema}" CASCADE'], statements)

    def test_failed_schema_cleanup_propagates(self) -> None:
        with patch("psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("kiwoom_monitor_diagnostic_test",)

            def execute(statement):
                if not isinstance(statement, str) and statement.as_string().startswith("DROP SCHEMA"):
                    raise OSError("injected cleanup failure")

            cursor.execute.side_effect = execute
            with self.assertRaisesRegex(OSError, "cleanup failure"):
                with isolated_observation_schema(self.URL):
                    pass


class DedicatedPostgresUrlTests(unittest.TestCase):
    def test_candidate_src_precedes_installed_packages_and_test_root(self) -> None:
        root = Path("candidate.zip")
        with patch.object(sys, "path", ["existing"]):
            _install_project_import_paths(root)
            self.assertEqual(
                [str(root / "src"), str(root), "existing"],
                sys.path,
            )

    def test_replaces_database_and_preserves_connection_options(self) -> None:
        source = "postgresql://user:secret@database:5432/kiwoom_monitor?sslmode=prefer"
        self.assertEqual(
            "postgresql://user:secret@database:5432/kiwoom_monitor_diagnostic_test?sslmode=prefer",
            _dedicated_url(source),
        )

    def test_rejects_diagnostic_database_as_source(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "points at the diagnostic test DB"):
            _dedicated_url(
                "postgresql://user:secret@database:5432/kiwoom_monitor_diagnostic_test"
            )

    def test_rejects_non_postgresql_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not a PostgreSQL URL"):
            _dedicated_url("sqlite:///monitor.db")

    def test_accepts_explicit_diagnostic_url_without_live_database_url(self) -> None:
        target = "postgresql://user:secret@test-host:5432/kiwoom_monitor_diagnostic_test?sslmode=require"
        self.assertEqual(target, _resolve_target_url("", target))

    def test_explicit_url_must_name_the_dedicated_database(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must target kiwoom_monitor_diagnostic_test"):
            _explicit_dedicated_url("postgresql://user:secret@test-host/kiwoom_monitor")

    def test_explicit_url_rejects_invalid_driver_or_missing_host(self) -> None:
        for value in ("sqlite:///monitor.db", "postgresql:///kiwoom_monitor_diagnostic_test"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "not a PostgreSQL URL"):
                    _explicit_dedicated_url(value)

    def test_without_explicit_url_still_derives_from_live_database_url(self) -> None:
        live = "postgresql://user:secret@test-host:5432/kiwoom_monitor?sslmode=require"
        self.assertEqual(_dedicated_url(live), _resolve_target_url(live, ""))

    def test_main_rejects_empty_skip_and_expected_failure_without_contacting_database(self) -> None:
        class Passing(unittest.TestCase):
            def test_contract(self) -> None:
                self.assertTrue(True)

        class Skipped(unittest.TestCase):
            @unittest.skip("required environment missing")
            def test_contract(self) -> None:
                self.fail("should not run")

        class ExpectedFailure(unittest.TestCase):
            @unittest.expectedFailure
            def test_contract(self) -> None:
                self.fail("broken contract")

        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            "kiwoom_monitor_diagnostic_test",
        )
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        for cases, expected in (((), 1), ((Skipped("test_contract"),), 1),
                                ((ExpectedFailure("test_contract"),), 1),
                                ((Passing("test_contract"),), 0)):
            with self.subTest(cases=cases), patch.dict(sys.modules, {"psycopg": driver}), \
                    patch.dict("os.environ", {
                        "KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL":
                        "postgresql://user:secret@localhost/kiwoom_monitor_diagnostic_test",
                        "KIWOOM_SERVER_DATABASE_URL": "",
                    }), patch.object(sys, "argv", ["runner", "--test", "synthetic.contract"]), \
                    patch.object(unittest.defaultTestLoader, "loadTestsFromName",
                                 return_value=unittest.TestSuite(cases)), \
                    redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                self.assertEqual(expected, main())
        self.assertEqual(4, driver.connect.call_count)


if __name__ == "__main__":
    unittest.main()
