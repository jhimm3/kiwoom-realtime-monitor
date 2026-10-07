from io import StringIO
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
from PySide6.QtWidgets import QApplication

import test_research_partition_search as fixtures
from kiwoom_monitor.presentation.research_dialog import ResearchDialog
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.application.research_search import ExperimentSpec
from kiwoom_monitor.research_process import main


class CampaignRegistrationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.PartitionSearchTests()
        self.fixture.setUp()
        self.root = self.fixture.fixture.root
        self.request, _ = self.fixture.request()
        self.path = self.root / 'request.json'
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.patches = [patch('kiwoom_monitor.presentation.research_dialog.QSettings', return_value=self.settings),
                        patch('kiwoom_monitor.presentation.research_dialog.QTimer.singleShot')]
        for item in self.patches:
            item.start()
        self.dialogs = []
        self.fake_processes = []

    def tearDown(self):
        for process in self.fake_processes:
            process.poll.return_value = 0
        for dialog in self.dialogs:
            dialog.stop()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.fixture.doCleanups()

    def dialog(self):
        dialog = ResearchDialog(self.root / 'state')
        self.dialogs.append(dialog)
        dialog._request_path.setText(str(self.path))
        return dialog

    def begin(self, dialog=None):
        dialog = dialog or self.dialog()
        process = MagicMock()
        process.poll.return_value = None
        self.fake_processes.append(process)
        def start(*args, **kwargs):
            dialog._manager._process = process
            return process
        with patch.object(dialog._manager, 'start', side_effect=start) as launch, patch('kiwoom_monitor.research_process.load_research_input', side_effect=AssertionError('GUI must not load exports')):
            dialog._register_campaign_request()
        self.assertIsNotNone(dialog._registration, dialog._campaign_status.text())
        self.assertTrue(launch.call_args.kwargs['below_normal_priority'])
        return dialog, process, launch.call_args.args[0]

    def child_result(self, dialog, process):
        registration = dialog._registration
        with patch('sys.stdout', new=StringIO()):
            code = main(['--request', str(registration['request']), '--register-campaign', registration['selection']['campaign_id'],
                         '--result', str(registration['result']), '--cancel', str(registration['cancel'])])
        process.returncode = code
        process.poll.return_value = code
        return json.loads(registration['result'].read_text())

    def finish(self, dialog):
        with patch('kiwoom_monitor.research_process.load_research_input', side_effect=AssertionError('GUI must not load exports')), patch.object(ResearchRepository, 'load_campaign_jobs', side_effect=AssertionError('registration must read one job')):
            dialog._poll_process()
        self.assertIsNone(dialog._registration)
        self.assertFalse(dialog._cancel.isEnabled())
        self.assertTrue(dialog._register_campaign.isEnabled())

    def test_click_freezes_request_and_creates_only_paused_owner_metadata(self):
        dialog, process, command = self.begin()
        registration = dialog._registration
        snapshot = json.loads(registration['request'].read_text())
        self.assertEqual(json.loads(json.dumps(self.request.search.to_dict())), snapshot['search'])
        self.assertEqual(str(self.request.dataset), snapshot['dataset'])
        self.assertIn('--register-campaign', command)
        self.assertIn(str(registration['cancel']), command)
        self.assertNotEqual(dialog._cancel_path, registration['cancel'])
        self.assertNotEqual(dialog._result_path, registration['result'])
        repository = dialog._campaign_repository()
        campaign_id = dialog._campaign_selection['campaign_id']
        self.assertEqual('PAUSED', repository.load_campaign(campaign_id)['desired_state'])
        self.assertEqual((), repository.load_campaign_jobs(campaign_id))
        self.assertFalse(dialog._campaign_run.isEnabled())
        self.assertFalse(dialog._campaign_budget.isEnabled())
        self.assertFalse(dialog._run.isEnabled())
        self.assertTrue(dialog._poll.isActive())
        process.poll.return_value = 0

    def test_success_restores_selection_after_original_request_is_deleted(self):
        dialog, process, _ = self.begin()
        owned_files = [dialog._registration[name] for name in ('request', 'result', 'cancel')]
        dialog._table.setRowCount(1)
        self.path.unlink()
        result = self.child_result(dialog, process)
        self.assertEqual('ok', result['status'])
        self.finish(dialog)
        self.assertIn('등록 완료', dialog._status.text())
        self.assertEqual(1, dialog._table.rowCount())
        self.assertTrue(all(not path.exists() for path in owned_files))
        restored = self.dialog()
        with patch.object(restored._manager, 'start') as launch:
            restored._restore_campaign()
        launch.assert_not_called()
        self.assertEqual(dialog._campaign_selection, restored._campaign_selection)
        job = restored._campaign_repository().load_campaign_job(result['campaign_id'], result['job_id'])
        self.assertEqual('train', job['source_request']['research_context']['development_partition']['fold_name'])

    def test_duplicate_click_and_unpolled_exit_cannot_start_other_operations(self):
        dialog, process, _ = self.begin()
        registration = dialog._registration
        process.poll.return_value = 0
        with patch.object(dialog._manager, 'start') as launch, patch('kiwoom_monitor.presentation.research_dialog.load_research_process_request') as parse:
            dialog._register_campaign_request()
            dialog._start()
            dialog._start_campaign()
            dialog._restore_campaign()
        launch.assert_not_called()
        parse.assert_not_called()
        self.assertIs(registration, dialog._registration)

    def test_cancel_uses_operation_file_and_creates_no_job(self):
        dialog, process, _ = self.begin()
        dialog._request_cancel()
        self.assertTrue(dialog._registration['cancel'].exists())
        self.assertFalse(dialog._cancel_path.exists())
        result = self.child_result(dialog, process)
        self.assertEqual('cancelled', result['status'])
        self.finish(dialog)
        self.assertIn('등록 취소', dialog._status.text())
        self.assertEqual((), dialog._campaign_repository().load_campaign_jobs(dialog._campaign_selection['campaign_id']))

    def test_queued_state_change_is_rejected_until_registration_is_consumed(self):
        dialog, process, _ = self.begin()
        process.poll.return_value = 0
        self.assertFalse(dialog._set_campaign_state('RUNNING'))
        self.assertEqual('PAUSED', dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])

    def test_close_requests_cancel_without_blocking_and_owner_stays_recoverable(self):
        dialog, process, _ = self.begin()
        event = MagicMock()
        with patch.object(dialog._manager, 'stop') as stop:
            dialog.closeEvent(event)
        stop.assert_not_called()
        event.ignore.assert_called_once()
        self.assertTrue(dialog._registration['cancel'].exists())
        self.assertTrue(dialog._campaign_path.exists())
        self.child_result(dialog, process)
        self.finish(dialog)
        self.assertTrue(dialog._campaign_suspended)
        self.assertEqual('PAUSED', dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])

    def test_commit_before_close_reports_completion_and_preserves_job(self):
        dialog, process, _ = self.begin()
        result = self.child_result(dialog, process)
        dialog.closeEvent(MagicMock())
        self.finish(dialog)
        self.assertIn('취소 요청 전에', dialog._status.text())
        self.assertIsNotNone(dialog._campaign_repository().load_campaign_job(result['campaign_id'], result['job_id']))

    def test_launch_error_cleans_operation_and_does_not_mark_worker_failed(self):
        dialog = self.dialog()
        with patch.object(dialog._manager, 'start', side_effect=OSError('fixture launch denied')):
            dialog._register_campaign_request()
        self.assertIsNone(dialog._registration)
        self.assertFalse(list(dialog._state_dir.glob('campaign_registration_*')))
        self.assertFalse(dialog._poll.isActive())
        self.assertTrue(dialog._run.isEnabled())
        self.assertIn('fixture launch denied', dialog._campaign_status.text())
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual(0, worker['failure_count'])
        self.assertEqual('PAUSED', dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])

    def test_native_failure_does_not_display_success_even_after_commit(self):
        dialog, process, _ = self.begin()
        result = self.child_result(dialog, process)
        process.returncode = 1
        process.poll.return_value = 1
        self.finish(dialog)
        self.assertIn('확인 필요', dialog._status.text())
        self.assertIsNotNone(dialog._campaign_repository().load_campaign_job(result['campaign_id'], result['job_id']))

    def test_wrong_result_identity_is_rejected_without_changing_selection(self):
        for field, value in (('campaign_id', 'foreign-campaign'), ('job_id', 'foreign-job'), ('experiment_id', 'foreign-experiment')):
            with self.subTest(field=field):
                dialog, process, _ = self.begin()
                selection = dict(dialog._campaign_selection)
                result = self.child_result(dialog, process)
                result[field] = value
                dialog._registration['result'].write_text(json.dumps(result))
                self.finish(dialog)
                self.assertIn('확인 필요', dialog._status.text())
                self.assertEqual(selection, dialog._campaign_selection)

    def test_duplicate_completed_registration_has_separate_files_and_one_job(self):
        dialog, process, _ = self.begin()
        first_path = dialog._registration['request']
        first = self.child_result(dialog, process)
        self.finish(dialog)
        dialog, process, _ = self.begin(dialog)
        self.assertNotEqual(first_path, dialog._registration['request'])
        second = self.child_result(dialog, process)
        self.finish(dialog)
        self.assertFalse(second['registered'])
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertIn('이미 등록', dialog._status.text())
        self.assertEqual(1, len(dialog._campaign_repository().load_campaign_jobs(first['campaign_id'])))

    def test_stop_uses_registration_cancel_and_cleans_only_owned_files(self):
        dialog, process, _ = self.begin()
        original = self.path.read_bytes()
        owned = [dialog._registration[name] for name in ('request', 'result', 'cancel')]
        def stop(**kwargs):
            self.assertTrue(dialog._registration['cancel'].exists())
            process.poll.return_value = 0
            dialog._manager.clear()
        with patch.object(dialog._manager, 'stop', side_effect=stop):
            dialog.stop()
        self.assertTrue(all(not path.exists() for path in owned))
        self.assertEqual(original, self.path.read_bytes())
        self.assertTrue(dialog._campaign_path.exists())

    def test_slow_real_child_keeps_qt_events_running_and_registers_without_trials(self):
        script = self.root / 'slow_registration.py'
        script.write_text(
            'import sys, time\nimport kiwoom_monitor.research_process as module\n'
            'original = module.load_research_input\n'
            'def delayed(*args, **kwargs):\n    time.sleep(0.5)\n    return original(*args, **kwargs)\n'
            'module.load_research_input = delayed\nraise SystemExit(module.main(sys.argv[1:]))\n', encoding='utf-8')
        dialog = self.dialog()
        heartbeat = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
        try:
            with patch('kiwoom_monitor.presentation.research_dialog.build_auxiliary_command', side_effect=lambda module, switch, arguments: [sys.executable, str(script), *arguments]):
                dialog._register_campaign_request()
            self.assertIsNotNone(dialog._registration)
            process = dialog._manager.process
            self.assertIsNone(process.poll())
            timer.start()
            deadline = time.monotonic() + 12
            while dialog._registration is not None and time.monotonic() < deadline:
                self.app.processEvents()
                dialog._poll_process()
                time.sleep(0.005)
            self.assertIsNone(dialog._registration, dialog._status.text())
            self.assertEqual(0, process.returncode)
            self.assertGreaterEqual(len(heartbeat), 3)
            self.assertIn('등록 완료', dialog._status.text())
            repository = dialog._campaign_repository()
            job = repository.load_campaign_jobs(dialog._campaign_selection['campaign_id'])[0]
            self.assertEqual((), repository.load_search_trials(ExperimentSpec.from_dict(job['request']).experiment_id))
            self.assertEqual('PAUSED', repository.load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])
        finally:
            timer.stop()
