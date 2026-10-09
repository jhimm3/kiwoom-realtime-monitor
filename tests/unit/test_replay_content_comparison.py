"""Compare real content changes without erasing lineage or source-time evidence."""
from copy import deepcopy
import json
import unittest

from kiwoom_monitor.central_server.diagnostic_replay_comparison import (
    TABLE_FIELDS, collect_final_content_comparison,
)


def fixture():
    revisions = [{
        'revision_id': name, 'accepted_sequence': sequence,
        'revision_of': parent, 'kind': 'minute_bar', 'subject': '005930',
        'observation_key': '2026-10-08:09:00:SOR', 'source_id': '0B',
        'received_at': '2026-10-09T00:00:00+00:00',
        'effective_at': '2026-10-08T00:00:00+00:00',
        'available_at': '2026-10-08T00:01:02+00:00',
        'payload_hash': str(sequence), 'payload_json': {'close': 100 + sequence},
        'completeness': 'complete',
    } for name, sequence, parent in [('a', 1, None), ('b', 2, 'a')]]
    return {
        'central_dataset_snapshots': [{
            'kind': 'top20_membership', 'subject': '2026-10-08',
            'snapshot_key': '09:00:00', 'saved_at': 10., 'payload_json': {'code': '005930'},
        }],
        'central_documents': [{
            'collection': 'top20_daily_entrants', 'owner': '2026-10-08',
            'document_key': '005930', 'updated_at': 10., 'document_json': {'value': 1},
        }],
        'central_minute_bar_operations': [{
            'operation_id': 'recorded-operation', 'operation_hash': 'recorded-hash',
            'processed_at': '2026-10-09T00:00:00+00:00',
        }],
        'central_observation_revisions': revisions,
    }


class Cursor:
    def __init__(self, tables):
        self.tables, self.calls, self.batch = tables, 0, []

    def execute(self, query):
        if self.calls == 0:
            fields = ('revision_id', 'accepted_sequence', 'kind', 'subject',
                      'observation_key', 'source_id')
            rows = [{key: row[key] for key in fields}
                    for row in self.tables['central_observation_revisions']]
        else:
            rows = self.tables[list(TABLE_FIELDS)[self.calls - 1]]
        self.calls += 1
        self.batch = [(json.dumps(row),) for row in rows]

    def fetchmany(self, size):
        result, self.batch = self.batch[:size], self.batch[size:]
        return result


def collect(tables, **limits):
    return collect_final_content_comparison(
        Cursor(tables), max_bytes=limits.get('max_bytes', 1_000_000),
        max_rows=limits.get('max_rows', 100),
    )


def signatures(result):
    return {name: (row['rows'], row['sha256']) for name, row in result['tables'].items()}


class ReplayContentComparisonTests(unittest.TestCase):
    def test_generated_times_and_uuids_change_but_same_content_and_parent_chain_match(self):
        first, second = fixture(), fixture()
        for table, fields in TABLE_FIELDS.items():
            for row in second[table]:
                for field in fields:
                    row[field] = 20. if isinstance(row[field], float) else '2026-10-10T00:00:00+00:00'
        second['central_observation_revisions'][0]['revision_id'] = 'x'
        second['central_observation_revisions'][1].update(revision_id='y', revision_of='x')
        a, b = collect(first), collect(second)
        self.assertEqual(signatures(a), signatures(b))
        for name in TABLE_FIELDS:
            self.assertNotEqual(a['tables'][name]['generated_time_fields'],
                                b['tables'][name]['generated_time_fields'])
            self.assertTrue(b['tables'][name]['content_projection_valid'])
        self.assertFalse(b['timing_equivalence_verified'])
        self.assertFalse(b['functional_equivalence_verified'])

    def test_payload_keys_source_times_status_and_operation_hash_remain_comparable(self):
        cases = [
            ('central_dataset_snapshots', 'payload_json', {'code': '000660'}),
            ('central_documents', 'document_json', {'value': 2}),
            ('central_documents', 'document_key', '000660'),
            ('central_minute_bar_operations', 'operation_hash', 'changed'),
            ('central_observation_revisions', 'payload_json', {'close': 999}),
            ('central_observation_revisions', 'effective_at', '2026-10-08T00:02:00+00:00'),
            ('central_observation_revisions', 'available_at', '2026-10-08T00:03:00+00:00'),
            ('central_observation_revisions', 'completeness', 'partial'),
            ('central_observation_revisions', 'new_schema_field', 'preserved'),
        ]
        expected = signatures(collect(fixture()))
        for table, field, value in cases:
            with self.subTest(table=table, field=field):
                changed = fixture()
                changed[table][0][field] = value
                self.assertNotEqual(expected[table], signatures(collect(changed))[table])

    def test_missing_forward_and_wrong_scope_parents_are_explicitly_invalid(self):
        cases = [
            ('revision_parent_missing', lambda rows: rows[1].update(revision_of='absent')),
            ('revision_parent_not_earlier', lambda rows: rows[0].update(revision_of='b')),
            ('revision_parent_scope_mismatch', lambda rows: rows[1].update(source_id='ka10080')),
        ]
        for reason, change in cases:
            with self.subTest(reason=reason):
                rows = fixture()
                change(rows['central_observation_revisions'])
                table = collect(rows)['tables']['central_observation_revisions']
                self.assertFalse(table['content_projection_valid'])
                self.assertEqual(1, table['lineage_errors'][reason])

    def test_changed_lineage_and_sequence_do_not_match_even_if_payload_is_equal(self):
        expected = signatures(collect(fixture()))['central_observation_revisions']
        for field, value in [('revision_of', None), ('accepted_sequence', 3)]:
            changed = fixture()
            changed['central_observation_revisions'][1][field] = value
            self.assertNotEqual(expected, signatures(collect(changed))['central_observation_revisions'])

    def test_row_order_is_irrelevant_but_duplicate_multiplicity_is_preserved(self):
        a = fixture()
        b = deepcopy(a)
        b['central_observation_revisions'].reverse()
        self.assertEqual(signatures(collect(a)), signatures(collect(b)))
        b['central_documents'].append(deepcopy(b['central_documents'][0]))
        self.assertNotEqual(signatures(collect(a))['central_documents'],
                            signatures(collect(b))['central_documents'])

    def test_size_limit_charges_identity_pass_and_duplicate_revision_identity_fails(self):
        for limits in ({'max_bytes': 10}, {'max_rows': 2}):
            with self.subTest(limits=limits), self.assertRaisesRegex(RuntimeError, 'size_limit'):
                collect(fixture(), **limits)
        rows = fixture()
        rows['central_observation_revisions'].append(deepcopy(rows['central_observation_revisions'][0]))
        with self.assertRaisesRegex(RuntimeError, 'duplicate_revision_identity'):
            collect(rows)


if __name__ == '__main__':
    unittest.main()
