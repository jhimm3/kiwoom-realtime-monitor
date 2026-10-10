"""Bounded activity samples on the already-owned offline replay connection.

The lease's management connection is exclusively borrowed during native execution;
the sampler is joined before status, digest, restore or lease release touches it.
Writer connections and transaction behavior are unchanged.
"""
from __future__ import annotations

from collections import Counter
from threading import Event, Thread
from pathlib import Path
import time


def replay_process_usage():
    values = dict(process_cpu_seconds=time.process_time(), rss_bytes=None,
                  process_lifetime_peak_rss_bytes=None)
    try:
        lines = Path('/proc/self/status').read_text().splitlines()
        for line in lines:
            for key, target in (('VmRSS:', 'rss_bytes'), ('VmHWM:', 'process_lifetime_peak_rss_bytes')):
                if line.startswith(key):
                    amount, unit = line.split()[1:]
                    if unit == 'kB':
                        values[target] = int(amount)*1024
    except (OSError, ValueError):
        pass
    return values


class ReplayActivitySampler:
    def __init__(self, connection, *, interval=.025, max_samples=4096):
        self.connection = connection
        self.interval, self.max_samples = interval, max_samples
        self.samples = []
        self.errors = []
        self.unidentifiable_rows = Counter()
        self.queries = 0
        self.dropped = 0
        self.rows_truncated = False
        self.started_at = self.finished_at = None
        self._stop, self._ready = Event(), Event()
        self._thread = None
        self._usage_before = self._usage_after = None

    def _read(self):
        started = time.time()
        # Local settings disappear on transaction exit. Never set cluster or
        # writer settings, and never return SQL text or values from pg_stat_activity.
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SET LOCAL transaction_read_only=on")
            cursor.execute("SET LOCAL statement_timeout='500ms'")
            cursor.execute(
                "SELECT pid,EXTRACT(EPOCH FROM backend_start),state,"
                "COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
                "pg_blocking_pids(pid),upper(btrim(query))='COMMIT' "
                "FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() ORDER BY pid LIMIT 65")
            rows = cursor.fetchall()
        finished = time.time()
        self.queries += 1
        self.rows_truncated |= len(rows) > 64
        for pid, backend_start, state, wait_type, wait_event, blockers, is_commit in rows[:64]:
            missing = [name for name, value in (
                ('pid', pid), ('backend_start', backend_start), ('blocking_pids', blockers)
            ) if value is None]
            if missing:
                self.unidentifiable_rows.update(missing)
                continue
            try:
                pid, backend_start = int(pid), float(backend_start)
                blockers = [int(value) for value in blockers]
            except (TypeError, ValueError, OverflowError):
                self.unidentifiable_rows.update(['invalid_backend_identity'])
                continue
            if len(self.samples) >= self.max_samples:
                self.dropped += 1
                continue
            self.samples.append(dict(pid=pid, backend_started_at=backend_start,
                state=state, wait_type=wait_type, wait_event=wait_event,
                blocking_pids=blockers,
                statement_type='COMMIT' if is_commit else 'other',
                started_at=started, finished_at=finished))

    def _run(self):
        try:
            while not self._stop.is_set():
                self._read()
                self._ready.set()
                if self._stop.wait(self.interval):
                    break
        except Exception as error:
            self.errors.append(type(error).__name__)  # Never expose URL/SQL/error text.
        finally:
            self._ready.set()

    def __enter__(self):
        self.started_at = time.time()
        self._usage_before = replay_process_usage()
        self._thread = Thread(target=self._run, name='replay-activity', daemon=False)
        try:
            self._thread.start()
        except Exception as error:
            self.errors.append(type(error).__name__)
            self._thread = None
        else:
            self._ready.wait(1)
        return self

    def __exit__(self, *unused):
        self._stop.set()
        interrupted = None
        if self._thread is not None:
            # Native diagnostic work retains its owner until completion too.
            # A timeout is not permission to restore while it still uses the lease.
            while self._thread.is_alive():
                try:
                    self._thread.join(.1)
                except BaseException as error:
                    if interrupted is None:
                        interrupted = error
        self.finished_at = time.time()
        self._usage_after = replay_process_usage()
        if interrupted is not None:
            raise interrupted

    def report(self):
        usage = dict(before=self._usage_before, after=self._usage_after,
            process_cpu_ms=(self._usage_after['process_cpu_seconds']-
                            self._usage_before['process_cpu_seconds'])*1000
                if self._usage_after is not None and self._usage_before is not None else None,
            scope='offline worker process; includes observer; RSS endpoints and lifetime peak, '
                  'not operating app or per-interval peak memory')
        return dict(state='incomplete' if self.errors or self.dropped or self.rows_truncated
                        or self.unidentifiable_rows
                    else 'complete', target_interval_ms=self.interval*1000,
                    started_at=self.started_at, finished_at=self.finished_at,
                    query_count=self.queries, samples=self.samples,
                    dropped_samples=self.dropped, rows_truncated=self.rows_truncated,
                    unidentifiable_row_fields=dict(self.unidentifiable_rows),
                    error_types=self.errors, observer_drained=self._thread is None
                        or not self._thread.is_alive(),
                    process_usage=usage,
                    scope='owned replay database; management connection; fixed read-only activity',
                    scope_note='point samples add observer work; no_sample does not exclude waits; '
                               'idle COMMIT may reflect client acknowledgement delay; '
                               'null wait event does not prove CPU execution')


def correlate_replay_commits(raw, activity):
    """Require the complete query interval inside the exact same-backend COMMIT."""
    details = []
    for call in raw.get('calls', []):
        start, end, pid = (call.get(key) for key in
                          ('commit_started_at', 'commit_finished_at', 'backend_pid'))
        if start is None or end is None or pid is None:
            continue
        matched = [row for row in activity['samples'] if row['pid'] == pid
                   # The measured call starts before connect(), so a valid
                   # freshly connected backend is newer than call.started_at.
                   # A backend created after COMMIT began cannot own this COMMIT.
                   and row['backend_started_at'] <= start
                   and start <= row['started_at'] <= row['finished_at'] <= end
                   and row['statement_type'] == 'COMMIT']
        details.append(dict(call_id=call['call_id'], backend_pid=pid,
            writer_family=call['writer_family'], writer_kind=call['writer_kind'],
            commit_ms=call.get('commit_ms'), sample_count=len(matched),
            wait_samples=dict(Counter(f"{r['wait_type'] or 'NONE'}:{r['wait_event'] or 'NONE'}"
                                      for r in matched)),
            backend_states=dict(Counter(r['state'] for r in matched)),
            blocking_pids=sorted({p for r in matched for p in r['blocking_pids']}),
            sampling_status='sampled' if matched else 'no_sample'))
    return dict(state=activity['state'] if not raw.get('dropped') and not raw.get('raw_truncated')
                and not raw.get('truncated') else 'incomplete', calls=details,
                target_interval_ms=activity['target_interval_ms'],
                scope_note='same backend and COMMIT interval; point counts are not wait duration '
                           'or device attribution; no_sample does not exclude waits')
