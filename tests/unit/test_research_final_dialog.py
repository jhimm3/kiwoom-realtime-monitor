from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QTableWidgetItem

import test_research_final_cli as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.presentation.research_dialog import FinalHoldoutDialog, ResearchDialog


class FinalHoldoutDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.FinalHoldoutCliTests()
        self.fixture.setUp()
        self.root = self.fixture.fixture.root
        self.dialogs = []
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            process.poll.return_value = 0
        for dialog in self.dialogs:
            dialog.stop()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.fixture.doCleanups()

    def dialog(self, state_dir=None):
        dialog = FinalHoldoutDialog(state_dir or self.root / 'state')
        self.dialogs.append(dialog)
        dialog._request_path.setText(str(self.fixture.path))
        dialog._table.setRowCount(1)
        dialog._table.setItem(0, 0, QTableWidgetItem('previous'))
        return dialog

    def launch(self, dialog, action=None):
        process = MagicMock()
        process.poll.return_value = None
        self.processes.append(process)

        def start(*args, **kwargs):
            dialog._manager._process = process
            return process

        with (patch.object(dialog._manager, 'start', side_effect=start) as launcher,
              patch.object(rp.ResearchRepository, '__init__', side_effect=AssertionError('GUI may not open DB'))):
            (action or dialog._start)()
        self.assertIsNotNone(dialog._operation, dialog._status.text())
        self.assertTrue(launcher.call_args.kwargs['below_normal_priority'])
        return process, launcher.call_args.args[0]

    def child_result(self, dialog, process, execute_side_effect=None):
        operation = dialog._operation
        arguments = ['--evaluate-final', str(operation['request']), '--result', str(operation['result']),
                     '--cancel', str(operation['cancel'])]
        context = patch.object(rp, 'execute_research', side_effect=execute_side_effect) \
            if execute_side_effect is not None else patch.object(rp, 'execute_research', wraps=rp.execute_research)
        with context, patch('sys.stdout', new=StringIO()):
            code = rp.main(arguments)
        process.poll.return_value = code
        return json.loads(operation['result'].read_text(encoding='utf-8'))

    def complete_final(self, dialog):
        process, _ = self.launch(dialog)
        self.child_result(dialog, process)
        dialog._poll_process()

    def test_restart_restores_base_batch_and_reuses_completed_candidate(self):
        dialog = self.dialog()
        self.complete_final(dialog)
        saved = dialog._saved_request_path
        self.assertEqual({}, json.loads(saved.read_text(encoding='utf-8'))['recoveries'])
        repository = ResearchRepository(self.fixture.request.database)
        executions = repository.load_final_holdout_executions(self.fixture.fixture.batch.batch_id)
        events = repository.load_final_holdout_events(self.fixture.fixture.batch.window_id)
        self.fixture.path.write_text('{broken', encoding='utf-8')

        restarted = FinalHoldoutDialog(self.root / 'state')
        self.dialogs.append(restarted)
        self.assertTrue(restarted._resume.isEnabled())
        self.assertEqual(str(saved), restarted._request_path.text())
        process, _ = self.launch(restarted, restarted._resume.click)
        result = self.child_result(restarted, process)
        self.assertEqual('CACHED', result['candidates'][0]['state'])
        restarted._poll_process()
        self.assertEqual(executions, repository.load_final_holdout_executions(self.fixture.fixture.batch.batch_id))
        self.assertEqual(events, repository.load_final_holdout_events(self.fixture.fixture.batch.window_id))

    def test_invalid_saved_final_request_does_not_enable_resume_or_open_database(self):
        state = self.root / 'state'
        state.mkdir()
        saved = state / 'last_final_holdout_request.json'
        saved.write_text('{broken', encoding='utf-8')
        dialog = FinalHoldoutDialog(state)
        self.dialogs.append(dialog)
        self.assertFalse(dialog._resume.isEnabled())
        self.assertIn('복원 실패', dialog._status.text())
        self.assertFalse(self.fixture.request.database.exists())
        self.assertEqual('{broken', saved.read_text(encoding='utf-8'))

    def test_child_identity_receipt_is_preserved_on_forced_stop(self):
        dialog = self.dialog()
        process = MagicMock()
        process.pid = os.getpid()
        process.poll.return_value = None
        self.processes.append(process)
        with patch.object(dialog._manager, 'start', return_value=process):
            dialog._start()
        owner = dialog._operation['owner']
        self.assertTrue(owner.is_file())
        with patch.object(dialog._manager, 'stop'):
            dialog.stop()
        self.assertTrue(owner.exists())

    def child_exposure(self, dialog, process):
        operation = dialog._operation
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--expose-final', str(operation['request']),
                '--result', str(operation['result']), '--cancel', str(operation['cancel'])])
        process.poll.return_value = code
        return json.loads(operation['result'].read_text(encoding='utf-8'))

    def test_normalized_snapshot_child_result_and_owned_cleanup(self):
        dialog = self.dialog()
        process, command = self.launch(dialog)
        operation = dict(dialog._operation)
        self.assertIn('--evaluate-final', command)
        snapshot = rp.load_final_holdout_execution_request(operation['request']).to_dict()
        self.assertEqual(str(self.fixture.request.dataset.resolve()), snapshot['candidates'][0]['dataset'])
        source_before = operation['request'].read_bytes()
        self.fixture.path.write_text('{changed', encoding='utf-8')
        result = self.child_result(dialog, process)
        self.assertEqual(source_before, operation['request'].read_bytes())
        dialog._poll_process()
        self.assertEqual('COMPLETED', result['candidates'][0]['state'])
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())
        self.assertIn('전체 1 / 완료 1', dialog._summary.text())
        self.assertFalse(dialog._resume.isEnabled())
        self.assertTrue(all(not operation[key].exists() for key in ('request', 'result', 'cancel')))

    def test_running_candidate_snapshot_updates_table_without_enabling_exposure(self):
        dialog = self.dialog()
        process, _ = self.launch(dialog)
        execute = rp.execute_research

        def inspect_progress(*args, **kwargs):
            dialog._poll_process()
            self.assertEqual('실행 중', dialog._table.item(0, 1).text())
            self.assertIn('최종 평가 진행 중', dialog._status.text())
            self.assertFalse(dialog._expose.isEnabled())
            return execute(*args, **kwargs)

        result = self.child_result(dialog, process, inspect_progress)
        dialog._poll_process()
        self.assertEqual('COMPLETED', result['candidates'][0]['state'])
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())

    def test_wrong_scope_running_snapshot_keeps_previous_table_until_valid_result(self):
        dialog = self.dialog()
        process, _ = self.launch(dialog)
        dialog._operation['result'].write_text(json.dumps({
            'status': 'running', 'kind': 'independent_final_holdout',
            'version': 'independent_final_holdout_result/v1', 'batch_id': 'wrong',
        }), encoding='utf-8')
        dialog._poll_process()
        self.assertIn('진행 확인 실패', dialog._status.text())
        self.assertEqual('previous', dialog._table.item(0, 0).text())
        self.assertFalse(dialog._expose.isEnabled())
        self.child_result(dialog, process)
        dialog._poll_process()
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())

    def test_terminal_failure_requires_selected_reason_and_creates_explicit_recovery_snapshot(self):
        dialog = self.dialog()
        first_process, _ = self.launch(dialog)
        self.child_result(dialog, first_process, ValueError('review fixture'))
        dialog._poll_process()
        self.assertEqual('실패 · 명시 복구 필요', dialog._table.item(0, 1).text())
        self.assertFalse(dialog._resume.isEnabled())
        dialog._table.selectRow(0)
        self.assertFalse(dialog._recover.isEnabled())
        dialog._recovery_reason.setText('원인과 출력 부재를 확인함')
        self.assertTrue(dialog._recover.isEnabled())
        previous_owner = self.fixture.document['owner_token']
        second_process, _ = self.launch(dialog, dialog._recover.click)
        recovery = rp.load_final_holdout_execution_request(dialog._operation['request'])
        self.assertNotEqual(previous_owner, recovery.owner_token)
        self.assertEqual(self.fixture.candidate_hash, recovery.recoveries[0][0])
        self.assertEqual('원인과 출력 부재를 확인함', recovery.recoveries[0][2])
        self.assertEqual((), rp.load_final_holdout_execution_request(dialog._saved_request_path).recoveries)
        recovered = self.child_result(dialog, second_process)
        dialog._poll_process()
        self.assertEqual('COMPLETED', recovered['candidates'][0]['state'])
        rows = ResearchRepository(self.fixture.request.database).load_final_holdout_executions(
            self.fixture.fixture.batch.batch_id)
        self.assertEqual(2, rows[0]['generation'])

    def test_cancel_before_preparation_preserves_previous_table_and_can_resume(self):
        dialog = self.dialog()
        process, _ = self.launch(dialog)
        dialog._request_cancel()
        self.child_result(dialog, process)
        dialog._poll_process()
        self.assertEqual('previous', dialog._table.item(0, 0).text())
        self.assertIn('입력 확인 전 취소', dialog._status.text())
        self.assertTrue(dialog._resume.isEnabled())
        self.assertFalse(self.fixture.request.database.exists())

    def test_resource_block_is_visible_and_candidate_is_recoverable(self):
        dialog = self.dialog()
        process, _ = self.launch(dialog)
        result = self.child_result(dialog, process, rp.ResearchResourceBlocked('memory fixture'))
        dialog._poll_process()
        self.assertEqual(3, process.poll())
        self.assertEqual('CANCELLED', result['candidates'][0]['state'])
        self.assertIn('자원 한도', dialog._status.text())
        dialog._table.selectRow(0)
        dialog._recovery_reason.setText('자원 예산 조정 확인')
        self.assertTrue(dialog._recover.isEnabled())

    def test_completed_result_can_be_irreversibly_exposed_through_owned_child(self):
        dialog = self.dialog()
        self.complete_final(dialog)
        dialog._exposure_reason.setText('최종 결과를 보고 다음 진입 규칙을 설계함')
        self.assertTrue(dialog._expose.isEnabled())
        process, command = self.launch(dialog, dialog._expose.click)
        self.assertIn('--expose-final', command)
        request = rp.load_final_holdout_exposure_request(dialog._operation['request'])
        self.assertEqual(self.fixture.fixture.batch.batch_id, request.batch.batch_id)
        self.assertEqual('최종 결과를 보고 다음 진입 규칙을 설계함', request.reason)
        result = self.child_exposure(dialog, process)
        dialog._poll_process()
        self.assertEqual('EXPOSED_DEVELOPMENT', result['state'])
        self.assertIn('EXPOSED_DEVELOPMENT', dialog._summary.text())
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())
        self.assertFalse(dialog._expose.isEnabled())
        self.assertFalse(dialog._resume.isEnabled())
        self.assertFalse(dialog._saved_request_path.exists())
        window = ResearchRepository(self.fixture.request.database).load_final_holdout_window(
            self.fixture.fixture.batch.window_id)
        self.assertEqual('EXPOSED_DEVELOPMENT', window['state'])

    def test_exposure_result_scope_mismatch_preserves_final_table_and_allows_retry(self):
        dialog = self.dialog()
        self.complete_final(dialog)
        dialog._exposure_reason.setText('다음 가설에 사용')
        process, _ = self.launch(dialog, dialog._expose.click)
        result = self.child_exposure(dialog, process)
        result['window_id'] = 'other'
        dialog._operation['result'].write_text(json.dumps(result), encoding='utf-8')
        dialog._poll_process()
        self.assertIn('결과 확인 실패', dialog._status.text())
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())
        # The ledger mutation succeeded, so an untrusted envelope must not make the UI claim success.
        self.assertTrue(dialog._expose.isEnabled())

    def test_cancelled_exposure_keeps_reserved_window_and_final_result(self):
        dialog = self.dialog()
        self.complete_final(dialog)
        dialog._exposure_reason.setText('다음 가설에 사용')
        process, _ = self.launch(dialog, dialog._expose.click)
        dialog._request_cancel()
        self.child_exposure(dialog, process)
        dialog._poll_process()
        self.assertIn('기록 전 취소', dialog._status.text())
        self.assertEqual('실행 완료', dialog._table.item(0, 1).text())
        self.assertTrue(dialog._expose.isEnabled())
        window = ResearchRepository(self.fixture.request.database).load_final_holdout_window(
            self.fixture.fixture.batch.window_id)
        self.assertEqual('FINAL_RESERVED', window['state'])

    def test_wrong_scope_native_exit_and_oversize_preserve_previous_table(self):
        mutations = ('scope', 'native', 'oversize')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                dialog = self.dialog()
                process, _ = self.launch(dialog)
                frozen_request = dialog._operation['request']
                if mutation == 'oversize':
                    dialog._operation['result'].write_bytes(b' ' * (16 * 1024 * 1024 + 1))
                    process.poll.return_value = 0
                else:
                    result = self.child_result(dialog, process)
                    if mutation == 'scope':
                        result['batch_id'] = 'other'
                    else:
                        process.poll.return_value = 1
                    dialog._operation['result'].write_text(json.dumps(result), encoding='utf-8')
                dialog._poll_process()
                self.assertIn('결과 확인 실패', dialog._status.text())
                self.assertEqual('previous', dialog._table.item(0, 0).text())
                self.assertTrue(frozen_request.is_file())

    def test_invalid_request_and_operation_collision_do_not_launch_or_create_database(self):
        dialog = self.dialog()
        self.fixture.path.write_text('{}', encoding='utf-8')
        with patch.object(dialog._manager, 'start', side_effect=AssertionError('must not launch')):
            dialog._start()
        self.assertIn('요청 확인 실패', dialog._status.text())
        self.assertFalse(self.fixture.request.database.exists())
        self.fixture.write()
        inside = self.fixture.request.dataset / 'state'
        other = self.dialog(inside)
        with patch.object(other._manager, 'start', side_effect=AssertionError('must not launch')):
            other._start()
        self.assertIn('outside', other._status.text())
        self.assertFalse(inside.exists())

    def test_close_is_nonblocking_and_stop_preserves_recovery_evidence(self):
        dialog = self.dialog()
        process, _ = self.launch(dialog)
        operation = dict(dialog._operation)
        keep = self.root / 'state' / 'keep.json'
        keep.write_text('keep', encoding='utf-8')
        dialog.show()
        with patch.object(dialog._manager, 'stop', side_effect=AssertionError('close must not wait')):
            dialog.close()
        self.assertFalse(dialog.isVisible())
        self.assertTrue(operation['cancel'].exists())
        with patch.object(dialog._manager, 'stop') as stop:
            dialog.stop()
        self.assertEqual(3.0, stop.call_args.kwargs['graceful_timeout'])
        self.assertTrue(keep.exists())
        self.assertTrue(self.fixture.path.exists())
        self.assertTrue(operation['request'].exists())
        self.assertTrue(operation['cancel'].exists())
        process.poll.return_value = 0

    def test_parent_reuses_final_window_and_propagates_close_and_stop(self):
        settings = MagicMock()
        settings.value.side_effect = lambda key, default=None, **kwargs: default
        with patch('kiwoom_monitor.presentation.research_dialog.QSettings', return_value=settings):
            parent = ResearchDialog(self.root / 'parent-state')
        self.dialogs.append(parent)
        parent._worker_retry_timer.stop()
        parent._evaluate_final.click()
        child = parent._final_holdout_dialog
        parent._evaluate_final.click()
        self.assertIs(child, parent._final_holdout_dialog)
        with patch.object(child, 'close') as close:
            parent.close()
            close.assert_called_once()
        with patch.object(child, 'stop') as stop:
            parent.stop()
            stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
