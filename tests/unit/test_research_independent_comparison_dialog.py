from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QTableWidgetItem

import test_research_independent_comparison_process as fixtures
from kiwoom_monitor.presentation.research_dialog import ResearchDialog, IndependentComparisonDialog
from kiwoom_monitor.research_process import main


class IndependentComparisonDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo, self.path = fixtures.seed(self.root)
        self.dialogs, self.processes = [], []

    def tearDown(self):
        for process in self.processes:
            process.poll.return_value = 0
        for dialog in self.dialogs:
            dialog.stop(); dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def dialog(self):
        dialog = IndependentComparisonDialog(self.root / 'state')
        self.dialogs.append(dialog)
        dialog._request_path.setText(str(self.path))
        dialog._table.setRowCount(1); dialog._table.setItem(0, 0, QTableWidgetItem('previous'))
        return dialog

    def begin(self, dialog=None):
        dialog = dialog or self.dialog()
        process = MagicMock(); process.poll.return_value = None
        self.processes.append(process)
        def start(*args, **kwargs):
            dialog._manager._process = process; return process
        with (patch.object(dialog._manager, 'start', side_effect=start) as launch,
              patch.object(fixtures.ResearchRepository, '__init__', side_effect=AssertionError('GUI may not open DB'))):
            dialog._start()
        self.assertIsNotNone(dialog._operation, dialog._status.text())
        self.assertTrue(launch.call_args.kwargs['below_normal_priority'])
        return dialog, process, launch.call_args.args[0]

    def child_result(self, dialog, process):
        operation = dialog._operation
        with patch('sys.stdout', new=StringIO()):
            code = main(['--compare-runs', str(operation['request']), '--result', str(operation['result']), '--cancel', str(operation['cancel'])])
        process.poll.return_value = code
        return json.loads(operation['result'].read_text(encoding='utf-8'))

    def test_success_displays_scope_strata_and_fractional_median_and_cleans_only_owned_files(self):
        before = self.repo.path.read_bytes()
        dialog, process, command = self.begin()
        operation = dict(dialog._operation)
        self.assertIn('--compare-runs', command)
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual(2, dialog._table.rowCount())
        self.assertIn('25.5원', dialog._summary.text())
        self.assertIn('연속 계좌 수익률 아님', dialog._summary.text())
        self.assertIn('005930', dialog._table.item(0, 0).toolTip())
        self.assertIn('2026-09-14 09:00:00', dialog._table.item(0, 0).text())
        self.assertIsNone(dialog._operation)
        self.assertTrue(dialog._run.isEnabled())
        self.assertTrue(self.path.exists())
        self.assertEqual(before, self.repo.path.read_bytes())
        self.assertTrue(all(not operation[key].exists() for key in ('request', 'result', 'cancel')))

    def test_snapshot_survives_original_change_and_duplicate_call_after_unpolled_exit_is_blocked(self):
        dialog, process, _ = self.begin()
        source = dialog._operation['request'].read_bytes()
        self.path.write_text('{changed')
        self.child_result(dialog, process)
        with patch.object(dialog._manager, 'start', side_effect=AssertionError('no second launch')):
            dialog._start()
        self.assertEqual(source, dialog._operation['request'].read_bytes())
        dialog._poll_process()
        self.assertEqual(2, dialog._table.rowCount())

    def test_cancel_preserves_previous_table_and_uses_own_file(self):
        dialog, process, _ = self.begin()
        dialog._request_cancel()
        self.assertTrue(dialog._operation['cancel'].is_file())
        self.child_result(dialog, process); dialog._poll_process()
        self.assertIn('취소됨', dialog._status.text())
        self.assertEqual('previous', dialog._table.item(0, 0).text())

    def test_close_is_nonblocking_cancellation_and_hidden_result_is_consumed(self):
        dialog, process, _ = self.begin()
        dialog.show()
        with patch.object(dialog._manager, 'stop', side_effect=AssertionError('close must not wait')):
            dialog.close()
        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog._poll.isActive())
        self.child_result(dialog, process); dialog._poll_process()
        self.assertIsNone(dialog._operation)

    def test_nonzero_exit_and_wrong_envelope_or_inner_scope_or_version_preserve_previous_table(self):
        for mutation in ('native', 'kind', 'database', 'run_ids', 'inner_ids', 'version', 'malformed'):
            with self.subTest(mutation=mutation):
                dialog, process, _ = self.begin()
                result = self.child_result(dialog, process)
                if mutation == 'native': process.poll.return_value = 1
                elif mutation == 'kind': result['kind'] = 'single_run'
                elif mutation == 'database': result['database'] = 'other.sqlite3'
                elif mutation == 'run_ids': result['run_ids'] = ['other']
                elif mutation == 'inner_ids': result['comparison']['partitions'][0]['run_id'] = 'other'
                elif mutation == 'version': result['comparison']['version'] = 'v2'
                else: result['comparison']['partitions'][1].pop('closed_trade_count')
                dialog._operation['result'].write_text(json.dumps(result))
                dialog._poll_process()
                self.assertIn('실패', dialog._status.text())
                self.assertEqual('previous', dialog._table.item(0, 0).text())
                self.assertIsNone(dialog._operation)

    def test_launch_error_cleans_temp_files_and_preserves_selection_and_table(self):
        dialog = self.dialog()
        with patch.object(dialog._manager, 'start', side_effect=OSError('launch refused')):
            dialog._start()
        self.assertIn('launch refused', dialog._status.text())
        self.assertFalse(list((self.root / 'state').glob('independent_comparison_*')))
        self.assertEqual('previous', dialog._table.item(0, 0).text())
        self.assertTrue(self.path.exists())

    def test_parent_button_reuses_viewer_and_parent_stop_and_close_propagate(self):
        settings = MagicMock(); settings.value.side_effect = lambda key, default=None, **kwargs: default
        with patch('kiwoom_monitor.presentation.research_dialog.QSettings', return_value=settings):
            parent = ResearchDialog(self.root / 'parent-state')
        self.dialogs.append(parent)
        parent._compare_partitions.click()
        viewer = parent._comparison_dialog
        parent._compare_partitions.click()
        self.assertIs(viewer, parent._comparison_dialog)
        with patch.object(viewer, 'close') as close:
            parent.close(); close.assert_called_once()
        with patch.object(viewer, 'stop') as stop:
            parent.stop(); stop.assert_called_once()

    def test_app_stop_requests_own_cancel_before_stopping_child_and_cleans_only_owned_files(self):
        dialog, process, _ = self.begin()
        operation = dict(dialog._operation)
        def stop(**kwargs):
            self.assertTrue(operation['cancel'].exists())
            process.poll.return_value = 0
        with patch.object(dialog._manager, 'stop', side_effect=stop):
            dialog.stop()
        self.assertIsNone(dialog._operation)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.repo.path.exists())
        self.assertFalse(any(operation[key].exists() for key in ('request', 'result', 'cancel')))

    def test_simultaneous_viewers_share_no_operation_files(self):
        first, first_process, _ = self.begin()
        second, second_process, _ = self.begin()
        first_files, second_files = dict(first._operation), dict(second._operation)
        self.assertTrue(all(first_files[key] != second_files[key] for key in ('request', 'result', 'cancel')))
        first._request_cancel()
        self.assertFalse(second_files['cancel'].exists())
        self.child_result(first, first_process); first._poll_process()
        self.assertTrue(second_files['request'].exists())
        self.child_result(second, second_process); second._poll_process()
        self.assertEqual(2, second._table.rowCount())

    def test_missing_result_displays_incomplete_scope_without_hiding_missing_row(self):
        document = json.loads(self.path.read_text()); document['run_ids'].append('missing')
        self.path.write_text(json.dumps(document))
        dialog, process, _ = self.begin()
        self.child_result(dialog, process); dialog._poll_process()
        self.assertEqual(3, dialog._table.rowCount())
        self.assertEqual('MISSING', dialog._table.item(2, 1).text())
        self.assertIn('확인 필요', dialog._status.text())

    def test_real_child_keeps_qt_heartbeat_alive_while_reading_in_background(self):
        dialog = self.dialog()
        script = self.root / 'delayed_query.py'
        script.write_text('import time\nfrom kiwoom_monitor import research_process as rp\n'
            'original = rp.execute_independent_comparison\n'
            'def delayed(*args, **kwargs):\n    time.sleep(0.5)\n    return original(*args, **kwargs)\n'
            'rp.execute_independent_comparison = delayed\nraise SystemExit(rp.main())\n')
        def command(module, flag, args): return [sys.executable, str(script), *args]
        beats = []
        timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: beats.append(time.monotonic()))
        timer.start()
        try:
            with patch('kiwoom_monitor.presentation.research_dialog.build_auxiliary_command', side_effect=command):
                dialog._start()
            self.assertIsNotNone(dialog._operation, dialog._status.text())
            deadline = time.monotonic() + 15
            while dialog._operation is not None and time.monotonic() < deadline:
                self.app.processEvents(); time.sleep(0.01)
            self.assertIsNone(dialog._operation, dialog._status.text())
            self.assertEqual(2, dialog._table.rowCount(), dialog._status.text())
            self.assertGreaterEqual(len(beats), 3)
        finally:
            timer.stop()
