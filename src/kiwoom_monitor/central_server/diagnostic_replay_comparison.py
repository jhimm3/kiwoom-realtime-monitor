"""Bounded, post-drain content evidence on an already-owned replay DB cursor.

Exact baseline/reset digests remain authoritative. This projection compares stored
content and revision lineage, not wall-clock-dependent reader behavior. No native
writer, connection, clock, UUID generator or baseline data is changed here.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from psycopg import sql

TABLE_FIELDS = {
    'central_dataset_snapshots': ('saved_at',),
    'central_documents': ('updated_at',),
    'central_minute_bar_operations': ('processed_at',),
    'central_observation_revisions': ('received_at',),
}
_REVISION_TABLE = 'central_observation_revisions'
_REVISION_KEY = ('kind', 'subject', 'observation_key', 'source_id')


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _multiset_digest(values):
    # Fixed-width row hashes preserve duplicate multiplicity with bounded memory.
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value)
    return digest.hexdigest()


def _project_row(table, row, revisions):
    projected = dict(row)
    omitted = {field: projected.pop(field) for field in TABLE_FIELDS[table]}
    errors = []
    if table == _REVISION_TABLE:
        identifier = projected.pop('revision_id')
        sequence = projected['accepted_sequence']
        key = tuple(projected[field] for field in _REVISION_KEY)
        if revisions.get(identifier) != (sequence, key):
            errors.append('revision_identity_mismatch')
        prior = projected.pop('revision_of')
        predecessor = revisions.get(prior) if prior is not None else None
        if prior is not None and predecessor is None:
            errors.append('revision_parent_missing')
        elif predecessor is not None:
            if predecessor[0] >= sequence:
                errors.append('revision_parent_not_earlier')
            if predecessor[1] != key:
                errors.append('revision_parent_scope_mismatch')
        # An unresolved parent stays visible; never turn a broken edge into NULL.
        projected['revision_identity_sequence'] = sequence
        projected['revision_parent_sequence'] = (
            predecessor[0] if predecessor is not None else
            {'unresolved_revision_id': prior} if prior is not None else None
        )
    return projected, omitted, errors


def collect_final_content_comparison(cursor, *, max_bytes, max_rows):
    """Read only four explicit tables, outside the workload measurement interval.

    The identity pass is also charged to the limits. Keep scalar reference keys
    and SHA-256 values, never all payload rows. Caller owns drain/restore/fencing.
    """
    total_bytes = total_rows = 0

    def read_rows():
        nonlocal total_bytes, total_rows
        while batch := cursor.fetchmany(256):
            for (encoded,) in batch:
                total_bytes += len(encoded.encode('utf-8'))
                total_rows += 1
                if total_bytes > max_bytes or total_rows > max_rows:
                    raise RuntimeError('replay_content_comparison_size_limit')
                yield json.loads(encoded)

    # Resolve IDs within the same immutable final state, not from another run.
    cursor.execute(
        'SELECT jsonb_build_object(\'revision_id\',revision_id, '
        '\'accepted_sequence\',accepted_sequence, \'kind\',kind, '
        '\'subject\',subject, \'observation_key\',observation_key, '
        '\'source_id\',source_id)::text FROM public.central_observation_revisions'
    )
    revisions = {}
    sequences = set()
    for row in read_rows():
        identifier, sequence = row['revision_id'], row['accepted_sequence']
        if identifier in revisions or sequence in sequences:
            raise RuntimeError('replay_content_comparison_duplicate_revision_identity')
        revisions[identifier] = (sequence, tuple(row[field] for field in _REVISION_KEY))
        sequences.add(sequence)

    tables = {}
    for table, fields in TABLE_FIELDS.items():
        cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t').format(
            sql.Identifier('public', table)))
        row_hashes, errors = [], Counter()
        field_hashes = {field: [] for field in fields}
        nulls = Counter()
        for row in read_rows():
            projected, omitted, row_errors = _project_row(table, row, revisions)
            errors.update(row_errors)
            row_hashes.append(hashlib.sha256(_encoded(projected)).digest())
            for field, value in omitted.items():
                field_hashes[field].append(hashlib.sha256(_encoded(value)).digest())
                nulls[field] += value is None
        tables[table] = {
            'rows': len(row_hashes), 'sha256': _multiset_digest(row_hashes),
            'content_projection_valid': not errors,
            'lineage_errors': dict(sorted(errors.items())),
            'generated_time_fields': {
                field: {'rows': len(field_hashes[field]), 'null_rows': nulls[field],
                        'sha256': _multiset_digest(field_hashes[field])}
                for field in fields
            },
        }
    return {
        'schema_version': 1, 'scope': 'stored_content_and_revision_lineage_only',
        'timing_equivalence_verified': False, 'functional_equivalence_verified': False,
        'exact_final_tables_retained': True, 'measurement_interval_excluded': True,
        'revision_identity_policy': 'accepted_sequence_with_validated_parent_scope',
        'read_rows': total_rows, 'read_bytes': total_bytes, 'tables': tables,
    }
