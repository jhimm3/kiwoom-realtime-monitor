"""Opt-in real PostgreSQL ownership/reset gates, on the NEW replay DB only.

An operator must provision and seal a controlled fixture first. Tests restore
that exact baseline before/after, never provision implicitly or touch the old
integration DB. URL and owner token are supplied only via environment variables.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline


class RecordedReplayBaselinePostgresTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        if not url or not token:
            self.skipTest('provisioned/sealed dedicated replay DB and owner token are required')
        self.lease = baseline.ReplayDatabaseLease(url, token)
        self.lease.__enter__()
        self.addCleanup(self.lease.__exit__, None, None, None)
        self.baseline_id = self.lease.status()['baseline_id']
        if not self.baseline_id:
            self.skipTest('operator must seal a controlled baseline before these gates')
        self.lease.restore(self.baseline_id)
        self.addCleanup(self.lease.restore, self.baseline_id)
        self.owner = 'replay-gate-' + uuid4().hex

    def write(self):
        self.lease.store().upsert_documents('replay_gate', [{
            'owner': self.owner, 'key': 'latest', 'document': {'value': 123},
        }])

    def test_native_per_call_close_restore_rows_and_next_sequence_are_repeatable(self):
        with self.lease.connection.cursor() as cursor:
            before = self.lease._sequences(cursor)
        for _ in range(3):
            store = self.lease.store()
            self.write()
            self.assertIsNotNone(store.load_document('replay_gate', self.owner, 'latest'))
            self.assertEqual(0, self.lease.status()['owned_connections'])
            with self.lease.connection.cursor() as cursor:
                cursor.execute("SELECT nextval('central_observation_revisions_accepted_sequence_seq')")
                self.assertEqual(next(iter(before.values()))['next_value'], cursor.fetchone()[0])
            restored = self.lease.restore(self.baseline_id)
            self.assertTrue(restored['baseline_managed'])
            self.assertFalse(restored['source_state_equivalent'])
            with self.assertRaisesRegex(RuntimeError, 'retired'):
                store.load_document('replay_gate', self.owner, 'latest')
            self.assertIsNone(self.lease.store().load_document('replay_gate', self.owner, 'latest'))

    def test_failed_post_restore_verification_rolls_back_rows_and_sequence_restart(self):
        self.write()
        with self.lease.connection.cursor() as cursor:
            cursor.execute("SELECT nextval('central_observation_revisions_accepted_sequence_seq')")
            cursor.fetchone()
            before = self.lease._sequences(cursor)
        original = self.lease._tables_digest
        def fail_verification(cursor, schema):
            if schema == 'public':
                raise RuntimeError('injected failure after rows and sequence restart')
            return original(cursor, schema)
        with patch.object(self.lease, '_tables_digest', side_effect=fail_verification):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                self.lease.restore(self.baseline_id)
        with self.lease.connection.cursor() as cursor:
            self.assertEqual(before, self.lease._sequences(cursor))
            cursor.execute('SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s',
                           ('replay_gate', self.owner, 'latest'))
            self.assertIsNotNone(cursor.fetchone())
        with self.assertRaisesRegex(RuntimeError, 'restore_required'):
            self.lease.store()

    def test_wrong_baseline_open_native_and_foreign_session_each_prevent_reset(self):
        self.write()
        with self.assertRaisesRegex(RuntimeError, 'expected_baseline_mismatch'):
            self.lease.restore('0' * 64)
        self.lease.restore(self.baseline_id)
        self.write()
        native = self.lease.store()._connect()
        try:
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                self.lease.restore(self.baseline_id)
        finally:
            native.close()
        outsider = baseline._maintenance_connection(self.lease.database_url)
        try:
            with self.assertRaisesRegex(RuntimeError, 'external_database_session'):
                self.lease.restore(self.baseline_id)
            with outsider.cursor() as cursor:
                cursor.execute('SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s',
                               ('replay_gate', self.owner, 'latest'))
                self.assertIsNotNone(cursor.fetchone())
        finally:
            outsider.close()

    def test_changed_baseline_snapshot_fails_before_reset_and_is_rolled_back(self):
        self.write()
        # Keep the intentional corruption in an outer transaction, so it is
        # rolled back even if the assertion fails. Restore uses a savepoint.
        with self.lease.connection.transaction(force_rollback=True):
            with self.lease.connection.cursor() as cursor:
                cursor.execute('INSERT INTO replay_baseline.central_documents '
                               '(collection,owner,document_key,updated_at,document_json) '
                               'VALUES(%s,%s,%s,0,%s::jsonb)',
                               ('replay_gate', self.owner, 'corrupt', '{}'))
            with self.assertRaisesRegex(RuntimeError, 'snapshot_changed'):
                self.lease.restore(self.baseline_id)
            with self.lease.connection.cursor() as cursor:
                cursor.execute('SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s',
                               ('replay_gate', self.owner, 'latest'))
                self.assertIsNotNone(cursor.fetchone())


if __name__ == '__main__':
    unittest.main()
