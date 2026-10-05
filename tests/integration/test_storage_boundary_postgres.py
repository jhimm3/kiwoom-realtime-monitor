"""Opt-in PostgreSQL storage verification against a dedicated disposable DB.

Set KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL to a database named exactly
kiwoom_monitor_diagnostic_test. This file refuses any operational DB name.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from kiwoom_monitor.central_server.database import (
    PostgresQueryStore, _insert_postgres_observation_revisions_batch,
    _execute_multirow_upsert,
)
from kiwoom_monitor.central_server.diagnostic_metrics import refresh_capture_state
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key, daily_bar_observation, minute_bar_observation,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataValueKind, MarketDatasetKind, ObservationOrigin,
)
from kiwoom_monitor.domain.research_contract import ObservationRevisionSource


TEST_DATABASE_NAME = "kiwoom_monitor_diagnostic_test"


@contextmanager
def _writer_metrics_capture():
    """Enable a private, short-lived capture so writer assertions are observable."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "controls.json"
        expires_at = time.time() + 120
        session_id = uuid.uuid4().hex
        path.write_text(json.dumps({
            "schema": 1,
            "instance_id": instance_id(),
            "diagnostic_tool": {"expires_at": expires_at, "owner": "test",
                                 "session_id": session_id},
            "capture": {"expires_at": expires_at, "owner": "test"},
        }), encoding="utf-8")
        environment = patch.dict(os.environ, {
            "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path),
        })
        environment.start()
        refresh_capture_state(force=True)
        try:
            yield
        finally:
            try:
                path.write_text(json.dumps({
                    "schema": 1, "instance_id": instance_id(),
                    "diagnostic_tool": {"expires_at": time.time() + 120,
                                         "session_id": session_id},
                }), encoding="utf-8")
                refresh_capture_state(force=True)
            finally:
                environment.stop()


class PostgresStorageBoundaryTests(unittest.TestCase):
    def test_live_minute_read_uses_one_snapshot_across_concurrent_commit(self) -> None:
        from threading import Event
        from kiwoom_monitor.central_server import database_market_bars
        code = str(900000 + uuid.uuid4().int % 99999)
        operation_id = 'diagnostic-live-' + uuid.uuid4().hex
        delta = {
            'trading_date': '2099-01-10', 'minute': '10:04', 'code': code, 'market': 'KRX',
            'open': 100, 'high': 110, 'low': 100, 'close': 110, 'volume': 13,
            'trade_value_million_won': 7, 'updated_at': 1_790_000_000.0,
            'operation_id': operation_id,
        }
        selected, committed = Event(), Event()
        original = database_market_bars._read_realtime_minute_overlay

        def overlay(*args, **kwargs):
            # Canonical rows have already been read. Commit on a peer connection
            # before the operation-marker lookup to expose a mixed-snapshot bug.
            selected.set()
            if not committed.wait(10):
                raise TimeoutError('concurrent writer did not commit')
            return original(*args, **kwargs)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with patch.object(database_market_bars, '_read_realtime_minute_overlay', side_effect=overlay):
                    future = executor.submit(self.store.load_minute_bars, code, delta['trading_date'],
                                             'KRX', realtime_deltas=[delta])
                    try:
                        self.assertTrue(selected.wait(10))
                        self.store.save_minute_bars([delta])
                    finally:
                        committed.set()
                    [during] = future.result(timeout=15)
            [after] = self.store.load_minute_bars(code, delta['trading_date'], 'KRX', realtime_deltas=[delta])
            self.assertEqual(13, during['volume'])
            self.assertEqual(during, after)
            self.assertEqual(after, self.store.load_minute_bars(code, delta['trading_date'], 'KRX')[0])
        finally:
            committed.set()
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute('DELETE FROM central_minute_bar_operations WHERE operation_id=%s', (operation_id,))
                cursor.execute('DELETE FROM central_minute_bars WHERE code=%s AND trading_date=%s',
                               (code, delta['trading_date']))

    @classmethod
    def setUpClass(cls) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated PostgreSQL diagnostic test database not configured")
        if urlsplit(url).path.lstrip("/") != TEST_DATABASE_NAME:
            raise RuntimeError("refusing storage integration test outside dedicated diagnostic database")
        cls.store = PostgresQueryStore(url)
        with cls.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != TEST_DATABASE_NAME:
                raise RuntimeError("connected PostgreSQL database is not the dedicated diagnostic test database")
        cls.store.initialize()

    def test_realtime_minute_batch_prefetches_guards_and_preserves_replay(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_metrics import summarize_market_bar_saves

        token = uuid.uuid4().hex
        code = f"D{token[:9]}"
        operation_ids = [f"diagnostic-{token}-{index}" for index in range(2)]
        values = [{
            "trading_date": "2099-01-09", "minute": f"10:0{index}", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050 + index, "volume": 7 + index,
            "trade_value_million_won": 1, "updated_at": 1_790_000_000.0 + index,
            "operation_id": operation_ids[index],
        } for index in range(2)]
        observations = []
        for value in values:
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            observations.append((bar_observation_key(observation), observation))

        def save_and_counts():
            started = time.time() - 0.01
            self.store.save_minute_bars(values, observations=observations)
            summary = summarize_market_bar_saves(started, time.time() + 1)
            samples = summary["writer_transactions"]["realtime_minute"]["call_samples"]
            return samples[-1]["domain_counts"]

        with _writer_metrics_capture():
            try:
                first_counts = save_and_counts()
                self.assertEqual(1, first_counts["operation_lookup_statements"])
                self.assertEqual(2, first_counts["operation_lookup_keys"])
                self.assertEqual(1, first_counts["authority_lookup_statements"])
                self.assertEqual(2, first_counts["authority_lookup_keys"])
                self.assertEqual(2, first_counts["operation_inserts"])

                replay_counts = save_and_counts()
                self.assertEqual(1, replay_counts["operation_lookup_statements"])
                self.assertEqual(2, replay_counts["operation_lookup_keys"])
                self.assertEqual(0, replay_counts["authority_lookup_statements"])
                self.assertEqual(2, replay_counts["replayed"])
                rows = self.store.load_minute_bars(code, "2099-01-09", "KRX")
                self.assertEqual([7, 8], [row["volume"] for row in rows])
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM central_minute_bar_operations WHERE operation_id=ANY(%s)",
                        (operation_ids,),
                    )
                    self.assertEqual(2, cursor.fetchone()[0])
            finally:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=%s",
                        (f"{code}:KRX",),
                    )
                    cursor.execute(
                        "DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=%s",
                        (f"{code}:KRX",),
                    )
                    cursor.execute(
                        "DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)",
                        (operation_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_minute_bars WHERE code=%s AND trading_date=%s",
                        (code, "2099-01-09"),
                    )

    def test_realtime_minute_metadata_is_scoped_by_subject_for_same_minute(self) -> None:
        token = uuid.uuid4().hex
        codes = [f"D{token[:8]}A", f"D{token[:8]}B"]
        values = [{
            "trading_date": "2099-01-11", "minute": "10:01", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050, "volume": 7, "trade_value_million_won": 1,
            "updated_at": 1_790_000_000.0, "operation_id": f"diagnostic-{token}-{index}",
        } for index, code in enumerate(codes)]
        observations = []
        for value in values:
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source=f"source-{value['code']}", value_kind=DataValueKind.ACTUAL,
            )
            observations.append((bar_observation_key(observation), observation))
        operation_ids = [value["operation_id"] for value in values]
        try:
            self.store.save_minute_bars(values, observations=observations)
            key = "2099-01-11T10:01"
            for code in codes:
                metadata = self.store.load_market_data_metadata(
                    MarketDatasetKind.MINUTE_BAR, f"{code}:KRX", key,
                )
                self.assertIsNotNone(metadata)
                self.assertEqual(f"source-{code}", metadata.source)
        finally:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=ANY(%s)",
                    ([f"{code}:KRX" for code in codes],),
                )
                cursor.execute(
                    "DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=ANY(%s)",
                    ([f"{code}:KRX" for code in codes],),
                )
                cursor.execute("DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)",
                               (operation_ids,))
                cursor.execute("DELETE FROM central_minute_bars WHERE code=ANY(%s) AND trading_date=%s",
                               (codes, "2099-01-11"))

    def test_realtime_minute_duplicate_keys_keep_sequential_delta_updates(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_metrics import summarize_market_bar_saves

        token = uuid.uuid4().hex
        code = f"D{token[:9]}"
        operation_ids = [f"diagnostic-{token}-{index}" for index in range(2)]
        values = [{
            "trading_date": "2099-01-10", "minute": "10:00", "code": code,
            "market": "KRX", "open": 10000, "high": 10100 + index,
            "low": 9900 - index, "close": 10050 + index,
            "volume": 7 + index, "trade_value_million_won": 1,
            "updated_at": 1_790_000_000.0 + index,
            "operation_id": operation_ids[index],
        } for index in range(2)]
        observations = []
        for value in values:
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            observations.append((bar_observation_key(observation), observation))

        with _writer_metrics_capture():
            try:
                started = time.time() - 0.01
                self.store.save_minute_bars(values, observations=observations)
                summary = summarize_market_bar_saves(started, time.time() + 1)
                counts = summary["writer_transactions"]["realtime_minute"]["call_samples"][-1]["domain_counts"]
                self.assertEqual(2, counts["authority_lookup_statements"])
                self.assertEqual(2, counts["authority_lookup_keys"])
                [saved] = self.store.load_minute_bars(code, "2099-01-10", "KRX")
                self.assertEqual((15, 2, 10051), (
                    saved["volume"], saved["trade_value_million_won"], saved["close"],
                ))
            finally:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=%s",
                        (f"{code}:KRX",),
                    )
                    cursor.execute(
                        "DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=%s",
                        (f"{code}:KRX",),
                    )
                    cursor.execute(
                        "DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)",
                        (operation_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_minute_bars WHERE code=%s AND trading_date=%s",
                        (code, "2099-01-10"),
                    )

    def test_parallel_distinct_operations_and_commit_ack_retry(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        op_a = f"diagnostic-{uuid.uuid4()}"
        op_b = f"diagnostic-{uuid.uuid4()}"

        def bar(operation_id: str, volume: int) -> dict:
            return {
                "trading_date": "2099-01-02", "minute": "10:00", "code": code,
                "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
                "close": 10050, "volume": volume,
                "trade_value_million_won": 1, "updated_at": 1_790_000_000.0,
                "operation_id": operation_id,
            }

        first, second = bar(op_a, 7), bar(op_b, 11)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.store.save_minute_bars, [row]) for row in (first, second)]
            for future in futures:
                future.result(timeout=30)
        # A lost acknowledgment after COMMIT must not add volume again.
        self.store.save_minute_bars([first])
        rows = self.store.load_minute_bars(code, "2099-01-02", "KRX")
        self.assertEqual(1, len(rows))
        self.assertEqual(18, rows[0]["volume"])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM central_minute_bar_operations WHERE operation_id=ANY(%s)",
                           ([op_a, op_b],))
            self.assertEqual(2, cursor.fetchone()[0])

    def test_pg17_io_views_are_readable_and_optional_failure_is_isolated(self) -> None:
        import psycopg
        from scripts.nas_workload_diagnostic import (
            _checkpointer_snapshot, _optional_io_snapshot, _pg_stat_io_snapshot,
        )

        url = os.environ["KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as connection, connection.cursor() as cursor:
            io_snapshot = _optional_io_snapshot(cursor, _pg_stat_io_snapshot)
            checkpoint_snapshot = _optional_io_snapshot(cursor, _checkpointer_snapshot)
            self.assertTrue(io_snapshot["available"], io_snapshot.get("reason"))
            self.assertTrue(checkpoint_snapshot["available"], checkpoint_snapshot.get("reason"))
            self.assertTrue(io_snapshot["rows"])
            self.assertIn("buffers_written", checkpoint_snapshot["counters"])

            def unsupported_query(probe_cursor):
                probe_cursor.execute("SELECT missing_column FROM pg_stat_io")

            failed = _optional_io_snapshot(cursor, unsupported_query)
            self.assertFalse(failed["available"])
            # The measurement connection is autocommit: a failed optional read
            # must not poison subsequent statistics reads.
            self.assertTrue(_optional_io_snapshot(cursor, _checkpointer_snapshot)["available"])

    def test_daily_bar_replay_skips_unchanged_canonical_update(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        original = {
            "trading_date": "2099-01-02", "code": code, "market": "KRX",
            "open": 10000, "high": 10100, "low": 9900, "close": 10050,
            "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        replay = {**original, "updated_at": original["updated_at"] + 60}
        corrected = {**replay, "close": 10060, "updated_at": replay["updated_at"] + 60}

        self.store.replace_daily_bars([original])
        self.store.replace_daily_bars([replay])
        [unchanged] = self.store.load_daily_bars(code, "KRX", 1)
        self.store.replace_daily_bars([corrected])
        [changed] = self.store.load_daily_bars(code, "KRX", 1)

        self.assertEqual(original["updated_at"], unchanged["updated_at"])
        self.assertEqual(corrected["close"], changed["close"])
        self.assertEqual(corrected["updated_at"], changed["updated_at"])

    def test_daily_bar_batch_keeps_last_duplicate_and_metadata(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        base = {
            "trading_date": "2099-01-03", "code": code, "market": "KRX",
            "open": 10000, "high": 10100, "low": 9900, "close": 10050,
            "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        last = {**base, "close": 10080, "updated_at": base["updated_at"] + 60}
        first_observation = daily_bar_observation(
            base, completeness=DataCompleteness.COMPLETE,
        )
        last_observation = daily_bar_observation(
            last, completeness=DataCompleteness.COMPLETE,
        )
        key = bar_observation_key(first_observation)

        self.store.replace_daily_bars(
            [base, last], observations=[
                (key, first_observation), (key, last_observation),
            ],
        )

        [saved] = self.store.load_daily_bars(code, "KRX", 1)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT available_at FROM central_market_data_observation_meta "
                "WHERE dataset_kind='daily_bar' AND subject=%s AND observation_key=%s",
                (last_observation.subject, key),
            )
            metadata = cursor.fetchone()
        self.assertEqual(last["close"], saved["close"])
        self.assertEqual(last["updated_at"], saved["updated_at"])
        self.assertEqual(last_observation.metadata.available_at, metadata[0])

    def test_daily_bar_batch_rolls_back_canonical_when_metadata_is_invalid(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        value = {
            "trading_date": "2099-01-04", "code": code, "market": "KRX",
            "open": 10000, "high": 10100, "low": 9900, "close": 10050,
            "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        observation = daily_bar_observation(
            value, completeness=DataCompleteness.COMPLETE,
        )

        with self.assertRaisesRegex(ValueError, "observation_key"):
            self.store.replace_daily_bars(
                [value], observations=[(" ", observation)],
            )

        self.assertEqual([], self.store.load_daily_bars(code, "KRX", 10))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM central_market_data_observation_meta "
                "WHERE dataset_kind='daily_bar' AND subject=%s",
                (observation.subject,),
            )
            self.assertEqual(0, cursor.fetchone()[0])

    def test_minute_bar_batch_keeps_last_duplicate_and_rolls_back_with_metadata(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        base = {
            "trading_date": "2099-01-05", "minute": "10:00", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050, "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        last = {**base, "close": 10080, "updated_at": base["updated_at"] + 60}
        first_observation = minute_bar_observation(
            base, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        last_observation = minute_bar_observation(
            last, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        key = bar_observation_key(first_observation)

        self.store.replace_minute_bars([base, last], observations=[
            (key, first_observation), (key, last_observation),
        ])
        [saved] = self.store.load_minute_bars(code, "2099-01-05", "KRX")
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT available_at FROM central_market_data_observation_meta "
                "WHERE dataset_kind='minute_bar' AND subject=%s AND observation_key=%s",
                (last_observation.subject, key),
            )
            metadata = cursor.fetchone()

        invalid = {**base, "minute": "10:01"}
        invalid_observation = minute_bar_observation(
            invalid, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        with self.assertRaisesRegex(ValueError, "observation_key"):
            self.store.replace_minute_bars(
                [invalid], observations=[(" ", invalid_observation)],
            )

        self.assertEqual(last["close"], saved["close"])
        self.assertEqual(last["updated_at"], saved["updated_at"])
        self.assertEqual(last_observation.metadata.available_at, metadata[0])
        self.assertFalse(any(
            row["minute"] == "10:01"
            for row in self.store.load_minute_bars(code, "2099-01-05", "KRX")
        ))

    def test_duplicate_minute_transition_preserves_final_update_time(self) -> None:
        code = str(700000 + uuid.uuid4().int % 100000)
        original = {
            "trading_date": "2099-01-06", "minute": "10:00", "code": code,
            "market": "KRX", "open": 100, "high": 110, "low": 90,
            "close": 100, "volume": 10, "trade_value_million_won": 1,
            "updated_at": 1000.0,
        }
        try:
            self.store.replace_minute_bars([original])
            self.store.replace_minute_bars([
                {**original, "close": 101, "updated_at": 1001.0},
                {**original, "updated_at": 1002.0},
            ])
            [saved] = self.store.load_minute_bars(code, "2099-01-06", "KRX")
            self.assertEqual(100, saved["close"])
            self.assertEqual(1002.0, saved["updated_at"])
        finally:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM central_minute_bars WHERE code=%s AND trading_date=%s",
                               (code, "2099-01-06"))

    def test_minute_multirow_crosses_postgres_batch_boundary_with_metadata(self) -> None:
        code = str(700000 + uuid.uuid4().int % 100000)
        values = []
        observations = []
        for index in range(1001):
            value = {
                "trading_date": "2099-01-06",
                "minute": f"{index // 60:02d}:{index % 60:02d}",
                "code": code, "market": "KRX", "open": 10000,
                "high": 10100, "low": 9900, "close": 10050,
                "volume": index + 1, "trade_value_million_won": 10,
                "updated_at": 1_790_000_000.0 + index,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            values.append(value)
            observations.append((bar_observation_key(observation), observation))
        corrected = {**values[0], "close": 10080, "updated_at": 1_790_002_000.0}
        corrected_observation = minute_bar_observation(
            corrected, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        values.append(corrected)
        observations.append((bar_observation_key(corrected_observation), corrected_observation))

        statements = []
        def record_upsert(executor, prefix, rows, suffix, **kwargs):
            count = _execute_multirow_upsert(executor, prefix, rows, suffix, **kwargs)
            statements.append((prefix, count))
            return count

        try:
            with patch("kiwoom_monitor.central_server.database_market_bars._execute_multirow_upsert",
                       side_effect=record_upsert):
                self.store.replace_minute_bars(values, observations=observations)
            self.assertEqual(3, len(statements))
            self.assertEqual([1, 2, 2], [count for _, count in statements])
            saved = self.store.load_minute_bars(code, "2099-01-06", "KRX")
            self.assertEqual(1001, len(saved))
            self.assertEqual(10080, next(row["close"] for row in saved if row["minute"] == "00:00"))
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT available_at FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='minute_bar' AND subject=%s AND observation_key=%s",
                    (corrected_observation.subject, observations[-1][0]),
                )
                self.assertEqual(corrected_observation.metadata.available_at, cursor.fetchone()[0])
                cursor.execute(
                    "SELECT revision_id,revision_of FROM central_observation_revisions "
                    "WHERE kind='minute_bar' AND subject=%s AND observation_key=%s "
                    "ORDER BY accepted_sequence",
                    (corrected_observation.subject, observations[-1][0]),
                )
                chain = cursor.fetchall()
                self.assertEqual(2, len(chain))
                self.assertEqual(chain[0][0], chain[1][1])
        finally:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=%s",
                               (f"{code}:KRX",))
                cursor.execute("DELETE FROM central_market_data_observation_meta "
                               "WHERE dataset_kind='minute_bar' AND subject=%s", (f"{code}:KRX",))
                cursor.execute("DELETE FROM central_minute_bars WHERE code=%s AND trading_date=%s",
                               (code, "2099-01-06"))

    def test_closed_query_remains_canonical_after_late_realtime_save_and_finalize(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        base = {
            "trading_date": "2099-01-02", "minute": "10:04", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050, "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        query = {**base, "volume": 200, "trade_value_million_won": 20}
        observation = minute_bar_observation(
            query, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        self.store.save_minute_bars([{**base, "operation_id": f"early-{uuid.uuid4()}"}])
        self.store.replace_minute_bars(
            [query], observations=[(bar_observation_key(observation), observation)],
        )
        self.store.save_minute_bars([{**base, "operation_id": f"late-{uuid.uuid4()}"}])
        self.store.finalize_minute_bars([{
            "trading_date": base["trading_date"], "minute": base["minute"],
            "code": code, "market": "KRX", "available_at": 4_071_000_000.0,
            "capture_quality": "complete", "finalization_source": "timer",
            "operation_id": f"final-{uuid.uuid4()}",
        }])
        [saved] = self.store.load_minute_bars(code, "2099-01-02", "KRX")
        self.assertEqual(200, saved["volume"])
        self.assertEqual(20, saved["trade_value_million_won"])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT origin,source,completeness FROM central_market_data_observation_meta "
                "WHERE dataset_kind='minute_bar' AND subject=%s AND observation_key=%s",
                (f"{code}:KRX", "2099-01-02T10:04"),
            )
            self.assertEqual(
                ("query", "kiwoom-ka10080;trade_value=ohlcv_estimate", "complete"),
                tuple(cursor.fetchone()),
            )

    def test_parallel_closed_query_and_realtime_write_keep_query_values(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        base = {
            "trading_date": "2099-01-02", "minute": "10:05", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050, "volume": 100, "trade_value_million_won": 10,
            "updated_at": 1_790_000_000.0,
        }
        query = {**base, "volume": 200, "trade_value_million_won": 20}
        observation = minute_bar_observation(
            query, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        realtime = {**base, "operation_id": f"parallel-{uuid.uuid4()}"}
        realtime_observation = minute_bar_observation(
            realtime, origin=ObservationOrigin.REALTIME,
            completeness=DataCompleteness.IN_PROGRESS,
            source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.store.replace_minute_bars, [query],
                                observations=[(bar_observation_key(observation), observation)]),
                executor.submit(self.store.save_minute_bars, [realtime],
                                observations=[(bar_observation_key(realtime_observation), realtime_observation)]),
            ]
            for future in futures:
                future.result(timeout=30)
        [saved] = self.store.load_minute_bars(code, "2099-01-02", "KRX")
        self.assertEqual(200, saved["volume"])
        self.assertEqual(20, saved["trade_value_million_won"])

    def test_query_revision_batch_keeps_same_key_chain_and_replay_deduplication(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)

        def query_bar(close: int):
            value = {
                "trading_date": "2099-01-02", "minute": "10:01", "code": code,
                "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
                "close": close, "volume": 100, "trade_value_million_won": 1,
                "updated_at": 1_790_000_000.0,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            return value, (bar_observation_key(observation), observation)

        first, second = query_bar(10050), query_bar(10060)
        self.store.replace_minute_bars(
            [first[0], second[0]], observations=[first[1], second[1]],
        )
        self.store.replace_minute_bars([second[0]], observations=[second[1]])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT accepted_sequence,revision_id,revision_of FROM central_observation_revisions "
                "WHERE kind='minute_bar' AND subject=%s AND source_id=%s "
                "ORDER BY accepted_sequence", (f"{code}:KRX", "kiwoom-ka10080;trade_value=ohlcv_estimate"),
            )
            revisions = cursor.fetchall()
        self.assertEqual(2, len(revisions))
        self.assertLess(revisions[0][0], revisions[1][0])
        self.assertIsNone(revisions[0][2])
        self.assertEqual(revisions[0][1], revisions[1][2])

    def test_revision_batch_preserves_sequence_and_chain_across_chunks(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        sources = []
        for close in range(10_000, 11_001):
            value = {
                "trading_date": "2099-01-02", "minute": "10:06", "code": code,
                "market": "KRX", "open": 10_000, "high": close, "low": 9_900,
                "close": close, "volume": 100, "trade_value_million_won": 1,
                "updated_at": 1_790_000_000.0,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            sources.append(ObservationRevisionSource.from_observation(
                "minute_bar", observation.subject, bar_observation_key(observation),
                {"close": close}, observation,
            ))
        key = (sources[0].subject, sources[0].observation_key, sources[0].source_id)
        with self.store._connect() as connection, connection.cursor() as cursor:
            statements, rows = _insert_postgres_observation_revisions_batch(
                cursor, sources, {key: None},
            )
            self.assertEqual((2, 1001), (statements, rows))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT accepted_sequence,revision_id,revision_of "
                "FROM central_observation_revisions "
                "WHERE kind='minute_bar' AND subject=%s AND observation_key=%s "
                "AND source_id=%s ORDER BY accepted_sequence",
                key,
            )
            revisions = cursor.fetchall()
        self.assertEqual(1001, len(revisions))
        self.assertIsNone(revisions[0][2])
        self.assertTrue(all(current[0] > previous[0] and current[2] == previous[1]
                            for previous, current in zip(revisions, revisions[1:])))

    def test_query_revision_batch_rolls_back_canonical_and_history_together(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)
        value = {
            "trading_date": "2099-01-02", "minute": "10:02", "code": code,
            "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
            "close": 10050, "volume": 100, "trade_value_million_won": 1,
            "updated_at": 1_790_000_000.0,
        }
        observation = minute_bar_observation(
            value, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        second_value = {**value, "minute": "10:07", "close": 10060}
        second_observation = minute_bar_observation(
            second_value, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        def fail_after_insert(cursor, sources, latest_by_key, *, execute_seconds=None):
            _insert_postgres_observation_revisions_batch(
                cursor, sources, latest_by_key, execute_seconds=execute_seconds,
            )
            raise RuntimeError("revision write failed")

        with patch(
            "kiwoom_monitor.central_server.database_market_bars._insert_postgres_observation_revisions_batch",
            side_effect=fail_after_insert,
        ), self.assertRaisesRegex(RuntimeError, "revision write failed"):
            self.store.replace_minute_bars(
                [value, second_value], observations=[
                    (bar_observation_key(observation), observation),
                    (bar_observation_key(second_observation), second_observation),
                ],
            )
        self.assertEqual([], self.store.load_minute_bars(code, "2099-01-02", "KRX"))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM central_observation_revisions "
                "WHERE kind='minute_bar' AND subject=%s", (code,),
            )
            self.assertEqual(0, cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*) FROM central_market_data_observation_meta "
                "WHERE dataset_kind='minute_bar' AND subject=%s",
                (f"{code}:KRX",),
            )
            self.assertEqual(0, cursor.fetchone()[0])

    def test_parallel_query_revisions_for_same_key_form_one_chain(self) -> None:
        code = str(900000 + uuid.uuid4().int % 99999)

        def save(close: int) -> None:
            value = {
                "trading_date": "2099-01-02", "minute": "10:03", "code": code,
                "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
                "close": close, "volume": 100, "trade_value_million_won": 1,
                "updated_at": 1_790_000_000.0,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            self.store.replace_minute_bars(
                [value], observations=[(bar_observation_key(observation), observation)],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(save, close) for close in (10050, 10060)]
            for future in futures:
                future.result(timeout=30)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision_id,revision_of FROM central_observation_revisions "
                "WHERE kind='minute_bar' AND subject=%s AND source_id=%s "
                "ORDER BY accepted_sequence", (f"{code}:KRX", "kiwoom-ka10080;trade_value=ohlcv_estimate"),
            )
            revisions = cursor.fetchall()
        self.assertEqual(2, len(revisions))
        self.assertIsNone(revisions[0][1])
        self.assertEqual(revisions[0][0], revisions[1][1])


if __name__ == "__main__":
    unittest.main()
