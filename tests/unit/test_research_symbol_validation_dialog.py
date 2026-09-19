from copy import deepcopy
from datetime import datetime, timedelta
import json
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import test_research_development_validation_dialog as ui
import test_research_symbol_validation as fixtures
from kiwoom_monitor import research_process as rp


class SymbolValidationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.SymbolValidationTests(); self.fixture.setUp()
        self.root, self.path = self.fixture.root, self.fixture.path
        self.dialogs, self.processes = [], []

    tearDown = ui.DevelopmentValidationDialogTests.tearDown
    dialog = ui.DevelopmentValidationDialogTests.dialog
    begin = ui.DevelopmentValidationDialogTests.begin
    child_result = ui.DevelopmentValidationDialogTests.child_result

    def test_real_execution_group_rows_empty_bucket_and_no_cross_group_sum(self):
        dialog, process, command = self.begin()
        result = self.child_result(dialog, process); dialog._poll_process()
        self.assertIn('--validate-partitions', command)
        self.assertEqual(4, dialog._table.rowCount())
        self.assertEqual(['train / 그룹 1', 'train / 그룹 2', 'validation / 그룹 1', 'validation / 그룹 2'],
                         [dialog._table.item(i, 0).text() for i in range(4)])
        self.assertEqual(2, dialog._groups.rowCount())
        self.assertEqual('2', dialog._groups.item(1, 2).text())
        self.assertEqual('0', dialog._groups.item(1, 4).text())
        self.assertEqual('N/A', dialog._groups.item(1, 6).text())
        self.assertIn('NO_CLOSED_TRADE', dialog._table.item(1, 3).text())
        self.assertIn('전체 구간 실행 완료', dialog._status.text())
        self.assertIsNone(result['comparison'])
        self.assertIn('합산하지 않음', dialog._groups.item(0, 6).toolTip())

    def test_resume_uses_locked_snapshot_and_cached_results(self):
        dialog, process, _ = self.begin()
        snapshot = dialog._operation['request'].read_bytes()
        self.child_result(dialog, process); dialog._poll_process()
        self.path.write_text('{broken', encoding='utf-8')
        dialog, process, _ = self.begin(dialog, resume=True)
        self.assertEqual(snapshot, dialog._operation['request'].read_bytes())
        result = self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual((0, 4), (result['attempted_now'], result['cached_count']))
        self.assertEqual('완료 결과 재사용', dialog._table.item(3, 2).text())

    def test_cancel_before_input_load_keeps_matrix_and_resume(self):
        dialog, process, _ = self.begin(); dialog._request_cancel()
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual('취소됨', dialog._status.text())
        self.assertEqual(4, dialog._table.rowCount())
        self.assertEqual('2', dialog._groups.item(0, 3).text())
        self.assertEqual('미판정', dialog._groups.item(0, 8).text())
        dialog, process, _ = self.begin(dialog, resume=True)
        self.assertEqual(4, self.child_result(dialog, process)['attempted_now'])
        dialog._poll_process()

    def test_maximum_200_pending_steps_are_visible_without_gui_input_read(self):
        document = deepcopy(self.fixture.document)
        start = datetime.fromisoformat(document['request']['evaluation']['folds'][0]['start'])
        folds = [dict(name=f'f{i}', role='TRAIN', start=(start + timedelta(minutes=3*i)).isoformat(),
                      end=(start + timedelta(minutes=3*i+1)).isoformat()) for i in range(10)]
        document['request']['evaluation']['folds'] = folds
        document['fold_names'] = [fold['name'] for fold in folds]
        document['symbol_partition'].update(bucket_count=20, buckets=list(range(20)))
        self.fixture.write(document)
        dialog, process, _ = self.begin(); dialog._request_cancel()
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual(200, dialog._table.rowCount())
        self.assertEqual(20, dialog._groups.rowCount())
        self.assertEqual('10', dialog._groups.item(19, 3).text())
        self.assertIn('미시작 200', dialog._summary.text())

    def test_v1_request_after_grouped_result_restores_time_only_display(self):
        dialog, process, _ = self.begin()
        self.child_result(dialog, process); dialog._poll_process()
        self.assertFalse(dialog._groups.isHidden())
        dialog._request_path.setText(str(self.fixture.fixture.path))
        dialog, process, _ = self.begin(dialog)
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual(2, dialog._table.rowCount())
        self.assertTrue(dialog._groups.isHidden())
        self.assertEqual(0, dialog._groups.rowCount())

    def test_invalid_matrix_group_scope_counts_and_metrics_preserve_both_tables(self):
        dialog, process, _ = self.begin()
        result = self.child_result(dialog, process)
        process.poll.return_value = None
        dialog._apply_progress(result, finished=False)
        def capture():
            return [[table.item(i, j).text() for i in range(table.rowCount()) for j in range(table.columnCount())]
                    for table in (dialog._table, dialog._groups)]
        before = capture()
        mutations = (
            lambda doc: doc.update(symbol_partition={}),
            lambda doc: doc.update(requested_step_count=True),
            lambda doc: doc['steps'].reverse(),
            lambda doc: doc['steps'][0].update(step_key='other/bucket-0'),
            lambda doc: doc['group_comparisons'].reverse(),
            lambda doc: doc['group_comparisons'][0].update(run_ids=doc['run_ids']),
            lambda doc: doc['group_comparisons'][0].update(completed_step_count=True),
            lambda doc: doc['group_comparisons'][0].update(comparison_scope='all_groups'),
            lambda doc: doc['group_comparisons'][0].update(comparison=None),
            lambda doc: doc['group_comparisons'][0]['comparison'].update(median_partition_pnl_won=float('nan')),
            lambda doc: doc['group_comparisons'][0]['comparison'].update(worst_partition_drawdown_won=-1),
            lambda doc: doc.update(batch_status='PARTIAL'),
        )
        for mutate in mutations:
            doc = deepcopy(result); mutate(doc)
            with self.subTest(mutate=mutate):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    dialog._apply_progress(doc, finished=False)
                self.assertEqual(before, capture())

    def test_partial_group_comparison_complete_is_not_whole_request_complete(self):
        dialog, process, _ = self.begin()
        original = rp._write_result
        seen = []
        def publish(path, document):
            original(path, document)
            dialog._apply_progress(document, finished=False)
            seen.append((document['batch_status'], dialog._summary.text(), dialog._groups.item(0, 2).text()))
            if document['steps'][0]['state'] == 'COMPLETED':
                dialog._request_cancel()
        with patch.object(rp, '_write_result', side_effect=publish):
            result = self.child_result(dialog, process)
        dialog._poll_process()
        self.assertEqual('CANCELLED', result['batch_status'])
        self.assertEqual('1', dialog._groups.item(0, 2).text())
        self.assertIn('미시작 3', dialog._summary.text())
        self.assertTrue(any(state == 'PARTIAL' for state, _, _ in seen))

    def test_median_preserves_fraction(self):
        dialog, process, _ = self.begin()
        result = self.child_result(dialog, process)
        result['group_comparisons'][0]['comparison']['median_partition_pnl_won'] = 1000.5
        dialog._apply_progress(result, finished=True)
        self.assertEqual('1,000.5원', dialog._groups.item(0, 6).text())

    def test_duplicate_launch_unpolled_exit_and_close_keep_owned_child(self):
        dialog, process, _ = self.begin()
        self.child_result(dialog, process)
        with patch.object(dialog._manager, 'start', side_effect=AssertionError('duplicate')):
            dialog._start(); dialog._launch(dialog._last_request)
        dialog.show(); dialog.close()
        self.assertFalse(dialog.isVisible()); self.assertIsNotNone(dialog._operation)
        dialog._poll_process(); self.assertIsNone(dialog._operation)

    def test_native_zero_with_running_state_does_not_show_success(self):
        dialog, process, _ = self.begin()
        result = self.child_result(dialog, process)
        result['steps'][0]['state'] = 'RUNNING'
        result['group_comparisons'][0]['completed_step_count'] -= 1
        result['batch_status'] = 'PARTIAL'
        dialog._operation['result'].write_text(json.dumps(result), encoding='utf-8')
        dialog._poll_process()
        self.assertIn('진행 확인 실패', dialog._status.text())
        self.assertEqual('previous', dialog._table.item(0, 0).text())

    def test_real_hidden_child_keeps_qt_heartbeat_and_native_zero(self):
        dialog = self.dialog(); beats = []
        documents = []
        original = dialog._apply_progress
        def capture(document, **kwargs):
            documents.append(deepcopy(document)); original(document, **kwargs)
        heartbeat = QTimer(); heartbeat.setInterval(10); heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        heartbeat.start()
        try:
            with patch.object(dialog, '_apply_progress', side_effect=capture):
                dialog._start(); process = dialog._manager.process
                self.assertIsNotNone(process, dialog._status.text())
                deadline = time.monotonic() + 20
                while dialog._operation is not None and time.monotonic() < deadline:
                    self.app.processEvents(); time.sleep(.01)
            self.assertIsNone(dialog._operation, dialog._status.text())
            details = dialog._status.text() + ' ' + json.dumps(documents[-1] if documents else {}, ensure_ascii=False)
            self.assertEqual(0, process.poll(), details)
            self.assertGreaterEqual(len(beats), 3)
            self.assertEqual(4, dialog._table.rowCount())
            self.assertEqual(2, dialog._groups.rowCount())
            self.assertIn('전체 구간 실행 완료', dialog._status.text())
        finally:
            heartbeat.stop()


if __name__ == '__main__':
    unittest.main()
