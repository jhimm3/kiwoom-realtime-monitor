"""Exclusive ownership and immutable logical baselines for recorded experiments.

This resource belongs only to kiwoom_monitor_replay_test. It is provisioned
offline, separately from the application's schema and existing integration DB.
Native replay methods retain their own per-call connections and transactions.
"""
from __future__ import annotations

import hashlib
import json
import re
from threading import Condition, Lock, RLock
from urllib.parse import parse_qsl, unquote, urlsplit

import psycopg
from psycopg import sql

from .central_schema import central_schema_migrations
from .database import PostgresQueryStore
from .schema_migrations import CentralSchemaMigrationRunner

DATABASE_NAME = 'kiwoom_monitor_replay_test'
ROLE_NAME = 'kiwoom_monitor_replay'
BASELINE_VERSION = 1
_ADVISORY_KEY = 0x4B5752504C415931
_PROCESS_RUN_LOCK = Lock()
MAX_BASELINE_BYTES = 64 * 1024 * 1024
MAX_BASELINE_ROWS = 250_000
# Exact reset scope for the current natural-key operation allowlist. No CASCADE,
# news, execution, credentials, account bindings or schema migrations are reset.
TABLES = (
    'central_realtime_latest', 'central_minute_bars', 'central_second_trade_bars',
    'central_daily_bars', 'central_five_minute_bars', 'central_dataset_snapshots',
    'central_market_data_observation_meta', 'central_observation_revisions',
    'central_minute_bar_operations', 'central_documents', 'central_external_bars',
    'central_shadow_monitor_state', 'central_shadow_checkpoint_state',
    'central_shadow_checkpoint_frames',
)
SEQUENCES = ('central_observation_revisions_accepted_sequence_seq',)
DEFAULT_CONFIG = {'observation_history_enabled': True, 'shadow_checkpoint_frames_enabled': False}


def validate_replay_url(database_url: str) -> str:
    parts = urlsplit(database_url)
    if (parts.scheme not in {'postgres', 'postgresql'} or not parts.netloc
            or unquote(parts.path) != '/' + DATABASE_NAME or parts.fragment
            or unquote(parts.username or '') != ROLE_NAME
            or any(key.lower() in {'dbname', 'database', 'service', 'options', 'user'}
                   for key, _ in parse_qsl(parts.query))):
        raise ValueError('replay_requires_dedicated_database_and_role_url')
    return database_url


def _config(value):
    result = dict(DEFAULT_CONFIG if value is None else value)
    if set(result) != set(DEFAULT_CONFIG) or any(type(item) is not bool for item in result.values()):
        raise ValueError('replay_baseline_config_invalid')
    return result


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _maintenance_connection(database_url):
    validate_replay_url(database_url)
    return psycopg.connect(database_url, autocommit=True, connect_timeout=5,
                           options='-c search_path=public -c timezone=UTC '
                                   '-c lock_timeout=5000 -c statement_timeout=60000')


def _identity(cursor):
    cursor.execute('SELECT current_database(), current_user, r.rolsuper, r.rolcreatedb, '
                   'r.rolcreaterole, r.rolreplication, r.rolbypassrls, '
                   'pg_get_userbyid(d.datdba) FROM pg_roles r CROSS JOIN pg_database d '
                   'WHERE r.rolname=current_user AND d.datname=current_database()')
    row = cursor.fetchone()
    if (not row or row[:2] != (DATABASE_NAME, ROLE_NAME)
            or any(row[2:7]) or row[7] != ROLE_NAME):
        raise RuntimeError('replay_database_or_role_identity_mismatch')


def _marker(cursor, owner_token):
    cursor.execute('SELECT owner_token, version FROM replay_meta.ownership WHERE singleton')
    if cursor.fetchone() != (owner_token, BASELINE_VERSION):
        raise RuntimeError('replay_database_ownership_mismatch')


class _OwnedConnection(psycopg.Connection):
    """Native psycopg connection with a close receipt for the lease owner."""
    def __exit__(self, exc_type, exc, traceback):
        # ObservedDBConnection invokes this native method on its proxy, so use
        # the explicit base implementation rather than zero-argument super().
        # Psycopg can skip close when COMMIT/ROLLBACK raises. This unpooled replay
        # connection belongs to the completed call and must release its receipt
        # even after an acknowledgement loss, before the lease can restore.
        try:
            return psycopg.Connection.__exit__(self, exc_type, exc, traceback)
        except BaseException:
            self.close()
            raise

    def close(self):
        super().close()
        release = getattr(self, '_replay_release', None)
        if release is not None:
            self._replay_release = None
            release()


class _ReplayStore(PostgresQueryStore):
    def __init__(self, lease):
        super().__init__(lease.database_url, **lease.config)
        self._lease = lease
        self._generation = lease._generation

    def _connect(self):
        return self._lease.connect_store(self._generation)

    def initialize(self):
        raise RuntimeError('replay_schema_changes_require_offline_provisioning')


class ReplayDatabaseLease:
    """Hold process/DB locks through restore, native execution, and drain.

    Releasing the lease fences new connections and waits for every connection
    already opening/open to close. It never treats cancellation as DB rollback.
    """
    def __init__(self, database_url, owner_token, *, config=None):
        self.database_url = validate_replay_url(database_url)
        if type(owner_token) is not str or not re.fullmatch('[a-f0-9]{32}', owner_token):
            raise ValueError('replay_owner_token_invalid')
        self.owner_token, self._config = owner_token, _config(config)
        self.connection = None
        self._condition = Condition()
        self._management_lock = RLock()
        self._generation = 0
        self._open_count = 0
        self._active = False
        self._run_ready = False
        self._baseline_id = None
        self._process_locked = False

    @property
    def config(self):
        return dict(self._config)

    def __enter__(self):
        with self._management_lock:
            return self._enter(provision=False)

    def _enter(self, *, provision):
        if self.connection is not None or self._active:
            raise RuntimeError('replay_lease_already_entered')
        if not _PROCESS_RUN_LOCK.acquire(blocking=False):
            raise RuntimeError('replay_process_run_busy')
        self._process_locked = True
        try:
            self.connection = _maintenance_connection(self.database_url)
            with self.connection.cursor() as cursor:
                _identity(cursor)
                cursor.execute('SELECT pg_try_advisory_lock(%s)', (_ADVISORY_KEY,))
                if not cursor.fetchone()[0]:
                    raise RuntimeError('replay_database_run_busy')
                if not provision:
                    _marker(cursor, self.owner_token)
                self._no_external_sessions(cursor)
            self._active = True
            self._generation += 1
            return self
        except BaseException:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            _PROCESS_RUN_LOCK.release()
            self._process_locked = False
            raise

    def __exit__(self, exc_type, exc, traceback):
        with self._management_lock:
            self._retire()

    def _retire(self):
        with self._condition:
            self._active = False
            self._run_ready = False
            while self._open_count:
                self._condition.wait()
        try:
            if self.connection is not None:
                self.connection.close()  # Also releases the session advisory lock.
        finally:
            self.connection = None
            if self._process_locked:
                self._process_locked = False
                _PROCESS_RUN_LOCK.release()

    def _no_external_sessions(self, cursor):
        cursor.execute('SELECT pid FROM pg_stat_activity WHERE datname=current_database() '
                       'AND pid<>pg_backend_pid() '
                       'AND backend_type IS DISTINCT FROM \'autovacuum worker\'')
        if cursor.fetchone() is not None:
            raise RuntimeError('replay_external_database_session_present')

    def _maintenance(self, cursor):
        # Fence before checking: no owned connect may race with restore/seal.
        with self._condition:
            if not self._active or self._open_count:
                raise RuntimeError('replay_owned_connections_not_drained')
            self._run_ready = False
        _identity(cursor)
        _marker(cursor, self.owner_token)
        self._no_external_sessions(cursor)

    def _lock_tables(self, cursor):
        cursor.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
            sql.SQL(',').join(sql.Identifier('public', name) for name in TABLES)))
        cursor.execute('LOCK TABLE replay_meta.ownership IN ACCESS EXCLUSIVE MODE')
        self._no_external_sessions(cursor)

    def _schema(self, cursor):
        cursor.execute('SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,'
                       'column_default FROM information_schema.columns WHERE table_schema=\'public\' '
                       'AND table_name=ANY(%s) ORDER BY table_name,ordinal_position', (list(TABLES),))
        columns = cursor.fetchall()
        if {row[0] for row in columns} != set(TABLES):
            raise RuntimeError('replay_baseline_required_tables_missing')
        cursor.execute('SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=\'public\' '
                       'AND tablename=ANY(%s) ORDER BY tablename,indexname', (list(TABLES),))
        indexes = cursor.fetchall()
        cursor.execute('SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k '
                       'JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace '
                       'WHERE n.nspname=\'public\' AND c.relname=ANY(%s) '
                       'ORDER BY c.relname,k.conname', (list(TABLES),))
        constraints = cursor.fetchall()
        cursor.execute('SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.reloptions,'
                       'pg_get_userbyid(c.relowner) '
                       'FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
                       'WHERE n.nspname=\'public\' AND c.relname=ANY(%s) ORDER BY c.relname',
                       (list(TABLES),))
        table_options = cursor.fetchall()
        if any(row[1] or row[2] for row in table_options):
            raise RuntimeError('replay_baseline_row_security_unsupported')
        if any(row[4] != ROLE_NAME for row in table_options):
            raise RuntimeError('replay_baseline_table_owner_mismatch')
        cursor.execute('SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid '
                       'JOIN pg_namespace n ON n.oid=c.relnamespace '
                       'WHERE n.nspname=\'public\' AND c.relname=ANY(%s) AND NOT t.tgisinternal LIMIT 1',
                       (list(TABLES),))
        if cursor.fetchone() is not None:
            raise RuntimeError('replay_baseline_custom_trigger_unsupported')
        cursor.execute('SELECT version,name FROM public.central_schema_migrations ORDER BY version')
        migrations = cursor.fetchall()
        return _hash({'columns': columns, 'indexes': indexes, 'constraints': constraints,
                      'table_options': table_options, 'migrations': migrations})

    def _sequences(self, cursor):
        result = {}
        for name in SEQUENCES:
            cursor.execute('SELECT seqincrement,seqmin,seqmax,seqstart,seqcache,seqcycle '
                           'FROM pg_sequence WHERE seqrelid=to_regclass(%s)', ('public.' + name,))
            definition = cursor.fetchone()
            if not definition or definition[0] != 1 or definition[4] != 1 or definition[5]:
                raise RuntimeError('replay_baseline_sequence_definition_unsupported')
            cursor.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier('public', name)))
            last, called = cursor.fetchone()
            result[name] = {'definition': list(definition), 'next_value': last + int(called)}
        return result

    def _tables_digest(self, cursor, schema):
        result, total_bytes, total_rows = {}, 0, 0
        for name in TABLES:
            cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t '
                                   'ORDER BY to_jsonb(t)::text COLLATE "C"').format(sql.Identifier(schema, name)))
            digest, count = hashlib.sha256(), 0
            while rows := cursor.fetchmany(256):
                for (encoded,) in rows:
                    raw = encoded.encode('utf-8')
                    total_bytes += len(raw)
                    total_rows += 1
                    if total_bytes > MAX_BASELINE_BYTES or total_rows > MAX_BASELINE_ROWS:
                        raise RuntimeError('replay_baseline_size_limit')
                    digest.update(len(raw).to_bytes(8, 'big'))
                    digest.update(raw)
                    count += 1
            result[name] = {'rows': count, 'sha256': digest.hexdigest()}
        return result

    def _baseline(self, cursor):
        cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
        row = cursor.fetchone()
        if not row or row[0] != _hash(row[1]) or row[1].get('version') != BASELINE_VERSION:
            raise RuntimeError('replay_baseline_missing_or_invalid')
        if row[1].get('config') != self.config:
            raise RuntimeError('replay_baseline_config_mismatch')
        return row

    def seal(self):
        """Seal existing test fixture once. No production snapshot is taken."""
        with self._management_lock:
            if not self._active:
                raise RuntimeError('replay_lease_not_ready_or_retired')
            return self._seal()

    def _seal(self):
        with self.connection.transaction(), self.connection.cursor() as cursor:
            self._maintenance(cursor)
            self._lock_tables(cursor)
            cursor.execute('SELECT baseline_id FROM replay_meta.baseline WHERE singleton')
            if cursor.fetchone() is not None:
                raise RuntimeError('replay_baseline_already_sealed')
            schema_hash = self._schema(cursor)
            for name in TABLES:
                cursor.execute(sql.SQL('CREATE TABLE {} AS TABLE {}').format(
                    sql.Identifier('replay_baseline', name), sql.Identifier('public', name)))
            manifest = {'version': BASELINE_VERSION, 'origin': 'controlled_fixture',
                        'source_state_equivalent': False, 'config': self.config,
                        'schema_sha256': schema_hash, 'tables': self._tables_digest(cursor, 'replay_baseline'),
                        'sequences': self._sequences(cursor)}
            baseline_id = _hash(manifest)
            cursor.execute('INSERT INTO replay_meta.baseline(singleton,baseline_id,manifest) '
                           'VALUES(true,%s,%s::jsonb)', (baseline_id, json.dumps(manifest)))
        return {'baseline_id': baseline_id, 'manifest': manifest}

    def restore(self, expected_baseline_id):
        """Atomic, exact-table restore. Sequence RESTART is transactional too."""
        with self._management_lock:
            if not self._active:
                raise RuntimeError('replay_lease_not_ready_or_retired')
            return self._restore(expected_baseline_id)

    def _restore(self, expected_baseline_id):
        with self.connection.transaction(), self.connection.cursor() as cursor:
            self._maintenance(cursor)
            baseline_id, manifest = self._baseline(cursor)
            if baseline_id != expected_baseline_id:
                raise RuntimeError('replay_expected_baseline_mismatch')
            self._lock_tables(cursor)
            if self._schema(cursor) != manifest['schema_sha256']:
                raise RuntimeError('replay_baseline_schema_mismatch')
            if self._tables_digest(cursor, 'replay_baseline') != manifest['tables']:
                raise RuntimeError('replay_baseline_snapshot_changed')
            sequence_before = self._sequences(cursor)
            if any(sequence_before[name]['definition'] != manifest['sequences'][name]['definition']
                   for name in SEQUENCES):
                raise RuntimeError('replay_baseline_sequence_definition_changed')
            cursor.execute(sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(
                sql.Identifier('public', name) for name in TABLES)))
            for name in TABLES:
                cursor.execute(sql.SQL('INSERT INTO {} SELECT * FROM {}').format(
                    sql.Identifier('public', name), sql.Identifier('replay_baseline', name)))
            for name in SEQUENCES:
                # Preserve the next observable value, with RESTART's rollback and
                # locking semantics. Do not use nontransactional setval().
                cursor.execute(sql.SQL('ALTER SEQUENCE {} RESTART WITH {}').format(
                    sql.Identifier('public', name), sql.Literal(manifest['sequences'][name]['next_value'])))
            if (self._tables_digest(cursor, 'public') != manifest['tables']
                    or self._sequences(cursor) != manifest['sequences']):
                raise RuntimeError('replay_restored_baseline_verification_failed')
            self._no_external_sessions(cursor)
        with self._condition:
            self._baseline_id, self._run_ready = baseline_id, True
            self._generation += 1
        return {'baseline_id': baseline_id, 'baseline_managed': True,
                'source_state_equivalent': False, 'sequence_semantics': 'next_value',
                'table_counts': {name: value['rows'] for name, value in manifest['tables'].items()}}

    def status(self):
        with self._management_lock:
            if not self._active:
                raise RuntimeError('replay_lease_not_ready_or_retired')
            return self._status()

    def _status(self):
        with self.connection.cursor() as cursor:
            _identity(cursor)
            _marker(cursor, self.owner_token)
            cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
            row = cursor.fetchone()
        with self._condition:
            count = self._open_count
        return {'database': DATABASE_NAME, 'ownership_verified': True,
                'baseline_id': row[0] if row else None, 'baseline_sealed': bool(row),
                'owned_connections': count, 'run_ready': self._run_ready}

    def store(self):
        with self._condition:
            if not self._active or not self._run_ready:
                raise RuntimeError('replay_baseline_restore_required')
            return _ReplayStore(self)

    def connect_store(self, generation):
        with self._condition:
            if (not self._active or not self._run_ready
                    or generation != self._generation):
                raise RuntimeError('replay_lease_not_ready_or_retired')
            self._open_count += 1
        connection = None
        released = False

        def release():
            nonlocal released
            with self._condition:
                if not released:
                    released = True
                    self._open_count -= 1
                    self._condition.notify_all()
        try:
            connection = _OwnedConnection.connect(self.database_url, autocommit=True, connect_timeout=5,
                options='-c search_path=public -c timezone=UTC -c statement_timeout=60000')
            connection._replay_release = release
            # The lease already verified role privileges and the ownership
            # marker. libpq identity adds no per-call validation SQL/COMMIT.
            if (connection.info.dbname, connection.info.user) != (DATABASE_NAME, ROLE_NAME):
                raise RuntimeError('replay_database_or_role_identity_mismatch')
            connection.autocommit = False
            return connection
        except BaseException:
            if connection is not None:
                connection.close()
            else:
                release()
            raise


def provision_existing_empty_database(database_url, owner_token):
    """Offline only: the operator first creates the dedicated role and empty DB."""
    lease = ReplayDatabaseLease(database_url, owner_token)
    lease._enter(provision=True)
    try:
        with lease.connection.transaction(), lease.connection.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
                           'WHERE n.nspname NOT IN (\'pg_catalog\',\'information_schema\') '
                           'AND n.nspname NOT LIKE \'pg_toast%\' AND c.relkind IN (\'r\',\'p\',\'v\',\'m\',\'S\') LIMIT 1')
            if cursor.fetchone() is not None:
                raise RuntimeError('replay_provision_requires_empty_database')
            cursor.execute('CREATE SCHEMA replay_meta')
            cursor.execute('CREATE SCHEMA replay_baseline')
            cursor.execute('CREATE TABLE replay_meta.ownership(singleton BOOLEAN PRIMARY KEY CHECK(singleton), '
                           'owner_token TEXT NOT NULL,version INTEGER NOT NULL)')
            cursor.execute('CREATE TABLE replay_meta.baseline(singleton BOOLEAN PRIMARY KEY CHECK(singleton), '
                           'baseline_id TEXT NOT NULL,manifest JSONB NOT NULL)')
            CentralSchemaMigrationRunner(cursor, 'postgres').apply(central_schema_migrations())
            cursor.execute('INSERT INTO replay_meta.ownership VALUES(true,%s,%s)', (owner_token, BASELINE_VERSION))
        return lease.status()
    finally:
        lease.__exit__(None, None, None)
