from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure import app_paths
from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease, StrictRestoreCoordinator, StrictRestoreError


class AppPathsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.local = self.root / 'user-local'
        self.enterContext(patch.dict(os.environ, {
            'LOCALAPPDATA': str(self.local),
            'KIWOOM_DESKTOP_DATA_DIR': str(self.root / 'lease-association-only'),
        }))

    def source_module(self, source=True):
        root = self.root / ('checkout' if source else 'venv/Lib')
        module = root / ('src' if source else 'site-packages') / 'kiwoom_monitor/infrastructure/app_paths.py'
        module.parent.mkdir(parents=True)
        module.touch()
        (root / 'pyproject.toml').write_text('[project]\nname="fixture"\n', encoding='utf-8')
        return root, module

    def frozen_install(self):
        executable = self.root / 'installed/KiwoomMonitor.exe'
        legacy = executable.parent / 'data'
        legacy.mkdir(parents=True)
        for name in ('api.env', 'account-bindings.dat', 'google_drive_client.json', 'google_drive_token.dat'):
            (legacy / name).write_bytes(('legacy-' + name).encode())
        with closing(sqlite3.connect(legacy / 'monitor.sqlite3')) as connection:
            with connection:
                connection.execute('CREATE TABLE fixture(value TEXT)')
                connection.execute("INSERT INTO fixture VALUES('legacy-monitor')")
        for directory in ('strength_icons', 'near_high_icons', 'near_high_sounds'):
            (legacy / directory).mkdir()
            (legacy / directory / 'custom.dat').write_bytes(directory.encode())
        for name in ('interest.mp3', 'caution.mp3', 'fire.mp3'):
            (legacy / 'near_high_sounds' / name).write_bytes(name.encode())
        original = {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
        self.enterContext(patch.object(sys, 'frozen', True, create=True))
        self.enterContext(patch.object(sys, 'executable', str(executable)))
        return legacy, original

    def test_source_checkout_keeps_project_data_and_ignores_desktop_lease_association(self):
        root, module = self.source_module()
        with patch.object(app_paths, '__file__', str(module)), patch.object(sys, 'frozen', False, create=True), \
                patch.object(app_paths.shutil, 'copy2', side_effect=AssertionError('getter copied a file')), \
                patch.object(app_paths.shutil, 'copytree', side_effect=AssertionError('getter copied a directory')):
            paths = AppPaths.for_current_user()
            self.assertEqual(root / 'data', paths.data_dir)
            self.assertEqual(root / 'data/logs', paths.log_dir)
            self.assertEqual(root / 'data/monitor.sqlite3', paths.database_path)
            self.assertTrue(paths.log_dir.is_dir())
            self.assertIsNone(paths.desktop_startup_preparation())
        self.assertFalse((self.root / 'lease-association-only').exists())

    def test_installed_wheel_uses_user_data_not_Lib_data_even_with_unrelated_project_marker(self):
        lib, module = self.source_module(source=False)
        with patch.object(app_paths, '__file__', str(module)), patch.object(sys, 'frozen', False, create=True):
            paths = AppPaths.for_current_user()
            self.assertEqual(self.local / 'KiwoomMonitor/data', paths.data_dir)
            self.assertFalse((lib / 'data').exists())
            self.assertIsNone(paths.desktop_startup_preparation())
        self.assertFalse((self.root / 'lease-association-only').exists())

    def test_frozen_getter_and_all_repeated_default_lookups_are_copy_free(self):
        legacy, original = self.frozen_install()
        with patch.object(app_paths.shutil, 'copy2', side_effect=AssertionError('getter copied')), \
                patch.object(app_paths.shutil, 'copytree', side_effect=AssertionError('getter copied')):
            for _ in range(3):
                paths = AppPaths.for_current_user()
                self.assertEqual(self.local / 'KiwoomMonitor/data', paths.data_dir)
                self.assertTrue(paths.log_dir.is_dir())
                self.assertFalse(paths.database_path.exists())
                self.assertEqual([], [p for p in paths.data_dir.rglob('*') if p.is_file()])
        self.assertEqual(original, {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob('*') if p.is_file()})

    def test_query_copies_only_three_public_sounds_inside_actual_exclusive_lease(self):
        legacy, original = self.frozen_install()
        paths = AppPaths.for_current_user()
        calls = []
        native_copy = app_paths.shutil.copy2
        def copy(source, destination):
            with self.assertRaises(StrictRestoreError):
                AppDataLease(paths.data_dir, exclusive=False, timeout=0.05)
            calls.append(Path(source).name)
            return native_copy(source, destination)
        with patch.object(AppPaths, '_migrate_legacy_personal_data', side_effect=AssertionError('query imported personal data')), \
                patch.object(app_paths.shutil, 'copy2', side_effect=copy):
            lease, outcome = StrictRestoreCoordinator(paths.database_path, paths.news_database_path).enter_main(
                startup_preparation=lambda: paths.desktop_startup_preparation(query_only=True))
        try:
            self.assertIsNone(outcome)
            self.assertEqual(['interest.mp3', 'caution.mp3', 'fire.mp3'], calls)
            self.assertEqual({'near_high_sounds/' + name for name in calls},
                             {p.relative_to(paths.data_dir).as_posix() for p in paths.data_dir.rglob('*') if p.is_file() and p.name != '.strict_restore.lock'})
            self.assertIsNone(paths.desktop_startup_preparation(query_only=True))
            with self.assertRaises(StrictRestoreError):
                AppDataLease(paths.data_dir, exclusive=True, timeout=0.05)
        finally:
            lease.close()
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass
        self.assertEqual(original, {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob('*') if p.is_file()})

    def test_full_preserves_legacy_order_no_overwrite_custom_assets_and_closed_DB_bytes(self):
        legacy, original = self.frozen_install()
        paths = AppPaths.for_current_user()
        (paths.data_dir / 'api.env').write_bytes(b'current-api')
        (paths.data_dir / 'strength_icons').mkdir()
        (paths.data_dir / 'strength_icons/keep.png').write_bytes(b'current-icon')
        calls = []
        native_file, native_tree = app_paths.shutil.copy2, app_paths.shutil.copytree
        def copy_file(source, destination):
            calls.append(('file', Path(source).name))
            return native_file(source, destination)
        def copy_tree(source, destination):
            calls.append(('directory', Path(source).name))
            return native_tree(source, destination)
        with patch.object(app_paths.shutil, 'copy2', side_effect=copy_file), \
                patch.object(app_paths.shutil, 'copytree', side_effect=copy_tree):
            lease, _ = StrictRestoreCoordinator(paths.database_path, paths.news_database_path).enter_main(
                startup_preparation=lambda: paths.desktop_startup_preparation())
        lease.close()
        self.assertEqual([('file', 'account-bindings.dat'), ('file', 'monitor.sqlite3'),
                          ('file', 'google_drive_client.json'), ('file', 'google_drive_token.dat'),
                          ('directory', 'near_high_icons'), ('directory', 'near_high_sounds')], calls)
        self.assertEqual(b'current-api', (paths.data_dir / 'api.env').read_bytes())
        self.assertEqual(b'current-icon', (paths.data_dir / 'strength_icons/keep.png').read_bytes())
        self.assertFalse((paths.data_dir / 'strength_icons/custom.dat').exists())
        self.assertEqual(original['monitor.sqlite3'], paths.database_path.read_bytes())
        with closing(sqlite3.connect(paths.database_path)) as connection:
            self.assertEqual('legacy-monitor', connection.execute('SELECT value FROM fixture').fetchone()[0])
        self.assertIsNone(paths.desktop_startup_preparation())
        self.assertEqual(original, {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob('*') if p.is_file()})

    def test_prepared_action_rechecks_destinations_instead_of_overwriting_new_state(self):
        self.frozen_install()
        paths = AppPaths.for_current_user()
        action = paths.desktop_startup_preparation(query_only=True)
        self.assertIsNotNone(action)
        target = paths.data_dir / 'near_high_sounds'
        target.mkdir()
        (target / 'interest.mp3').write_bytes(b'accepted-after-plan')
        lease, _ = StrictRestoreCoordinator(paths.database_path, paths.news_database_path).enter_main(
            startup_preparation=lambda: action)
        lease.close()
        self.assertEqual(b'accepted-after-plan', (target / 'interest.mp3').read_bytes())
        self.assertEqual(b'caution.mp3', (target / 'caution.mp3').read_bytes())
        self.assertEqual(b'fire.mp3', (target / 'fire.mp3').read_bytes())

    def test_nonfrozen_wheel_never_imports_executable_legacy_data(self):
        self.frozen_install()
        _, module = self.source_module(source=False)
        with patch.object(sys, 'frozen', False), patch.object(app_paths, '__file__', str(module)):
            paths = AppPaths.for_current_user()
            self.assertIsNone(paths.desktop_startup_preparation())
            self.assertFalse(paths.database_path.exists())

    def _private_copy_fixture(self, directory=False, directory_name='strength_icons'):
        legacy = self.root / 'installation/data'
        legacy.mkdir(parents=True)
        source = legacy / (directory_name if directory else 'api.env')
        if directory:
            source.mkdir()
            (source / 'original.png').write_bytes(b'owned-original')
        else:
            source.write_bytes(b'owned-original')
        self.enterContext(patch.object(sys, 'frozen', True, create=True))
        self.enterContext(patch.object(sys, 'executable', str(legacy.parent / 'KiwoomMonitor.exe')))
        paths = AppPaths.for_current_user()
        coordinator = StrictRestoreCoordinator(paths.database_path, paths.news_database_path)
        return source, paths.data_dir / source.name, paths, coordinator

    def _failed_private_copy_retries_without_partial(self, directory=False, directory_name='strength_icons'):
        source, target, paths, coordinator = self._private_copy_fixture(directory, directory_name)
        original = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()} if directory else source.read_bytes()
        failure = OSError('test-owned private-copy-failure')
        def fail(src, dst):
            self.assertEqual(source, src)
            with self.assertRaises(StrictRestoreError):
                AppDataLease(paths.data_dir, exclusive=False, timeout=0.05)
            if directory:
                Path(dst).mkdir()
                (Path(dst) / 'partial.png').write_bytes(b'partial')
            else:
                Path(dst).write_bytes(b'partial')
            self.assertFalse(target.exists())
            raise failure
        with patch.object(app_paths.shutil, 'copytree' if directory else 'copy2', side_effect=fail):
            with self.assertRaises(OSError) as caught:
                coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        self.assertIs(failure, caught.exception)
        self.assertFalse(target.exists())
        self.assertFalse(paths.database_path.exists())
        self.assertEqual([], list(paths.data_dir.glob('.*.tmp')))
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass
        self.assertIsNotNone(paths.desktop_startup_preparation())
        lease, _ = coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        lease.close()
        self.assertIsNone(paths.desktop_startup_preparation())
        copied = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()} if directory else target.read_bytes()
        preserved = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()} if directory else source.read_bytes()
        self.assertEqual(original, copied)
        self.assertEqual(original, preserved)
        self.assertEqual([], list(paths.data_dir.glob('.*.tmp')))

    def test_private_file_copy_error_propagates_cleans_partial_and_retries_on_next_start(self):
        self._failed_private_copy_retries_without_partial()

    def test_private_directory_copy_error_propagates_cleans_partial_and_retries_on_next_start(self):
        self._failed_private_copy_retries_without_partial(directory=True)

    def test_user_sound_directory_extra_asset_copy_error_remains_fatal_and_retries(self):
        self._failed_private_copy_retries_without_partial(directory=True, directory_name='near_high_sounds')

    def test_private_publish_error_preserves_original_and_retries_with_no_completed_target(self):
        source, target, paths, coordinator = self._private_copy_fixture()
        failure = OSError('test-owned publish-failure')
        with patch.object(Path, 'rename', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        self.assertIs(failure, caught.exception)
        self.assertFalse(target.exists())
        self.assertEqual([], list(paths.data_dir.glob('.*.tmp')))
        self.assertEqual(b'owned-original', source.read_bytes())
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass
        lease, _ = coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        lease.close()
        self.assertEqual(b'owned-original', target.read_bytes())

    def test_private_staging_does_not_overwrite_destination_accepted_during_copy(self):
        source, target, paths, coordinator = self._private_copy_fixture()
        native_copy = app_paths.shutil.copy2
        def copy(src, dst):
            native_copy(src, dst)
            target.write_bytes(b'accepted-during-copy')
        with patch.object(app_paths.shutil, 'copy2', side_effect=copy):
            lease, _ = coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        lease.close()
        self.assertEqual(b'accepted-during-copy', target.read_bytes())
        self.assertEqual(b'owned-original', source.read_bytes())
        self.assertEqual([], list(paths.data_dir.glob('.*.tmp')))

    def test_private_copy_error_is_not_replaced_by_staging_cleanup_error(self):
        source, target, paths, coordinator = self._private_copy_fixture()
        failure = OSError('test-owned original-copy-failure')
        with patch.object(app_paths.shutil, 'copy2', side_effect=failure), \
                patch.object(app_paths.shutil, 'rmtree', side_effect=OSError('test-owned cleanup-failure')):
            with self.assertRaises(OSError) as caught:
                coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        self.assertIs(failure, caught.exception)
        self.assertFalse(target.exists())
        self.assertEqual(b'owned-original', source.read_bytes())
        self.assertIsNotNone(paths.desktop_startup_preparation())
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass
        # A retained staging directory is not the final destination or a marker.
        retained = list(paths.data_dir.glob('.*.tmp'))
        self.assertEqual(1, len(retained))
        for item in retained:
            app_paths.shutil.rmtree(item)
        lease, _ = coordinator.enter_main(startup_preparation=lambda: paths.desktop_startup_preparation())
        lease.close()
        self.assertEqual(b'owned-original', target.read_bytes())

    def test_public_default_sound_failure_remains_best_effort_without_private_import(self):
        sounds = self.root / 'installation/data/near_high_sounds'
        sounds.mkdir(parents=True)
        self.enterContext(patch.object(sys, 'frozen', True, create=True))
        self.enterContext(patch.object(sys, 'executable', str(sounds.parent.parent / 'KiwoomMonitor.exe')))
        paths = AppPaths.for_current_user()
        self.assertIsNone(paths.desktop_startup_preparation())
        for name in ('interest.mp3', 'caution.mp3', 'fire.mp3'):
            (sounds / name).write_bytes(name.encode())
        for query_only in (False, True):
            with self.subTest(query_only=query_only), \
                    patch.object(app_paths.shutil, 'copy2', side_effect=OSError('test-owned optional sound failure')), \
                    patch.object(app_paths.shutil, 'copytree', side_effect=AssertionError('builtin-only folder treated as private')), \
                    patch.object(AppPaths, '_copy_legacy_asset', side_effect=AssertionError('builtin-only sound blocked startup')):
                lease, _ = StrictRestoreCoordinator(paths.database_path, paths.news_database_path).enter_main(
                    startup_preparation=lambda: paths.desktop_startup_preparation(query_only=query_only))
            lease.close()
        self.assertFalse(paths.database_path.exists())
        self.assertFalse((paths.data_dir / 'api.env').exists())
        self.assertEqual({name: name.encode() for name in ('interest.mp3', 'caution.mp3', 'fire.mp3')},
                         {p.name: p.read_bytes() for p in sounds.iterdir()})
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass


class BootstrapPathGateTests(unittest.TestCase):
    def test_private_copy_failure_stops_bootstrap_before_desktop_or_DB_initialization(self):
        from kiwoom_monitor import bootstrap
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'installation/data/api.env'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'owned-original')
            paths = AppPaths(root / 'user/data', root / 'user/data/logs', root / 'user/data/monitor.sqlite3')
            paths.log_dir.mkdir(parents=True)
            failure = OSError('test-owned private-copy-failure')
            def fail(src, dst):
                Path(dst).write_bytes(b'partial')
                raise failure
            with ExitStack() as stack:
                stack.enter_context(patch.object(sys, 'frozen', True, create=True))
                stack.enter_context(patch.object(sys, 'executable', str(root / 'installation/KiwoomMonitor.exe')))
                stack.enter_context(patch.object(bootstrap.AppPaths, 'for_current_user', return_value=paths))
                stack.enter_context(patch.object(bootstrap, '_set_taskbar_app_id'))
                stack.enter_context(patch.object(bootstrap, 'configure_logging'))
                stack.enter_context(patch.object(app_paths.shutil, 'copy2', side_effect=fail))
                desktop = stack.enter_context(patch.object(bootstrap, '_run_desktop'))
                initialize = stack.enter_context(patch.object(bootstrap.Database, 'initialize'))
                stack.enter_context(patch.object(bootstrap, 'QApplication'))
                critical = stack.enter_context(patch.object(bootstrap.QMessageBox, 'critical'))
                with self.assertLogs('kiwoom_monitor.bootstrap', level='ERROR'):
                    self.assertIsNone(bootstrap._main_with_restore_gate())
                desktop.assert_not_called()
                initialize.assert_not_called()
                critical.assert_called_once_with(None, 'Google Drive 복원 중단', str(failure))
            self.assertEqual(b'owned-original', source.read_bytes())
            self.assertFalse((paths.data_dir / 'api.env').exists())
            self.assertFalse(paths.database_path.exists())
            self.assertEqual([], list(paths.data_dir.glob('.*.tmp')))
            with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
                pass

    def test_real_root_gate_prepares_query_before_desktop_and_holds_lease_through_exception(self):
        from kiwoom_monitor import bootstrap
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'installation/data/near_high_sounds'
            legacy.mkdir(parents=True)
            (legacy / 'interest.mp3').write_bytes(b'public-sound')
            paths = AppPaths(root / 'user/data', root / 'user/data/logs', root / 'user/data/monitor.sqlite3')
            paths.log_dir.mkdir(parents=True)
            failure = RuntimeError('desktop failed after preparation')
            def desktop(actual_paths, outcome, *, query_only):
                self.assertIs(paths, actual_paths)
                self.assertTrue(query_only)
                self.assertIsNone(outcome)
                self.assertEqual(b'public-sound', (paths.data_dir / 'near_high_sounds/interest.mp3').read_bytes())
                self.assertFalse(paths.database_path.exists())
                with self.assertRaises(StrictRestoreError):
                    AppDataLease(paths.data_dir, exclusive=True, timeout=0.05)
                raise failure
            with ExitStack() as stack:
                stack.enter_context(patch.object(sys, 'frozen', True, create=True))
                stack.enter_context(patch.object(sys, 'executable', str(root / 'installation/KiwoomMonitor.exe')))
                stack.enter_context(patch.object(bootstrap.AppPaths, 'for_current_user', return_value=paths))
                stack.enter_context(patch.object(bootstrap, '_set_taskbar_app_id'))
                stack.enter_context(patch.object(bootstrap, 'configure_logging'))
                stack.enter_context(patch.object(bootstrap, '_run_desktop', side_effect=desktop))
                with self.assertRaises(RuntimeError) as caught:
                    bootstrap._main_with_restore_gate(query_only=True)
                self.assertIs(failure, caught.exception)
            with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
                pass


    def test_explicit_role_bad_known_columns_stop_before_cache_api_window_or_writer_and_return_lease(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.persistence.database import Database
        for missing in (False, True):
            with self.subTest(missing=missing):
                root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'query-role'
                paths = AppPaths.for_query_role(root)
                Database(paths.database_path).initialize_query_role(role='fixture-query')
                with closing(sqlite3.connect(paths.database_path)) as connection, connection:
                    if missing:
                        connection.execute("DELETE FROM column_settings WHERE column_name='rank'")
                    else:
                        connection.execute("UPDATE column_settings SET width=NULL WHERE column_name='rank'")
                before = paths.database_path.read_bytes()
                messages = []
                actual_columns = bootstrap.MainWindow.COLUMNS
                with patch.object(bootstrap, '_set_taskbar_app_id'), patch.object(bootstrap, 'configure_logging'), \
                        patch.object(bootstrap, 'create_query_client', side_effect=AssertionError('API started')) as api, \
                        patch.object(bootstrap, 'MainWindow', side_effect=AssertionError('window started')) as window, \
                        patch('kiwoom_monitor.presentation.app_controller.MarketCacheWriter', side_effect=AssertionError('writer started')) as writer, \
                        patch.object(bootstrap.QMessageBox, 'critical', side_effect=lambda *args: messages.append(args)), \
                        patch.object(sys, 'argv', ['monitor', '--query-only', '--query-data-dir', str(root), '--query-settings-role', 'fixture-query']):
                    window.COLUMNS = actual_columns
                    bootstrap.main()
                    api.assert_not_called()
                    window.assert_not_called()
                    writer.assert_not_called()
                self.assertEqual(1, len(messages))
                self.assertIn('column_settings', messages[0][2])
                self.assertEqual(before, paths.database_path.read_bytes())
                self.assertTrue(paths.database_path.exists())
                with AppDataLease(root, exclusive=True, timeout=0.05):
                    pass

    def test_explicit_role_foreign_cache_is_reported_without_api_worker_or_original_mutation(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.persistence.database import Database
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'query-role'
        paths = AppPaths.for_query_role(root)
        with closing(sqlite3.connect(paths.database_path)) as connection, connection:
            connection.execute("CREATE TABLE foreign_user_data(value BLOB)")
            connection.execute("INSERT INTO foreign_user_data VALUES(?)", (b'original',))
        protected = {path:path.read_bytes() for path in (paths.database_path,)}
        messages = []
        actual_columns = bootstrap.MainWindow.COLUMNS
        with patch.object(bootstrap, '_set_taskbar_app_id'), patch.object(bootstrap, 'configure_logging'), \
                patch.object(bootstrap, 'create_query_client', side_effect=AssertionError('API started')) as api, \
                patch.object(bootstrap, 'MainWindow', side_effect=AssertionError('window started')) as window, \
                patch('kiwoom_monitor.presentation.app_controller.MarketCacheWriter', side_effect=AssertionError('writer started')) as writer, \
                patch.object(bootstrap.QMessageBox, 'critical', side_effect=lambda *args: messages.append(args)), \
                patch.object(sys, 'argv', ['monitor', '--query-only', '--query-data-dir', str(root), '--query-settings-role', 'fixture-query']):
            window.COLUMNS = actual_columns
            bootstrap.main()
            api.assert_not_called()
            window.assert_not_called()
            writer.assert_not_called()
        self.assertEqual(1, len(messages))
        self.assertIn('query-role.sqlite3', messages[0][2])
        self.assertEqual(protected, {path:path.read_bytes() for path in protected})
        with AppDataLease(root, exclusive=True, timeout=0.05):
            pass

    def test_query_root_unc_syntax_is_rejected_without_probing_and_extended_local_is_not_unc(self):
        for value in (r"\\fixture.invalid\share\role", r"\\?\UNC\fixture.invalid\share\role", "//fixture.invalid/share/role"):
            with self.subTest(value=value):
                self.assertTrue(app_paths._query_root_is_unc(Path(value)))
                with patch.object(Path, 'resolve', side_effect=AssertionError('UNC resolve')), \
                        patch.object(Path, 'mkdir', side_effect=AssertionError('UNC write')):
                    with self.assertRaisesRegex(ValueError, 'UNC'):
                        AppPaths.for_query_role(Path(value))
        for value in (r"C:\role", r"\\?\C:\role", r"\\?\Volume{fixture}\role", "/local/role"):
            with self.subTest(value=value):
                self.assertFalse(app_paths._query_root_is_unc(Path(value)))

    def test_explicit_role_rejects_invalid_rank_before_cache_api_or_window_and_preserves_original(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.persistence.database import Database
        for index, (value, missing) in enumerate((("invalid", False), ("5 ", False), (None, False), (None, True))):
            with self.subTest(value=value, missing=missing):
                root = Path(self.enterContext(tempfile.TemporaryDirectory())) / f"invalid-query-{index}"
                paths = AppPaths.for_query_role(root)
                Database(paths.database_path).initialize_query_role(role='fixture-query')
                with closing(sqlite3.connect(paths.database_path)) as connection, connection:
                    if missing:
                        connection.execute("DELETE FROM settings WHERE key='rank_query_type'")
                    else:
                        connection.execute("UPDATE settings SET value=? WHERE key='rank_query_type'", (value,))
                before = paths.database_path.read_bytes()
                messages = []
                with patch.object(bootstrap, '_set_taskbar_app_id'), patch.object(bootstrap, 'configure_logging'), \
                        patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                        patch.object(bootstrap, 'create_query_client', side_effect=AssertionError('API started')), \
                        patch.object(bootstrap, 'MainWindow', side_effect=AssertionError('window started')), \
                        patch.object(bootstrap.QMessageBox, 'critical', side_effect=lambda *args: messages.append(args)), \
                        patch.object(sys, 'argv', ['monitor', '--query-only', '--query-data-dir', str(root), '--query-settings-role', 'fixture-query']):
                    bootstrap.main()
                self.assertEqual(1, len(messages))
                self.assertIn('rank_query_type', messages[0][2])
                self.assertEqual(before, paths.database_path.read_bytes())
                self.assertTrue(paths.database_path.exists())
                self.assertFalse((root / 'query_connection.json').exists())
                with AppDataLease(root, exclusive=True, timeout=0.05):
                    pass

    def test_explicit_query_root_selects_only_new_role_files_and_never_reads_default_or_imports(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'chosen-role-root'
        with patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                patch.object(AppPaths, '_migrate_legacy_personal_data', side_effect=AssertionError('personal import')):
            paths = AppPaths.for_query_role(root)
        self.assertEqual(root, paths.data_dir)
        self.assertEqual(root / 'query-role.sqlite3', paths.database_path)
        self.assertTrue(paths.log_dir.is_dir())
        self.assertEqual([], [path for path in root.rglob('*') if path.is_file()])

    def test_explicit_query_root_keeps_existing_folder_lease_until_desktop_failure(self):
        from kiwoom_monitor import bootstrap
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'chosen-role-root'
        failure = RuntimeError('controlled desktop failure')
        def desktop(paths, outcome, *, query_only, settings_role):
            self.assertEqual(root, paths.data_dir)
            self.assertEqual(root / 'query-role.sqlite3', paths.database_path)
            self.assertTrue(query_only)
            self.assertEqual('fixture-query', settings_role)
            self.assertIsNone(outcome)
            with self.assertRaises(StrictRestoreError):
                AppDataLease(root, exclusive=True, timeout=0.05)
            raise failure
        with patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                patch.object(bootstrap, '_set_taskbar_app_id'), patch.object(bootstrap, 'configure_logging'), \
                patch.object(bootstrap, '_run_desktop', side_effect=desktop):
            with self.assertRaises(RuntimeError) as caught:
                bootstrap._main_with_restore_gate(query_only=True, query_data_dir=root, settings_role='fixture-query')
        self.assertIs(failure, caught.exception)
        self.assertFalse((root / 'monitor.sqlite3').exists())
        with AppDataLease(root, exclusive=True, timeout=0.05):
            pass


class ExplicitFullRolePathTests(unittest.TestCase):
    setUp = AppPathsTests.setUp
    frozen_install = AppPathsTests.frozen_install

    def test_explicit_full_root_selects_its_monitor_and_child_paths_without_default_or_query_lookup(self):
        root = self.root / 'chosen-full-root'
        with patch.dict(os.environ, {'KIWOOM_DESKTOP_DATA_DIR': str(self.root / 'other-root')}), \
                patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                patch.object(AppPaths, 'for_query_role', side_effect=AssertionError('query root read')), \
                patch.object(app_paths.shutil, 'copy2', side_effect=AssertionError('implicit file copy')), \
                patch.object(app_paths.shutil, 'copytree', side_effect=AssertionError('implicit directory copy')):
            paths = AppPaths.for_full_role(root)
        self.assertEqual(root, paths.data_dir)
        self.assertEqual(root / 'logs', paths.log_dir)
        self.assertEqual(root / 'monitor.sqlite3', paths.database_path)
        self.assertEqual(root / 'news.sqlite3', paths.news_database_path)
        self.assertEqual(root / 'journal.sqlite3', paths.journal_database_path)
        self.assertEqual(root / 'account-bindings.dat', paths.account_binding_path)
        self.assertTrue(paths.log_dir.is_dir())
        self.assertEqual([], [path for path in root.rglob('*') if path.is_file()])
        self.assertFalse(self.local.exists())
        self.assertFalse((self.root / 'other-root').exists())

    def test_explicit_full_and_query_roots_share_unc_and_redirect_guards_before_any_write(self):
        for factory in (AppPaths.for_full_role, AppPaths.for_query_role):
            for value in (r'\\fixture.invalid\share\role', r'\\?\UNC\fixture.invalid\share\role', '//fixture.invalid/share/role'):
                with self.subTest(factory=factory.__name__, value=value), \
                        patch.object(Path, 'resolve', side_effect=AssertionError('UNC resolve')), \
                        patch.object(Path, 'mkdir', side_effect=AssertionError('UNC write')):
                    with self.assertRaisesRegex(ValueError, 'UNC'):
                        factory(Path(value))
            root = self.root / factory.__name__
            for redirected in (False, True):
                with self.subTest(factory=factory.__name__, redirected=redirected), \
                        patch.object(Path, 'resolve', return_value=root / 'redirected' if redirected else root), \
                        patch.object(Path, 'is_symlink', return_value=not redirected), \
                        patch.object(Path, 'mkdir', side_effect=AssertionError('redirected root write')):
                    with self.assertRaises(ValueError):
                        factory(root)
            self.assertFalse(root.exists())

    def test_frozen_explicit_full_preparation_copies_only_public_sounds_inside_actual_root_lease(self):
        legacy, original = self.frozen_install()
        paths = AppPaths.for_full_role(self.root / 'chosen-full-root')
        copied, native_copy = [], app_paths.shutil.copy2

        def copy(source, destination):
            self.assertEqual(legacy / 'near_high_sounds', Path(source).parent)
            with self.assertRaises(StrictRestoreError):
                AppDataLease(paths.data_dir, exclusive=False, timeout=0.05)
            copied.append(Path(source).name)
            return native_copy(source, destination)

        with patch.object(AppPaths, '_migrate_legacy_personal_data', side_effect=AssertionError('legacy personal import')), \
                patch.object(app_paths.shutil, 'copytree', side_effect=AssertionError('legacy directory import')):
            with patch.object(app_paths.shutil, 'copy2', side_effect=AssertionError('preparation probe copied')):
                self.assertTrue(callable(paths.desktop_startup_preparation(copy_legacy_personal=False)))
            with patch.object(app_paths.shutil, 'copy2', side_effect=copy):
                lease, outcome = StrictRestoreCoordinator(paths.database_path, paths.news_database_path).enter_main(
                    startup_preparation=lambda: paths.desktop_startup_preparation(copy_legacy_personal=False))
            try:
                self.assertIsNone(outcome)
                self.assertEqual(['interest.mp3', 'caution.mp3', 'fire.mp3'], copied)
                self.assertEqual({'near_high_sounds/' + name for name in copied},
                                 {path.relative_to(paths.data_dir).as_posix() for path in paths.data_dir.rglob('*')
                                  if path.is_file() and path.name != '.strict_restore.lock'})
                self.assertIsNone(paths.desktop_startup_preparation(copy_legacy_personal=False))
                with self.assertRaises(StrictRestoreError):
                    AppDataLease(paths.data_dir, exclusive=True, timeout=0.05)
            finally:
                lease.close()
        with AppDataLease(paths.data_dir, exclusive=True, timeout=0.05):
            pass
        self.assertEqual(original, {path.relative_to(legacy).as_posix(): path.read_bytes()
                                    for path in legacy.rglob('*') if path.is_file()})
        self.assertFalse(self.local.exists())


class ExplicitFullRoleBootstrapTests(unittest.TestCase):
    setUp = AppPathsTests.setUp
    frozen_install = AppPathsTests.frozen_install

    def test_full_cli_dispatches_the_explicit_root_and_role_together(self):
        from kiwoom_monitor import bootstrap
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'full-root'
        with patch.object(sys, 'argv', ['monitor', '--full-data-dir', str(root), '--full-settings-role', 'full-local']), \
                patch.object(bootstrap, '_main_with_restore_gate') as gate:
            bootstrap.main()
        gate.assert_called_once_with(full_data_dir=root, settings_role='full-local')
        self.assertFalse(root.exists())

    def test_full_cli_rejects_partial_or_mixed_role_options_before_resolving_any_root(self):
        from contextlib import redirect_stderr
        import io
        from kiwoom_monitor import bootstrap
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'full-root'
        full = ['--full-data-dir', str(root), '--full-settings-role', 'full-local']
        cases = (
            ['--full-data-dir', str(root)], ['--full-settings-role', 'full-local'],
            ['--full-data-dir', str(root), '--full-settings-role', ''],
            ['--full-data-dir', '', '--full-settings-role', 'full-local'],
            ['--full-data-dir', '   ', '--full-settings-role', 'full-local'],
            ['--full-data-dir', str(root), '--full-settings-role', '   '],
            [*full, '--query-only'], [*full, '--query-data-dir', str(root / 'query')],
            [*full, '--query-settings-role', 'query-local'],
            [*full, '--query-only', '--query-data-dir', str(root / 'query'), '--query-settings-role', 'query-local'],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), \
                    patch.object(sys, 'argv', ['monitor', *arguments]), \
                    patch.object(bootstrap, '_main_with_restore_gate') as gate, \
                    patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                    patch.object(AppPaths, 'for_full_role', side_effect=AssertionError('full root created')), \
                    patch.object(AppPaths, 'for_query_role', side_effect=AssertionError('query root created')):
                with self.assertRaises(SystemExit) as caught:
                    bootstrap.main()
                self.assertEqual(2, caught.exception.code)
                gate.assert_not_called()
        self.assertFalse(root.exists())

    def test_adding_full_cli_keeps_default_and_query_dispatch_explicitly_unchanged(self):
        from kiwoom_monitor import bootstrap
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'query-root'
        for arguments, expected in (
            ([], {}), (['--query-only'], {'query_only': True}),
            (['--query-only', '--query-data-dir', str(root), '--query-settings-role', 'query-local'],
             {'query_only': True, 'query_data_dir': root, 'settings_role': 'query-local'}),
        ):
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['monitor', *arguments]), \
                    patch.object(bootstrap, '_main_with_restore_gate') as gate:
                bootstrap.main()
                gate.assert_called_once_with(**expected)
        self.assertFalse(root.exists())

    def test_full_root_gate_binds_current_restore_validation_and_returns_real_lease_after_desktop_failure(self):
        from kiwoom_monitor import bootstrap
        legacy, original = self.frozen_install()
        root = self.root / 'chosen-full-root'
        native_coordinator = StrictRestoreCoordinator
        callbacks, coordinator_options = {}, []
        failure = RuntimeError('controlled explicit full desktop failure')

        def coordinator(*args, **kwargs):
            coordinator_options.append((args, kwargs))
            actual = native_coordinator(*args, **kwargs)
            native_enter = actual.enter_main

            def enter(**options):
                callbacks.update(options)
                return native_enter(**options)

            actual.enter_main = enter
            return actual

        def desktop(paths, outcome, *, settings_role, settings_layout):
            self.assertEqual(root, paths.data_dir)
            self.assertEqual(root / 'monitor.sqlite3', paths.database_path)
            self.assertEqual(root / 'news.sqlite3', paths.news_database_path)
            self.assertEqual(root / 'journal.sqlite3', paths.journal_database_path)
            self.assertEqual(('full-local', 'full'), (settings_role, settings_layout))
            self.assertIsNone(outcome)
            self.assertTrue(callable(callbacks['startup_preparation']))
            self.assertTrue(callable(callbacks['restore_target_check']))
            for name in ('interest.mp3', 'caution.mp3', 'fire.mp3'):
                self.assertEqual(name.encode(), (root / 'near_high_sounds' / name).read_bytes())
            for name in ('api.env', 'account-bindings.dat', 'google_drive_client.json', 'google_drive_token.dat',
                         'strength_icons', 'near_high_icons', 'near_high_sounds/custom.dat'):
                self.assertFalse((root / name).exists())
            with AppDataLease(root, exclusive=False, timeout=0.05):
                pass
            with self.assertRaises(StrictRestoreError):
                AppDataLease(root, exclusive=True, timeout=0.05)
            with self.assertRaises((ValueError, sqlite3.Error)):
                callbacks['restore_target_check']()
            self.assertFalse(paths.database_path.exists())
            first = bootstrap.Database(paths.database_path).initialize_full_role(role='full-local')
            before = paths.database_path.read_bytes()
            replay = callbacks['restore_target_check']()
            self.assertFalse(first['replayed'])
            self.assertTrue(replay['replayed'])
            self.assertEqual(before, paths.database_path.read_bytes())
            raise failure

        with patch.object(AppPaths, 'for_current_user', side_effect=AssertionError('default root read')), \
                patch.object(AppPaths, 'for_query_role', side_effect=AssertionError('query root read')), \
                patch.object(AppPaths, '_migrate_legacy_personal_data', side_effect=AssertionError('legacy personal import')), \
                patch.object(bootstrap, '_set_taskbar_app_id'), patch.object(bootstrap, 'configure_logging'), \
                patch.object(bootstrap, 'StrictRestoreCoordinator', side_effect=coordinator), \
                patch.object(bootstrap, '_run_desktop', side_effect=desktop):
            with self.assertRaises(RuntimeError) as caught:
                bootstrap._main_with_restore_gate(full_data_dir=root, settings_role='full-local')
        self.assertIs(failure, caught.exception)
        self.assertEqual([((root / 'monitor.sqlite3', root / 'news.sqlite3'),
                           {'expected_backup_set': bootstrap.CURRENT_BACKUP_SET})], coordinator_options)
        self.assertNotIn('allow_restore', callbacks)
        self.assertFalse((root / 'query-role.sqlite3').exists())
        self.assertFalse((root / 'api.env').exists())
        self.assertEqual(original, {path.relative_to(legacy).as_posix(): path.read_bytes()
                                    for path in legacy.rglob('*') if path.is_file()})
        with AppDataLease(root, exclusive=True, timeout=0.05):
            pass

    def test_full_desktop_rejects_unreadable_rank_or_known_column_before_external_setup_and_preserves_bytes(self):
        from kiwoom_monitor import bootstrap
        for stage, statement in (
            ('rank_query_type', "UPDATE settings SET value='invalid' WHERE key='rank_query_type'"),
            ('rank_query_type', "DELETE FROM settings WHERE key='rank_query_type'"),
            ('column_settings', "UPDATE column_settings SET width=NULL WHERE column_name='rank'"),
            ('column_settings', "DELETE FROM column_settings WHERE column_name='rank'"),
        ):
            with self.subTest(statement=statement):
                root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'full-root'
                paths = AppPaths.for_full_role(root)
                bootstrap.Database(paths.database_path).initialize_full_role(role='full-local')
                with closing(sqlite3.connect(paths.database_path)) as connection, connection:
                    connection.execute(statement)
                before = paths.database_path.read_bytes()
                columns = bootstrap.MainWindow.COLUMNS
                with patch.dict(os.environ, {}), \
                        patch.object(bootstrap, 'DataSourceConfig', side_effect=AssertionError('external setup started')), \
                        patch.object(bootstrap, 'create_query_client', side_effect=AssertionError('API started')), \
                        patch.object(bootstrap, 'migrate_legacy_news_database', side_effect=AssertionError('news migration started')), \
                        patch.object(bootstrap, 'MainWindow', side_effect=AssertionError('window started')) as window, \
                        patch.object(bootstrap, 'QApplication'), \
                        patch.object(bootstrap.QMessageBox, 'critical') as critical:
                    window.COLUMNS = columns
                    with self.assertLogs('kiwoom_monitor.bootstrap', level='ERROR'):
                        bootstrap._run_desktop(paths, None, settings_role='full-local', settings_layout='full')
                    window.assert_not_called()
                    critical.assert_called_once()
                    self.assertIn(stage, critical.call_args.args[2])
                self.assertEqual(before, paths.database_path.read_bytes())
                self.assertFalse(paths.news_database_path.exists())
                self.assertFalse(paths.journal_database_path.exists())

    def test_full_desktop_passes_native_settings_and_explicit_child_paths_to_the_existing_window_boundary(self):
        from unittest.mock import Mock
        from kiwoom_monitor import bootstrap
        paths = AppPaths.for_full_role(self.root / 'chosen-full-root')
        bootstrap.Database(paths.database_path).initialize_full_role(role='full-local')
        before = paths.database_path.read_bytes()
        columns = bootstrap.MainWindow.COLUMNS
        failure = RuntimeError('controlled stop at existing Window constructor')

        def window(**options):
            self.assertIsInstance(options['settings'], bootstrap.SettingsRepository)
            self.assertEqual(paths.database_path, options['settings'].database_path)
            self.assertIsInstance(options['columns'], bootstrap.ColumnSettingsRepository)
            self.assertTrue(options['columns'].list())
            self.assertEqual('full-local', options['settings_role'])
            self.assertIs(False, options['query_only'])
            self.assertEqual(paths.data_dir, options['data_dir'])
            self.assertEqual(paths.database_path, options['monitor_database_path'])
            self.assertEqual(paths.data_dir / 'news.sqlite3', options['news_database_path'])
            self.assertEqual(paths.data_dir / 'journal.sqlite3', options['journal_database_path'])
            self.assertEqual(paths.data_dir / 'research', options['research_data_dir'])
            self.assertEqual(paths.data_dir / 'naver_news.dat', options['news_config_path'])
            self.assertEqual(str(paths.data_dir), os.environ['KIWOOM_DESKTOP_DATA_DIR'])
            raise failure

        with patch.dict(os.environ, {}), patch.object(sys, 'excepthook', sys.excepthook), \
                patch.object(bootstrap.DataSourceConfig, 'connection_path', return_value=paths.data_dir / 'api.env'), \
                patch.object(bootstrap.DataSourceConfig, 'load', return_value=bootstrap.DataSourceSettings()), \
                patch.object(bootstrap.LocalApiConfig, 'load', side_effect=ValueError('offline fixture API boundary')), \
                patch.object(bootstrap, 'create_query_client', side_effect=AssertionError('external API started')) as api, \
                patch.object(bootstrap, 'LocalCentralServerProcess', side_effect=AssertionError('server process started')), \
                patch.object(bootstrap, 'migrate_legacy_news_database') as migration, \
                patch.object(bootstrap, 'GoogleDriveSyncService', return_value=Mock(connected=False)) as drive, \
                patch.object(bootstrap, 'QApplication'), patch.object(bootstrap, 'QIcon'), \
                patch.object(bootstrap, 'MainWindow', side_effect=window) as constructor:
            constructor.COLUMNS = columns
            with self.assertLogs('kiwoom_monitor.bootstrap', level='WARNING'):
                with self.assertRaises(RuntimeError) as caught:
                    bootstrap._run_desktop(paths, None, settings_role='full-local', settings_layout='full')
            self.assertIs(failure, caught.exception)
            constructor.assert_called_once()
            api.assert_not_called()
            migration.assert_called_once_with(paths.database_path, paths.news_database_path)
            drive.assert_called_once_with(paths.database_path, paths.news_database_path,
                                          backup_set=bootstrap.CURRENT_BACKUP_SET)
        self.assertEqual(before, paths.database_path.read_bytes())
        self.assertFalse(paths.news_database_path.exists())
        self.assertFalse(paths.journal_database_path.exists())


class LocalServerStoreOwnershipTests(unittest.TestCase):
    def _native_entry_and_reopen(self, *, explicit_full):
        from urllib.parse import unquote
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.central_server.database import SQLiteQueryStore, create_query_store
        from kiwoom_monitor.central_server.database_query_cache import StoredQuery
        from kiwoom_monitor.infrastructure import central_server_process
        from tests.unit.test_role_settings_transition import FullRoleCurrentStateTests, NOW

        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'owned-data'
        if explicit_full:
            fixture, state, _ = FullRoleCurrentStateTests.fixture(self, ordinary=True)
            source_before = fixture.source.read_bytes()
            paths = AppPaths.for_full_role(root)
            first = FullRoleCurrentStateTests.install(self, state, paths.database_path)
            with closing(sqlite3.connect(paths.database_path)) as connection:
                receipt_before = connection.execute(
                    'SELECT preservation_receipt_json FROM role_settings_schema').fetchone()[0]
                tables_before = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
        else:
            (root / 'logs').mkdir(parents=True)
            paths = AppPaths(root, root / 'logs', root / 'monitor.sqlite3')
            bootstrap.Database(paths.database_path).initialize()
        before = paths.database_path.read_bytes()
        rank_before = bootstrap.Database(paths.database_path).settings.get('rank_query_type')
        (root / 'api.env').write_text(
            'KIWOOM_ENVIRONMENT=mock\nKIWOOM_APP_KEY=fixture-never-transmit\n'
            'KIWOOM_SECRET_KEY=fixture-never-transmit-secret\n', encoding='utf-8')
        configuration = bootstrap.DataSourceConfig.connection_path(root / 'api.env')
        source = bootstrap.DataSourceSettings('local_server', 'http://127.0.0.1:19262', 'fixture-token')
        bootstrap.DataSourceConfig(configuration).save(source)
        packet = StoredQuery({'fixture': 'central-native-owner'}, True, 'next-fixture')
        launches, native_errors, guarded = [], [], []

        class StopBeforeRuntime(BaseException):
            pass

        def forbidden(name):
            def reject(*args, **kwargs):
                guarded.append(name)
                raise AssertionError('unexpected side effect: ' + name)
            return reject

        def intercept(command, **options):
            self.assertIn('kiwoom_monitor.central_server', command)
            environment = options['env']
            dsn = environment['KIWOOM_SERVER_DATABASE_URL']
            self.assertTrue(dsn.startswith('sqlite:///'))
            target = Path(unquote(dsn.removeprefix('sqlite:///'))).resolve()
            target.relative_to(root)
            expected = root / 'central.sqlite3' if explicit_full else paths.database_path
            self.assertEqual(expected, target)
            launches.append(dsn)
            self.assertEqual(1, len(launches))
            self.assertEqual('mock', environment['KIWOOM_ENVIRONMENT'])
            self.assertEqual('fixture-never-transmit', environment['KIWOOM_APP_KEY'])
            with self.assertRaises(StrictRestoreError):
                AppDataLease(root, exclusive=True, timeout=0.05)
            store = create_query_store(dsn, now_provider=lambda: NOW)
            try:
                self.assertIsInstance(store, SQLiteQueryStore)
                store.initialize()
                store.save_query('root-owner', 'fixture', NOW.timestamp() + 60, packet)
                self.assertEqual(packet, store.load_query('root-owner'))
            except BaseException as error:
                native_errors.append((error, error.__traceback__))
            finally:
                store.close()
            raise StopBeforeRuntime()

        columns = bootstrap.MainWindow.COLUMNS
        with ExitStack() as guards:
            guards.enter_context(patch.dict(os.environ, {}))
            guards.enter_context(patch.object(sys, 'frozen', False, create=True))
            guards.enter_context(patch.object(sys, 'argv', ['native-local-server-owner']))
            guards.enter_context(patch.object(AppPaths, 'for_current_user',
                side_effect=forbidden('default root') if explicit_full else None, return_value=paths))
            guards.enter_context(patch.object(bootstrap, '_set_taskbar_app_id'))
            guards.enter_context(patch.object(bootstrap, 'configure_logging'))
            guards.enter_context(patch.object(central_server_process.LocalCentralServerProcess, '_is_ready', return_value=False))
            guards.enter_context(patch.object(central_server_process, 'urlopen', side_effect=forbidden('HTTP')))
            popen = guards.enter_context(patch('subprocess.Popen', side_effect=intercept))
            guards.enter_context(patch('socket.create_connection', side_effect=forbidden('socket connection')))
            guards.enter_context(patch('socket.socket.connect', side_effect=forbidden('socket connect')))
            guards.enter_context(patch('socket.socket.connect_ex', side_effect=forbidden('socket connect_ex')))
            guards.enter_context(patch('urllib.request.urlopen', side_effect=forbidden('HTTP urlopen')))
            guards.enter_context(patch('threading.Thread.start', side_effect=forbidden('thread start')))
            for name in ('create_query_client', 'CentralContentClient', 'CentralOperationalSettingsClient',
                         'CentralCredentialsClient', 'QApplication', 'QIcon', 'migrate_legacy_news_database'):
                guards.enter_context(patch.object(bootstrap, name, side_effect=forbidden(name)))
            window = guards.enter_context(patch.object(bootstrap, 'MainWindow', side_effect=forbidden('MainWindow')))
            window.COLUMNS = columns
            guards.enter_context(patch.object(bootstrap.QMessageBox, 'critical', side_effect=forbidden('QMessageBox')))
            with self.assertRaises(StopBeforeRuntime):
                if explicit_full:
                    bootstrap._main_with_restore_gate(full_data_dir=root, settings_role='full-local')
                else:
                    bootstrap._main_with_restore_gate()
            popen.assert_called_once()
        if native_errors:
            error, original_traceback = native_errors[0]
            raise error.with_traceback(original_traceback)
        self.assertEqual([], guarded)
        with AppDataLease(root, exclusive=True, timeout=0.05):
            pass
        if explicit_full:
            self.assertEqual(root / 'central.sqlite3', paths.central_database_path)
            self.assertEqual(before, paths.database_path.read_bytes())
            replay = bootstrap.Database(paths.database_path).initialize_full_role(role='full-local', allow_create=False)
            self.assertTrue(replay['replayed'])
            self.assertEqual(first['receipt'], replay['receipt'])
            with closing(sqlite3.connect(paths.database_path)) as connection:
                self.assertEqual(tables_before, {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")})
                self.assertEqual(receipt_before, connection.execute(
                    'SELECT preservation_receipt_json FROM role_settings_schema').fetchone()[0])
            self.assertEqual(source_before, fixture.source.read_bytes())
        else:
            self.assertFalse((root / 'central.sqlite3').exists())
            bootstrap.Database(paths.database_path).initialize()
        self.assertEqual(rank_before, bootstrap.Database(paths.database_path).settings.get('rank_query_type'))
        reopened = create_query_store(launches[0], now_provider=lambda: NOW)
        try:
            reopened.initialize()
            self.assertEqual(packet, reopened.load_query('root-owner'))
        finally:
            reopened.close()
        if explicit_full:
            self.assertEqual(before, paths.database_path.read_bytes())
        self.assertFalse(paths.news_database_path.exists())
        self.assertFalse(paths.journal_database_path.exists())

    def test_explicit_full_bootstrap_keeps_native_central_cache_and_reopen_outside_owned_ui_schema(self):
        self._native_entry_and_reopen(explicit_full=True)

    def test_default_bootstrap_keeps_existing_combined_legacy_store_and_native_cache_reopen(self):
        self._native_entry_and_reopen(explicit_full=False)


if __name__ == '__main__':
    unittest.main()
