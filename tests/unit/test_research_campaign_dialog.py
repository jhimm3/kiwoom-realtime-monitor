from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLineEdit,
    QTableWidget,
)

from kiwoom_monitor.presentation.research_dialog import ResearchDialog
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from test_research_campaign_execution import write_campaign_request


class CampaignDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path, self.request = write_campaign_request(self.root)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.patches = [patch('kiwoom_monitor.presentation.research_dialog.QSettings', return_value=self.settings),
                        patch('kiwoom_monitor.presentation.research_dialog.QTimer.singleShot')]
        for item in self.patches:
            item.start()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.stop()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def dialog(self):
        dialog = ResearchDialog(self.root / 'state')
        self.dialogs.append(dialog)
        dialog._request_path.setText(str(self.path))
        return dialog

    def register(self):
        dialog = self.dialog()
        dialog._register_campaign_request()
        self.assertIsNotNone(dialog._campaign_selection, dialog._campaign_status.text())
        return dialog

    def test_registration_is_paused_and_duplicate_request_does_not_add_work(self):
        dialog = self.register()
        dialog._register_campaign_request()
        repository = dialog._campaign_repository()
        campaign_id = dialog._campaign_selection['campaign_id']
        self.assertEqual('PAUSED', repository.load_campaign(campaign_id)['desired_state'])
        self.assertEqual(1, len(repository.load_campaign_jobs(campaign_id)))
        self.assertFalse(dialog._manager.is_running)

    def test_automatic_hypothesis_policy_bootstraps_visible_campaign_nodes(self):
        dialog = self.register()
        repository = dialog._campaign_repository()
        campaign_id = dialog._campaign_selection['campaign_id']
        campaign = repository.load_campaign(campaign_id)
        job = repository.load_campaign_jobs(campaign_id)[0]
        policy = ResearchCampaignPolicy(
            auto_hypotheses=True,
            hypothesis_seed=7,
            max_hypotheses=20,
            max_generated_hypotheses_per_cycle=4,
            hypothesis_parameter_values={
                BREAKOUT_FAMILY_ID: {'target_bps': (400, 500, 600)},
            },
        )
        self.assertTrue(dialog._save_campaign_hypothesis_policy(
            policy, campaign['revision'], job['job_id'],
        ))
        saved = repository.load_campaign(campaign_id)
        hypotheses = repository.load_campaign_registered_hypotheses(campaign_id)
        self.assertTrue(saved['policy']['auto_hypotheses'])
        self.assertEqual(2, saved['revision'])
        self.assertEqual(3, len(hypotheses))
        self.assertEqual({400, 600}, {item.changed_to for item in hypotheses[1:]})
        self.assertTrue(dialog._campaign_hypotheses.isEnabled())
        self.assertIn('revision 2', dialog._campaign_status.toolTip())

    def test_automatic_hypothesis_policy_cannot_change_while_running(self):
        dialog = self.register()
        repository = dialog._campaign_repository()
        campaign_id = dialog._campaign_selection['campaign_id']
        campaign = repository.load_campaign(campaign_id)
        job = repository.load_campaign_jobs(campaign_id)[0]
        dialog._set_campaign_state('RUNNING')
        self.assertFalse(dialog._campaign_hypotheses.isEnabled())
        self.assertFalse(dialog._save_campaign_hypothesis_policy(
            ResearchCampaignPolicy(auto_hypotheses=True),
            campaign['revision'], job['job_id'],
        ))
        self.assertIn('일시정지', dialog._campaign_status.text())

    def test_automatic_hypothesis_editor_saves_one_parameter_and_shows_current_ledger(self):
        dialog = self.register()
        campaign_id = dialog._campaign_selection['campaign_id']
        seen_rows = []
        def save(editor):
            next(
                box for box in editor.findChildren(QCheckBox)
                if box.text().startswith('완료된 개발 결과')
            ).setChecked(True)
            combos = editor.findChildren(QComboBox)
            parameter = next(combo for combo in combos if combo.findData('target_bps') >= 0)
            parameter.setCurrentIndex(parameter.findData('target_bps'))
            editor.findChild(QLineEdit).setText('400, 500, 600')
            seen_rows.append(editor.findChild(QTableWidget).rowCount())
            editor.findChild(QDialogButtonBox).accepted.emit()
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, 'exec', new=save):
            dialog._edit_campaign_hypotheses()
        campaign = dialog._campaign_repository().load_campaign(campaign_id)
        self.assertEqual([0], seen_rows)
        self.assertEqual(
            [400, 500, 600],
            campaign['policy']['hypothesis_parameter_values'][BREAKOUT_FAMILY_ID]['target_bps'],
        )
        self.assertEqual(
            3,
            len(dialog._campaign_repository().load_campaign_registered_hypotheses(campaign_id)),
        )

    def test_input_folder_editor_saves_and_disables_without_reading_dataset(self):
        dialog = self.register()
        watch = self.root / 'prepared'
        def save_folder(editor):
            editor.findChild(QLineEdit).setText(str(watch))
            editor.findChild(QCheckBox).setChecked(False)
            editor.findChild(QDialogButtonBox).accepted.emit()
            return 1
        with patch.object(QDialog, 'exec', new=save_folder), patch('kiwoom_monitor.research_process.load_research_input') as load:
            dialog._edit_campaign_inputs()
        load.assert_not_called()
        source = dialog._campaign_repository().load_campaign_input_sources(dialog._campaign_selection['campaign_id'])[0]
        self.assertEqual(str(watch.resolve()), source['root'])
        self.assertFalse(source['enabled'])
        self.assertEqual('', source['scope_json'])

    def test_running_campaign_input_editor_is_disabled_and_does_not_open(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        self.assertFalse(dialog._campaign_inputs.isEnabled())
        with patch.object(QDialog, 'exec') as show:
            dialog._edit_campaign_inputs()
        show.assert_not_called()

    def test_nas_prepare_editor_uses_existing_connection_config_reference(self):
        from PySide6.QtWidgets import QSpinBox
        dialog = self.register()
        def save_nas(editor):
            editor.findChild(QLineEdit).setText(str(self.root / 'prepared'))
            next(box for box in editor.findChildren(QCheckBox) if box.text() == 'NAS에서 새 자료 자동 준비').setChecked(True)
            editor.findChild(QSpinBox).setValue(2)
            editor.findChild(QDialogButtonBox).accepted.emit()
            return 1
        with patch.object(QDialog, 'exec', new=save_nas):
            dialog._edit_campaign_inputs()
        source = dialog._campaign_repository().load_campaign_input_sources(dialog._campaign_selection['campaign_id'])[0]
        self.assertEqual(1, source['nas_auto_prepare'])
        self.assertEqual(2 * 1024 ** 3, source['storage_cap_bytes'])
        self.assertEqual(str((dialog._state_dir.parent / 'data_source.json').resolve()), source['nas_config_path'])

    def test_start_uses_database_campaign_arguments_not_external_request(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start') as start:
            dialog._start_campaign()
        command = start.call_args.args[0]
        self.assertIn('--campaign', command)
        self.assertNotIn('--request', command)
        self.assertTrue(start.call_args.kwargs['below_normal_priority'])
        self.assertEqual('RUNNING', dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])

    def test_launch_error_is_saved_and_scheduled_with_backoff(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch denied')):
            dialog._start_campaign()
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual('FAILED', worker['state'])
        self.assertEqual(1, worker['failure_count'])
        self.assertTrue(dialog._worker_retry_timer.isActive())
        self.assertGreater(dialog._worker_retry_timer.interval(), 29000)

    def test_directory_error_releases_preclaimed_worker(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        original_mkdir = Path.mkdir
        def fail_state_directory(path, *args, **kwargs):
            if path == dialog._state_dir:
                raise OSError('directory denied')
            return original_mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', new=fail_state_directory):
            dialog._launch_campaign_worker()
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual('FAILED', worker['state'])
        self.assertEqual(1, worker['failure_count'])
        self.assertIsNone(dialog._worker_claim)
        self.assertTrue(dialog._worker_retry_timer.isActive())

    def test_restore_does_not_reset_failure_or_launch_during_backoff(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch denied')):
            dialog._start_campaign()
        restored = self.dialog()
        with patch.object(restored._manager, 'start') as start:
            restored._restore_campaign()
        start.assert_not_called()
        self.assertEqual(1, restored._campaign_repository().load_campaign_worker(restored._campaign_selection['campaign_id'])['failure_count'])
        self.assertTrue(restored._worker_retry_timer.isActive())

    def test_live_external_worker_prevents_duplicate_process(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        repository = dialog._campaign_repository()
        repository.claim_campaign_worker(dialog._campaign_selection['campaign_id'], owner_token='external')
        with patch.object(dialog._manager, 'start') as start:
            dialog._launch_campaign_worker()
        start.assert_not_called()
        self.assertTrue(dialog._worker_retry_timer.isActive())

    def test_unexpected_success_code_is_still_a_worker_failure(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start'):
            dialog._start_campaign()
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        dialog._manager._process = process
        dialog._poll_process()
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual('FAILED', worker['state'])
        self.assertEqual(1, worker['failure_count'])
        self.assertTrue(dialog._worker_retry_timer.isActive())

    def test_hidden_worker_exit_is_expected_and_has_no_restart_timer(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start'):
            dialog._start_campaign()
        process = MagicMock()
        process.poll.return_value = None
        dialog._manager._process = process
        dialog.closeEvent(MagicMock())
        process.poll.return_value = 0
        process.returncode = 0
        dialog._poll_process()
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual('IDLE', worker['state'])
        self.assertEqual(0, worker['failure_count'])
        self.assertFalse(dialog._worker_retry_timer.isActive())

    def test_manual_start_explicitly_resets_failure_backoff(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch denied')):
            dialog._start_campaign()
        with patch.object(dialog._manager, 'start') as start:
            dialog._start_campaign()
        start.assert_called_once()
        worker = dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])
        self.assertEqual('STARTING', worker['state'])
        self.assertEqual(0, worker['failure_count'])
        self.assertEqual(2, worker['generation'])

    def test_finite_completion_reschedules_waiting_campaign_recovery(self):
        dialog = self.register()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch denied')):
            dialog._start_campaign()
        dialog._worker_retry_timer.stop()
        dialog._campaign_process = False
        process = MagicMock()
        process.poll.return_value = 0
        dialog._manager._process = process
        dialog._result_path.write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
        dialog._poll_process()
        self.assertTrue(dialog._worker_retry_timer.isActive())
        self.assertEqual(1, dialog._campaign_repository().load_campaign_worker(dialog._campaign_selection['campaign_id'])['failure_count'])

    def test_unreadable_finite_result_still_reschedules_campaign_recovery(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        dialog._campaign_process = False
        process = MagicMock()
        process.poll.return_value = 0
        dialog._manager._process = process
        dialog._poll_process()
        self.assertIn('결과 읽기 실패', dialog._status.text())
        self.assertTrue(dialog._worker_retry_timer.isActive())

    def test_pause_and_stop_are_durable_without_a_process(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        dialog._set_campaign_state('PAUSED')
        campaign_id = dialog._campaign_selection['campaign_id']
        self.assertEqual('PAUSED', dialog._campaign_repository().load_campaign(campaign_id)['desired_state'])
        dialog._set_campaign_state('STOPPED')
        restored = self.dialog()
        with patch.object(restored._manager, 'start') as start:
            restored._restore_campaign()
        start.assert_not_called()
        self.assertEqual(campaign_id, restored._campaign_selection['campaign_id'])

    def test_restore_running_intent_works_when_original_json_has_gone(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        self.path.unlink()
        restored = self.dialog()
        with patch.object(restored._manager, 'start') as start:
            restored._restore_campaign()
        self.assertTrue(start.called)
        self.assertEqual(dialog._campaign_selection, restored._campaign_selection)

    def test_hiding_requests_worker_cancel_without_changing_running_intent(self):
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        dialog._campaign_process = True
        process = MagicMock()
        process.poll.return_value = None
        dialog._manager._process = process
        event = MagicMock()
        dialog.closeEvent(event)
        self.assertTrue(dialog._cancel_path.is_file())
        self.assertTrue(dialog._campaign_suspended)
        self.assertEqual('RUNNING', dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])['desired_state'])
        dialog._manager.clear()

    def test_budget_limit_has_a_different_label_from_space_completion(self):
        dialog = self.register()
        campaign = dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])
        dialog._display_campaign({**campaign, 'operational_state': 'NEEDS_ATTENTION', 'reason': 'trial_budget_exhausted'})
        self.assertIn('횟수 한도', dialog._campaign_status.text())
        self.assertNotIn('조합 검증 완료', dialog._campaign_status.text())

    def test_another_database_cannot_silently_move_the_campaign(self):
        dialog = self.register()
        original = dict(dialog._campaign_selection)
        other_root = self.root / 'other'
        other_root.mkdir()
        other, _ = write_campaign_request(other_root)
        dialog._request_path.setText(str(other))
        dialog._register_campaign_request()
        self.assertEqual(original, dialog._campaign_selection)
        self.assertIn('동일한 연구 DB', dialog._campaign_status.text())

    def test_failed_intent_write_does_not_launch_worker(self):
        import sqlite3
        dialog = self.register()
        with patch('kiwoom_monitor.presentation.research_dialog.ResearchRepository.set_campaign_desired_state',
                   side_effect=sqlite3.OperationalError('disk full')), patch.object(dialog._manager, 'start') as start:
            dialog._start_campaign()
        start.assert_not_called()
        self.assertIn('disk full', dialog._campaign_status.text())

    def test_paused_budget_save_preserves_original_spec(self):
        from dataclasses import replace
        from kiwoom_monitor.application.research_search import ExperimentSpec
        dialog = self.register()
        repository = dialog._campaign_repository()
        job = repository.load_campaign_jobs(dialog._campaign_selection['campaign_id'])[0]
        spec = ExperimentSpec.from_dict(job['request'])
        self.assertTrue(dialog._save_campaign_budget(job['job_id'], replace(spec, max_trials=6), 1))
        current = repository.load_campaign_jobs(dialog._campaign_selection['campaign_id'])[0]
        self.assertEqual(6, current['request']['max_trials'])
        self.assertEqual(job['request_json'], current['request_json'])

    def test_running_budget_change_is_disabled_and_rejected(self):
        from dataclasses import replace
        from kiwoom_monitor.application.research_search import ExperimentSpec
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        job = dialog._campaign_repository().load_campaign_jobs(dialog._campaign_selection['campaign_id'])[0]
        self.assertFalse(dialog._campaign_budget.isEnabled())
        self.assertFalse(dialog._save_campaign_budget(job['job_id'], replace(ExperimentSpec.from_dict(job['request']), max_trials=6), 1))

    def test_resource_blocked_job_can_be_explicitly_returned_to_pending(self):
        from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
        from kiwoom_monitor.research_process import execute_campaign_cycle
        dialog = self.register()
        dialog._set_campaign_state('RUNNING')
        repository = dialog._campaign_repository()
        campaign_id = dialog._campaign_selection['campaign_id']
        with patch('kiwoom_monitor.research_process.ResearchResourceGuard.preflight', side_effect=ResearchResourceBlocked('memory')):
            execute_campaign_cycle(repository, campaign_id, self.request.runs_dir)
        dialog._set_campaign_state('PAUSED')
        job = repository.load_campaign_jobs(campaign_id)[0]
        self.assertTrue(dialog._retry_campaign_work(job['job_id']))
        self.assertEqual('PENDING', repository.load_campaign_jobs(campaign_id)[0]['state'])

    def test_budget_editor_saves_spinbox_values(self):
        from PySide6.QtWidgets import QDialog, QSpinBox, QPushButton
        dialog = self.register()
        def save(editor):
            spins = editor.findChildren(QSpinBox)
            spins[0].setValue(6)
            spins[2].setValue(1024)
            next(button for button in editor.findChildren(QPushButton) if button.text() == '예산 저장').click()
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, 'exec', new=save):
            dialog._edit_campaign_budget()
        job = dialog._campaign_repository().load_campaign_jobs(dialog._campaign_selection['campaign_id'])[0]
        self.assertEqual(6, job['request']['max_trials'])
        self.assertEqual(1024, job['request']['resource_budget']['memory_mb'])

    def test_budget_controls_enable_after_worker_has_exited(self):
        import json
        dialog = self.register()
        campaign = dialog._campaign_repository().load_campaign(dialog._campaign_selection['campaign_id'])
        dialog._result_path.write_text(json.dumps({'kind': 'campaign', 'campaign': campaign}), encoding='utf-8')
        dialog._campaign_process = True
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        dialog._manager._process = process
        dialog._campaign_budget.setEnabled(False)
        dialog._poll_process()
        self.assertTrue(dialog._campaign_budget.isEnabled())


if __name__ == '__main__':
    unittest.main()
