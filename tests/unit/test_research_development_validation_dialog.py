from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QTableWidgetItem

import test_research_development_validation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.presentation.research_dialog import (
    DevelopmentValidationDialog, ResearchDialog, _record_research_operation_owner,
)


class DevelopmentValidationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.DevelopmentValidationTests(); self.fixture.setUp()
        self.root, self.path = self.fixture.root, self.fixture.path
        self.dialogs, self.processes = [], []

    def tearDown(self):
        for process in self.processes:
            process.poll.return_value = 0
        for dialog in self.dialogs:
            dialog.stop(); dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.fixture.doCleanups()

    def dialog(self, state=None):
        dialog = DevelopmentValidationDialog(state or self.root / 'state')
        self.dialogs.append(dialog)
        dialog._request_path.setText(str(self.path))
        dialog._table.setRowCount(1)
        dialog._table.setItem(0, 0, QTableWidgetItem('previous'))
        return dialog

    def begin(self, dialog=None, *, resume=False):
        dialog = dialog or self.dialog()
        process = MagicMock(); process.poll.return_value = None
        self.processes.append(process)
        def start(*args, **kwargs):
            dialog._manager._process = process; return process
        with (patch.object(dialog._manager, 'start', side_effect=start) as launch,
              patch.object(rp.ResearchRepository, '__init__', side_effect=AssertionError('GUI may not open DB')),
              patch.object(rp, 'load_research_input', side_effect=AssertionError('GUI may not read input'))):
            if resume: dialog._launch(dialog._last_request)
            else: dialog._start()
        self.assertIsNotNone(dialog._operation, dialog._status.text())
        self.assertTrue(launch.call_args.kwargs['below_normal_priority'])
        command = launch.call_args.args[0]
        marker = command.index('--validation-owner-token')
        self.assertTrue(command[marker + 1].startswith('development-ui-owner-'))
        return dialog, process, launch.call_args.args[0]

    def child_result(self, dialog, process):
        operation = dialog._operation
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--validate-partitions', str(operation['request']), '--result', str(operation['result']), '--cancel', str(operation['cancel'])])
        process.poll.return_value = code
        return json.loads(operation['result'].read_text(encoding='utf-8'))

    def test_success_runs_real_engine_and_displays_all_folds_and_keeps_source(self):
        dialog, process, command = self.begin()
        operation = dict(dialog._operation)
        self.assertIn('--validate-partitions', command)
        self.child_result(dialog, process); dialog._poll_process()
        self.assertIn('전체 구간 실행 완료', dialog._status.text())
        self.assertEqual(2, dialog._table.rowCount())
        self.assertIn('2026-09-14 09:02:00', dialog._table.item(0, 1).text())
        self.assertEqual('실행 완료', dialog._table.item(0, 2).text())
        self.assertIn('초기 현금은 구간마다 별개', dialog._summary.text())
        self.assertIsNone(dialog._operation)
        self.assertTrue(self.path.exists()); self.assertTrue(self.fixture.batch.request.database.exists())
        self.assertTrue(all(not operation[key].exists() for key in ('request', 'result', 'cancel')))
        self.assertTrue(dialog._resume.isEnabled())

    def test_roundtrip_snapshot_absolute_paths_and_resume_ignores_modified_source(self):
        dialog, process, _ = self.begin()
        snapshot = dialog._operation['request'].read_bytes()
        parsed = rp.load_development_validation_request(dialog._operation['request'])
        self.assertEqual(parsed.to_dict(), self.fixture.batch.to_dict())
        self.assertTrue(Path(parsed.to_dict()['request']['dataset']).is_absolute())
        self.path.write_text('{broken')
        self.child_result(dialog, process); dialog._poll_process()
        dialog, process, _ = self.begin(dialog, resume=True)
        self.assertEqual(snapshot, dialog._operation['request'].read_bytes())
        result = self.child_result(dialog, process)
        self.assertEqual(0, result['attempted_now']); self.assertEqual(2, result['cached_count'])
        dialog._poll_process()
        self.assertEqual('완료 결과 재사용', dialog._table.item(1, 2).text())

    def test_restart_restores_frozen_request_and_reuses_completed_runs(self):
        dialog, process, _ = self.begin()
        saved = dialog._saved_request_path
        self.assertEqual(self.fixture.batch.to_dict(), rp.load_development_validation_request(saved).to_dict())
        self.child_result(dialog, process); dialog._poll_process()
        self.path.write_text('{broken', encoding='utf-8')

        restarted = DevelopmentValidationDialog(self.root / 'state')
        self.dialogs.append(restarted)
        self.assertTrue(restarted._resume.isEnabled())
        self.assertEqual(str(saved), restarted._request_path.text())
        restarted, process, _ = self.begin(restarted, resume=True)
        result = self.child_result(restarted, process)
        self.assertEqual(0, result['attempted_now'])
        self.assertEqual(2, result['cached_count'])
        restarted._poll_process()

    def test_invalid_saved_request_does_not_enable_resume_or_touch_database(self):
        state = self.root / 'state'
        state.mkdir()
        saved = state / 'last_development_validation_request.json'
        saved.write_text('{broken', encoding='utf-8')
        dialog = self.dialog(state)
        self.assertFalse(dialog._resume.isEnabled())
        self.assertIn('복원 실패', dialog._status.text())
        self.assertFalse(self.fixture.batch.request.database.exists())
        self.assertEqual('{broken', saved.read_text(encoding='utf-8'))

    def test_duplicate_launch_after_native_exit_before_poll_is_blocked(self):
        dialog, process, _ = self.begin()
        self.child_result(dialog, process)
        with patch.object(dialog._manager, 'start', side_effect=AssertionError('duplicate')):
            dialog._start(); dialog._launch(dialog._last_request)
        dialog._poll_process()
        self.assertIsNone(dialog._operation)

    def test_cancel_publishes_all_pending_rows_and_resume_can_execute(self):
        dialog, process, _ = self.begin()
        dialog._request_cancel()
        self.assertTrue(dialog._operation['cancel'].exists())
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual('취소됨', dialog._status.text())
        self.assertEqual(2, dialog._table.rowCount())
        self.assertEqual('미시작', dialog._table.item(1, 2).text())
        dialog, process, _ = self.begin(dialog, resume=True)
        result = self.child_result(dialog, process)
        self.assertEqual(2, result['attempted_now']); dialog._poll_process()

    def progress(self):
        batch = self.fixture.batch
        rows = [dict(fold_name=fold.name, role=fold.role, start=fold.start, end=fold.end,
                     run_id='', state='NOT_STARTED', reason='') for fold in batch.request.evaluation.folds if fold.name in batch.fold_names]
        return {'status': 'ok', 'kind': 'independent_development_validation', 'batch_status': 'PARTIAL',
                'database': str(batch.request.database.resolve()), 'fold_names': list(batch.fold_names),
                'implementation_hash': 'locked', 'steps': rows, 'run_ids': [],
                'comparison_scope': 'identified_runs_only/v1', 'comparison': None}

    def test_live_progress_preserves_pending_rows_and_checks_hash_stability(self):
        dialog, process, _ = self.begin()
        document = self.progress(); document['steps'][0].update(run_id='one', state='RUNNING'); document['run_ids'] = ['one']
        path = dialog._operation['result']
        path.write_text(json.dumps(document)); dialog._poll_process()
        self.assertEqual('실행 중', dialog._table.item(0, 2).text())
        self.assertEqual('미시작', dialog._table.item(1, 2).text())
        self.assertIn('진행 중', dialog._status.text()); self.assertIsNotNone(dialog._operation)
        document['implementation_hash'] = 'different'; path.write_text(json.dumps(document)); dialog._poll_process()
        self.assertIn('hash 불일치', dialog._status.text())
        self.assertEqual('실행 중', dialog._table.item(0, 2).text())

    def test_partial_comparison_complete_does_not_mark_entire_batch_complete(self):
        dialog, process, _ = self.begin()
        document = self.progress(); document['steps'][0].update(run_id='one', state='CACHED'); document['run_ids'] = ['one']
        document['comparison'] = {'version': 'independent_development_comparison/v1', 'status': 'COMPLETE',
            'requested_count': 1, 'partitions': [{'run_id': 'one', 'status': 'ELIGIBLE', 'net_pnl_won': 100, 'max_drawdown_won': 20}]}
        dialog._operation['result'].write_text(json.dumps(document)); process.poll.return_value = 0
        dialog._poll_process()
        self.assertIn('일부 구간 완료', dialog._status.text())
        self.assertIn('전체 2 / 완료 1 / 미시작 1', dialog._summary.text())

    def test_resource_block_native_three_displays_all_rows_and_remains_resumable(self):
        dialog, process, _ = self.begin()
        with patch.object(rp, 'research_input_encoded_bytes', side_effect=rp.ResearchResourceBlocked('test memory cap')):
            self.child_result(dialog, process)
        self.assertEqual(3, process.poll())
        dialog._poll_process()
        self.assertEqual('자원 한도로 중단됨', dialog._status.text())
        self.assertEqual(2, dialog._table.rowCount())
        self.assertTrue(dialog._resume.isEnabled())

    def test_native_zero_with_stale_running_checkpoint_is_not_success(self):
        dialog, process, _ = self.begin()
        document = self.progress(); document['steps'][0].update(run_id='one', state='RUNNING'); document['run_ids'] = ['one']
        frozen_request = dialog._operation['request']
        dialog._operation['result'].write_text(json.dumps(document)); process.poll.return_value = 0
        dialog._poll_process()
        self.assertIn('완료 상태 불일치', dialog._status.text())
        self.assertEqual('previous', dialog._table.item(0, 0).text())
        self.assertTrue(frozen_request.is_file())

    def test_failure_busy_and_cache_invalid_visible_without_automatic_retry(self):
        for state in ('FAILED', 'BUSY', 'CACHE_INVALID', 'BUDGET_EXHAUSTED'):
            dialog, process, _ = self.begin()
            document = self.progress(); document['steps'][0].update(state=state, reason='review')
            dialog._operation['result'].write_text(json.dumps(document)); process.poll.return_value = 0
            dialog._poll_process()
            self.assertEqual('review', dialog._table.item(0, 6).text())
            self.assertIn('일부', dialog._status.text()); self.assertFalse(dialog._poll.isActive())

    def test_scope_period_and_false_completion_errors_preserve_previous_table(self):
        for change in ('database', 'fold_names', 'period', 'run_ids', 'batch_status', 'comparison'):
            dialog, process, _ = self.begin()
            document = self.progress()
            if change == 'period': document['steps'][0]['end'] = document['steps'][1]['end']
            elif change == 'comparison': document['comparison'] = {'version': 'wrong'}
            elif change == 'run_ids': document['run_ids'] = ['other']
            elif change == 'batch_status': document['batch_status'] = 'COMPLETED'
            else: document[change] = 'wrong'
            dialog._operation['result'].write_text(json.dumps(document)); process.poll.return_value = 0
            dialog._poll_process()
            self.assertIn('확인 실패', dialog._status.text())
            self.assertEqual('previous', dialog._table.item(0, 0).text())
            self.assertIsNone(dialog._operation)

    def test_bad_native_exit_missing_oversize_and_json_results_preserve_table(self):
        for kind in ('native', 'missing', 'large', 'json', 'list'):
            dialog, process, _ = self.begin(); path = dialog._operation['result']
            if kind == 'native': path.write_text(json.dumps(self.progress()))
            elif kind == 'large': path.write_bytes(b' ' * (16 * 1024 * 1024 + 1))
            elif kind == 'json': path.write_text('{')
            elif kind == 'list': path.write_text('[]')
            process.poll.return_value = 1 if kind == 'native' else 0
            dialog._poll_process()
            self.assertIn('확인 실패', dialog._status.text())
            self.assertEqual('previous', dialog._table.item(0, 0).text())

    def test_invalid_request_and_launch_failure_do_not_touch_source_or_db(self):
        dialog = self.dialog(); original = self.path.read_bytes()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch failed')):
            dialog._start()
        self.assertIsNone(dialog._operation); self.assertTrue(dialog._run.isEnabled())
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.fixture.batch.request.database.exists())
        self.assertEqual([], list((self.root / 'state').glob('development_validation_*')))
        self.assertTrue((self.root / 'state' / 'last_development_validation_request.json').exists())
        self.path.write_text('{}'); dialog._start()
        self.assertIn('요청 확인 실패', dialog._status.text())

    def test_operation_files_inside_frozen_dataset_rejected_before_launch(self):
        dialog = self.dialog(self.fixture.batch.request.dataset / 'state')
        with patch.object(dialog._manager, 'start', side_effect=AssertionError('must not launch')):
            dialog._start()
        self.assertIsNone(dialog._operation)
        self.assertIn('outside', dialog._status.text())
        self.assertFalse((self.fixture.batch.request.dataset / 'state').exists())

    def test_close_is_nonblocking_cancellation_and_poll_finishes_while_hidden(self):
        dialog, process, _ = self.begin(); dialog.show()
        with patch.object(dialog._manager, 'stop', side_effect=AssertionError('close must not wait')):
            dialog.close()
        self.assertFalse(dialog.isVisible()); self.assertTrue(dialog._poll.isActive())
        self.assertTrue(dialog._operation['cancel'].exists())
        self.child_result(dialog, process); dialog._poll_process()
        self.assertIsNone(dialog._operation)

    def test_app_stop_cancels_owned_child_and_preserves_other_files(self):
        dialog, process, _ = self.begin(); operation = dict(dialog._operation)
        process.pid = os.getpid()
        self.assertTrue(_record_research_operation_owner(operation, process, 'development_validation'))
        unrelated = self.root / 'state' / 'keep.json'; unrelated.write_text('keep')
        with patch.object(dialog._manager, 'stop') as stop:
            dialog.stop()
        self.assertEqual(3.0, stop.call_args.kwargs['graceful_timeout'])
        self.assertTrue(unrelated.exists()); self.assertTrue(self.path.exists())
        self.assertTrue(operation['request'].exists())
        self.assertTrue(operation['cancel'].exists())
        self.assertTrue(operation['owner'].exists())

    def test_parent_reuses_window_and_propagates_close_and_stop(self):
        with patch('kiwoom_monitor.presentation.research_dialog.QSettings') as settings:
            settings.return_value.value.side_effect = lambda key, default=None, **kw: default
            parent = ResearchDialog(self.root / 'parent'); self.dialogs.append(parent)
        parent._worker_retry_timer.stop()
        parent._show_development_validation(); first = parent._validation_dialog
        parent._show_development_validation(); self.assertIs(first, parent._validation_dialog)
        with patch.object(first, 'close') as close:
            parent.close(); close.assert_called_once()
        with patch.object(first, 'stop') as stop:
            parent.stop(); stop.assert_called_once()

    def test_real_hidden_child_keeps_qt_heartbeat_and_exits_zero(self):
        dialog = self.dialog(); beats = []
        documents = []
        original = dialog._apply_progress
        def capture(document, **kwargs):
            documents.append(deepcopy(document)); original(document, **kwargs)
        heartbeat = QTimer(); heartbeat.setInterval(10); heartbeat.timeout.connect(lambda: beats.append(1)); heartbeat.start()
        try:
            with patch.object(dialog, '_apply_progress', side_effect=capture):
                dialog._start()
                self.assertIsNotNone(dialog._operation, dialog._status.text())
                process = dialog._manager.process
                deadline = time.monotonic() + 30
                while dialog._operation is not None and time.monotonic() < deadline:
                    self.app.processEvents(); time.sleep(0.01)
            self.assertIsNone(dialog._operation, dialog._status.text())
            details = dialog._status.text() + ' ' + json.dumps(documents[-1] if documents else {}, ensure_ascii=False)
            self.assertEqual(0, process.poll(), details)
            self.assertGreaterEqual(len(beats), 3)
            self.assertIn('전체 구간 실행 완료', dialog._status.text(), details)
        finally:
            heartbeat.stop()


if __name__ == '__main__':
    unittest.main()
