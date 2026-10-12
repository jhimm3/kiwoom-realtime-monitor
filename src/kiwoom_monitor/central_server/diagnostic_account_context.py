"""Bounded account prerequisites from a read-only source snapshot.

No vault, credential profile, original identity fingerprint or authority token
is serialized. This is a projected context, not a whole-DB source baseline.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime

VERSION = 'account-context/v4'
VERSIONS = ('account-context/v1', 'account-context/v2', 'account-context/v3', VERSION)
ARCHIVE_VERSIONS = frozenset({'account-context/v3', VERSION})
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 50_000
SOURCE_READ_SECONDS = 10
COLUMNS_V1 = {
    'central_account_registry': ('account_ref', 'broker', 'environment', 'identity_fingerprint', 'created_at', 'status'),
    'central_account_binding_revisions': ('binding_id', 'credential_profile_id', 'broker', 'environment',
        'account_ref', 'binding_revision', 'verified_at', 'verification_method'),
    'central_execution_runtime_leases': ('owner_key', 'owner_alias', 'lease_expires_at', 'updated_at'),
    'central_documents': ('collection', 'owner', 'document_key', 'updated_at', 'document_json'),
}
DOCUMENT_COLLECTIONS_V1 = ('server_account_settings', 'execution_mock_automation_control')
DOCUMENT_COLLECTIONS = (*DOCUMENT_COLLECTIONS_V1, 'real_account_recovery', 'real_account_event')
CLOCK_COLLECTION = 'execution_mock_automation_clock_current'
DOCUMENT_COLLECTIONS_V4 = (*DOCUMENT_COLLECTIONS_V1, CLOCK_COLLECTION)
COLUMNS = {**COLUMNS_V1,
    'central_execution_intents': ('intent_id', 'run_id', 'environment', 'account_ref', 'state',
        'broker_order_id', 'last_broker_as_of', 'created_at', 'updated_at', 'document_json'),
    'central_execution_events': ('accepted_sequence', 'event_id', 'intent_id', 'state',
        'occurred_at', 'received_at', 'broker_execution_id', 'document_json'),
    'central_execution_account_snapshots': ('snapshot_id', 'environment', 'account_ref',
        'as_of', 'received_at', 'document_json'),
}
EVENT_SEQUENCE = 'central_execution_events_accepted_sequence_seq'


def _columns(document):
    return COLUMNS_V1 if document['version'] == VERSIONS[0] else COLUMNS


def _collections(document):
    if document['version'] == VERSION:
        return DOCUMENT_COLLECTIONS_V4
    return DOCUMENT_COLLECTIONS if document['version'] == 'account-context/v2' else DOCUMENT_COLLECTIONS_V1


def _digest(value):
    digest, size = hashlib.sha256(), 0
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                 allow_nan=False).iterencode(value):
        for start in range(0, len(part), 16384):
            raw = part[start:start + 16384].encode('utf-8')
            size += len(raw)
            if size > MAX_BYTES:
                raise ValueError('account_context_byte_limit')
            digest.update(raw)
    return digest.hexdigest(), size


def _fingerprint(reference):
    return hashlib.sha256(('recorded-test-account:' + reference).encode('utf-8')).hexdigest()


def _value(value):
    return value.isoformat() if isinstance(value, datetime) else value


def prepare_archive_seed(document, owner_rows):
    if document['version'] not in ARCHIVE_VERSIONS:
        return []
    from .diagnostic_account_archive import prepare_seed
    from .diagnostic_account_input import METHODS
    if any(row.get('account_alias_domain') != document['alias_domain']
           for row in owner_rows if row.get('method') in METHODS):
        raise ValueError('account_context_archive_frontier_invalid')
    return prepare_seed(document['archive_index'], owner_rows, maximum_bytes=MAX_BYTES)


def sealed_context_contract(document, owner_rows, bindings, *, archive_seed=None):
    """Pure contract preparation; called before seal and independently before reset."""
    from .diagnostic_replay_baseline import _hash
    contract = {'version': document['version'], 'trace_id': document['trace_id'],
                'sha256': document['sha256'], 'source_state_equivalent': False,
                'owner_bindings_sha256': _hash({domain + ':' + alias: value.token
                    for (domain, alias), value in bindings.items()})}
    if document['version'] in ARCHIVE_VERSIONS:
        from .diagnostic_account_input import METHODS
        from .diagnostic_replay_database_cli import _input_hash
        seed = prepare_archive_seed(document, owner_rows) if archive_seed is None else archive_seed
        account_rows = [row for row in owner_rows if row.get('method') in METHODS]
        contract.update(archive_seed_sha256=_hash(seed), archive_frontier_sha256=_input_hash(account_rows),
                        archive_scope='selected_native_input_keys_with_initial_identity_index')
    return contract


def read_recorded_account_context(trace_id):
    """Scan checksummed chunk metadata; hydrate only the one context capsule."""
    from . import diagnostic_trace as trace
    from .diagnostic_replay_contract import ACCOUNT_CONTEXT_COPY_BYTES, ACCOUNT_CONTEXT_PROFILE, thaw_payload
    from .diagnostic_trace_payload import BlockPayloadReference
    manifest = trace.status(trace_id)
    trace._scoped_capture_manifest(manifest, trace_id)
    if manifest.get('account_context', {}).get('state') != 'captured':
        raise ValueError('recorded_account_context_missing')
    selected = None
    for row, _ in trace._top20_source_rows(trace_id, manifest):
        if row.get('event_type') == 'account_context':
            if (selected is not None or row.get('payload_profile') != ACCOUNT_CONTEXT_PROFILE
                    or row.get('account_context_version') not in VERSIONS or 'payload_ref' not in row):
                raise ValueError('recorded_account_context_invalid')
            selected = row
    if selected is None:
        raise ValueError('recorded_account_context_missing')
    cache = {}
    trace._load_window_payload(trace_id, selected['payload_ref'], manifest, cache, 0)
    value = cache[selected['payload_ref']]
    if type(value) is BlockPayloadReference:
        value = value.load()
    document = validate_account_context(thaw_payload(value, maximum_bytes=ACCOUNT_CONTEXT_COPY_BYTES))
    if (document['version'] != selected['account_context_version']
            or document['trace_id'] != trace_id or document['sha256'] != manifest['account_context'].get('sha256')
            or document['snapshot_finished_mono_ns'] > manifest['started_mono_ns']):
        raise ValueError('recorded_account_context_identity_mismatch')
    return document


def read_account_context(store, projection, *, source_release):
    """Use one MVCC snapshot before recorder admission; never hold trace's lock."""
    from .database import PostgresQueryStore, SQLiteQueryStore
    from .diagnostic_replay_contract import freeze_payload
    began_wall, began_mono = time.time(), time.monotonic_ns()
    deadline = time.monotonic() + SOURCE_READ_SECONDS
    tables, bindings, archive_index, count, charged = {name: [] for name in COLUMNS}, [], [], 0, 0

    def read(cursor, postgres):
        nonlocal count, charged
        for table, columns in COLUMNS.items():
            if time.monotonic() >= deadline:
                raise ValueError('account_context_snapshot_timeout')
            # Deliberately exclude the source fingerprint from SELECT itself.
            selected = tuple(name for name in columns if name != 'identity_fingerprint')
            selected = tuple('owner_token' if name == 'owner_alias' else name for name in selected)
            expressions = list(selected)
            if 'document_json' in columns:
                size = 'octet_length(document_json::text)' if postgres else 'length(CAST(document_json AS BLOB))'
                expressions[-1] = f'CASE WHEN {size}<=8388608 THEN document_json ELSE NULL END'
            query = 'SELECT ' + ','.join(expressions) + ' FROM ' + table
            if table == 'central_documents':
                query += ' WHERE collection IN (' + ','.join("'" + value + "'" for value in DOCUMENT_COLLECTIONS_V4) + ')'
            query += ' ORDER BY ' + (','.join(selected[:3]) if table == 'central_documents' else selected[0])
            query += ' LIMIT ' + str(MAX_ROWS - count + 1)
            cursor.execute(query)
            while rows := cursor.fetchmany(16):
                for row in rows:
                    if time.monotonic() >= deadline:
                        raise ValueError('account_context_snapshot_timeout')
                    count += 1
                    if count > MAX_ROWS:
                        raise ValueError('account_context_row_limit')
                    value = dict(zip(selected, map(_value, row)))
                    if table == 'central_account_registry':
                        value['identity_fingerprint'] = _fingerprint(value['account_ref'])
                    elif table == 'central_execution_runtime_leases':
                        alias, metadata = projection.project_initial_lease(value['owner_key'], value.pop('owner_token'))
                        value['owner_alias'] = alias
                        bindings.append(metadata)
                    if 'document_json' in columns:
                        if value['document_json'] is None:
                            raise ValueError('account_context_document_oversized_or_null')
                        if type(value['document_json']) is str:
                            value['document_json'] = json.loads(value['document_json'])
                    # Bound the retained projection as rows arrive. Secret guards
                    # apply also to nested settings/control documents.
                    charged += freeze_payload(value).charge + 1024
                    if charged > MAX_BYTES:
                        raise ValueError('account_context_byte_limit')
                    tables[table].append(value)
        from .diagnostic_account_archive import pack_index
        cursor.execute("SELECT collection,owner,document_key,updated_at FROM central_documents "
                       "WHERE collection IN ('real_account_recovery','real_account_event') "
                       'ORDER BY collection,owner,document_key LIMIT ' + str(MAX_ROWS - count + 1))

        def identities():
            while rows := cursor.fetchmany(16):
                for row in rows:
                    if time.monotonic() >= deadline:
                        raise ValueError('account_context_snapshot_timeout')
                    yield row

        index, index_count, index_charge = pack_index(identities(), maximum_rows=MAX_ROWS-count,
                                                    maximum_bytes=MAX_BYTES-charged)
        archive_index.extend(index)
        count += index_count
        charged += index_charge

    try:
        if isinstance(store, PostgresQueryStore):
            with store._connect() as connection:
                with connection.cursor() as settings:
                    settings.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    settings.execute("SET LOCAL statement_timeout='10000'")
                # An unnamed psycopg cursor can buffer the entire SELECT result
                # in libpq before fetchmany. Use a transaction-owned server cursor.
                with connection.cursor(name='recorded_account_context') as cursor:
                    read(cursor, True)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT seqincrement,seqcache,seqcycle FROM pg_sequence '
                                   "WHERE seqrelid='public.central_execution_events_accepted_sequence_seq'::regclass")
                    if tuple(cursor.fetchone()) != (1, 1, False):
                        raise ValueError('account_context_sequence_definition_unsupported')
                    cursor.execute('SELECT last_value,is_called FROM public.' + EVENT_SEQUENCE)
                    last, called = cursor.fetchone()
                    next_sequence = last + int(called)
            isolation = 'repeatable_read_read_only'
        elif isinstance(store, SQLiteQueryStore):
            with store._lock, store._connection() as connection:
                connection.execute('BEGIN')
                read(connection.cursor(), False)
                row = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='central_execution_events'").fetchone()
                next_sequence = 1 if row is None else row[0] + 1
            isolation = 'sqlite_read_transaction_fixture'
        else:
            raise ValueError('account_context_store_unsupported')
    except Exception as error:
        # Database exception text may contain connection credentials.
        if type(error) is ValueError and str(error).startswith('account_context_'):
            raise
        raise ValueError('account_context_read_failed') from None
    document = {'version': VERSION, 'trace_id': projection.trace_id, 'alias_domain': projection.trace_id,
        'source_release': source_release, 'snapshot_started_at': began_wall,
        'snapshot_finished_at': time.time(), 'snapshot_started_mono_ns': began_mono,
        'snapshot_finished_mono_ns': time.monotonic_ns(), 'isolation': isolation,
        'source_state_equivalent': False, 'row_count': count, 'tables': tables,
        'owner_bindings': bindings, 'execution_event_next_sequence': next_sequence,
        'archive_index': archive_index}
    digest, _ = _digest(document)
    document['sha256'] = digest
    freeze_payload(document, maximum_bytes=MAX_BYTES)
    return validate_account_context(document)


def validate_account_context(document):
    from .diagnostic_replay_contract import freeze_payload
    from .diagnostic_account_input import resolve_owner_bindings
    from .database_account_identity import _canonical_account_ref
    if type(document) is not dict or document.get('version') not in VERSIONS:
        raise ValueError('account_context_invalid')
    expected_fields = {
            'version', 'trace_id', 'alias_domain', 'source_release', 'snapshot_started_at',
            'snapshot_finished_at', 'snapshot_started_mono_ns', 'snapshot_finished_mono_ns',
            'isolation', 'source_state_equivalent', 'row_count', 'tables', 'owner_bindings', 'sha256'}
    if document['version'] != VERSIONS[0]:
        expected_fields.add('execution_event_next_sequence')
    if document['version'] in ARCHIVE_VERSIONS:
        expected_fields.add('archive_index')
    columns = _columns(document)
    if (set(document) != expected_fields or document['source_state_equivalent'] is not False
            or type(document['trace_id']) is not str or not 1 <= len(document['trace_id']) <= 128
            or document['alias_domain'] != document['trace_id']
            or type(document['source_release']) is not str or not 1 <= len(document['source_release']) <= 256
            or document['isolation'] not in {'repeatable_read_read_only', 'sqlite_read_transaction_fixture'}
            or type(document['tables']) is not dict or set(document['tables']) != set(columns)
            or type(document['owner_bindings']) is not list):
        raise ValueError('account_context_invalid')
    digest, _ = _digest({key: value for key, value in document.items() if key != 'sha256'})
    if digest != document['sha256']:
        raise ValueError('account_context_checksum_mismatch')
    count, charge = 0, 0
    for table, rows in document['tables'].items():
        if type(rows) is not list:
            raise ValueError('account_context_rows_invalid')
        keys = set()
        for row in rows:
            count += 1
            if count > MAX_ROWS or type(row) is not dict or set(row) != set(columns[table]):
                raise ValueError('account_context_rows_invalid')
            if table in {'central_account_registry', 'central_account_binding_revisions'}:
                if (row['broker'] != 'kiwoom' or row['environment'] not in {'real', 'mock'}
                        or _canonical_account_ref(row['account_ref']) != row['account_ref']):
                    raise ValueError('account_context_account_scope_invalid')
                if table == 'central_account_registry' and row['identity_fingerprint'] != _fingerprint(row['account_ref']):
                    raise ValueError('account_context_fingerprint_invalid')
            if table == 'central_documents':
                if row['collection'] not in _collections(document):
                    raise ValueError('account_context_document_scope_invalid')
                if row['collection'] == CLOCK_COLLECTION:
                    from .diagnostic_replay_contract import validate_account_clock_document
                    try:
                        validate_account_clock_document(row['owner'], row['document_key'], row['document_json'])
                    except ValueError:
                        raise ValueError('account_context_clock_invalid') from None
                key = row['collection'], row['owner'], row['document_key']
            else:
                key = row[columns[table][0]]
            if key in keys:
                raise ValueError('account_context_duplicate_key')
            keys.add(key)
            charge += freeze_payload(row).charge + 1024
            if charge > MAX_BYTES:
                raise ValueError('account_context_byte_limit')
    if document['version'] in ARCHIVE_VERSIONS:
        from .diagnostic_account_archive import iter_index
        if type(document['archive_index']) is not list:
            raise ValueError('account_context_archive_index_invalid')
        for group in document['archive_index']:
            charge += freeze_payload(group).charge + 1024
            if charge > MAX_BYTES:
                raise ValueError('account_context_byte_limit')
        for _ in iter_index(document['archive_index']):
            count += 1
            if count > MAX_ROWS:
                raise ValueError('account_context_row_limit')
    if type(document['row_count']) is not int or document['row_count'] != count:
        raise ValueError('account_context_rows_invalid')
    if document['version'] != VERSIONS[0]:
        intents = {row['intent_id']: row for row in document['tables']['central_execution_intents']}
        events = document['tables']['central_execution_events']
        event_ids = set()
        for row in events:
            if (type(row['accepted_sequence']) is not int or row['accepted_sequence'] < 1
                    or row['intent_id'] not in intents or row['event_id'] in event_ids):
                raise ValueError('account_context_ledger_lineage_invalid')
            event_ids.add(row['event_id'])
        for table in ('central_execution_intents', 'central_execution_account_snapshots'):
            for row in document['tables'][table]:
                # Native offline/import ledger callers retain opaque text refs.
                # They do not register identities or grant runtime ownership.
                reference = row['account_ref']
                valid_reference = (type(reference) is str and bool(reference)
                    if document['version'] in ARCHIVE_VERSIONS else _canonical_account_ref(reference) == reference)
                if (row['environment'] not in {'real', 'mock'}
                        or not valid_reference):
                    raise ValueError('account_context_account_scope_invalid')
        for table in ('central_execution_intents', 'central_execution_events', 'central_execution_account_snapshots'):
            for row in document['tables'][table]:
                if type(row['document_json']) is not dict:
                    raise ValueError('account_context_ledger_document_invalid')
                # Native readers consume JSON; ownership checks consume columns.
                # Reject conflicting representations before touching a test DB.
                for field, value in row['document_json'].items():
                    if field in row and field != 'document_json' and value != row[field]:
                        timestamps = {'last_broker_as_of', 'created_at', 'updated_at', 'occurred_at', 'received_at', 'as_of'}
                        try:
                            same_time = (field in timestamps and type(value) is str and type(row[field]) is str
                                and datetime.fromisoformat(value.replace('Z', '+00:00')) ==
                                    datetime.fromisoformat(row[field].replace('Z', '+00:00')))
                        except ValueError:
                            same_time = False
                        if not same_time:
                            raise ValueError('account_context_ledger_document_mismatch')
        next_sequence = document['execution_event_next_sequence']
        if (type(next_sequence) is not int or not 1 <= next_sequence <= 9223372036854775807
                or next_sequence <= max((row['accepted_sequence'] for row in events), default=0)):
            raise ValueError('account_context_sequence_invalid')
    resolved = resolve_owner_bindings(document['owner_bindings'])
    leases = document['tables']['central_execution_runtime_leases']
    if len(document['owner_bindings']) != len(leases) or len(resolved) > len(leases):
        raise ValueError('account_context_lease_binding_invalid')
    for row, owner in zip(leases, document['owner_bindings']):
        binding = owner.get('execution_owner_binding', {})
        if (owner.get('account_alias_domain') != document['alias_domain']
                or binding.get('owner_key') != row['owner_key'] or binding.get('owner_alias') != row['owner_alias']
                or binding.get('run_id') is not None or binding.get('prefix_matches') is not None):
            raise ValueError('account_context_lease_binding_invalid')
    for prefix in ('snapshot_started', 'snapshot_finished'):
        if (type(document[prefix + '_at']) not in (int, float)
                or not math.isfinite(document[prefix + '_at'])
                or type(document[prefix + '_mono_ns']) is not int):
            raise ValueError('account_context_time_invalid')
    if document['snapshot_finished_mono_ns'] < document['snapshot_started_mono_ns']:
        raise ValueError('account_context_time_invalid')
    if document['snapshot_finished_at'] < document['snapshot_started_at']:
        raise ValueError('account_context_time_invalid')
    return document


def _apply_account_context(cursor, document, *, bindings, archive_seed=()):
    """Only the verified v3 lease calls this inside its sealing transaction."""
    from psycopg import sql
    from .diagnostic_account_input import restore_account_arguments
    validate_account_context(document)
    columns_by_table = _columns(document)
    for table in reversed(columns_by_table):
        if table == 'central_documents':
            collections = DOCUMENT_COLLECTIONS_V1 if document['version'] == VERSIONS[0] else DOCUMENT_COLLECTIONS
            cursor.execute('DELETE FROM central_documents WHERE collection=ANY(%s)', (list(collections),))
        else:
            cursor.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier('public', table)))
    for table in columns_by_table:
        rows = document['tables'][table]
        if table == 'central_documents' and document['version'] in ARCHIVE_VERSIONS:
            rows = [*rows, *archive_seed]
        columns = tuple('owner_token' if name == 'owner_alias' else name for name in columns_by_table[table])
        for index, row in enumerate(rows):
            value = dict(row)
            if table == 'central_execution_runtime_leases':
                owner = document['owner_bindings'][index]
                restored = restore_account_arguments(owner,
                    {'owner_key': row['owner_key'], 'owner_alias': row['owner_alias']}, bindings=bindings)
                value['owner_token'] = restored['owner_token']
            if 'document_json' in columns:
                value['document_json'] = json.dumps(value['document_json'], ensure_ascii=False, allow_nan=False)
            cursor.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                sql.Identifier('public', table), sql.SQL(',').join(map(sql.Identifier, columns)),
                sql.SQL(',').join(sql.Placeholder() for _ in columns)), tuple(value[name] for name in columns))
    if document['version'] != VERSIONS[0]:
        # RESTART is transactional; setval would survive a failed seal.
        cursor.execute(sql.SQL('ALTER SEQUENCE {} RESTART WITH {}').format(
            sql.Identifier('public', EVENT_SEQUENCE), sql.Literal(document['execution_event_next_sequence'])))
