"""Query composition through real Qt, native storage and loopback reader HTTP."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pickle
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
ABSENT = (
    "research_process.py", "research_run.py", "research_candidate.py", "news_process.py",
    "presentation/research_dialog.py", "presentation/candidate_monitor_dialog.py",
    "presentation/mock_automation_dialog.py", "presentation/mock_automation_candidate_dialog.py",
    "presentation/stock_news_window.py", "presentation/market_news_window.py",
    "presentation/news_settings_dialog.py", "presentation/news_workers.py",
    "presentation/morning_market_brief_dialog.py", "infrastructure/news_ai.py",
    "application/research_execution.py", "application/research_families.py",
    "application/breakout_strategy.py", "application/leader_breakout.py",
    "central_server/news_service.py", "central_server/ai_service.py",
)

CHILD = r'''
import faulthandler, json, os, subprocess, sys, time
from pathlib import Path
if os.name == 'nt':
    import runpy
    runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
source, data, forbidden = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
faulthandler.dump_traceback_later(15)
sys.path.insert(0, str(source))
from PySide6.QtCore import QDate, QSettings, QTimer, QThread
from PySide6.QtWidgets import QPushButton
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data / 'qsettings'))
import kiwoom_monitor.bootstrap as bootstrap
from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.presentation.main_window import MainWindow
from kiwoom_monitor.presentation.process_control import AuxiliaryProcessManager
from kiwoom_monitor.presentation import api_settings_dialog as api_settings
from kiwoom_monitor.presentation.app_controller import AppController
from kiwoom_monitor.infrastructure.krx import stock_catalog_worker
catalog_fetch_calls = []
def reject_catalog_fetch(*args, **kwargs):
    catalog_fetch_calls.append('fetch_krx_stock_catalog')
    raise AssertionError('query role attempted an external KRX catalog fetch')
stock_catalog_worker.fetch_krx_stock_catalog = reject_catalog_fetch
native_apply_runtime = AppController._apply_api_runtime
def apply_runtime(controller, runtime, actions):
    # Isolate the same external common-market transports on reload as at startup.
    for key in ('realtime_worker_factory', 'minute_history_worker_factory',
                'fundamentals_worker_factory', 'daily_high_worker_factory',
                'historical_high_worker_factory', 'nxt_eligibility_worker_factory'):
        runtime[key] = None
    return native_apply_runtime(controller, runtime, actions)
AppController._apply_api_runtime = apply_runtime
assert Path(bootstrap.__file__).resolve().is_relative_to(source.resolve()), bootstrap.__file__
def forbidden_call(*args, **kwargs):
    raise AssertionError('query composition touched an optional producer/process factory')
for name in ('LocalCentralServerProcess', 'CentralContentSyncService', 'CentralCredentialsClient',
             'migrate_legacy_news_database'):
    setattr(bootstrap, name, forbidden_call)
AuxiliaryProcessManager.start = forbidden_call
AuxiliaryProcessManager.stop = forbidden_call
subprocess.Popen = forbidden_call
api_settings.CentralCredentialsClient = forbidden_call
api_settings.CentralOperationalSettingsClient = forbidden_call
bootstrap.LocalApiConfig.load = forbidden_call
bootstrap.LocalApiConfig.load_profiles = forbidden_call
bootstrap.LocalApiConfig.save_profiles = forbidden_call
bootstrap.RealtimeTradeWorker = forbidden_call
assert 'kiwoom_monitor.infrastructure.kiwoom_rest.validation_client' not in sys.modules
from kiwoom_monitor.infrastructure.kiwoom_rest import client_factory
client_factory.KiwoomRestClient = forbidden_call
client_factory.LocalAccountBindingConfig.load_bindings = forbidden_call
original_factory = bootstrap.create_query_client
runtime_sources = []
def remote_factory(*args, **kwargs):
    source = kwargs['source_settings']
    assert source.mode in {'local_server', 'personal_server'}
    assert not source.local_fallback_enabled and not source.parallel_validation_enabled
    assert kwargs['local_settings'] is None
    runtime_sources.append(source.mode)
    client = original_factory(*args, **kwargs)
    # The fixed-date fixture clock comes from an actual supplier HTTP response.
    client.load_stored_top20_series('2026-10-22')
    return client
bootstrap.create_query_client = remote_factory
from PySide6.QtCore import QObject, Slot, qInstallMessageHandler
from shiboken6 import isValid
from threading import Event
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker
settings_observations = []
use_persist_barrier = os.environ.get('KIWOOM_QUERY_SAVE_GATE') != '0'
def lifecycle(event, **values):
    print('QUERY_SETTINGS_LIFECYCLE=' + json.dumps(dict(event=event, **values)), file=sys.stderr, flush=True)
qInstallMessageHandler(lambda kind, context, message: lifecycle('qt', kind=str(kind), message=message))
class SettingsObserver(QObject):
    def __init__(self, worker, dialog, parent):
        super().__init__(parent)
        self.worker, self.dialog = worker, dialog
    @Slot(object)
    def saved(self, source):
        worker = self.worker
        value = dict(valid=isValid(worker), parent_is_dialog=worker.parent() is self.dialog,
                     running=worker.isRunning(), finished=worker.isFinished(),
                     gui_receiver=QThread.currentThread() == bootstrap.QApplication.instance().thread(),
                     worker_qobject_in_gui=worker.thread() == bootstrap.QApplication.instance().thread(),
                     field_cleared=self.dialog._save_request is None)
        settings_observations.append(value)
        lifecycle('saved.observer', **value)
original_config = (data / 'query_connection.json').read_bytes()
connection_required = json.loads(original_config)['mode'] == 'local'
role_kind = os.environ.get('KIWOOM_TEST_QUERY_ROLE_KIND')
if role_kind:
    from kiwoom_monitor.presentation import news_window_coordinator
    news_window_coordinator.QSettings = forbidden_call
paths = AppPaths(data, data / 'logs', data / ('query-role.sqlite3' if role_kind else 'monitor.sqlite3'))
paths.log_dir.mkdir(exist_ok=True)
def selected_paths(cls):
    if role_kind:
        raise AssertionError('explicit query role read default app data')
    return paths
bootstrap.AppPaths.for_current_user = classmethod(selected_paths)
outcome, errors, windows = {}, [], []
bootstrap.QMessageBox.critical = lambda *args: (errors.append(str(args)), bootstrap.QApplication.instance().quit())
class ObservedWindow(MainWindow):
    def __init__(self, *args, **kwargs):
        assert kwargs['query_only'] is True
        # Only external common-market inputs are isolated. Real ranking/chart
        # readers, processor omission, native writer and shutdown stay active.
        for name in ('realtime_worker_factory', 'minute_history_worker_factory',
                     'fundamentals_worker_factory', 'daily_high_worker_factory',
                     'historical_high_worker_factory', 'nxt_eligibility_worker_factory'):
            kwargs[name] = None
        if kwargs['ranking_loader'] is not None:
            kwargs['ranking_loader']._client.load_stored_top20_series('2026-10-22')
        else:
            assert connection_required
            assert (data / 'query_connection.json').read_bytes() == original_config
        super().__init__(*args, **kwargs)
        assert self._settings_role == kwargs['settings_role']
        if role_kind:
            assert self._settings_role == 'fixture-query'
        windows.append(self)
        self.deadline = time.monotonic() + 12
        self.requested_chart = False
        self.settings_applied = False
        self.settings_reload_finished = False
        self.catalog_boundary_checked = False
        assert self._google_drive_sync is None
        self.drive_policy_before = {} if role_kind else {key: self._settings.get(key) for key in (
            'google_drive_auto_upload', 'google_drive_unsynced_changes', 'google_drive_local_changed_at')}
        if not role_kind:
            assert self.drive_policy_before['google_drive_auto_upload'] == '1'
        else:
            assert self._settings.database_path == data / 'query-role.sqlite3'
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(self._settings.database_path)) as connection:
                restarted = connection.execute("SELECT value FROM settings WHERE key='fixture:current-api'").fetchone() is not None
                if restarted:
                    assert connection.execute("SELECT value FROM settings WHERE key='rank_query_type'").fetchone() == ('2',)
                    assert connection.execute("SELECT width FROM column_settings WHERE column_name='rank'").fetchone() == (97,)
            assert self._api_config_path() == data / 'api.env'
            assert self._local_asset_path('data/near_high_sounds/interest.mp3') == data / 'near_high_sounds/interest.mp3'
            assert Path(self._top20_trade_value_window._window_settings.fileName()).resolve() == (data / 'query-windows.ini').resolve()
            if role_kind == 'selected-current-state':
                self.assert_selected_current_state(restarted=restarted)
        self._google_drive_worker_controller.start = forbidden_call
        self._app_controller.api_reload_completed.connect(lambda *_: setattr(self, 'settings_reload_finished', True))
        self.probe = QTimer(self)
        self.probe.timeout.connect(self.check)
        self.probe.start(20)
    def activate_startup(self, *, initial_google_drive_download=False):
        super().activate_startup(initial_google_drive_download=initial_google_drive_download)
        if connection_required:
            QTimer.singleShot(0, self.configure_supplier)
    def assert_selected_current_state(self, *, restarted):
        import sqlite3
        from contextlib import closing
        assert self._theme_store is not None
        assert self._theme_store.active_profile == 'Preserved Profile'
        assert self._theme_store.list_profiles() == ('Preserved Profile',)
        assert self._theme_store.list_themes() == (('AI', '#123456'),)
        assert self._theme_store.themes_for_stock('000001') == ('AI',)
        assert self._theme_store.color_for_stock_theme('000001', 'AI') == '#654321'
        assert self._theme_store.all_by_name() == {'fixture-1': 'AI'}
        assert self._theme_store.theme_name_decisions() == (('alias', 'SOURCE', 'AI', 'user'),)
        assert self._stock_lookup.find_code_by_name('LegacyAlias') == '000001'
        assert self._settings.get('fixture:preserved') == 'native-user-setting'
        assert self._rank_query_selector.currentData() == ('2' if restarted else '5')
        assert self._table.columnWidth(0) == (97 if restarted else 137)
        with closing(sqlite3.connect(self._settings.database_path)) as connection:
            assert connection.execute('SELECT profile_id,profile_name FROM theme_profiles').fetchall() == [(7, 'Preserved Profile')]
            assert connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='fixture:preserved'").fetchone() == ('2020-01-02T10:02:03+09:00',)
            assert connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='rank'").fetchone() == (('2026-10-22T20:06:59.654321+09:00' if restarted else '2020-01-02T10:02:03+09:00'),)
            receipt = json.loads(connection.execute('SELECT preservation_receipt_json FROM role_settings_schema').fetchone()[0])
        assert receipt['plan']['binding']['source_instance'] == 'fixture-current-source'
        assert receipt['plan']['binding']['source_owner'] == 'fixture-original'
        assert receipt['plan']['binding']['operation_id'] == 'fixture-current-cut'
        assert receipt['plan']['binding']['target_role'] == 'fixture-query'
    def configure_supplier(self):
        dialog = bootstrap.QApplication.activeModalWidget()
        assert dialog is not None and dialog._query_only and dialog._section == 'nas'
        assert (data / 'query_connection.json').read_bytes() == original_config
        assert dialog._data_source_mode.findData('local') == -1
        dialog._validate_and_accept()
    def assert_query_catalog_boundary(self):
        from dataclasses import replace
        from unittest.mock import patch
        controller = self._app_controller.krx_catalog
        assert controller._worker_factory is stock_catalog_worker.KrxStockCatalogWorker
        assert controller.worker is None and not controller.is_running
        assert not self._app_controller.closing and not self._app_controller.api_reloading
        assert not self._ranking_execution.priority_preparing
        codes = tuple(self._row_by_code)
        assert len(codes) == 20 and self._is_after_hours_data_pause()
        original = self._app_controller._secondary_actions
        phases, catalog_callbacks, stamp_reads = [], [], []
        native_get = self._settings.get
        def observe_get(key, *args, **kwargs):
            if key == 'krx_stock_catalog_date':
                stamp_reads.append(key)
            return native_get(key, *args, **kwargs)
        def observe_catalog():
            catalog_callbacks.append('secondary.start_catalog')
            return original.start_catalog()
        def observe_phase(request, phase):
            phases.append((request.after_hours_pause, phase.value))
            return original.phase_started(request, phase)
        observed = replace(original, start_catalog=observe_catalog, phase_started=observe_phase)
        with patch.object(self._app_controller, '_secondary_actions', observed), \
                patch.object(self._settings, 'get', side_effect=observe_get), \
                patch.object(controller, 'start', wraps=controller.start) as start_catalog, \
                patch.object(self, '_start_historical_high_loading', wraps=self._start_historical_high_loading) as high:
            self._app_controller.start_secondary_loading(codes)
            assert phases == [(True, 'after_hours')]
            assert not catalog_callbacks
            self._app_controller.nxt_eligibility.finished.emit()
            MainWindow._start_daily_krx_catalog_sync(self)
            assert not catalog_callbacks
            start_catalog.assert_not_called()
            high.assert_not_called()
        assert not stamp_reads and not catalog_fetch_calls
        assert controller.worker is None and not controller.is_running
        outcome['query_auto_catalog'] = dict(fetch_attempts=len(catalog_fetch_calls),
            secondary_after_hours=phases, catalog_callback_hits=len(catalog_callbacks),
            real_default_controller=True, public_method_called=True)
        self.catalog_boundary_checked = True
    def check(self):
        try:
            assert time.monotonic() < self.deadline, 'query table/chart timed out'
            if self._table.rowCount() != 20:
                return
            if not self.requested_chart:
                self.requested_chart = True
                self._top20_trade_value_window.date_edit.setDate(QDate(2026, 10, 22))
                self.findChild(QPushButton, 'top20_index_button').click()
                return
            chart = self._top20_trade_value_chart
            if not any(row[1:] == (7.0, 2.0, 0.0) for row in chart._completed):
                return
            if not self.catalog_boundary_checked:
                self.assert_query_catalog_boundary()
            if role_kind == 'selected-current-state':
                row = self._row_by_code['000001']
                assert self._table.item(row, 1).text() == 'fixture-1'
                badges = self._table.cellWidget(row, 2).findChildren(QPushButton)
                assert [button.text() for button in badges] == ['AI']
                assert 'background:#654321;' in badges[0].styleSheet()
            if self.settings_applied:
                if not self.settings_reload_finished:
                    return
                assert len(runtime_sources) >= 2, runtime_sources
                self.finish()
                return
            self.probe.stop()
            assert self._candidate_dialog is self._research_dialog is self._mock_automation_dialog is None
            assert self._entry_snapshot_writer is None
            assert not self._journal_news_timer.isActive()
            for name in ('market_news_button', 'morning_market_brief_button',
                         'candidate_monitor_button', 'research_button', 'mock_automation_button'):
                assert not self.findChild(QPushButton, name).isEnabled(), name
            self._show_market_news()
            self._show_morning_market_brief()
            self._show_trading_journal()
            self._open_news_api_settings()
            self._poll_journal_news_request()
            self._ensure_journal_process()
            self._show_research_dialog()
            self._show_candidate_monitor()
            self._show_mock_automation()
            self._change_environment()
            self._open_settings()
            assert self._settings_dialog._query_only is True
            assert self._settings_dialog._backup_exporter is None
            assert self._settings_dialog._backup_importer is None
            assert not any(button.text() in {'설정 백업 저장', '설정 백업 불러오기'}
                           for button in self._settings_dialog.findChildren(QPushButton))
            def inspect_connection_dialog():
                dialog = bootstrap.QApplication.activeModalWidget()
                try:
                    assert dialog._query_only is True
                    assert dialog._section == 'nas'
                    assert dialog._data_source_mode.findData('local') == -1
                    assert not dialog._local_fallback.isEnabled()
                    assert not dialog._parallel_validation.isEnabled()
                    assert not hasattr(dialog, '_nas_credentials_button')
                    assert not hasattr(dialog, '_nas_provider_button')
                    assert dialog._operations_worker is None
                    dialog._open_nas_credentials()
                    dialog._open_nas_provider_credentials('kiwoom_real')
                    dialog._load_nas_operational_settings()
                    dialog._save_nas_operational_settings(dialog.data_source_values)
                    dialog._test_connection()
                    def connection_finished():
                        if dialog._connection_worker is not None:
                            QTimer.singleShot(20, connection_finished)
                            return
                        if dialog._connection_status.text() != '연결 성공':
                            errors.append(dialog._connection_status.text())
                        dialog._validate_and_accept()
                    QTimer.singleShot(20, connection_finished)
                except BaseException as error:
                    errors.append(repr(error))
                    dialog.reject()
            QTimer.singleShot(0, inspect_connection_dialog)
            self._settings_dialog._open_api_settings('kiwoom')
            self.settings_reload_finished = False
            dialog = self._settings_dialog
            if role_kind:
                assert Path(dialog._window_settings.fileName()).resolve() == (data / 'query-windows.ini').resolve()
                assert dialog._theme_backup_exporter is not None and dialog._theme_backup_importer is not None
                assert dialog._drive_connector is dialog._journal_backup_importer is dialog._journal_backup_exporter is None
                dialog._rank_query_type.setCurrentIndex(dialog._rank_query_type.findData('2'))
            persist_entered, persist_release = Event(), Event()
            original_persist = dialog._settings.set_many
            def observe_persist(values):
                lifecycle('persist.body', in_gui=QThread.currentThread() == bootstrap.QApplication.instance().thread())
                persist_entered.set()
                assert persist_release.wait(5), 'persist observer was not installed'
                return original_persist(values)
            if use_persist_barrier:
                dialog._settings.set_many = observe_persist
            dialog._save()
            if use_persist_barrier:
                assert persist_entered.wait(2)
                request = dialog._save_request
                assert request.parent() is dialog
                observer = SettingsObserver(request, dialog, self)
                request.succeeded.connect(observer.saved)
                persist_release.set()
                dialog._settings.set_many = original_persist
            self.settings_applied = True
            self.probe.start(20)
            return
        except BaseException as error:
            errors.append(repr(error))
            self.probe.stop()
            self.close()
    def finish(self):
        try:
            assert not errors, errors
            assert not catalog_fetch_calls, catalog_fetch_calls
            assert self.catalog_boundary_checked
            assert self._google_drive_sync is None
            assert not self._google_drive_debounce.isActive()
            assert self.drive_policy_before == {key: self._settings.get(key) for key in self.drive_policy_before}
            for operation in ('metadata', 'upload', 'download', 'restore'):
                self._start_google_drive_sync(operation)
            assert not self._google_drive_worker_controller.is_running
            for module in forbidden:
                assert module not in sys.modules, module
            if role_kind:
                from datetime import datetime
                import sqlite3
                from contextlib import closing
                from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting
                current_client = self._app_controller.ranking_loader._client
                stamp = datetime.fromisoformat('2026-10-22T20:06:59.654321+09:00')
                current_client.server_now = lambda: stamp.replace(tzinfo=None)
                self._settings.set('fixture:current-api', 'confirmed')
                self._table.setColumnWidth(0, 97)
                self._save_columns()
                with closing(sqlite3.connect(self._settings.database_path)) as connection:
                    assert connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='fixture:current-api'").fetchone() == (stamp.isoformat(),)
                    assert connection.execute("SELECT updated_at FROM central_column_setting_versions WHERE column_name='rank'").fetchone() == (stamp.isoformat(),)
                if role_kind == 'selected':
                    assert self._theme_store is None
                    with closing(sqlite3.connect(self._settings.database_path)) as connection:
                        assert connection.execute('SELECT profile_name FROM theme_profiles').fetchall() == []
                        assert connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone() == ('기본 테마',)
                    assert '미준비' in self._theme_trade_summary.text()
                elif role_kind == 'selected-current-state':
                    self.assert_selected_current_state(restarted=True)
                else:
                    self._theme_store.set_theme_alias('원테마', '사용자편집')
            # This price belongs to the explicit shutdown-drain probe. A night rank
            # may render the code but must never insert the stock cache row for it.
            self._stock_lookup.cache_ranking_stocks((('999999', 'shutdown fixture', 'KRX'),))
            self._app_controller.queue_price_cache('999999', price=123456)
            outcome.update(rows=self._table.rowCount(), chart_sum=9.0,
                            source=str(Path(bootstrap.__file__).resolve()), pid=os.getpid(),
                            connection_required=connection_required, runtime_sources=runtime_sources)
            if role_kind == 'selected-current-state':
                outcome.update(selected_current_state=True, profile_id=7, profile='Preserved Profile',
                               theme='AI', badge_color='#654321', alias='LegacyAlias', save_ack=bool(settings_observations) if use_persist_barrier else None)
            self.probe.stop()
            self._top20_trade_value_window.close()
            self.close()
        except BaseException as error:
            errors.append(repr(error))
            self.probe.stop()
            self.close()
bootstrap.MainWindow = ObservedWindow
sys.argv = [sys.argv[0], '--query-only']
if role_kind:
    sys.argv += ['--query-data-dir', str(data), '--query-settings-role', 'fixture-query']
try:
    bootstrap.main()
except SystemExit as result:
    assert result.code == 0, result.code
assert not errors, errors
assert outcome, 'query did not finish'
if use_persist_barrier:
    assert settings_observations and all(value == dict(valid=True, parent_is_dialog=True, running=False,
        finished=True, gui_receiver=True, worker_qobject_in_gui=True, field_cleared=True)
        for value in settings_observations), settings_observations
assert not any(worker.isRunning() for window in windows for worker in window.findChildren(QThread))
assert not paths.news_database_path.exists()
assert not paths.journal_database_path.exists()
assert 'KIWOOM_DESKTOP_DATA_DIR' not in os.environ
for module in forbidden:
    assert module not in sys.modules, module
print('QUERY_EVIDENCE=' + json.dumps(outcome, ensure_ascii=False))
faulthandler.cancel_dump_traceback_later()
'''


class QueryOnlyCompositionTests(unittest.TestCase):
    def test_entry_option_is_explicit_and_default_keeps_full_desktop(self):
        from kiwoom_monitor import bootstrap
        with patch.object(bootstrap, '_main_with_restore_gate') as run:
            with patch.object(sys, 'argv', ['monitor']):
                bootstrap.main()
            run.assert_called_once_with()
            run.reset_mock()
            with patch.object(sys, 'argv', ['monitor', '--query-only']):
                bootstrap.main()
            run.assert_called_once_with(query_only=True)
            run.reset_mock()
            with patch.object(sys, 'argv', ['monitor', '--query-only', '--query-data-dir', 'chosen-role-root', '--query-settings-role', 'fixture-query']):
                bootstrap.main()
            run.assert_called_once_with(query_only=True, query_data_dir=Path('chosen-role-root'), settings_role='fixture-query')
            for argv in (['--query-data-dir', 'chosen-role-root'], ['--query-only', '--query-settings-role', 'fixture-query'],
                         ['--query-data-dir', 'chosen-role-root', '--query-settings-role', 'fixture-query']):
                with self.subTest(argv=argv), patch.object(sys, 'argv', ['monitor', *argv]), self.assertRaises(SystemExit) as caught:
                    bootstrap.main()
                self.assertEqual(2, caught.exception.code)

    def test_result_contract_keeps_alias_fields_values_hash_and_legacy_pickle(self):
        from kiwoom_monitor.domain import news_ai_contract as common
        from kiwoom_monitor.infrastructure import news_ai as producer
        for name in ('AINewsAnalysis', 'AICompanyImpact', 'AIThemeCandidate', 'AIRequestUsage',
                     'ANALYSIS_PROMPT_VERSION', 'DEFAULT_MODELS', 'MODEL_OPTIONS'):
            self.assertIs(getattr(common, name), getattr(producer, name))
        result = common.AINewsAnalysis('s', '중립', 80, 'r')
        self.assertEqual(['summary', 'outlook', 'confidence', 'reason', 'positive_evidence',
                          'negative_evidence', 'category', 'company_impacts', 'theme_candidates'],
                         [field.name for field in fields(result)])
        self.assertEqual({'summary': 's', 'outlook': '중립', 'confidence': 80, 'reason': 'r',
                          'positive_evidence': (), 'negative_evidence': (), 'category': '기타 증권뉴스',
                          'company_impacts': (), 'theme_candidates': ()}, asdict(result))
        expected = 'target-company-theme-v3:' + hashlib.sha256(
            'target-company-theme-v3\0삼성전자\0본문'.encode()).hexdigest()
        self.assertEqual(expected, producer.analysis_body_hash(' 삼성전자 ', '본문'))
        old_pickle = pickle.dumps(result, protocol=0).replace(
            b'kiwoom_monitor.domain.news_ai_contract', b'kiwoom_monitor.infrastructure.news_ai')
        self.assertEqual(result, pickle.loads(old_pickle))
        with self.assertRaises(TypeError):
            common.DEFAULT_MODELS['openai'] = 'changed'

    def test_query_connection_settings_save_without_operations_or_credential_reads(self):
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
        from kiwoom_monitor.presentation import api_settings_dialog as ui
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='query-connection-settings-') as directory:
            root = Path(directory)
            path = root / 'api.env'
            path.write_bytes(b'untouched-fixture-placeholder')
            source = DataSourceSettings('personal_server', 'https://fixture.invalid', 'fixture-token')
            DataSourceConfig(root / 'query_connection.json').save(source)
            with patch.object(ui, 'CentralCredentialsClient', side_effect=AssertionError('credentials read')), \
                 patch.object(ui, 'CentralOperationalSettingsClient', side_effect=AssertionError('operations read')), \
                 patch.object(ui.LocalApiConfig, 'load_profiles', side_effect=AssertionError('local keys read')):
                dialog = ui.ApiSettingsDialog(path, section='kiwoom', query_only=True)
                try:
                    dialog.show()
                    app.processEvents()
                    dialog._open_nas_credentials()
                    dialog._open_nas_provider_credentials('openai')
                    dialog._load_nas_operational_settings()
                    dialog._save_nas_operational_settings(source)
                    self.assertIsNone(dialog._operations_worker)
                    self.assertFalse(hasattr(dialog, '_nas_credentials_button'))
                    self.assertFalse(hasattr(dialog, '_nas_provider_button'))
                    dialog._server_url.setText('https://changed.invalid')
                    dialog._validate_and_accept()
                    changed = DataSourceConfig(root / 'query_connection.json').load()
                    self.assertEqual('https://changed.invalid', changed.server_url)
                    self.assertEqual(source.access_token, changed.access_token)
                    self.assertNotIn('query_only', json.loads((root / 'query_connection.json').read_text()))
                    self.assertEqual(b'untouched-fixture-placeholder', path.read_bytes())
                finally:
                    dialog.deleteLater()
                    app.processEvents()

        # The same existing owner also protects full-mode save/error/retry/cancel.
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QDialog
        from shiboken6 import isValid
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
        from kiwoom_monitor.presentation import settings_dialog as basic
        from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker, _running_requests
        created_requests = []

        class BarrierWorker(SettingsRequestWorker):
            def __init__(self, *args, retain_until_joined=False):
                super().__init__(*args, retain_until_joined=retain_until_joined)
                self.gated = getattr(args[0], '__name__', '?') == 'persist'
                created_requests.append(self)
                self.entered = threading.Event()
                self.body_release = threading.Event()
                self.native_release = threading.Event()
            def run(self):
                if not self.gated:
                    return super().run()
                self.entered.set()
                assert self.body_release.wait(5), 'save body barrier was not released'
                super().run()
                assert self.native_release.wait(5), 'native save barrier was not joined'
            def wait(self, *args):
                if self.gated:
                    self.body_release.set()
                    self.native_release.set()
                return super().wait(*args)

        class ObservedSettings(basic.SettingsDialog):
            def __setattr__(self, name, value):
                if name == '_save_request' and value is None and getattr(self, name, None) is not None:
                    self.clears.append((self._save_request.parent() is self,
                                        self._save_request.isRunning(), self._save_request.isFinished()))
                return super().__setattr__(name, value)

        for query_only in (False, True):
            for exit_mode in ('success', 'failure_retry', 'reject', 'close'):
                with self.subTest(query_only=query_only, exit_mode=exit_mode), tempfile.TemporaryDirectory(
                    prefix='query-settings-owner-') as directory:
                    root = Path(directory)
                    created_requests = []
                    database_path = root / 'monitor.sqlite3'
                    Database(database_path).initialize()
                    settings = SettingsRepository(database_path)
                    initial_upload = settings.get('google_drive_auto_upload')
                    source = DataSourceSettings('personal_server', 'https://fixture.invalid', 'fixture-token', True, True)
                    connection_file = root / ('query_connection.json' if query_only else 'data_source.json')
                    DataSourceConfig(connection_file).save(source)
                    before_source = connection_file.read_bytes()
                    dialog = ObservedSettings(settings, root / 'api.env', query_only=query_only)
                    dialog.clears = []
                    warnings = []
                    requests = []
                    original_set_many = settings.set_many
                    def persist(values):
                        if exit_mode == 'failure_retry' and not warnings:
                            raise OSError('controlled settings persist error')
                        return original_set_many(values)
                    def pump_until_finished():
                        deadline = time.monotonic() + 5
                        while dialog._save_request is not None and time.monotonic() < deadline:
                            app.processEvents()
                        self.assertIsNone(dialog._save_request)
                    try:
                        dialog.show()
                        with patch.object(basic, 'SettingsRequestWorker', BarrierWorker), \
                             patch.object(settings, 'set_many', side_effect=persist), \
                             patch.object(basic.QMessageBox, 'warning', side_effect=lambda *args: warnings.append(args)):
                            dialog._save()
                            request = dialog._save_request
                            requests.append(request)
                            self.assertIs(request.parent(), dialog)
                            self.assertTrue(request.entered.wait(2))
                            if exit_mode in {'reject', 'close'}:
                                getattr(dialog, exit_mode)()
                                self.assertTrue(request.isFinished())
                            else:
                                request.body_release.set()
                            pump_until_finished()
                            if exit_mode == 'failure_retry':
                                self.assertEqual(1, len(warnings))
                                self.assertIn('controlled settings persist error', warnings[0][-1])
                                self.assertTrue(dialog.isEnabled())
                                dialog._save()
                                request = dialog._save_request
                                requests.append(request)
                                self.assertTrue(request.entered.wait(2))
                                request.body_release.set()
                                pump_until_finished()
                            if exit_mode in {'success', 'failure_retry'}:
                                self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
                            self.assertTrue(dialog.clears)
                            self.assertTrue(all(value == (True, False, True) for value in dialog.clears), dialog.clears)
                            self.assertEqual(before_source, connection_file.read_bytes())
                            self.assertEqual(initial_upload, settings.get('google_drive_auto_upload'))
                    finally:
                        for request in created_requests:
                            if isValid(request):
                                request.wait()
                        app.processEvents()
                        self.assertFalse(any(request in _running_requests for request in created_requests))
                        dialog.deleteLater()
                        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        app.processEvents()
                        self.assertFalse(isValid(dialog))
                        self.assertTrue(all(not isValid(request) for request in created_requests))

    def test_query_bootstrap_with_physically_absent_processors_reads_closes_and_restarts(self):
        QueryOnlyCompositionTests._physical_query_composition(self)

    def test_fresh_explicit_role_root_uses_native_settings_cache_and_restarts_without_default_data(self):
        self._physical_query_composition(role_kind='fresh')

    def test_selected_explicit_role_root_preserves_unknown_raw_rows_and_omitted_drive_settings(self):
        self._physical_query_composition(role_kind='selected')

    def test_selected_current_state_reaches_actual_query_role_render_save_ack_and_second_process(self):
        self._physical_query_composition(role_kind='selected-current-state')

    def _physical_query_composition(self, *, role_kind=None, prepare_runtime=None):
        import uvicorn
        from fastapi import FastAPI, Header, HTTPException
        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_dataset_read_routes import create_market_dataset_read_router
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient

        with tempfile.TemporaryDirectory(prefix='query-physical-absence-') as directory:
            root = Path(directory)
            source = root / 'isolated' / 'src'
            shutil.copytree(ROOT / 'src', source, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            package = source / 'kiwoom_monitor'
            for relative in ABSENT:
                (package / relative).unlink()
            if prepare_runtime is not None:
                prepare_runtime(source.parent / 'site-packages')
            data = root / ('chosen-role-root' if role_kind else 'data')
            research = data / 'research'
            research.mkdir(parents=True)
            repository = ResearchRepository(research / 'research.sqlite3')
            repository.create_campaign('keep-running', 'existing research', ResearchCampaignPolicy())
            repository.set_campaign_desired_state('keep-running', 'RUNNING')
            (research / 'research_campaign_selection.json').write_text(json.dumps({
                'version': 'research_campaign_selection/v1', 'campaign_id': 'keep-running',
                'database': str(repository._path), 'runs_dir': str(research / 'runs'),
            }), encoding='utf-8')
            (research / 'last_research_result.json').write_text('{"preserved":true}', encoding='utf-8')
            (research / 'research_cancel.request').write_text('existing-cancel\n', encoding='ascii')
            research_settings = data / 'qsettings' / 'KiwoomMonitor' / 'ResearchDialog.ini'
            research_settings.parent.mkdir(parents=True)
            research_settings.write_bytes(b'[General]\ncontinuous_resume=true\n')
            settings_before = research_settings.read_bytes()
            before = {path.name: path.read_bytes() for path in research.iterdir() if path.is_file()}
            store = SQLiteQueryStore(root / 'server.sqlite3', now_provider=lambda: datetime.fromisoformat('2026-10-22T20:06:00+09:00'))
            store.initialize()
            items = [{'stk_cd': f'{index:06}', 'stk_nm': f'fixture-{index}', 'bigd_rank': str(index),
                      'base_comp_chgr': '1.0', 'dt': '20261022', 'tm': '200600'} for index in range(1, 21)]
            store.save_dataset_snapshot('top20_membership', '2026-10-22', '2026-10-22T20:06:00', {'items': items})
            store.save_dataset_snapshot('ranking', '2', '2026-10-22T20:06:00', {'items': items})
            store.save_dataset_snapshot('top20_index', '2026-10-22', '2026-10-22T09:00', {
                'minute': '2026-10-22T09:00', 'capture_state': 'realtime_complete', 'market_values': [7, 2, 0],
            })
            role_protected = {}
            role_first_receipt = None
            if role_kind:
                from contextlib import closing
                import sqlite3
                from scripts import preserve_current_settings as settings_operator
                from kiwoom_monitor.infrastructure.persistence.database import Database
                original_monitor = data / 'monitor.sqlite3'
                Database(original_monitor).initialize()
                Database(original_monitor).settings.set('window_width', '777')
                role_protected[original_monitor] = original_monitor.read_bytes()
                if role_kind == 'selected':
                    original = root / 'original-role.sqlite3'
                    Database(original).initialize(settings_role='fixture-original')
                    with closing(sqlite3.connect(original)) as connection, connection:
                        connection.execute("DELETE FROM settings WHERE key LIKE 'google_drive_%'")
                        connection.execute("INSERT INTO settings VALUES('unrelated:null',NULL)")
                        connection.execute("INSERT INTO settings VALUES('unrelated:blob',?)", (b'raw\x00\xff',))
                        connection.execute("INSERT INTO column_settings VALUES('unknown-column','0',1,NULL)")
                    artifact, carrier = root / 'selected-artifact', root / 'selected-carrier'
                    stamp = datetime.fromisoformat('2026-10-22T20:06:00+09:00')
                    settings_operator.capture_settings(database=original, source_instance='fixture-source', source_owner='fixture-original',
                                                       output=artifact, sqlite_frozen=True, artifact_now=stamp)
                    _, rows, digest = settings_operator.verified_settings_rows(artifact)
                    selection = dict(format='current-settings-selection/v1', manifest_sha256=digest,
                                     source_instance='fixture-source', target_role='fixture-query',
                                     records=[dict(record_id=key, placement='TRANSFER', target_role='fixture-query') for key in rows])
                    settings_operator.accept_settings(artifact=artifact, selection=selection, target=carrier,
                                                      operation_id='fixture-install', target_id='fixture-query-target', receipt_now=stamp)
                    settings_operator.install_settings(artifact=artifact, selection=selection, accepted_target=carrier,
                                                       target=data / 'query-role.sqlite3', layout='query')
                    role_protected[original] = original.read_bytes()
                    role_protected[carrier / 'offline.sqlite3'] = (carrier / 'offline.sqlite3').read_bytes()
                elif role_kind == 'selected-current-state':
                    original = root / 'original-current-state.sqlite3'
                    Database(original).initialize()
                    stamp = datetime.fromisoformat('2026-10-22T20:06:00+09:00')
                    original_version = '2020-01-02T10:02:03+09:00'
                    selected_keys = {'theme_active_profile', 'rank_query_type', 'theme_badge_enabled', 'fixture:preserved'}
                    source_columns = (
                        ('rank', 1, 0, 137), ('stock', 1, 1, 181), ('themes', 1, 2, 239),
                        ('change_rate', 1, 3, 71), ('current_price', 1, 4, 83),
                        ('trade_value_1m', 1, 5, 89), ('strength_1m', 1, 6, 97),
                        ('trade_value_5m', 0, 7, 103), ('strength_5m', 0, 8, 107),
                        ('trade_value_60m', 0, 9, 109), ('strength_60m', 0, 10, 113),
                        ('trade_value_day', 0, 11, 127), ('strength_day', 0, 12, 131),
                        ('high_distance', 1, 13, 149), ('new_high_price', 1, 14, 157), ('market_cap', 1, 15, 163),
                    )
                    selected_column_names = {row[0] for row in source_columns}
                    with closing(sqlite3.connect(original)) as connection, connection:
                        connection.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (
                            ('theme_active_profile', 'Preserved Profile'), ('rank_query_type', '5'),
                            ('theme_badge_enabled', '1'), ('fixture:preserved', 'native-user-setting')))
                        connection.executemany('INSERT OR REPLACE INTO central_setting_versions VALUES(?,?)',
                                               [(key, original_version) for key in sorted(selected_keys)])
                        connection.executemany('UPDATE column_settings SET visible=?,position=?,width=? WHERE column_name=?',
                                               [(visible, position, width, name) for name, visible, position, width in source_columns])
                        connection.executemany('INSERT OR REPLACE INTO central_column_setting_versions VALUES(?,?)',
                                               [(name, original_version) for name, _, _, _ in source_columns])
                        connection.executemany('INSERT INTO theme_profiles VALUES(?,?)', ((7, 'Preserved Profile'), (9, 'Retained Profile')))
                        connection.execute("INSERT INTO profile_themes VALUES(7,'AI','#123456')")
                        connection.execute("INSERT INTO profile_stock_themes VALUES(7,'000001','AI','#654321')")
                        connection.execute('INSERT INTO profile_theme_name_decisions VALUES(?,?,?,?,?,?)', (7, 'alias', 'SOURCE', 'AI', 'user', original_version))
                        connection.execute('INSERT INTO profile_theme_suggestions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (7, '000001', 'original-news', 'AI', 'original evidence', 88, 'provider', 'model', 'original-body-hash',
                             original_version, 'approved', '["AI"]', None, 'original article', original_version, 'https://example.invalid/original'))
                        connection.execute('INSERT OR REPLACE INTO stocks(code,name,market,updated_at) VALUES(?,?,?,?)',
                                           ('000001', 'fixture-1', 'KOSPI', original_version))
                        connection.execute('INSERT OR REPLACE INTO stock_aliases VALUES(?,?)', ('LegacyAlias', '000001'))
                    role_protected[original] = original.read_bytes()
                    artifact = root / 'selected-current-state-artifact'
                    captured = settings_operator.capture_current_state(database=original, source_instance='fixture-current-source',
                        source_owner='fixture-original', operation_id='fixture-current-cut', output=artifact, sqlite_frozen=True, artifact_now=stamp)
                    manifest, records, digest = settings_operator.verified_current_state_rows(artifact)
                    entries = []
                    captured_source_rows = {table: [] for table in settings_operator.CURRENT_STATE_TABLES}
                    for key, item in records.items():
                        table = item['relation']
                        row = tuple(settings_operator.decode_value(value) for value in item['row'])
                        captured_source_rows[table].append(row)
                        chosen = (True if table in {'settings', 'central_setting_versions'}
                                  else row[0] in selected_column_names if table in {'column_settings', 'central_column_setting_versions'}
                                  else row[0] == 7 if table == 'theme_profiles' or table.startswith('profile_')
                                  else row[0] == '000001' if table == 'stocks' else row[1] == '000001')
                        entries.append(dict(record_id=key, placement='TRANSFER' if chosen else 'RETAIN_SOURCE',
                                            **({'target_role': 'fixture-query'} if chosen else {})))
                    selection = dict(format='current-state-selection/v1', manifest_sha256=digest, source_instance='fixture-current-source',
                                     source_owner='fixture-original', operation_id='fixture-current-cut', target_role='fixture-query', records=entries)
                    selected = settings_operator.select_current_state_rows(artifact, selection)
                    self.assertEqual('CLOSED', selected['closure']['state'])
                    self.assertEqual(sorted(source_columns), sorted(captured_source_rows['column_settings']))
                    self.assertEqual(sorted(source_columns), sorted(selected['rows']['column_settings']))
                    self.assertEqual(sorted((name, original_version) for name in selected_column_names), sorted(selected['rows']['central_column_setting_versions']))
                    self.assertGreater(selected['binding']['unselected'], 0)
                    self.assertEqual(captured['manifest_sha256'], digest)
                    self.assertEqual('fixture-current-cut', manifest['operation_id'])
                    installed = settings_operator.install_current_state(artifact=artifact, selection=selection, target=data / 'query-role.sqlite3',
                        operation_id='fixture-current-install', target_id='fixture-query-target', receipt_now=stamp)
                    self.assertEqual('SELECTED_USER_STATE', installed['seed_kind'])
                    self.assertEqual(selected['binding'], installed['receipt']['plan']['binding'])
                    self.assertEqual({table: len(rows) for table, rows in selected['rows'].items()}, installed['receipt']['plan']['relation_counts'])
                    self.assertEqual((False, False, False, 'NOT_EVALUATED', 'UNVERIFIED'), tuple(installed[key]
                        for key in ('applied', 'role_ready', 'order_authority', 'consumer_status', 'source_authenticity')))
                    role_first_receipt = None
                    actual_target_rows = {}
                    with closing(sqlite3.connect(data / 'query-role.sqlite3')) as connection:
                        for table, columns in settings_operator.CURRENT_STATE_TABLES.items():
                            actual_target_rows[table] = connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid").fetchall()
                            self.assertEqual(selected['rows'][table], actual_target_rows[table])
                    def encoded_rows(relations):
                        return {table: [[settings_operator.encode_value(value) for value in row] for row in rows]
                                for table, rows in relations.items()}
                    evidence_path = root.with_name(root.name + '-current-state-input-readback.json')
                    input_evidence = dict(format='query-current-state-fixture-input-readback/v1', actual_native_readback=True,
                        source_sha256=hashlib.sha256(role_protected[original]).hexdigest(), manifest_sha256=digest,
                        source_records=records, captured_source_rows=encoded_rows(captured_source_rows), selection=selection,
                        selected_rows=encoded_rows(selected['rows']), target_rows=encoded_rows(actual_target_rows),
                        target_native_types={table: [[type(value).__name__ for value in row] for row in rows]
                                             for table, rows in actual_target_rows.items()},
                        target_sha256=hashlib.sha256((data / 'query-role.sqlite3').read_bytes()).hexdigest(),
                        first_receipt=installed['receipt'],
                        evidence_origin='Already verified source stream rows and the same actual target SELECT results used by native equality; before either app child.',
                        raw_source_DB_copy=False, extra_native_queries=0)
                    evidence_path.write_text(json.dumps(input_evidence, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
                    print('QUERY_CURRENT_STATE_INPUT_READBACK=' + json.dumps(dict(path=str(evidence_path),
                        sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(), actual_native_readback=True,
                        captured_columns=len(captured_source_rows['column_settings']), selected_columns=len(selected['rows']['column_settings']),
                        target_columns=len(actual_target_rows['column_settings']), extra_native_queries=0)), flush=True)
                    for path in artifact.iterdir():
                        if path.is_file():
                            role_protected[path] = path.read_bytes()
            wire = []
            wire_status = []
            app = FastAPI()
            def authorize(authorization: str = Header(default='')):
                if authorization != 'Bearer private-query-fixture':
                    raise HTTPException(status_code=401)
            app.include_router(create_market_dataset_read_router(store, authorize, None))
            @app.get('/api/v1/capabilities')
            async def capabilities(authorization: str = Header(default='')):
                authorize(authorization)
                return {'api_version': 1, 'access_scope': 'query'}
            @app.middleware('http')
            async def clock(request, call_next):
                wire.append((request.method, request.url.path, request.headers.get('authorization')))
                response = await call_next(request)
                wire_status.append({'method': request.method, 'path': request.url.path, 'status': response.status_code,
                                    'authorization_present': bool(request.headers.get('authorization')), 'query_subject': request.query_params.get('subject')})
                response.headers['X-Kiwoom-Server-Time'] = '2026-10-22T20:06:00+09:00'
                return response
            # Same disposable uvicorn/socket lifecycle as test_news_api_contract_baseline.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', 0))
                listener.listen(128)
                url = f'http://127.0.0.1:{listener.getsockname()[1]}'
                server = uvicorn.Server(uvicorn.Config(app, host=None, port=None, log_level='critical'))
                thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=False)
                thread.start()
                try:
                    deadline = time.monotonic() + 20
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        thread.join(.02)
                    self.assertTrue(server.started)
                    with self.assertRaises(KiwoomApiError):
                        RemoteKiwoomRestClient(url, 'wrong').load_stored_top20_series('2026-10-22')
                    (data / 'query_connection.json').write_text(json.dumps({'mode': 'local_server',
                        'server_url': url, 'access_token': 'private-query-fixture'}), encoding='utf-8')
                    source_config = (data / 'query_connection.json').read_bytes()
                    modules = ['kiwoom_monitor.' + relative[:-3].replace('/', '.') for relative in ABSENT]
                    environment = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1',
                                       QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                    environment.pop('KIWOOM_DESKTOP_DATA_DIR', None)
                    if role_kind:
                        environment['KIWOOM_TEST_QUERY_ROLE_KIND'] = role_kind
                    (data / 'api.env').write_bytes(b'query-must-not-read-or-write-local-keys')
                    (data / 'account-bindings.dat').write_bytes(b'query-must-not-read-account-bindings')
                    source_cases = (
                        {'mode': 'local_server'},
                        {'mode': 'personal_server', 'local_fallback_enabled': True},
                        {'mode': 'personal_server', 'parallel_validation_enabled': True},
                        {'mode': 'local', 'local_fallback_enabled': True, 'parallel_validation_enabled': True},
                    )
                    if role_kind:
                        source_cases = source_cases[:1]
                    for attempt, policy in enumerate((*source_cases, source_cases[-1])):
                        with self.subTest(source=policy, native_barrier=attempt < len(source_cases)):
                            environment['KIWOOM_QUERY_SAVE_GATE'] = '1' if attempt < len(source_cases) else '0'
                            original_source = {**policy, 'server_url': url, 'access_token': 'private-query-fixture'}
                            (data / 'query_connection.json').write_text(json.dumps(original_source), encoding='utf-8')
                            try:
                                result = subprocess.run([sys.executable, '-c', CHILD, str(source), str(data), json.dumps(modules)],
                                                        cwd=root, env=environment, capture_output=True, text=True, encoding='utf-8', errors='backslashreplace', timeout=35)
                            except subprocess.TimeoutExpired as error:
                                print('QUERY_CONNECTION_TIMEOUT=' + json.dumps({
                                    'attempt': attempt, 'policy': policy,
                                    'stdout': error.stdout.decode('utf-8', 'backslashreplace') if isinstance(error.stdout, bytes) else error.stdout,
                                    'stderr': error.stderr.decode('utf-8', 'backslashreplace') if isinstance(error.stderr, bytes) else error.stderr,
                                }, ensure_ascii=False), flush=True)
                                raise
                            self.assertEqual(0, result.returncode, f'child {attempt}\n{result.stdout}\n{result.stderr}')
                            self.assertIn('QUERY_EVIDENCE=', result.stdout)
                            catalog_evidence = [json.loads(line.split('=', 1)[1])['query_auto_catalog']
                                for line in result.stdout.splitlines() if line.startswith('QUERY_EVIDENCE=')]
                            self.assertEqual([dict(fetch_attempts=0, secondary_after_hours=[[True, 'after_hours']],
                                catalog_callback_hits=0, real_default_controller=True, public_method_called=True)],
                                catalog_evidence)
                            print('QUERY_CONNECTION_CHILD=' + json.dumps({'policy': policy, 'native_barrier': attempt < len(source_cases), 'stdout': result.stdout, 'stderr': result.stderr}, ensure_ascii=False))
                            self.assertEqual(before, {path.name: path.read_bytes() for path in research.iterdir() if path.is_file()})
                            saved_source = json.loads((data / 'query_connection.json').read_text(encoding='utf-8'))
                            self.assertEqual('personal_server' if policy['mode'] == 'local' else policy['mode'], saved_source['mode'])
                            self.assertEqual(bool(policy.get('local_fallback_enabled')), saved_source['local_fallback_enabled'])
                            self.assertEqual(bool(policy.get('parallel_validation_enabled')), saved_source['parallel_validation_enabled'])
                            self.assertEqual(b'query-must-not-read-or-write-local-keys', (data / 'api.env').read_bytes())
                            self.assertEqual(b'query-must-not-read-account-bindings', (data / 'account-bindings.dat').read_bytes())
                            self.assertEqual(settings_before, research_settings.read_bytes())
                            actual_cache = data / ('query-role.sqlite3' if role_kind else 'monitor.sqlite3')
                            self.assertEqual(123456, StockRepository(actual_cache).load_last_prices(('999999',))['999999'])
                            if role_kind:
                                self.assertEqual(role_protected, {path: path.read_bytes() for path in role_protected})
                                self.assertTrue((data / 'query-windows.ini').is_file())
                                with closing(sqlite3.connect(data / 'query-role.sqlite3')) as connection:
                                    self.assertEqual(('2',), connection.execute("SELECT value FROM settings WHERE key='rank_query_type'").fetchone())
                                    if role_kind == 'selected-current-state':
                                        self.assertEqual(('SELECTED_USER_STATE',), connection.execute('SELECT seed_kind FROM role_settings_schema').fetchone())
                                    else:
                                        self.assertEqual(('SELECTED' if role_kind == 'selected' else 'FRESH',), connection.execute('SELECT seed_kind FROM role_settings_schema').fetchone())
                                    receipt = connection.execute('SELECT schema_version,role,seed_kind,seed_sha256,preservation_receipt_json FROM role_settings_schema').fetchone()
                                    if role_first_receipt is None:
                                        role_first_receipt = receipt
                                    else:
                                        self.assertEqual(role_first_receipt, receipt)
                                    if role_kind == 'selected-current-state':
                                        self.assertEqual(installed['receipt'], json.loads(receipt[4]))
                                        self.assertEqual([(7, 'Preserved Profile')], connection.execute('SELECT profile_id,profile_name FROM theme_profiles').fetchall())
                                        self.assertEqual([('LegacyAlias', '000001')], connection.execute('SELECT alias,stock_code FROM stock_aliases').fetchall())
                                        self.assertEqual([('native-user-setting',)], connection.execute("SELECT value FROM settings WHERE key='fixture:preserved'").fetchall())
                                        self.assertEqual([(original_version,)], connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='fixture:preserved'").fetchall())
                                    if role_kind == 'selected':
                                        self.assertEqual([(None,)], connection.execute("SELECT value FROM settings WHERE key='unrelated:null'").fetchall())
                                        self.assertEqual([(b'raw\x00\xff',)], connection.execute("SELECT value FROM settings WHERE key='unrelated:blob'").fetchall())
                                        self.assertEqual([('0',1,None)], connection.execute("SELECT visible,position,width FROM column_settings WHERE column_name='unknown-column'").fetchall())
                                        self.assertEqual([], connection.execute("SELECT key FROM settings WHERE key LIKE 'google_drive_%'").fetchall())
                                with closing(sqlite3.connect(actual_cache)) as connection:
                                    self.assertTrue({'settings','column_settings','central_setting_versions','central_column_setting_versions'} <= {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})
                                    if role_kind == 'selected-current-state':
                                        self.assertEqual([('AI',)], connection.execute("SELECT target_name FROM profile_theme_name_decisions WHERE source_name='SOURCE'").fetchall())
                                    else:
                                        self.assertEqual([] if role_kind == 'selected' else [('사용자편집',)], connection.execute("SELECT target_name FROM profile_theme_name_decisions WHERE source_name='원테마'").fetchall())
                                    self.assertFalse((data / 'query-cache.sqlite3').exists())
                                    self.assertFalse((data / 'query-settings.sqlite3').exists())
                    self.assertEqual('RUNNING', repository.load_campaign('keep-running')['desired_state'])
                    self.assertEqual((), repository.load_campaign_worker_attempts('keep-running'))
                    if role_kind == 'selected-current-state':
                        wire_evidence_path = root.with_name(root.name + '-current-state-wire-readback.json')
                        wire_evidence = dict(format='query-current-state-fixture-wire-readback/v1', actual_wire=True,
                            input_readback_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                            wire=wire_status, expected_source_query_type='5', expected_saved_query_type='2',
                            evidence_origin='Existing loopback middleware wire memory after both actual app children, before terminal wire assertions.',
                            extra_requests=0, extra_native_queries=0)
                        wire_evidence_path.write_text(json.dumps(wire_evidence, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
                        print('QUERY_CURRENT_STATE_WIRE_READBACK=' + json.dumps(dict(path=str(wire_evidence_path),
                            sha256=hashlib.sha256(wire_evidence_path.read_bytes()).hexdigest(), actual_wire=True,
                            requests=len(wire_status), extra_requests=0, extra_native_queries=0)), flush=True)
                    self.assertTrue(any(path.endswith('/top20_membership') for _, path, _ in wire))
                    self.assertTrue(any(path.endswith('/top20-series') for _, path, _ in wire))
                    self.assertTrue(all(method == 'GET' for method, _, _ in wire))
                    print('QUERY_CONNECTION_WIRE=' + json.dumps(wire_status, ensure_ascii=False))
                finally:
                    server.should_exit = True
                    thread.join(15)
                    self.assertFalse(thread.is_alive(), 'loopback server thread did not exit')


# Data-cut lineage is composed here; this is one CI module, not a second product role.
fixture = sys.modules[__name__]

EXTRA_ABSENT = (
    'infrastructure/naver_news.py', 'application/news_analysis.py',
    'infrastructure/naver_stock_news.py', 'infrastructure/naver_stock_market_news.py',
    'infrastructure/naver_stock_market_news_source.py', 'infrastructure/dart_disclosures.py',
    'infrastructure/news_source_documents.py', 'infrastructure/chatgpt_auth.py',
    'infrastructure/chatgpt_plan.py', 'infrastructure/chatgpt_linux_store.py',
    'application/news_rules.py', 'application/news_grouping.py',
    'application/news_auto_analysis.py', 'application/morning_market_brief.py',
    'central_server/news_sources.py', 'central_server/market_news_sources.py',
    'central_server/news_jobs.py', 'central_server/news_credentials.py',
    'infrastructure/kiwoom_rest/validation_client.py',
    'central_server/mock_automation_episode.py',
)
PACKAGE_PREAMBLE = r'''
import importlib.util
assert sys.flags.no_site == 1
runtime = source.parent / 'site-packages'
absent_packages = ('google', 'google_auth_oauthlib', 'googleapiclient', 'openai',
                   'anthropic', 'paddle', 'paddleocr', 'numpy')
assert sorted(path.name for path in runtime.iterdir()) == ['PIL', 'PySide6', 'shiboken6', 'tzdata', 'websockets']
for name in absent_packages:
    assert importlib.util.find_spec(name) is None, name
assert all('site-packages' not in path.lower() or Path(path).resolve() == runtime.resolve()
           for path in sys.path)
'''


def _stage_query_runtime(runtime: Path) -> None:
    # Hosted Windows installs dependencies and creates fixtures on separate volumes.
    runtime.mkdir()
    for name in ('PySide6', 'shiboken6', 'websockets', 'tzdata', 'PIL'):
        installed = Path(importlib.util.find_spec(name).origin).parent
        shutil.copytree(installed, runtime / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


NATIVE_READERS = r'''
import hashlib, pickle
from dataclasses import asdict
from datetime import datetime
from kiwoom_monitor.domain.news_ai_contract import NewsAssessment, StockNewsItem, AINewsAnalysis
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.central_news_client import _deserialize
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_service import news_at_execution
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot
fixed = datetime.fromisoformat('2026-10-22T20:06:00+09:00')
news_store = StockNewsRepository(data / 'accepted-news.sqlite3', now_provider=lambda: fixed)
assessment = NewsAssessment(True, 'contract', 'positive', 'preserved reason', 80, 70)
older = StockNewsItem('older', 'body', 'portal-older', 'original-older',
                     datetime.fromisoformat('2026-10-22T20:00:00+09:00'), assessment)
newer = StockNewsItem('newer', 'body', 'portal-newer', 'original-newer',
                     datetime.fromisoformat('2026-10-22T20:05:00+09:00'), assessment)
news_store.upsert('000001', (older, newer), checked_at=fixed)
loaded = news_store.load('000001')
assert loaded == (newer, older)
assert pickle.loads(pickle.dumps(loaded)) == loaded
expected = '[{"assessment": {"category": "contract", "outlook": "positive", "outlook_score": 70, "reason": "preserved reason", "relevance_score": 80, "relevant": true}, "description": "body", "link": "portal-newer", "original_link": "original-newer", "published_at": "2026-10-22T20:05:00+09:00", "title": "newer"}, {"assessment": {"category": "contract", "outlook": "positive", "outlook_score": 70, "reason": "preserved reason", "relevance_score": 80, "relevant": true}, "description": "body", "link": "portal-older", "original_link": "original-older", "published_at": "2026-10-22T20:00:00+09:00", "title": "older"}]'
serialized = json.dumps([{name: value for name, value in asdict(item).items()
                          if name in ('title', 'description', 'link', 'original_link', 'published_at', 'assessment')}
                         for item in loaded], sort_keys=True,
                        default=lambda value: value.isoformat())
assert serialized == expected
assert _deserialize({'title': 'newer', 'description': 'body', 'link': 'portal-newer',
                     'original_link': 'original-newer', 'published_at': '2026-10-22T20:05:00+09:00',
                     'relevant': True, 'category': 'contract', 'outlook': 'positive',
                     'reason': 'preserved reason', 'relevance_score': 80, 'outlook_score': 70}) == newer
ai_store = NewsAIRepository(data / 'accepted-news.sqlite3', now_provider=lambda: fixed)
analysis = AINewsAnalysis('summary', 'positive', 80, 'preserved reason', ('evidence',))
body_hash = 'target-company-theme-v3:' + hashlib.sha256(b'accepted body').hexdigest()
ai_store.save('000001', newer, 'fixture', 'fixture', body_hash, analysis)
assert ai_store.load('000001', newer).analysis == analysis
assert ai_store.load('000001', newer).body_hash == body_hash
assert news_identity(newer) == 'original-newer'
snapshot = TradeEntrySnapshot('execution', 'execution', '000001', 'fixture', 'buy',
                              fixed, 1000, 1, 'KRX')
at_execution = news_at_execution(news_store, snapshot)
assert [row['title'] for row in at_execution] == ['newer', 'older']
assert at_execution[0] == {'title': 'newer', 'link': 'original-newer',
                          'published_at': '2026-10-22T20:05:00+09:00', 'relevant': True,
                          'category': 'contract', 'outlook': 'positive', 'reason': 'preserved reason'}
native_evidence = {'ordered_json': serialized, 'ordered_json_sha256': hashlib.sha256(serialized.encode()).hexdigest(),
                   'identity': news_identity(newer), 'body_hash': body_hash, 'journal_titles': [row['title'] for row in at_execution]}
'''


class QueryNewsDataBoundaryTests(unittest.TestCase):
    def test_moved_values_preserve_full_alias_fields_json_and_old_pickle_paths(self):
        from kiwoom_monitor.domain import news_ai_contract as common
        from kiwoom_monitor.application import news_analysis as rules
        from kiwoom_monitor.infrastructure import naver_news as producer
        self.assertIs(common.NewsAssessment, rules.NewsAssessment)
        self.assertIs(common.NewsAssessment, producer.NewsAssessment)
        self.assertIs(common.StockNewsItem, producer.StockNewsItem)
        self.assertEqual(['relevant', 'category', 'outlook', 'reason', 'relevance_score', 'outlook_score'],
                         [field.name for field in fields(common.NewsAssessment)])
        self.assertEqual(['title', 'description', 'link', 'original_link', 'published_at', 'assessment', 'input_source'],
                         [field.name for field in fields(common.StockNewsItem)])
        item = common.StockNewsItem('title', 'body', 'portal', 'original', None,
                                   common.NewsAssessment(True, 'contract', 'positive', 'reason', 80, 70))
        expected = {'title': 'title', 'description': 'body', 'link': 'portal', 'original_link': 'original',
                    'published_at': None, 'assessment': {'relevant': True, 'category': 'contract',
                    'outlook': 'positive', 'reason': 'reason', 'relevance_score': 80, 'outlook_score': 70}, 'input_source': None}
        self.assertEqual(expected, asdict(item))
        with self.assertRaises(FrozenInstanceError):
            item.title = 'changed'
        legacy = pickle.dumps(item, protocol=0)
        for name, module in (('StockNewsItem', 'kiwoom_monitor.infrastructure.naver_news'),
                             ('NewsAssessment', 'kiwoom_monitor.application.news_analysis')):
            old = f'ckiwoom_monitor.domain.news_ai_contract\n{name}\n'.encode()
            self.assertIn(old, legacy)
            legacy = legacy.replace(old, f'c{module}\n{name}\n'.encode())
        self.assertEqual(item, pickle.loads(legacy))
        # A real old instance has no optional carrier in its pickle state.
        object.__delattr__(item, "input_source")
        old_state = pickle.loads(pickle.dumps(item, protocol=0))
        self.assertIsNone(getattr(old_state, "input_source", None))
        self.assertEqual(expected, asdict(old_state))

    def test_query_with_naver_rules_providers_and_optional_packages_physically_absent(self):
        from fastapi import FastAPI
        original_run, original_middleware = fixture.subprocess.run, FastAPI.middleware
        child = fixture.CHILD.replace('sys.path.insert(0, str(source))',
                                      'sys.path.insert(0, str(source))\n' + PACKAGE_PREAMBLE)
        child = child.replace("QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data / 'qsettings'))",
                              "QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data / 'qsettings'))\n"
                              "QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(data / 'qsettings' / 'system'))")
        child = child.replace('paths.log_dir.mkdir(exist_ok=True)',
                              'paths.log_dir.mkdir(exist_ok=True)\n' + NATIVE_READERS)
        child = child.replace("print('QUERY_EVIDENCE='", "outcome.update(native=native_evidence, absent_packages=absent_packages, runtime=str(runtime), absent_source_files=len(forbidden))\nprint('QUERY_EVIDENCE='")
        clock_frame_anchor = 'connection_required=connection_required, runtime_sources=runtime_sources)'
        self.assertEqual(1, child.count(clock_frame_anchor))
        child = child.replace(clock_frame_anchor,
            'connection_required=connection_required, runtime_sources=runtime_sources, '
            'supplier_clock=self._app_controller.ranking_now().isoformat(), '
            'completed_frame=[[str(row[0]), *row[1:]] for row in self._top20_trade_value_chart._completed])')
        wire, children, executions = [], [], []
        def run(*args, **kwargs):
            command = list(args[0])
            matched = len(command) > 2 and command[1:3] == ['-c', child]
            if matched:
                saved = json.loads((Path(command[4]) / 'query_connection.json').read_text(encoding='utf-8'))
                policy = (saved['mode'], bool(saved.get('local_fallback_enabled')), bool(saved.get('parallel_validation_enabled')))
                native_barrier = kwargs['env'].get('KIWOOM_QUERY_SAVE_GATE') != '0'
                runtime = Path(command[3]).parent / 'site-packages'
                command.insert(1, '-S')
                kwargs['env'] = dict(kwargs['env'], PYTHONPATH=os.pathsep.join((command[4], str(runtime))))
            result = original_run(command, **kwargs)
            if matched:
                for line in result.stdout.splitlines():
                    if line.startswith('QUERY_EVIDENCE='):
                        value = json.loads(line.split('=', 1)[1])
                        children.append(value)
                        executions.append({'policy': policy, 'native_barrier': native_barrier, 'evidence': value})
            return result
        def middleware(app, kind):
            decorate = original_middleware(app, kind)
            def register(function):
                async def observe(request, call_next):
                    response = await function(request, call_next)
                    wire.append({'method': request.method, 'path': request.url.path,
                                 'status': response.status_code,
                                 'authorization_present': bool(request.headers.get('authorization'))})
                    return response
                return decorate(observe)
            return register
        with patch.object(fixture, 'ABSENT', fixture.ABSENT + EXTRA_ABSENT), \
             patch.object(fixture, 'CHILD', child), patch.object(fixture.subprocess, 'run', run), \
             patch.object(FastAPI, 'middleware', middleware):
            fixture.QueryOnlyCompositionTests._physical_query_composition(self, prepare_runtime=_stage_query_runtime)
        # Independently specified five source-policy/persist-owner cases from source E.
        expected_cases = (
            (('local_server', False, False), True, False, 'local_server'),
            (('personal_server', True, False), True, False, 'personal_server'),
            (('personal_server', False, True), True, False, 'personal_server'),
            (('local', True, True), True, True, 'personal_server'),
            (('local', True, True), False, True, 'personal_server'),
        )
        observed_cases = tuple((row['policy'], row['native_barrier'], row['evidence']['connection_required'],
                                row['evidence']['runtime_sources'][0]) for row in executions)
        self.assertEqual(expected_cases, observed_cases)
        self.assertEqual(5, len(children))
        expected_packages = ['google', 'google_auth_oauthlib', 'googleapiclient', 'openai',
                             'anthropic', 'paddle', 'paddleocr', 'numpy']
        for expected, value in zip(expected_cases, children, strict=True):
            with self.subTest(policy=expected[0], native_barrier=expected[1]):
                self.assertEqual(40, value['absent_source_files'])
                self.assertEqual(expected_packages, value['absent_packages'])
                self.assertEqual(20, value['rows'])
                self.assertEqual(9.0, value['chart_sum'])
                self.assertTrue(any(row[1:] == [7.0, 2.0, 0.0] for row in value['completed_frame']))
                # Existing Remote client returns naive KST wall time at this public boundary.
                self.assertEqual('2026-10-22T20:06', datetime.fromisoformat(value['supplier_clock']).isoformat(timespec='minutes'))
                self.assertEqual(expected[2], value['connection_required'])
                self.assertGreaterEqual(len(value['runtime_sources']), 2)
                self.assertTrue(all(mode == expected[3] for mode in value['runtime_sources']))
                self.assertEqual('2e8a06897603371f088fc27d763994b50e6b2b91a1e9b4f01d06c7651e865f5a',
                                 value['native']['ordered_json_sha256'])
                self.assertEqual('original-newer', value['native']['identity'])
                self.assertEqual(['newer', 'older'], value['native']['journal_titles'])
                self.assertEqual(children[0]['native'], value['native'])
        self.assertTrue(all(row['method'] == 'GET' for row in wire))
        self.assertFalse(any(any(part in row['path'] for part in ('credentials', 'operations', 'order', 'account')) for row in wire))
        print('QUERY_DATA_BOUNDARY_EVIDENCE=' + json.dumps({
            'absent_source_files': len(fixture.ABSENT) + len(EXTRA_ABSENT), 'children': children, 'cases': executions, 'wire': wire,
            'temporary_child_root_absent': not Path(children[0]['source']).parents[3].exists()}, ensure_ascii=False))


import ast, asyncio, importlib.util, json, os, shutil, socket, subprocess, sys, tempfile, threading, unittest
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
composition = sys.modules[__name__]
from tests.unit.test_query_authority import ADMIN, QUERY, query_app, settings, wire_server
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomApiError
ROOT=Path(__file__).resolve().parents[2]
FIXED='2026-10-22T20:06:00+09:00'

NEGATIVE_CHILD=r"""
import json,os,sys
from pathlib import Path
if os.name == 'nt':
    import runpy
    runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
source,data=map(Path,sys.argv[1:3])
sys.path.insert(0,str(source))
from PySide6.QtCore import QSettings,QTimer
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,str(data/'qt'))
QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.SystemScope,str(data/'qt-system'))
import kiwoom_monitor.bootstrap as bootstrap
from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.presentation.main_window import MainWindow
original=bootstrap.DataSourceConfig.load
reads=[]
def load(config):
    reads.append(config._path.name)
    assert config._path.name != 'data_source.json','query read old administrator file'
    return original(config)
bootstrap.DataSourceConfig.load=load
paths=AppPaths(data,data/'logs',data/'monitor.sqlite3')
paths.log_dir.mkdir(exist_ok=True)
bootstrap.AppPaths.for_current_user=classmethod(lambda cls:paths)
errors=[]
bootstrap.QMessageBox.critical=lambda *args:errors.append(str(args))
class Window(MainWindow):
    def __init__(self,*args,**kwargs):
        assert kwargs['query_only'] and kwargs['ranking_loader'] is None
        assert kwargs['realtime_worker_factory'] is None
        super().__init__(*args,**kwargs)
    def activate_startup(self, *, initial_google_drive_download=False):
        super().activate_startup(initial_google_drive_download=initial_google_drive_download)
        QTimer.singleShot(0,self.reject_connection)
    def _start_daily_krx_catalog_sync(self):return
    def reject_connection(self):
        modal=bootstrap.QApplication.activeModalWidget()
        assert modal is not None and modal._query_only
        assert modal._data_source_path.name=='query_connection.json'
        modal.reject()
        self.close()
bootstrap.MainWindow=Window
sys.argv=['query','--query-only']
try:bootstrap.main()
except SystemExit as error:assert error.code==0
assert not errors,errors
assert reads and set(reads)=={'query_connection.json'},reads
print('QUERY_AUTH_REJECT='+json.dumps({'reads':reads,'admitted':False}))
"""

class QueryClientAuthorityTests(unittest.TestCase):
    def test_query_dialog_file_is_disjoint_and_does_not_load_old_admin(self):
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
        from kiwoom_monitor.presentation.settings_dialog import SettingsDialog
        from kiwoom_monitor.infrastructure.persistence.database import Database
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='query-client-config-') as folder:
            root=Path(folder); admin_path=root/'data_source.json'
            DataSourceConfig(admin_path).save(DataSourceSettings('personal_server','https://admin.invalid',ADMIN,True,True))
            original=admin_path.read_bytes();(root/'data_source.tmp').write_bytes(b'full-role-inflight')
            DataSourceConfigLoad=DataSourceConfig.load
            read_paths=[]
            def guarded(config):
                read_paths.append(config._path.name)
                self.assertNotEqual('data_source.json',config._path.name)
                return DataSourceConfigLoad(config)
            with patch.object(DataSourceConfig,'load',guarded):
                dialog=ApiSettingsDialog(root/'api.env',query_only=True)
                try:
                    self.assertEqual('',dialog._server_url.text())
                    self.assertEqual('',dialog._server_token.text())
                    self.assertEqual(root/'query_connection.json',dialog._data_source_path)
                    dialog._server_url.setText('https://query.invalid');dialog._server_token.setText(QUERY)
                    dialog._validate_and_accept()
                    saved=DataSourceConfigLoad(DataSourceConfig(root/'query_connection.json'))
                    self.assertEqual(('personal_server','https://query.invalid',QUERY),(saved.mode,saved.server_url,saved.access_token))
                    db=Database(root/'monitor.sqlite3');db.initialize()
                    basic=SettingsDialog(db.settings,root/'api.env',query_only=True)
                    self.assertEqual(root/'query_connection.json',basic._data_source_path)
                    self.assertEqual(saved,basic._data_source)
                    basic.done(1);basic.deleteLater()
                    restored=ApiSettingsDialog(root/'api.env',query_only=True)
                    self.assertEqual(QUERY,restored._server_token.text());restored.deleteLater()
                finally:dialog.deleteLater();app.processEvents()
            full=ApiSettingsDialog(root/'api.env',section='nas')
            self.assertEqual(ADMIN,full._server_token.text());full.deleteLater();app.processEvents()
            self.assertEqual(original,admin_path.read_bytes())
            self.assertEqual(b'full-role-inflight',(root/'data_source.tmp').read_bytes())
            self.assertFalse((root/'query_connection.tmp').exists())
            self.assertEqual({'query_connection.json'},set(read_paths))

    def test_scope_confirmation_uses_only_explicit_token_and_rejects_admin_and_invalid(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
        gui=QApplication.instance() or QApplication([])
        app=query_app(settings());wire=[]
        @app.middleware('http')
        async def capture(request,call_next):
            reply=await call_next(request)
            wire.append((request.method,request.url.path,request.headers.get('authorization'),reply.status_code))
            return reply
        with wire_server(app) as (base,_loop):
            query=RemoteKiwoomRestClient(base,QUERY)
            self.assertTrue(callable(getattr(query,'require_query_access',None)))
            document=query.require_query_access()
            self.assertEqual('query',document['access_scope'])
            with self.assertRaises(ValueError):RemoteKiwoomRestClient(base,ADMIN).require_query_access()
            with self.assertRaises(KiwoomApiError):RemoteKiwoomRestClient(base,'invalid-query').require_query_access()
            # Keep the original Qt receiver slots and native worker dispatch intact.
            with tempfile.TemporaryDirectory(prefix='query-client-connection-slot-') as folder:
                for token,accepted in ((QUERY,True),(ADMIN,False),('invalid-query',False)):
                    with self.subTest(slot_token='query' if accepted else 'admin' if token==ADMIN else 'invalid'):
                        dialog=ApiSettingsDialog(Path(folder)/'api.env',query_only=True)
                        try:
                            dialog._server_url.setText(base);dialog._server_token.setText(token)
                            dialog._test_connection()
                            request=dialog._connection_worker
                            self.assertIsNotNone(request)
                            deadline=time.monotonic()+12
                            while dialog._connection_worker is not None or not request.isFinished():
                                self.assertLess(time.monotonic(),deadline,'native connection slot did not finish')
                                gui.processEvents()
                            self.assertEqual(accepted,dialog._connection_status.text()=='연결 성공')
                            self.assertTrue(dialog._test_button.isEnabled())
                        finally:
                            dialog.reject();dialog.deleteLater()
                            gui.sendPostedEvents(None,QEvent.Type.DeferredDelete);gui.processEvents()
        expected=[('GET','/api/v1/capabilities','Bearer '+QUERY,200),('GET','/api/v1/capabilities','Bearer '+ADMIN,200),('GET','/api/v1/capabilities','Bearer invalid-query',401)]
        self.assertEqual(expected+expected,wire)

    def test_old_server_and_missing_token_never_admit_runtime_or_read_full_file(self):
        from fastapi import FastAPI
        apps=[('admin',query_app(settings()),ADMIN),('invalid',query_app(settings()),'invalid-query')]
        old=FastAPI()
        @old.get('/api/v1/capabilities')
        async def capabilities():return {'api_version':1}
        apps.append(('old',old,QUERY))
        for kind,app,token in apps:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory(prefix='query-client-reject-') as folder,wire_server(app) as (base,_loop):
                root=Path(folder);data=root/'data';data.mkdir()
                full={'mode':'personal_server','server_url':base,'access_token':ADMIN,'local_fallback_enabled':True,'parallel_validation_enabled':True}
                (data/'data_source.json').write_text(json.dumps(full),encoding='utf-8');before=(data/'data_source.json').read_bytes()
                DataSourceConfig(data/'query_connection.json').save(DataSourceSettings('personal_server',base,token))
                result=subprocess.run([sys.executable,'-c',NEGATIVE_CHILD,str(ROOT/'src'),str(data)],cwd=root,env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,encoding='utf-8',errors='backslashreplace',timeout=35)
                self.assertEqual(0,result.returncode,result.stdout+'\n'+result.stderr)
                self.assertIn('QUERY_AUTH_REJECT=',result.stdout)
                self.assertEqual(before,(data/'data_source.json').read_bytes())
        app=query_app(settings())
        with tempfile.TemporaryDirectory(prefix='query-client-missing-token-') as folder,wire_server(app) as (base,_loop):
            root=Path(folder);data=root/'data';data.mkdir();(data/'data_source.json').write_text(json.dumps({'mode':'personal_server','server_url':base,'access_token':ADMIN}),encoding='utf-8')
            (data/'query_connection.json').write_text(json.dumps({'mode':'personal_server','server_url':base,'access_token':''}),encoding='utf-8')
            result=subprocess.run([sys.executable,'-c',NEGATIVE_CHILD,str(ROOT/'src'),str(data)],cwd=root,env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,encoding='utf-8',errors='backslashreplace',timeout=35)
            self.assertEqual(0,result.returncode,result.stdout+'\n'+result.stderr)

    def test_physical_query_bootstrap_saves_and_two_processes_reuse_only_query_connection(self):
        from contextlib import asynccontextmanager
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        from kiwoom_monitor.central_server.market_query_routes import create_market_query_router
        import httpx
        class Transport:
            def request_with_continuation(self,api_id,path,body,**kwargs):return {'api_id':api_id,'body':body},False,''
        broker=CentralRestBroker(Transport());factory=create_market_query_router
        def market_router(store,authorize,_broker,collector,**kwargs):return factory(store,authorize,broker,collector,**kwargs)
        with tempfile.TemporaryDirectory(prefix='query-client-physical-') as folder:
            root=Path(folder);source=root/'isolated/src';shutil.copytree(ROOT/'src',source,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            for name in composition.ABSENT+composition.EXTRA_ABSENT:(source/'kiwoom_monitor'/name).unlink()
            runtime=root/'isolated/site-packages';_stage_query_runtime(runtime)
            data=root/'data';data.mkdir();(data/'api.env').write_bytes(b'no-local-key-read');(data/'account-bindings.dat').write_bytes(b'no-account-binding-read')
            with patch('kiwoom_monitor.central_server.market_query_routes.create_market_query_router',market_router):app=query_app(settings('sqlite:///'+str(root/'server.sqlite3')))
            store=app.state.query_authority_store
            items=[{'stk_cd':f'{i:06}','stk_nm':f'fixture-{i}','bigd_rank':str(i),'base_comp_chgr':'1.0','dt':'20261022','tm':'200600'} for i in range(1,21)]
            store.save_dataset_snapshot('top20_membership','2026-10-22','2026-10-22T20:06:00',{'items':items})
            store.save_dataset_snapshot('top20_index','2026-10-22','2026-10-22T09:00',{'minute':'2026-10-22T09:00','capture_state':'realtime_complete','market_values':[7,2,0]})
            wire=[]
            @app.middleware('http')
            async def clock(request,call_next):
                reply=await call_next(request);reply.headers['X-Kiwoom-Server-Time']=FIXED
                wire.append({'method':request.method,'path':request.url.path,'status':reply.status_code,'is_query':request.headers.get('authorization')=='Bearer '+QUERY,'is_admin':request.headers.get('authorization')=='Bearer '+ADMIN})
                return reply
            class WsObserver:
                def __init__(self,app):self.app=app
                async def __call__(self,scope,receive,send):
                    if scope['type']=='websocket':
                        headers=dict(scope['headers']);wire.append({'method':'WS','path':scope['path'],'is_query':headers.get(b'authorization')==('Bearer '+QUERY).encode(),'is_admin':headers.get(b'authorization')==('Bearer '+ADMIN).encode(),'url_token':bool(scope.get('query_string'))})
                    await self.app(scope,receive,send)
            app.add_middleware(WsObserver)
            original=app.router.lifespan_context
            @asynccontextmanager
            async def lifespan(selected):
                async with original(selected):
                    try:yield
                    finally:await broker.close()
            app.router.lifespan_context=lifespan
            child=composition.CHILD.replace("data / 'data_source.json'","data / 'query_connection.json'")
            child=child.replace("original_config = (data / 'query_connection.json').read_bytes()","original_config = (data / 'query_connection.json').read_bytes() if (data / 'query_connection.json').exists() else None")
            child=child.replace("connection_required = json.loads(original_config)['mode'] == 'local'","connection_required = original_config is None or json.loads(original_config)['mode'] == 'local'")
            child=child.replace("assert (data / 'query_connection.json').read_bytes() == original_config","assert ((data / 'query_connection.json').read_bytes() if (data / 'query_connection.json').exists() else None) == original_config")
            child=child.replace("QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data / 'qsettings'))","QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data / 'qsettings'))\nQSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.SystemScope,str(data/'qt-system'))")
            child=child.replace('sys.path.insert(0, str(source))','sys.path.insert(0, str(source))\n'+composition.PACKAGE_PREAMBLE)
            child=child.replace('native_apply_runtime = AppController._apply_api_runtime','realtime_factories = []\nnative_apply_runtime = AppController._apply_api_runtime')
            child=child.replace('    # Isolate the same external common-market transports on reload as at startup.','    realtime_factories.append(runtime[\'realtime_worker_factory\'])\n    # Isolate the same external common-market transports on reload as at startup.')
            child=child.replace("        # Only external common-market inputs are isolated.","        if kwargs.get('realtime_worker_factory') is not None: realtime_factories.append(kwargs['realtime_worker_factory'])\n        # Only external common-market inputs are isolated.")
            child=child.replace('        self.requested_chart = False','        self.ws_worker = None\n        self.ws_ready = False\n        self.requested_chart = False')
            child=child.replace('source, data, forbidden = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])','source, data, forbidden = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])\nsupplier_url, supplier_token = sys.argv[4:6]')
            child=child.replace('        dialog._validate_and_accept()','        dialog._server_url.setText(supplier_url)\n        dialog._server_token.setText(supplier_token)\n        dialog._validate_and_accept()',1)
            child=child.replace('    def finish(self):\n        try:',"    def finish(self):\n        if self.ws_worker is None:\n            self.ws_worker = realtime_factories[-1](('000001',))\n            self.ws_worker.subscription_ready.connect(lambda: setattr(self,'ws_ready',True))\n            self.ws_worker.start()\n            return\n        if not self.ws_ready: return\n        self.ws_worker.requestInterruption()\n        assert self.ws_worker.wait(5000)\n        try:")
            guard="""native_source_load = bootstrap.DataSourceConfig.load
source_reads=[]
def query_source_load(config):
    source_reads.append(config._path.name)
    assert config._path.name != 'data_source.json','query read administrator connection'
    return native_source_load(config)
bootstrap.DataSourceConfig.load=query_source_load
"""
            child=child.replace('original_config =',guard+'original_config =',1)
            tail="""from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
saved=bootstrap.DataSourceConfig(data/'query_connection.json').load()
remote=RemoteKiwoomRestClient(saved.server_url,saved.access_token)
assert remote.require_query_access()['access_scope']=='query'
assert remote.request('ka00198','/api/dostk/stkinfo',{'qry_tp':'5','fixture_tr':True})['api_id']=='ka00198'
assert CentralNewsClient(saved.server_url,saved.access_token).stored_page('000000','unknown',ai=None)==((),None)
assert source_reads and set(source_reads)=={'query_connection.json'},source_reads
assert all(window.ws_ready and not window.ws_worker.isRunning() for window in windows)
"""
            child=child.replace("print('QUERY_EVIDENCE='",tail+"outcome.update(query_only_source_reads=source_reads,ws_ready=True)\nprint('QUERY_EVIDENCE='")
            with wire_server(app) as (base,_loop):
                full={'mode':'personal_server','server_url':base,'access_token':ADMIN,'local_fallback_enabled':True,'parallel_validation_enabled':True}
                (data/'data_source.json').write_text(json.dumps(full),encoding='utf-8');before=(data/'data_source.json').read_bytes();(data/'data_source.tmp').write_bytes(b'full-inflight')
                environment=dict(os.environ,PYTHONPATH=os.pathsep.join((str(source),str(runtime))),PYTHONDONTWRITEBYTECODE='1',QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONIOENCODING='utf-8',KIWOOM_QUERY_SAVE_GATE='1')
                environment.pop('KIWOOM_DESKTOP_DATA_DIR',None)
                modules=['kiwoom_monitor.'+n[:-3].replace('/','.') for n in composition.ABSENT+composition.EXTRA_ABSENT]
                results=[]
                for process_index in (0,1):
                    result=subprocess.run([sys.executable,'-S','-c',child,str(source),str(data),json.dumps(modules),base,QUERY],cwd=root,env=environment,capture_output=True,text=True,encoding='utf-8',errors='backslashreplace',timeout=35)
                    self.assertEqual(0,result.returncode,result.stdout+'\n'+result.stderr)
                    value=json.loads(next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('QUERY_EVIDENCE=')))
                    self.assertEqual(process_index==0,value['connection_required']);self.assertTrue(value['ws_ready']);self.assertEqual(20,value['rows']);self.assertEqual(9.0,value['chart_sum']);results.append(value)
                    self.assertEqual(before,(data/'data_source.json').read_bytes());self.assertEqual(b'full-inflight',(data/'data_source.tmp').read_bytes())
                    self.assertEqual(b'no-local-key-read',(data/'api.env').read_bytes());self.assertEqual(b'no-account-binding-read',(data/'account-bindings.dat').read_bytes())
                    RemoteKiwoomRestClient(base,QUERY).require_query_access()
                self.assertNotEqual(results[0]['pid'],results[1]['pid'])
                self.assertTrue(any(x['method']=='WS' and not x['url_token'] for x in wire))
                self.assertTrue(any(x['method']=='POST' and x['path']=='/api/v1/kiwoom/query' and x['status']==200 for x in wire))
                self.assertTrue(all(x['is_query'] and not x['is_admin'] for x in wire),wire)
                print('QUERY_CLIENT_AUTHORITY_WIRE='+json.dumps({'children':results,'wire':wire,'server_survived_both_closes':True},ensure_ascii=False))
