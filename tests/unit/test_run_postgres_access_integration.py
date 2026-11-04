from __future__ import annotations

import sys
import json
import shutil
import subprocess
import tempfile
import textwrap
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
    def test_url_policy_is_shared_by_cli_and_replay_and_preserves_exact_errors(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_replay import dedicated_database_url
        from kiwoom_monitor.infrastructure.persistence import postgres_test_target as target

        self.assertIs(target.dedicated_url, _dedicated_url)
        self.assertIs(target.explicit_dedicated_url, _explicit_dedicated_url)
        self.assertIs(target.resolve_target_url, _resolve_target_url)
        source = "postgres://user:secret@host/live?sslmode=require#fragment"
        expected = "postgres://user:secret@host/kiwoom_monitor_diagnostic_test?sslmode=require#fragment"
        self.assertEqual(expected, dedicated_database_url(source))
        self.assertEqual(expected, _resolve_target_url(source, ""))
        cases = [
            ("", "", "KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL or KIWOOM_SERVER_DATABASE_URL is required"),
            ("sqlite:///db", "", "KIWOOM_SERVER_DATABASE_URL is not a PostgreSQL URL"),
            (expected, "", "KIWOOM_SERVER_DATABASE_URL points at the diagnostic test DB"),
            (source, "postgres://host/live", "diagnostic DB URL must target kiwoom_monitor_diagnostic_test"),
            (source, "postgres:///kiwoom_monitor_diagnostic_test", "KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL is not a PostgreSQL URL"),
        ]
        for live, explicit, message in cases:
            for resolve in (_resolve_target_url, dedicated_database_url):
                with self.subTest(live=live, explicit=explicit, resolve=resolve.__name__):
                    with self.assertRaises(RuntimeError) as caught:
                        resolve(live, explicit)
                    self.assertEqual(message, str(caught.exception))

    def test_copied_src_without_scripts_keeps_tool_cancel_report_and_native_reader(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            shutil.copytree(root / "src", candidate / "src",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            self.assertFalse((candidate / "scripts").exists())
            code = textwrap.dedent('''
                import importlib.util
                import json
                import os
                import sys
                from pathlib import Path
                from threading import Event
                from types import SimpleNamespace

                candidate = Path(sys.argv[1]).resolve()
                original = Path(sys.argv[2]).resolve()
                sys.path[:] = [str(candidate / 'src')] + [entry for entry in sys.path
                    if entry and ('site-packages' in Path(entry).parts
                        or not Path(entry).resolve().is_relative_to(original))]
                assert importlib.util.find_spec('scripts') is None
                from kiwoom_monitor.central_server import diagnostic_sampling as sampling
                from kiwoom_monitor.central_server.database import SQLiteQueryStore
                from kiwoom_monitor.central_server.diagnostic_replay import dedicated_database_url
                from kiwoom_monitor.central_server.diagnostic_runs import DiagnosticRuns
                from kiwoom_monitor.central_server.diagnostic_workloads import (
                    _set_tool, capture_status, diagnostic_run_lock, diagnostic_tool_status)

                target = dedicated_database_url('postgres://user:secret@host/live?sslmode=require')
                assert target == 'postgres://user:secret@host/kiwoom_monitor_diagnostic_test?sslmode=require'
                data = candidate / 'data'
                data.mkdir()
                path = data / 'controls.json'
                os.environ['KIWOOM_DIAGNOSTIC_WORKLOAD_PATH'] = str(path)
                session = _set_tool(path, True, 600)['diagnostic_tool']['session_id']
                stop = Event()
                stop.set()
                # An enabled real session cancels before DB admission. This is
                # the actual shared implementation, without import or DB doubles.
                result = sampling.run_measurement(
                    SimpleNamespace(command='measure', label='cancel', workload=''),
                    path, 5, 'copied-cancel', session, api=lambda *_: {}, stop=stop)
                assert result == {'kind': 'measure', 'test_id': 'copied-cancel',
                    'state': 'aborted', 'phase': {'label': 'cancel', 'state': 'aborted',
                    'reason': 'diagnostic_session_ended', 'samples': 0, 'elapsed_seconds': 0}}
                runs = DiagnosticRuns(path, lambda *_: {})
                runs._stop.set()
                run = {'run_id': 'copied-cancel', 'session_id': session,
                    'requested_seconds': 5, 'kind': 'measure', 'label': 'cancel', 'workload': ''}
                lock = diagnostic_run_lock(path)
                lock.__enter__()
                runs._run(run, lock, False)
                runs.close(timeout=None)
                report = runs.report('copied-cancel')
                assert report['state'] == 'aborted'
                assert report['result'] == result
                assert not capture_status(path)['enabled']
                assert diagnostic_tool_status(path)['session_id'] == session
                assert diagnostic_tool_status(path)['enabled']
                with diagnostic_run_lock(path):
                    pass

                store_path = data / 'native.sqlite3'
                store = SQLiteQueryStore(store_path)
                store.initialize()
                document = {'value': 17, 'source': 'retained-fixture',
                            'available_at': '2026-10-23T09:00:00+09:00'}
                store.upsert_documents('app_settings', [
                    {'owner': 'copied', 'key': 'accepted', 'document': document}])
                store.close()
                reopened = SQLiteQueryStore(store_path)
                reopened.initialize()
                assert reopened.load_documents('app_settings', 'copied')[0]['document'] == document
                reopened.close()
                modules = {name: str(Path(module.__file__).resolve())
                    for name, module in sys.modules.items()
                    if name.startswith('kiwoom_monitor') and getattr(module, '__file__', None)}
                assert all(Path(value).is_relative_to(candidate / 'src') for value in modules.values())
                assert not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules)
                print(json.dumps({'scripts_absent': True, 'tool_enabled': True,
                    'cancel_report': report['state'], 'native_reopen': True,
                    'source_modules': modules}, sort_keys=True))
            ''')
            process = subprocess.run(
                [sys.executable, "-I", "-c", code, str(candidate), str(root)],
                cwd=candidate, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            evidence = json.loads(process.stdout)
            self.assertTrue(evidence["scripts_absent"])
            self.assertTrue(evidence["tool_enabled"])
            self.assertEqual("aborted", evidence["cancel_report"])
            self.assertTrue(evidence["native_reopen"])
            self.assertIn("kiwoom_monitor.central_server.diagnostic_sampling", evidence["source_modules"])

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
        for value in ("sqlite:///monitor.db", "postgres-invalid://host/live", "postgresql+evil://host/live"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "not a PostgreSQL URL"):
                _dedicated_url(value)

    def test_accepts_explicit_diagnostic_url_without_live_database_url(self) -> None:
        target = "postgresql://user:secret@test-host:5432/kiwoom_monitor_diagnostic_test?sslmode=require"
        self.assertEqual(target, _resolve_target_url("", target))

    def test_explicit_url_must_name_the_dedicated_database(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must target kiwoom_monitor_diagnostic_test"):
            _explicit_dedicated_url("postgresql://user:secret@test-host/kiwoom_monitor")

    def test_explicit_url_rejects_invalid_driver_or_missing_host(self) -> None:
        for value in ("sqlite:///monitor.db", "postgresql:///kiwoom_monitor_diagnostic_test", "postgres-invalid://host/kiwoom_monitor_diagnostic_test"):
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

        class Failed(unittest.TestCase):
            def test_contract(self):
                self.fail("fixture")
        class Errored(unittest.TestCase):
            def test_contract(self):
                raise RuntimeError("fixture")
        class UnexpectedSuccess(unittest.TestCase):
            @unittest.expectedFailure
            def test_contract(self):
                pass

        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            "kiwoom_monitor_diagnostic_test",
        )
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        for cases, expected in (((), 1), ((Skipped("test_contract"),), 1),
                                ((ExpectedFailure("test_contract"),), 1),
                                ((Failed("test_contract"),), 1), ((Errored("test_contract"),), 1),
                                ((UnexpectedSuccess("test_contract"),), 1), ((Passing("test_contract"),), 0)):
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
        self.assertEqual(7, driver.connect.call_count)


if __name__ == "__main__":
    unittest.main()
