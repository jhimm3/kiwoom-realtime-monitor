"""Prove archive seed semantics before changing capsule admission or sealing."""
import hashlib
import json
import unittest
from datetime import datetime, timezone

from kiwoom_monitor.central_server.diagnostic_account_archive import pack_index, iter_index, prepare_seed
from kiwoom_monitor.central_server.diagnostic_replay_contract import freeze_payload, CODEC_VERSION
from kiwoom_monitor.central_server.diagnostic_account_input import VERSION
from kiwoom_monitor.central_server.database_account_settings import _real_account_recovery_document
from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery

LIMIT = 32 * 1024 * 1024
REF = 'f9f2a34b-9188-4f8e-9e14-6fbc271c0001'
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class AccountArchiveTests(unittest.TestCase):
    def operation(self):
        binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, REF), 3, NOW)
        recovery = AccountRecovery(AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), ())
        arguments = dict(binding=binding, recovery=recovery, received_at=NOW, settings_revision=2)
        _, body = _real_account_recovery_document(**arguments)
        key = hashlib.sha256(body.encode()).hexdigest()
        row = {'method': 'save_real_account_recovery', 'event_type': 'operation_start',
               'codec_version': CODEC_VERSION, 'account_input_version': VERSION,
               'account_alias_domain': 'fixture', 'payload': freeze_payload(arguments).value}
        return row, key, json.loads(body)

    def test_40452_old_identities_fit_original_budget_without_any_document_body(self):
        rows = (('real_account_recovery', 'kiwoom:real:' + REF, '%064x' % i, 1791763200.123456)
                for i in range(40452))
        index, count, charge = pack_index(rows, maximum_rows=50000, maximum_bytes=LIMIT)
        self.assertEqual(40452, count)
        self.assertLess(charge, LIMIT)
        self.assertLess(freeze_payload(index, maximum_bytes=LIMIT).charge, LIMIT)
        self.assertEqual(40452, sum(1 for _ in iter_index(index)))
        self.assertNotIn('document_json', json.dumps(index))

    def test_only_initial_touched_key_is_seeded_with_original_timestamp(self):
        row, key, body = self.operation()
        rows = sorted([('real_account_recovery', 'kiwoom:real:' + REF, key, 123.25),
                       ('real_account_recovery', 'kiwoom:real:' + REF, '0' * 64, 1.0)])
        index, _, _ = pack_index(rows, maximum_rows=50000, maximum_bytes=LIMIT)
        seed = prepare_seed(index, [row, row], maximum_bytes=LIMIT)
        self.assertEqual(1, len(seed))
        self.assertEqual(123.25, seed[0]['updated_at'])
        self.assertEqual(body, seed[0]['document_json'])
        self.assertEqual(key, seed[0]['document_key'])
        document = seed[0]['document_json']
        self.assertEqual(REF, document['scope']['account_ref'])
        self.assertEqual(2, document['settings_revision'])
        self.assertEqual(10000, document['recovery']['account']['available_cash_won'])
        self.assertEqual({'005930': 3}, document['recovery']['account']['positions'])

    def test_new_input_and_other_owner_do_not_gain_initial_rows(self):
        row, key, _ = self.operation()
        self.assertEqual([], prepare_seed([], [row], maximum_bytes=LIMIT))
        index, _, _ = pack_index([('real_account_event', 'other-owner', key, 1.0)],
                                 maximum_rows=50000, maximum_bytes=LIMIT)
        self.assertEqual([], prepare_seed(index, [row], maximum_bytes=LIMIT))

    def test_duplicate_unsorted_invalid_and_unbounded_indexes_are_rejected(self):
        for rows in ([('real_account_event', 'owner', 'x' * 64, 1)],
                     [('real_account_event', 'owner', '0' * 64, float('inf'))],
                     [('real_account_event', 'owner', '0' * 64, 1)] * 2,
                     [('real_account_event', 'owner', '1' * 64, 1),
                      ('real_account_event', 'owner', '0' * 64, 1)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                pack_index(rows, maximum_rows=50000, maximum_bytes=LIMIT)
        with self.assertRaisesRegex(ValueError, 'row_limit'):
            pack_index([('real_account_event', 'owner', '0' * 64, 1)], maximum_rows=0, maximum_bytes=LIMIT)
        with self.assertRaisesRegex(ValueError, 'byte_limit'):
            pack_index([('real_account_event', 'owner', '0' * 64, 1)], maximum_rows=50000, maximum_bytes=1)

    def test_missing_or_corrupt_recorded_payload_is_rejected(self):
        row, _, _ = self.operation()
        row.pop('payload')
        with self.assertRaises(ValueError):
            prepare_seed([], [row], maximum_bytes=LIMIT)
        for field, value in (("payload", {"invalid": "frozen payload"}), ("codec_version", "invalid"), ("account_input_version", "invalid")):
            row, _, _ = self.operation()
            row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                prepare_seed([], [row], maximum_bytes=LIMIT)

    def test_quota_scan_counts_only_regular_files_inside_valid_sessions(self):
        import tempfile
        from pathlib import Path
        from kiwoom_monitor.central_server.diagnostic_trace import _trace_storage_usage
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / '20261012T000000Z-123456789abc'
            session.mkdir()
            (session / 'payload').write_bytes(b'12345')
            (session / 'manifest').write_bytes(b'12')
            (session / 'subdirectory').mkdir()
            (session / 'subdirectory/ignored').write_bytes(b'123456789')
            other = root / 'not-a-trace'
            other.mkdir()
            (other / 'ignored').write_bytes(b'123456789')
            self.assertEqual((1, 7), _trace_storage_usage(root))
            import os
            from contextlib import contextmanager
            from types import SimpleNamespace
            from unittest.mock import patch
            original_scandir = os.scandir
            @contextmanager
            def scandir(path):
                with original_scandir(path) as entries:
                    values = list(entries)
                if Path(path) == root:
                    values.append(SimpleNamespace(name='20261012T000001Z-123456789abc', path=str(other), is_dir=lambda follow_symlinks=True: follow_symlinks))
                elif Path(path) == session:
                    values.append(SimpleNamespace(is_file=lambda follow_symlinks=True: follow_symlinks, stat=lambda follow_symlinks=True: SimpleNamespace(st_size=999999)))
                yield iter(values)
            with patch('kiwoom_monitor.central_server.diagnostic_trace.os.scandir', scandir):
                self.assertEqual((1, 7), _trace_storage_usage(root))



if __name__ == '__main__':
    unittest.main()
