"""Collector -> actual PostgreSQL writers, on the dedicated DB only."""
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from kiwoom_monitor.central_server.diagnostic_collector_replay import (
    _cleanup, _preflight, build_collector_fixture, run_collector_fixture,
)
from kiwoom_monitor.central_server.diagnostic_replay import _ReplayStore
from tests.collector_replay_clock import CollectorTestClock


class DiagnosticCollectorReplayPostgresTests(unittest.TestCase):
    def _url(self, run_id):
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            self.skipTest('dedicated PostgreSQL URL is required')
        _preflight(url, build_collector_fixture(run_id))
        return url

    def test_real_parser_scheduler_commits_verifies_and_cleans_while_preserving_peer(self):
        run_id = uuid4().hex
        url = self._url(run_id)
        peer_fixture = build_collector_fixture(uuid4().hex)
        _preflight(url, peer_fixture)
        store = _ReplayStore(url)
        peer_row = {**peer_fixture.minute_rows[0], 'updated_at': peer_fixture.origin.timestamp()}
        store.replace_minute_bars([peer_row])
        started, done = Event(), Event()
        started.set()
        try:
            with patch('kiwoom_monitor.central_server.diagnostic_collector_replay._ReplayClock', CollectorTestClock):
                result = run_collector_fixture(url, run_id, Event(), Event(), started, done)
            self.assertEqual('complete', result['state'], result)
            self.assertTrue(result['fixture_verification']['passed'], result)
            self.assertEqual(21, result['fixture_verification']['minute_rows'])
            self.assertEqual(1080, result['fixture_verification']['second_rows'])
            self.assertEqual(18, result['fixture_verification']['closed_revision_rows'])
            self.assertEqual('passed', result['cleanup'])
            self.assertTrue(done.is_set())
            _preflight(url, build_collector_fixture(run_id))  # Entire run scope is empty again.
            self.assertEqual(1, len(store.load_minute_bars(peer_fixture.code, '2026-10-06')))
        finally:
            _cleanup(store, peer_fixture, ())

    def test_statement_rollback_and_lost_commit_ack_retry_keep_values_and_cleanup(self):
        run_id = uuid4().hex
        url = self._url(run_id)

        class FailingStore(_ReplayStore):
            attempts = 0

            def save_minute_bars(self, values, *, observations=None):
                self.attempts += 1
                if self.attempts == 1:
                    # Fails inside the native transaction after canonical writes.
                    first_key, first_observation = observations[0]
                    invalid_observation = replace(
                        first_observation,
                        metadata=replace(first_observation.metadata, source=None),
                    )
                    return super().save_minute_bars(
                        values, observations=[(first_key, invalid_observation), *observations[1:]],
                    )
                result = super().save_minute_bars(values, observations=observations)
                if self.attempts == 2:
                    raise OSError('injected collector COMMIT acknowledgement loss')
                return result

        started, done = Event(), Event()
        started.set()
        with patch('kiwoom_monitor.central_server.diagnostic_collector_replay._ReplayStore', FailingStore), \
                patch('kiwoom_monitor.central_server.diagnostic_collector_replay._ReplayClock', CollectorTestClock):
            result = run_collector_fixture(url, run_id, Event(), Event(), started, done)
        self.assertEqual('complete', result['state'], result)
        self.assertTrue(result['fixture_verification']['passed'])
        failed = [call['error_type'] for call in result['calls'] if call['state'] == 'failed']
        self.assertEqual(['NotNullViolation', 'OSError'], failed)
        self.assertEqual(0, result['pending_records_after_drain'])
        self.assertEqual('passed', result['cleanup'])
        _preflight(url, build_collector_fixture(run_id))
