from __future__ import annotations

import unittest
import uuid
import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore, PostgresQueryStore
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    MOCK_AUTOMATION_SPEC_VERSION,
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    assess_mock_automation_readiness,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
MOCK_ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"


def _criteria(**changes: int | None) -> ForwardCriteria:
    values = {
        "minimum_comparable_observations": 100,
        "minimum_coverage_ppm": 990_000,
        "maximum_p95_delay_ms": 2_000,
        "maximum_gap_count": 1,
        "maximum_submission_unknown_count": 0,
        "maximum_rejected_order_count": 1,
        "maximum_cancel_failure_count": 0,
        "maximum_reconnect_count": 2,
        "maximum_balance_mismatch_count": 0,
        "minimum_active_day_count": 5,
        "minimum_closed_trade_count": 10,
        "minimum_adjusted_net_pnl_won": 0,
        "maximum_drawdown_ppm": 100_000,
        "maximum_exposure_ppm": 500_000,
    }
    values.update(changes)
    return ForwardCriteria(**values)


def _spec(
    criteria: ForwardCriteria | None = None, *, environment: str = "mock",
    session_profile: str | None = None, account_ref: str = "mock-account",
) -> ForwardEvaluationSpec:
    return ForwardEvaluationSpec(
        strategy_ref="strategy-1", family_version="family/v1",
        factor_versions=("factor-a/v1", "factor-b/v1"), policy_version="policy/v1",
        data_path="nas", account_ref=account_ref, environment=environment,
        evaluation_start=NOW + timedelta(days=1), evaluation_end=NOW + timedelta(days=8),
        frozen_at=NOW, criteria=criteria or _criteria(),
        session_profile=session_profile,
    )


def _automation_spec(
    profile: ForwardEvaluationSpec,
    *,
    complete: bool = True,
    maximum_concurrent_strategies: int | None = 1,
    binding_revision: int = 1,
    frozen_at: datetime = NOW,
) -> MockAutomationOperatingSpec:
    values = {
        "shadow_evidence_ref": "shadow-stage-revision" if complete else None,
        "maximum_concurrent_strategies": maximum_concurrent_strategies if complete else None,
        "maximum_concurrent_positions": 1 if complete else None,
        "maximum_capital_won": 10_000_000 if complete else None,
        "maximum_daily_loss_won": 300_000 if complete else None,
        "maximum_data_gap_seconds": 5 if complete else None,
        "maximum_submission_unknown_count": 0 if complete else None,
        "maximum_reconnect_count": 2 if complete else None,
        "maximum_balance_mismatch_count": 0 if complete else None,
        "supported_venue": "KRX" if complete else None,
        "session_profile": profile.session_profile if complete else None,
        "data_path": profile.data_path if complete else None,
        "candidate_transition_policy": MOCK_CANDIDATE_TRANSITION_POLICY if complete else None,
        "stop_policy": MOCK_STOP_POLICY if complete else None,
        "recovery_policy": MOCK_RECOVERY_POLICY if complete else None,
    }
    return MockAutomationOperatingSpec(
        strategy_ref=profile.strategy_ref,
        candidate_package_hash="a" * 64,
        final_result_hash="b" * 64,
        final_batch_id="final-batch-1",
        final_run_id="final-run-1",
        forward_profile_id=profile.profile_id,
        account_scope=AccountScope(
            broker="kiwoom",
            environment=AccountEnvironment.MOCK,
            account_ref=profile.account_ref,
        ),
        credential_profile_id="nas-mock-default",
        binding_revision=binding_revision,
        binding_verified_at=NOW,
        frozen_at=frozen_at,
        **values,
    )


class ExecutionActivationTests(unittest.TestCase):
    def test_profile_id_freezes_versions_path_account_period_and_criteria(self) -> None:
        first = _spec()
        same = _spec()
        changed = _spec(_criteria(maximum_gap_count=2))

        self.assertEqual(first.profile_id, same.profile_id)
        self.assertNotEqual(first.profile_id, changed.profile_id)
        self.assertEqual((), first.criteria.tbd_fields)

    def test_tbd_criteria_are_explicit_and_real_environment_is_rejected(self) -> None:
        self.assertEqual(
            ("minimum_closed_trade_count",),
            _criteria(minimum_closed_trade_count=None).tbd_fields,
        )
        with self.assertRaisesRegex(ValueError, "only the mock"):
            _spec(environment="real")

    def test_explicit_session_profile_is_part_of_forward_profile_identity(self) -> None:
        legacy = _spec()
        regular = _spec(session_profile="krx-regular/v1")
        after = _spec(session_profile="krx-after/v1")
        self.assertNotEqual(legacy.profile_id, regular.profile_id)
        self.assertNotEqual(regular.profile_id, after.profile_id)
        self.assertNotIn("session_profile", legacy.to_dict())
        self.assertEqual("krx-after/v1", after.to_dict()["session_profile"]["profile"])

    def test_stage_transition_requires_evidence_and_live_has_separate_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence"):
            strategy_stage_revision(
                strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.DRAFT,
                stage=StrategyLifecycleStage.EVALUATED, evidence_refs=(),
                reason="backtest reviewed", decided_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "O2b"):
            strategy_stage_revision(
                strategy_ref="strategy-1",
                previous_stage=StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
                stage=StrategyLifecycleStage.APPROVED_FOR_LIVE,
                evidence_refs=("forward-report-1",), reason="manual request", decided_at=NOW,
            )

    def test_mock_automation_spec_has_no_silent_defaults_and_stays_blocked(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        draft = _automation_spec(profile, complete=False)
        readiness = assess_mock_automation_readiness(
            draft, profile, strategy_stage=StrategyLifecycleStage.SHADOW,
        )

        self.assertEqual(MOCK_AUTOMATION_SPEC_VERSION, draft.version)
        self.assertEqual(MockAutomationReadinessStatus.BLOCKED, readiness.status)
        self.assertIn("TBD:maximum_capital_won", readiness.reasons)
        self.assertIn("TBD:recovery_policy", readiness.reasons)

    def test_complete_mock_policy_is_ready_but_does_not_enable_transport(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        operating = _automation_spec(profile)
        readiness = assess_mock_automation_readiness(
            operating, profile, strategy_stage=StrategyLifecycleStage.SHADOW,
        )

        self.assertEqual(MockAutomationReadinessStatus.READY, readiness.status)
        self.assertEqual((), readiness.reasons)
        self.assertNotEqual(
            operating.spec_id,
            _automation_spec(profile, maximum_concurrent_strategies=2).spec_id,
        )

    def test_unsupported_concurrency_and_non_shadow_stage_are_blocked(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        readiness = assess_mock_automation_readiness(
            _automation_spec(profile, maximum_concurrent_strategies=2),
            profile,
            strategy_stage=StrategyLifecycleStage.VALIDATED,
        )
        self.assertEqual(MockAutomationReadinessStatus.BLOCKED, readiness.status)
        self.assertIn("UNSUPPORTED_CONCURRENT_STRATEGY_COUNT", readiness.reasons)
        self.assertIn("STRATEGY_NOT_IN_SHADOW", readiness.reasons)

    def test_mock_automation_spec_rejects_real_account_scope(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        values = _automation_spec(profile).to_dict()
        values["account_scope"] = {
            "broker": "kiwoom", "environment": "real", "account_ref": str(uuid.uuid4()),
        }
        from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
        with self.assertRaisesRegex(ValueError, "verified mock"):
            mock_automation_spec_from_dict(values)

    def test_repository_requires_current_binding_and_keeps_operating_spec_immutable(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        store.register_account_identity({
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": "c" * 64, "account_ref": MOCK_ACCOUNT_REF,
            "created_at": NOW.isoformat(),
        })
        binding = store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": MOCK_ACCOUNT_REF,
            "verified_at": NOW.isoformat(), "verification_method": "ka00001",
        })
        repository = ForwardEvaluationRepository(store)
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        repository.save_profile(profile)
        operating = _automation_spec(profile, binding_revision=binding["binding_revision"])

        self.assertTrue(repository.save_mock_automation_spec(operating))
        self.assertFalse(repository.save_mock_automation_spec(operating))
        self.assertEqual((operating,), repository.load_mock_automation_specs(MOCK_ACCOUNT_REF))

        store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": MOCK_ACCOUNT_REF,
            "verified_at": (NOW + timedelta(minutes=1)).isoformat(),
            "verification_method": "ka00001",
        })
        stale = _automation_spec(
            profile, binding_revision=1, frozen_at=NOW + timedelta(minutes=2),
        )
        with self.assertRaisesRegex(ValueError, "current verified mock binding"):
            repository.save_mock_automation_spec(stale)

    def test_repository_keeps_profiles_and_stage_revisions_across_restart(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        spec = _spec()
        self.assertTrue(repository.save_profile(spec))
        self.assertFalse(repository.save_profile(spec))
        first = strategy_stage_revision(
            strategy_ref=spec.strategy_ref, previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=("research-report-1",),
            reason="manual research report reviewed", decided_at=NOW,
        )
        self.assertTrue(repository.save_stage_revision(first))
        self.assertFalse(repository.save_stage_revision(first))

        restored = ForwardEvaluationRepository(store)
        self.assertEqual(spec, restored.load_profile(spec.strategy_ref, spec.profile_id))
        self.assertEqual(StrategyLifecycleStage.EVALUATED, restored.latest_stage(spec.strategy_ref))
        self.assertEqual((first,), restored.load_stage_revisions(spec.strategy_ref))

    def test_stage_predecessor_check_serializes_distinct_revisions_and_same_id_retries(self) -> None:
        for same in (False, True):
            with self.subTest(same=same), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'central.sqlite'
                stores = [SQLiteQueryStore(path), SQLiteQueryStore(path)]
                stores[0].initialize()
                barrier = Barrier(2, timeout=10)
                values = [strategy_stage_revision(
                    strategy_ref='strategy', previous_stage=StrategyLifecycleStage.DRAFT,
                    stage=StrategyLifecycleStage.EVALUATED, evidence_refs=('report',),
                    reason='same' if same else str(i), decided_at=NOW,
                ) for i in range(2)]
                for store in stores:
                    native = store.insert_strategy_stage_revision
                    def gated(document, native=native):
                        barrier.wait()
                        return native(document)
                    store.insert_strategy_stage_revision = gated
                def save(store, value):
                    try:
                        return ForwardEvaluationRepository(store).save_stage_revision(value)
                    except ValueError as exc:
                        self.assertIn('strategy stage changed: expected draft, found evaluated', str(exc))
                        return 'stale'
                try:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(save, store, value) for store, value in zip(stores, values)]
                        outcomes = [future.result(timeout=20) for future in futures]
                    self.assertCountEqual([True, False] if same else [True, 'stale'], outcomes)
                    stored = ForwardEvaluationRepository(stores[0]).load_stage_revisions('strategy')
                    self.assertEqual(1, len(stored))
                    self.assertIn(stored[0], values)
                finally:
                    for store in stores:
                        store.close()

    def test_stage_native_costs_preserve_read_only_retry_and_rollback_before_ack(self) -> None:
        store = SQLiteQueryStore(Path(':memory:'), now_provider=lambda: NOW)
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        first = strategy_stage_revision(strategy_ref='strategy', previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=('report',), reason='first', decided_at=NOW)
        statements, connections = [], []
        native = store._connection
        @contextmanager
        def traced():
            connections.append(1)
            with native() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        with patch.object(store, '_connection', traced), patch.object(store, '_now_provider', return_value=NOW) as clock:
            self.assertTrue(repository.save_stage_revision(first))
            self.assertEqual(2, len(connections))
            self.assertEqual(3, sum(s.startswith('SELECT') for s in statements))
            self.assertEqual(1, sum(s.startswith('INSERT') for s in statements))
            self.assertEqual(1, sum(s == 'BEGIN IMMEDIATE' for s in statements))
            self.assertEqual(1, sum(s == 'COMMIT' for s in statements))
            clock.assert_called_once_with()
            statements.clear(); connections.clear(); clock.reset_mock()
            self.assertFalse(repository.save_stage_revision(first))
            self.assertEqual(1, len(connections))
            self.assertEqual(1, len(statements))
            self.assertTrue(statements[0].startswith('SELECT'))
            clock.assert_not_called()
        second = strategy_stage_revision(strategy_ref='strategy', previous_stage=StrategyLifecycleStage.EVALUATED,
            stage=StrategyLifecycleStage.VALIDATED, evidence_refs=('report-2',), reason='second',
            decided_at=NOW + timedelta(seconds=1))
        with native() as connection:
            connection.execute("CREATE TRIGGER reject_stage BEFORE INSERT ON central_documents "
                "WHEN NEW.collection='execution_strategy_stage_revisions' "
                "BEGIN SELECT RAISE(ABORT,'stage write failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'stage write failed'):
            repository.save_stage_revision(second)
        self.assertEqual((first,), repository.load_stage_revisions('strategy'))
        with native() as connection:
            connection.execute('DROP TRIGGER reject_stage')
        self.assertTrue(repository.save_stage_revision(second))
        self.assertFalse(repository.save_stage_revision(first))
        self.assertEqual(StrategyLifecycleStage.VALIDATED, repository.latest_stage('strategy'))

    def test_postgres_stage_lock_compare_insert_and_commit_ack_contract(self) -> None:
        first = strategy_stage_revision(strategy_ref='strategy', previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=('report',), reason='first', decided_at=NOW)
        other = strategy_stage_revision(strategy_ref='strategy', previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=('report',), reason='other', decided_at=NOW)
        class Cursor:
            def __init__(self, owner): self.owner = owner
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, params=()):
                self.owner.sql.append((sql, params))
            def fetchall(self): return self.owner.history
            def fetchone(self):
                sql, parameters = self.owner.sql[-1]
                if sql.startswith('INSERT'):
                    return (json.dumps(first.to_dict()),)
                return next(((json.dumps(raw),) for key, raw in self.owner.history if key == parameters[-1]), None)
        class Connection:
            def __init__(self, history, fail_commit):
                self.history, self.fail_commit = history, fail_commit
                self.sql = []
                self.commits = self.rollbacks = self.closes = 0
            def __enter__(self): return self
            def __exit__(self, kind, *_):
                if kind:
                    self.rollback()
                else:
                    self.commit()
                self.close()
            def commit(self):
                self.commits += 1
                if self.fail_commit: raise ConnectionError('commit acknowledgement lost')
            def rollback(self): self.rollbacks += 1
            def close(self): self.closes += 1
            def cursor(self): return Cursor(self)
        for case in ('fresh', 'same', 'stale', 'commit_error'):
            with self.subTest(case=case):
                history = [(first.revision_id, first.to_dict())] if case in ('same', 'stale') else []
                raw = Connection(history, case == 'commit_error')
                store = PostgresQueryStore('unused', now_provider=lambda: NOW)
                with patch.object(store, '_connect', return_value=raw), \
                        patch.object(store, '_now_provider', return_value=NOW) as clock:
                    if case == 'stale':
                        with self.assertRaisesRegex(ValueError, 'expected draft, found evaluated'):
                            store.insert_strategy_stage_revision(other.to_dict())
                    elif case == 'commit_error':
                        with self.assertRaisesRegex(ConnectionError, 'commit acknowledgement lost'):
                            store.insert_strategy_stage_revision(first.to_dict())
                    else:
                        self.assertEqual(case == 'fresh', store.insert_strategy_stage_revision(first.to_dict()))
                    self.assertEqual(1 if case in ('fresh', 'commit_error') else 0, clock.call_count)
                self.assertEqual(('SET TRANSACTION ISOLATION LEVEL READ COMMITTED', ()), raw.sql[0])
                self.assertEqual(('SELECT pg_advisory_xact_lock(hashtext(%s))', ('strategy-stage:strategy',)), raw.sql[1])
                self.assertEqual(('execution_strategy_stage_revisions', 'strategy',
                                  (other if case == 'stale' else first).revision_id), raw.sql[2][1])
                self.assertEqual(3 if case == 'same' else (5 if case in ('fresh', 'commit_error') else 4), len(raw.sql))
                if case != 'same':
                    self.assertEqual(('execution_strategy_stage_revisions', 'strategy'), raw.sql[3][1])
                    self.assertIn('ORDER BY updated_at DESC,owner,document_key LIMIT 10000', raw.sql[3][0])
                if case in ('fresh', 'commit_error'):
                    self.assertIn('ON CONFLICT(collection,owner,document_key) DO NOTHING RETURNING document_json', raw.sql[4][0])
                    self.assertEqual(first.to_dict(), json.loads(raw.sql[4][1][-1]))
                    self.assertEqual(NOW.timestamp(), raw.sql[4][1][-2])
                self.assertEqual(1 if case != 'stale' else 0, raw.commits)
                self.assertEqual(1 if case == 'stale' else 0, raw.rollbacks)
                self.assertEqual(0 if case == 'commit_error' else 1, raw.closes)


if __name__ == "__main__":
    unittest.main()
