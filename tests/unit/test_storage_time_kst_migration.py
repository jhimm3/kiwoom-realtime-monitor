import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.migrate_storage_time_kst import (
    apply_sqlite_plan, convert_json_paths, migrate_sqlite, sqlite_plan, utc_iso_to_kst,
)
from kiwoom_monitor.infrastructure.central_content_sync import _merge_journal_link


class StorageTimeKstMigrationTests(unittest.TestCase):
    def test_conversion_preserves_instant_fraction_and_crosses_date(self):
        examples = {
            '2026-10-16T20:01:02.123456789Z': '2026-10-17T05:01:02.123456789+09:00',
            '2024-02-29 23:59:59.1+00:00': '2024-03-01 08:59:59.1+09:00',
            '2025-12-31T23:00:00+00:00': '2026-01-01T08:00:00+09:00',
            '2026-10-16 05:00:00.1234+00': '2026-10-16 14:00:00.1234+09:00',
        }
        for old, new in examples.items():
            with self.subTest(old=old):
                self.assertEqual(utc_iso_to_kst(old), new)
                self.assertEqual(utc_iso_to_kst(new), new)

    def test_existing_kst_epoch_naive_unknown_offset_and_source_text_are_unchanged(self):
        for value in ('2026-10-16T14:00:00.1+09:00', '2026-10-16T14:00:00',
                      '2026-10-16T14:00:00-00:00', '2026-10-16', 1792104900.434946,
                      'text 2026-10-16T05:00:00Z', None):
            self.assertEqual(utc_iso_to_kst(value), value)

    def fixture(self, path):
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE events(id TEXT PRIMARY KEY,updated_at TEXT,epoch REAL,document_json TEXT)')
        connection.executemany('INSERT INTO events VALUES(?,?,?,?)', [
            ('utc', '2026-10-16T05:00:00+00:00', 1.5, '{"input_hash":"original","at":"2026-10-16T05:00:00Z"}'),
            ('kst', '2026-10-16T14:00:00+09:00', 2.5, '{}'),
            ('naive', '2026-10-16T14:00:00', 3.5, '{}'),
        ])
        connection.execute('CREATE TABLE keyed_time(updated_at TEXT PRIMARY KEY,content TEXT)')
        connection.execute("INSERT INTO keyed_time VALUES('2026-10-16T05:00:00Z','unchanged')")
        connection.commit()
        return connection

    def test_transaction_backup_preservation_and_second_run_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            path, backup = Path(directory) / 'db.sqlite3', Path(directory) / 'original.json'
            connection = self.fixture(path)
            before = connection.execute('SELECT id,epoch,document_json FROM events ORDER BY id').fetchall()
            result = migrate_sqlite(path, backup, apply=True)
            self.assertTrue(result['applied'])
            self.assertEqual(result['changed_cells'], 1)
            self.assertEqual(result['protected_key_cells'], 1)
            self.assertEqual(json.loads(backup.read_text())['changes'][0]['old'], '2026-10-16T05:00:00+00:00')
            self.assertEqual(connection.execute('SELECT id,epoch,document_json FROM events ORDER BY id').fetchall(), before)
            self.assertEqual(connection.execute("SELECT updated_at FROM events WHERE id='utc'").fetchone()[0], '2026-10-16T14:00:00+09:00')
            original_backup = backup.read_bytes()
            self.assertEqual(migrate_sqlite(path, backup, apply=True)['changed_cells'], 0)
            self.assertEqual(backup.read_bytes(), original_backup)
            connection.close()

    def test_dry_run_and_existing_backup_do_not_write_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path, backup = Path(directory) / 'db.sqlite3', Path(directory) / 'original.json'
            connection = self.fixture(path)
            before = path.read_bytes()
            self.assertEqual(migrate_sqlite(path, backup)['changed_cells'], 1)
            self.assertEqual(path.read_bytes(), before)
            backup.write_text('preserved')
            with self.assertRaises(FileExistsError):
                migrate_sqlite(path, backup, apply=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(backup.read_text(), 'preserved')
            connection.close()

    def test_concurrent_change_rejects_and_rolls_back_whole_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self.fixture(Path(directory) / 'db.sqlite3')
            changes, _ = sqlite_plan(connection)
            connection.execute("UPDATE events SET updated_at='new' WHERE id='utc'")
            connection.commit()
            connection.execute('BEGIN')
            with self.assertRaisesRegex(RuntimeError, 'storage_changed_since_plan'):
                apply_sqlite_plan(connection, changes)
            connection.rollback()
            self.assertEqual(connection.execute("SELECT updated_at FROM events WHERE id='utc'").fetchone()[0], 'new')
            connection.close()

    def test_invalid_explicit_date_does_not_silently_shift_or_fix(self):
        with self.assertRaises(ValueError):
            utc_iso_to_kst('2026-02-30T05:00:00Z')

    def test_settings_value_paths_are_explicit_and_keep_other_values(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        connection.executemany('INSERT INTO settings VALUES(?,?)', [
            ('google_drive_local_changed_at', '2026-10-16T05:00:00Z'),
            ('google_drive_last_upload_success_at', '2026-10-16T14:00:00+09:00'),
            ('user_text', '2026-10-16T05:00:00Z'),
        ])
        changes, protected = sqlite_plan(connection)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['key'], ['google_drive_local_changed_at'])
        self.assertEqual(protected, [])
        apply_sqlite_plan(connection, changes)
        self.assertEqual(sqlite_plan(connection), ([], []))
        self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='user_text'").fetchone()[0],
                         '2026-10-16T05:00:00Z')
        connection.close()

    def test_audited_json_paths_leave_original_and_hash_inputs_verbatim(self):
        original = {'available_at': '2026-10-16T05:00:00Z',
                    'bars': [{'available_at': '2026-10-16T05:00:00Z',
                              'bar_start': '2026-10-16T13:59:00+09:00', 'price': 100}],
                    'input_hash': 'keep', 'inputs': {'at': '2026-10-16T05:00:00Z'},
                    'raw_response': '{"at":"2026-10-16T05:00:00Z"}'}
        result = convert_json_paths(original, (('available_at',), ('bars','*','available_at')))
        self.assertEqual(result['available_at'], '2026-10-16T14:00:00+09:00')
        self.assertEqual(result['bars'][0]['available_at'], result['available_at'])
        self.assertEqual(original['available_at'], '2026-10-16T05:00:00Z')
        for key in ('inputs','raw_response','input_hash'):
            self.assertEqual(result[key], original[key])
        self.assertEqual(result['bars'][0]['price'], 100)
        self.assertEqual(result['bars'][0]['bar_start'], original['bars'][0]['bar_start'])
        self.assertEqual(convert_json_paths(result, (('available_at',), ('bars','*','available_at'))), result)

    def test_mixed_offset_sync_does_not_revert_or_resurrect_same_instant(self):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE journal_news_links(id TEXT PRIMARY KEY,updated_at TEXT,is_deleted INTEGER,source_content_hash TEXT)')
        cases = (
            ('2026-10-16T14:00:00+09:00', False, '2026-10-16T06:00:00+00:00', False, True),
            ('2026-10-16T06:00:00+00:00', False, '2026-10-16T14:00:00+09:00', False, False),
            ('2026-10-16T05:00:00+00:00', True, '2026-10-16T14:00:00+09:00', False, False),
            ('2026-10-16T14:00:00+09:00', True, '2026-10-16T06:00:00+00:00', False, True),
        )
        for stored, deleted, incoming, incoming_deleted, expected in cases:
            with self.subTest(stored=stored, deleted=deleted, incoming=incoming):
                connection.execute('DELETE FROM journal_news_links')
                connection.execute('INSERT INTO journal_news_links VALUES(?,?,?,?)', ('id', stored, deleted, 'h'))
                values = {'id': 'id', 'updated_at': incoming, 'is_deleted': incoming_deleted,
                          'source_content_hash': 'h', '_explicit_revision': True}
                self.assertEqual(_merge_journal_link(connection, ('id',), values), expected)
        connection.close()


if __name__ == '__main__':
    unittest.main()
