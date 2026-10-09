"""Consumer acceptance for safe bootstrap; frozen red reproductions stay separate."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import asyncio
import os
from threading import Event
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit
import uuid

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.breakout_strategy import StrategyState
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.database_observation_readers import OBSERVATION_DELIVERY_PROTOCOL
from kiwoom_monitor.central_server.market_observations import bar_observation_key, minute_bar_observation
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, DataValueKind, ObservationOrigin
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository


class ObservationBootstrapPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        cls.url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not cls.url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        if urlsplit(cls.url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('consumer gate requires diagnostic database')
        cls.store = PostgresQueryStore(cls.url)
        cls.store.initialize()

    def _exercise(self, *, mock=False, bootstrap_during_gap=True, normalized=False,
                  legacy=False, recovery_ack_loss=False):
        token = uuid.uuid4().hex[:12]
        codes = ['BS' + token, 'BT' + token]
        subjects = [code + ':KRX' for code in codes]
        entered, release = Event(), Event()
        monitor_ids = []
        owners = []
        at = datetime(2099, 1, 14, 10, 1, 10, tzinfo=timezone(timedelta(hours=9))).timestamp()
        values = [dict(trading_date='2099-01-14', minute='10:01', code=code,
                       market='KRX', open=100, high=110, low=90, close=105,
                       volume=10, trade_value_million_won=2, updated_at=at,
                       operation_id='bootstrap-seed-' + code) for code in codes]
        closures = [{**{key: row[key] for key in ('trading_date', 'minute', 'code', 'market')},
                     'available_at': at + 60, 'capture_quality': 'complete',
                     'finalization_source': 'timer', 'operation_id': 'bootstrap-close-' + row['code']}
                    for row in values]
        config = replace(default_shadow_breakout_config(), capital_won=1_000_000 + int(token[:8], 16))
        # A new store has no previously published frontier. Do not seed this test
        # from another consumer's already established RAM prefix.
        reader = PostgresQueryStore(self.url, shadow_checkpoint_frames_enabled=normalized)

        class HeldCommit(self.pg.Connection):
            def commit(connection):
                entered.set()
                if not release.wait(30):
                    raise TimeoutError('consumer native COMMIT gate timed out')
                return super().commit()

        held_store = PostgresQueryStore(self.url)
        held_store._connect = lambda: HeldCommit.connect(self.url)

        if mock:
            # Only reuse admission fixture values; stores/writers/checkpoints below
            # are real PostgreSQL. No order runtime/transport is provided.
            from tests.unit.test_mock_automation_runner import MockAutomationRunnerTests
            case = MockAutomationRunnerTests('test_checkpoint_from_other_spec_is_never_reused')
            case.setUp()
            try:
                package = replace(case.package, strategy_ref='bootstrap-' + token)
                account = str(uuid.uuid4())
                spec = replace(case.spec, strategy_ref=package.strategy_ref,
                               candidate_package_hash=package.package_hash,
                               account_scope=AccountScope('kiwoom', AccountEnvironment.MOCK, account))
                bundle = SimpleNamespace(
                    account_ref=account, run_id='bootstrap-run-' + token,
                    automation_context=SimpleNamespace(
                        execution_run_id='bootstrap-run-' + token, spec_id=spec.spec_id),
                    repository=case.events)
            finally:
                case.doCleanups()
            owners.extend((account, package.strategy_ref))
            reader.upsert_documents(ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
                [{'owner': package.strategy_ref, 'key': package.package_hash, 'document': package.to_dict()}])

        def open_actor():
            if mock:
                actor = MockAutomationRunner(reader, bundle, spec)
                actor._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                    active_spec_id=spec.spec_id, desired_state=SimpleNamespace(value='RUNNING'))
            else:
                actor = CandidateMonitor(reader, config, poll_seconds=1, universe_max_age_seconds=300)
                monitor_ids.append(actor.monitor_id)
            return actor

        def poll(actor):
            return asyncio.run(actor.run_once(limit=1 if legacy else 1000)) if mock else actor.run_once(limit=1 if legacy else 1000)

        def checkpoint(actor):
            if mock:
                rows = reader.load_documents('execution_mock_automation_runner_current', account, 1)
                return rows[0]['document'] if rows else None
            return reader.load_shadow_monitor_state(actor.monitor_id)

        try:
            observations = []
            for value in values:
                observation = minute_bar_observation(value, origin=ObservationOrigin.REALTIME,
                    completeness=DataCompleteness.IN_PROGRESS, source='kiwoom-websocket-0B',
                    value_kind=DataValueKind.ACTUAL)
                observations.append((bar_observation_key(observation), observation))
            self.store.save_minute_bars(values, observations=observations)
            if not bootstrap_during_gap:
                actor = open_actor()
                before = checkpoint(actor)
                self.assertIsNotNone(before)
                self.assertNotEqual(set(codes), {frame['code'] for frame in before['bars']})

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(held_store.finalize_minute_bars, [closures[0]])
                try:
                    self.assertTrue(entered.wait(10))
                    self.store.finalize_minute_bars([closures[1]])  # independent peer COMMIT
                    if legacy:
                        # Reproduce a pre-protocol checkpoint that advanced past an invisible low COMMIT.
                        high = max(row['accepted_sequence'] for row in
                                   self.store.load_observation_revisions('minute_bar', subjects[1]))
                        actor._cursor = high
                        actor._delivery_protocol = None
                        actor._state = StrategyState(emitted_candidate_keys=('original-emitted',))
                        if mock:
                            actor._status = {'state': 'WAITING_ORDER', 'orders_enabled': False,
                                             'pending_intent_id': 'original-pending'}
                            actor._fill_cursor = 73
                            actor._seen_fill_ids = {'original-fill'}
                        actor._save_checkpoint()
                        before = checkpoint(actor)
                        actor = open_actor()
                        self.assertIsNotNone(actor._frame_recovery)
                    if bootstrap_during_gap:
                        actor = open_actor()
                        self.assertTrue(actor._bootstrap_pending)
                        self.assertIsNone(checkpoint(actor))
                        self.assertEqual(0, poll(actor))
                        self.assertIsNone(checkpoint(actor))
                        actor = open_actor()  # Restart while pending cannot restore fake-ready state.
                        self.assertTrue(actor._bootstrap_pending)
                    else:
                        self.assertEqual(0, poll(actor))
                        self.assertEqual(before, checkpoint(actor))
                finally:
                    release.set()
                future.result(timeout=10)

            if legacy:
                for _ in range(100):
                    if actor._frame_recovery is None:
                        break
                    if recovery_ack_loss:
                        method = 'upsert_documents' if mock else 'save_shadow_monitor_state'
                        native = getattr(reader, method)
                        def lost_ack(*args, **kwargs):
                            native(*args, **kwargs)
                            raise OSError('injected recovery COMMIT acknowledgement loss')
                        from unittest.mock import patch
                        with patch.object(reader, method, side_effect=lost_ack):
                            try:
                                self.assertEqual(0, poll(actor))
                            except OSError as error:
                                self.assertIn('acknowledgement loss', str(error))
                                self.assertIsNone(actor._delivery_protocol)
                                actor = open_actor()  # Native COMMIT was real; restart uses repaired frames.
                                self.assertIsNone(actor._frame_recovery)
                                break
                    else:
                        self.assertEqual(0, poll(actor))
                    if actor._frame_recovery is not None:
                        self.assertEqual(before, checkpoint(actor))
                self.assertIsNone(actor._frame_recovery)
                saved = checkpoint(actor)
                self.assertEqual(before['input_cursor' if mock else 'cursor'],
                                 saved['input_cursor' if mock else 'cursor'])
                self.assertEqual(before['strategy_state'], saved['strategy_state'])
                self.assertFalse(saved['input_recovery']['historical_decisions_recomputed'])
                if mock:
                    for key in ('fill_cursor', 'seen_fill_ids', 'pending_intent_id',
                                'account_ref', 'spec_id', 'execution_run_id', 'candidate_package_hash'):
                        self.assertEqual(before[key], saved[key])
            else:
                count = poll(actor)
                self.assertEqual(0 if bootstrap_during_gap else 2, count)
            saved = checkpoint(actor)
            selected = [frame for frame in saved['bars'] if frame['code'] in codes]
            self.assertEqual(set(codes), {frame['code'] for frame in selected})
            self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved['delivery_protocol'])
            closed_sequences = [row['accepted_sequence'] for subject in subjects
                                for row in self.store.load_observation_revisions('minute_bar', subject)
                                if row['payload'].get('window_closed')]
            self.assertEqual(max(closed_sequences), saved['input_cursor' if mock else 'cursor'])
            restarted = open_actor()
            self.assertEqual(0, poll(restarted))
            self.assertEqual(saved, checkpoint(restarted))
            if (bootstrap_during_gap or legacy) and not mock:
                with reader._connect() as connection, connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM central_shadow_decisions WHERE monitor_id=%s',
                                   (actor.monitor_id,))
                    self.assertEqual(0, cursor.fetchone()[0])
        finally:
            release.set()
            with self.store._connect() as connection, connection.cursor() as cursor:
                for table in ('central_shadow_candidate_events', 'central_shadow_decisions',
                              'central_shadow_monitor_state'):
                    cursor.execute(f'DELETE FROM {table} WHERE monitor_id=ANY(%s)', (monitor_ids,))
                cursor.execute('DELETE FROM central_documents WHERE owner=ANY(%s)', (owners,))
                cursor.execute("DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=ANY(%s)", (subjects,))
                cursor.execute("DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=ANY(%s)", (subjects,))
                cursor.execute('DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)',
                               ([row['operation_id'] for row in [*values, *closures]],))
                cursor.execute('DELETE FROM central_minute_bars WHERE code=ANY(%s)', (codes,))

    def test_shadow_pending_bootstrap_seeds_late_commit_and_restarts_without_decisions(self):
        self._exercise()

    def test_shadow_pending_poll_delivers_both_closed_frames_once(self):
        self._exercise(bootstrap_during_gap=False)

    def test_normalized_shadow_bootstrap_preserves_delivery_protocol(self):
        self._exercise(normalized=True)

    def test_mock_pending_bootstrap_uses_native_checkpoint_and_never_dispatches_history(self):
        self._exercise(mock=True)

    def test_mock_pending_poll_keeps_actual_closed_frames_and_restart_cursor(self):
        self._exercise(mock=True, bootstrap_during_gap=False)

    def test_legacy_shadow_recovers_late_commit_frames_without_historical_decisions(self):
        self._exercise(bootstrap_during_gap=False, legacy=True)

    def test_legacy_normalized_shadow_recovers_and_restarts_after_lost_commit_ack(self):
        self._exercise(bootstrap_during_gap=False, normalized=True, legacy=True, recovery_ack_loss=True)

    def test_legacy_mock_preserves_intent_fill_and_execution_binding_during_repair(self):
        self._exercise(mock=True, bootstrap_during_gap=False, legacy=True)

    def test_legacy_mock_native_repair_commit_survives_lost_ack_and_restart(self):
        self._exercise(mock=True, bootstrap_during_gap=False, legacy=True, recovery_ack_loss=True)
