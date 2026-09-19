import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from kiwoom_monitor import research_process as rp


class ResultPublicationTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory(); self.addCleanup(root.cleanup)
        self.root = Path(root.name); self.path = self.root / 'progress.json'
        self.path.write_text('{"state":"old"}', encoding='utf-8')

    def conflict(self, winerror=5):
        error = PermissionError('temporary reader conflict'); error.winerror = winerror
        return error

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing contract')
    def test_real_reader_handle_conflict_releases_then_atomic_publication_succeeds(self):
        ready, release = Event(), Event(); snapshots = []; conflicts = []
        def reader():
            with self.path.open('rb') as stream:
                snapshots.append(stream.read()); ready.set(); release.wait(5)
        worker = Thread(target=reader); worker.start()
        original = Path.replace
        def replace(source, target):
            try:
                return original(source, target)
            except PermissionError as exc:
                conflicts.append(exc.winerror); release.set(); raise
        try:
            self.assertTrue(ready.wait(5))
            with patch.object(Path, 'replace', new=replace):
                rp._write_result(self.path, {'state': 'new'})
        finally:
            release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(conflicts)
        self.assertEqual([b'{"state":"old"}'], snapshots)
        self.assertEqual({'state': 'new'}, json.loads(self.path.read_text()))
        self.assertEqual([], list(self.root.glob('*.tmp')))

    @unittest.skipUnless(os.name == 'nt', 'Windows retry contract')
    def test_transient_conflict_retries_only_rename_without_reencoding(self):
        original = Path.replace; calls = []
        def replace(source, target):
            calls.append(source)
            if len(calls) < 4:
                raise self.conflict()
            return original(source, target)
        with (patch.object(Path, 'replace', new=replace), patch.object(rp.time, 'sleep') as sleep,
              patch.object(rp.json, 'dumps', wraps=json.dumps) as dumps):
            rp._write_result(self.path, {'state': 'new'})
        self.assertEqual(4, len(calls)); self.assertEqual(1, len(set(calls)))
        self.assertEqual(3, sleep.call_count); dumps.assert_called_once()
        self.assertEqual({'state': 'new'}, json.loads(self.path.read_text()))

    @unittest.skipUnless(os.name == 'nt', 'Windows retry contract')
    def test_persistent_conflict_is_bounded_keeps_old_snapshot_and_cleans_temp(self):
        with (patch.object(Path, 'replace', side_effect=self.conflict(32)) as replace,
              patch.object(rp.time, 'sleep') as sleep):
            with self.assertRaises(PermissionError):
                rp._write_result(self.path, {'state': 'new'})
        self.assertEqual(10, replace.call_count); self.assertEqual(9, sleep.call_count)
        self.assertEqual({'state': 'old'}, json.loads(self.path.read_text()))
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_unrelated_io_error_is_not_retried_and_preserves_old_snapshot(self):
        with (patch.object(Path, 'replace', side_effect=OSError('disk failure')) as replace,
              patch.object(rp.time, 'sleep') as sleep):
            with self.assertRaises(OSError):
                rp._write_result(self.path, {'state': 'new'})
        replace.assert_called_once(); sleep.assert_not_called()
        self.assertEqual({'state': 'old'}, json.loads(self.path.read_text()))
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_other_permission_error_is_not_retried(self):
        with (patch.object(Path, 'replace', side_effect=self.conflict(13)) as replace,
              patch.object(rp.time, 'sleep') as sleep):
            with self.assertRaises(PermissionError):
                rp._write_result(self.path, {'state': 'new'})
        replace.assert_called_once(); sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
