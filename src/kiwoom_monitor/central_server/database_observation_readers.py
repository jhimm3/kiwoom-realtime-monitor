"""Read-only observation revision queries for the store backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Any

from kiwoom_monitor.central_server.database_codec import (
    _observation_revision_columns,
    bounded_limit,
    observation_revision_result_rows,
)


OBSERVATION_DELIVERY_PROTOCOL = "observation_safe_prefix/v1"


@dataclass(frozen=True)
class ObservationRevisionPage:
    rows: tuple[dict[str, Any], ...] = ()
    ready: bool = True
    safe_through: int = 0
    exhausted: bool = True
    reason: str = ""


@dataclass(frozen=True)
class _SequenceFence:
    high: int
    owners: frozenset[tuple[int, str]]
    started: float


@dataclass
class ObservationDeliveryState:
    """Per-store read frontier; never a durable cursor or a writer lock."""
    lock: Any = field(default_factory=Lock)
    epoch: tuple[Any, ...] | None = None
    version: int = 0
    high_seen: int = 0
    safe: int = 0
    pending: _SequenceFence | None = None

    def snapshot_version(self) -> int:
        with self.lock:
            return self.version

    def advance(self, version: int, epoch: tuple[Any, ...], high: int,
                owners: frozenset[tuple[int, str]]) -> tuple[int | None, dict[str, object]]:
        with self.lock:
            # Another reader published after this probe began. Its fence wins;
            # this call retries on the existing poll rather than publishing stale I/O.
            if version != self.version:
                return None, {"reason": "concurrent_frontier_refresh"}
            if self.epoch != epoch:
                self.epoch = epoch
                self.high_seen = self.safe = 0
                self.pending = None
            if high < self.high_seen:
                raise RuntimeError("observation_sequence_regressed")
            self.high_seen = high
            pending = self.pending
            if pending is not None and not pending.owners.intersection(owners):
                self.safe = max(self.safe, pending.high)
                self.pending = None
            if self.pending is None and high > self.safe:
                if owners:
                    self.pending = _SequenceFence(high, owners, monotonic())
                else:
                    self.safe = high
            self.version += 1
            pending = self.pending
            return self.safe, {
                "safe_through": self.safe,
                "allocated_through": high,
                "captured_owners": len(pending.owners) if pending else 0,
                "remaining_owners": len(pending.owners.intersection(owners)) if pending else 0,
                "pending_age_ms": round((monotonic() - pending.started) * 1000, 3) if pending else 0,
            }


def _normalized_kinds(kinds: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in kinds if str(value)))


def _revision_page(rows, limit: int, safe: int) -> ObservationRevisionPage:
    return ObservationRevisionPage(tuple(observation_revision_result_rows(rows[:limit])),
                                   safe_through=safe, exhausted=len(rows) <= limit)


def _observation_revision_select(placeholder: str, *, postgres: bool = False) -> str:
    return (
        f"SELECT {_observation_revision_columns(postgres=postgres)} "
        f"FROM central_observation_revisions WHERE kind={placeholder}"
    )


class SQLiteObservationReaderStoreMixin:
    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = _observation_revision_select("?")
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return observation_revision_result_rows(rows)


    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return list(self.load_observation_revision_page(after_sequence, kinds, limit).rows)

    def load_observation_revision_page(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
        *, through_sequence: int | None = None,
    ) -> ObservationRevisionPage:
        normalized = _normalized_kinds(kinds)
        if not normalized:
            return ObservationRevisionPage()
        size = bounded_limit(limit, 5000)
        placeholders = ",".join("?" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns()} FROM central_observation_revisions "
            f"WHERE accepted_sequence>? AND accepted_sequence<=? AND kind IN ({placeholders}) "
            "ORDER BY accepted_sequence LIMIT ?"
        )
        with self._lock, self._connection() as connection:
            safe = int(connection.execute(
                "SELECT COALESCE(MAX(accepted_sequence),0) FROM central_observation_revisions"
            ).fetchone()[0])
            if through_sequence is not None:
                safe = min(safe, max(0, int(through_sequence)))
            parameters = [max(0, int(after_sequence)), safe, *normalized, size + 1]
            rows = connection.execute(sql, parameters).fetchall()
        return _revision_page(rows, size, safe)

    def load_observation_bootstrap(
        self, kinds: tuple[str, ...], per_kind_limit: int = 5000,
    ) -> ObservationRevisionPage:
        normalized = _normalized_kinds(kinds)
        if not normalized:
            return ObservationRevisionPage()
        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_observation_revision_columns()} FROM ("
                "SELECT *,ROW_NUMBER() OVER (PARTITION BY kind ORDER BY accepted_sequence DESC) AS seed_position "
                f"FROM central_observation_revisions WHERE kind IN ({placeholders})) AS seeds "
                "WHERE seed_position<=? ORDER BY accepted_sequence",
                [*normalized, bounded_limit(per_kind_limit, 5000)],
            ).fetchall()
        values = tuple(observation_revision_result_rows(rows))
        return ObservationRevisionPage(values, safe_through=max(
            (int(row['accepted_sequence']) for row in values), default=0))




class PostgresObservationReaderStoreMixin:
    def _observation_safe_frontier(self, cursor, connection) -> tuple[int | None, bool]:
        from psycopg import sql

        state = self._observation_delivery
        version = state.snapshot_version()
        started = monotonic()
        cursor.execute(
            "SELECT pg_postmaster_start_time(),d.oid,c.oid,c.relfilenode,c.relpersistence,"
            "n.nspname,c.relname,s.seqcache,s.seqincrement,s.seqcycle,"
            "current_setting('server_version_num')::integer,"
            "current_setting('max_prepared_transactions')::integer,"
            "current_setting('transaction_isolation'),pg_is_in_recovery() "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_sequence s ON s.seqrelid=c.oid "
            "JOIN pg_database d ON d.datname=current_database() "
            "WHERE c.oid=pg_get_serial_sequence('central_observation_revisions','accepted_sequence')::regclass"
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("observation_sequence_missing")
        boot, db_oid, seq_oid, filenode, persistence, schema, name, cache, increment, cycle, pg_version, prepared, isolation, standby = row
        if not (170000 <= pg_version < 180000 and persistence == 'p' and
                (cache, increment, cycle) == (1, 1, False) and prepared == 0 and
                isolation == 'read committed' and not standby):
            raise RuntimeError("observation_delivery_configuration_unsupported")
        epoch = (boot, db_oid, seq_oid, filenode)
        # Allocation ceiling MUST precede cohort sampling; each is a separate SQL.
        cursor.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(schema, name)))
        high, called = cursor.fetchone()
        high = int(high) if called else int(high) - 1
        cursor.execute(
            "SELECT pid,virtualtransaction,mode FROM pg_locks WHERE locktype='relation' "
            "AND database=%s AND relation=%s AND granted "
            "AND mode NOT IN ('AccessShareLock','RowShareLock')", (db_oid, seq_oid),
        )
        locks = cursor.fetchall()
        if any(pid is None or not vxid or mode != 'RowExclusiveLock' for pid, vxid, mode in locks):
            raise RuntimeError("observation_sequence_owner_unsupported")
        safe, diagnostic = state.advance(version, epoch, high,
                                         frozenset((int(pid), str(vxid)) for pid, vxid, _ in locks))
        duration = round((monotonic() - started) * 1000, 3)
        connection.record_phase_diagnostic('observation_delivery', {
            **diagnostic, "frontier_ms": duration, "duration_ms": duration,
            "rowcount": None, "samples": [], "sampling_status": "not_requested",
            "probe_errors": [], "exception_type": None, "samples_truncated": False,
            "probe_pending_at_capture": False,
        })
        return safe, bool(diagnostic.get('captured_owners'))

    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = _observation_revision_select("%s", postgres=True)
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        context = DBWriterContext(
            writer_family="read.observation_revisions",
            writer_kind="observation_revision",
            operation="load_observation_revisions",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return observation_revision_result_rows(rows)


    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return list(self.load_observation_revision_page(after_sequence, kinds, limit).rows)

    def load_observation_revision_page(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
        *, through_sequence: int | None = None,
    ) -> ObservationRevisionPage:
        normalized = _normalized_kinds(kinds)
        if not normalized:
            return ObservationRevisionPage()
        from .postgres_access import DBWriterContext, open_observed_connection

        after = max(0, int(after_sequence))
        size = bounded_limit(limit, 5000)
        placeholders = ",".join("%s" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns(postgres=True)} "
            "FROM central_observation_revisions WHERE accepted_sequence>%s "
            "AND accepted_sequence<=%s "
            f"AND kind IN ({placeholders}) ORDER BY accepted_sequence LIMIT %s"
        )
        context = DBWriterContext(
            writer_family="read.observation_revisions",
            writer_kind="observation_revisions_after",
            operation="load_observation_revisions_after",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            safe, pending = self._observation_safe_frontier(cursor, connection)
            if safe is None:
                return ObservationRevisionPage(ready=False, exhausted=False, reason='concurrent_frontier_refresh')
            if ((through_sequence is not None and safe < max(0, int(through_sequence))) or
                    after > safe or (after == safe and pending)):
                return ObservationRevisionPage(ready=False, safe_through=safe, exhausted=False,
                    reason='pending_sequence_commit' if pending else 'cursor_beyond_frontier')
            if through_sequence is not None:
                safe = min(safe, max(0, int(through_sequence)))
            parameters = [after, safe, *normalized, size + 1]
            # Fresh READ COMMITTED snapshot after observing the cohort's completion.
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return _revision_page(rows, size, safe)

    def load_observation_bootstrap(
        self, kinds: tuple[str, ...], per_kind_limit: int = 5000,
    ) -> ObservationRevisionPage:
        normalized = _normalized_kinds(kinds)
        if not normalized:
            return ObservationRevisionPage()
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(writer_family='read.observation_revisions',
            writer_kind='observation_bootstrap', operation='load_observation_bootstrap', access_mode='read')
        placeholders = ','.join('%s' for _ in normalized)
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            safe, pending = self._observation_safe_frontier(cursor, connection)
            if safe is None:
                return ObservationRevisionPage(ready=False, exhausted=False, reason='concurrent_frontier_refresh')
            # A previously safe prefix is a valid bootstrap even with later writes pending.
            if safe == 0 and pending:
                return ObservationRevisionPage(ready=False, exhausted=False, reason='pending_sequence_commit')
            cursor.execute(
                f"SELECT {_observation_revision_columns(postgres=True)} FROM ("
                "SELECT *,ROW_NUMBER() OVER (PARTITION BY kind ORDER BY accepted_sequence DESC) AS seed_position "
                "FROM central_observation_revisions WHERE accepted_sequence<=%s "
                f"AND kind IN ({placeholders})) AS seeds "
                "WHERE seed_position<=%s ORDER BY accepted_sequence",
                [safe, *normalized, bounded_limit(per_kind_limit, 5000)],
            )
            rows = cursor.fetchall()
        return ObservationRevisionPage(tuple(observation_revision_result_rows(rows)), safe_through=safe)
