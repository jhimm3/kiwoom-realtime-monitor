from __future__ import annotations

import hashlib
import asyncio
import threading
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from kiwoom_monitor.application.breakout_strategy import StrategyState, default_shadow_breakout_config
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.mock_automation_candidate import MockAutomationCandidatePackage
from kiwoom_monitor.application.research_execution import (
    COST_MODEL_VERSION,
    EXECUTION_MODEL_VERSION,
    SAME_BAR_PATH_VERSION,
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner, _checkpoint_bar_document
from kiwoom_monitor.central_server.database_observation_readers import (
    OBSERVATION_DELIVERY_PROTOCOL, ObservationRevisionPage,
)
from unittest.mock import patch
from kiwoom_monitor.domain.execution_activation import (
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    AccountExecutionEvent,
    AccountExecutionEventPage,
)
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)
ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _package() -> MockAutomationCandidatePackage:
    execution = SimulationExecutionConfig(
        EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, 10_000_000,
        SimulationCostModel(
            COST_MODEL_VERSION, 2, 18, 5, "official_period_rule", "fixture",
            "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
        ),
    )
    candidate = {
        "version": "final_candidate/v1",
        "family": BREAKOUT_FAMILY_ID,
        "parameters": default_shadow_breakout_config().to_dict(),
        "execution_model": execution.to_dict(),
        "session_profile": research_session_profile_document("krx-regular/v1"),
        "implementation_hash": "a" * 64,
    }
    evaluation = ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (ResearchFoldSpec(
            "final", "OOS", "2026-09-01T00:00:00+00:00",
            "2026-09-10T00:00:00+00:00",
        ),),
        60, 0, 0, 10, 3,
        final_holdout_accessed_at="2026-09-11T00:00:00+00:00",
        final_holdout_access_reason="locked final evaluation",
    )
    return MockAutomationCandidatePackage(
        strategy_ref="strategy-1", candidate_spec=candidate,
        candidate_spec_hash=_hash(candidate), scientific_implementation_hash="a" * 64,
        source_final_batch_id="batch-1", source_final_run_id="run-1",
        source_final_result_hash="b" * 64,
        source_run_started_at=NOW, source_run_finished_at=NOW + timedelta(minutes=1),
        evaluation_evidence={
            "report_id": "report-1", "report_status": "ELIGIBLE",
            "evaluation_spec": evaluation.to_dict(),
            "oos_fold": {
                "role": "OOS", "status": "ELIGIBLE", "closed_trade_count": 12,
                "active_day_count": 4, "net_realized_pnl_won": 120_000,
                "max_drawdown_ppm": 50_000,
            },
        },
    )


class _Events:
    def __init__(self):
        self.values = []

    def account_events(self, environment, account_ref, *, after_sequence, limit):
        values = tuple(value for value in self.values if value.accepted_sequence > after_sequence)[:limit]
        cursor = values[-1].accepted_sequence if values else after_sequence
        return AccountExecutionEventPage(values, cursor, False)

    def active_intents(self, environment, account_ref, run_id):
        return ()


CHECKPOINT_COLLECTION = "execution_mock_automation_runner_current"


def inspect_unfilled_preflight_checkpoint(database_path, spec_path):
    """Offline child-process restore: no gateway, transport or credentials."""
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    spec = mock_automation_spec_from_dict(json.loads(Path(spec_path).read_text(encoding='utf-8')))
    store = SQLiteQueryStore(Path(database_path)); store.initialize()
    try:
        row = store.load_document(CHECKPOINT_COLLECTION, spec.account_scope.account_ref, 'current')['document']
        run_id = row['execution_run_id']
        bundle = SimpleNamespace(account_ref=spec.account_scope.account_ref, run_id=run_id,
                                 repository=ExecutionRepository(store),
                                 runtime=SimpleNamespace(server_now=lambda: NOW),
                                 automation_context=SimpleNamespace(spec_id=spec.spec_id, execution_run_id=run_id))
        runner = MockAutomationRunner(store, bundle, spec)
        receipt = runner.status['unfilled_management_preflight']
        return {'pid': os.getpid(), **receipt,
                'child_exists': bundle.repository.load(receipt['receipt']['reserved_child_intent_id']) is not None}
    finally:
        store.close()


class UnfilledManagementPreflightTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_order_lifecycle import UnfilledOrderManagementTests
        MockAutomationRunnerTests.setUp(self)
        self.order = UnfilledOrderManagementTests()
        self.order.setUp()
        self.addCleanup(self.order.doCleanups)
        self.order.setup_order(account_ref=ACCOUNT_REF, run_id=self.run_id)
        # The actual lifecycle ledger and runner checkpoint share one native DB.
        self.store = self.order.store
        self.store.upsert_documents(
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            [{"owner": self.package.strategy_ref, "key": self.package.package_hash,
              "document": self.package.to_dict()}],
        )
        self.bundle.run_id = self.order.intent.run_id
        self.bundle.repository = self.order.repository
        self.bundle.runtime = SimpleNamespace(server_now=lambda: self.order.now[0])
        self.bundle.automation_context = SimpleNamespace(
            execution_run_id=self.bundle.run_id, spec_id=self.spec.spec_id,
        )
        self.control = SimpleNamespace(
            desired_state=SimpleNamespace(value='RUNNING'), active_spec_id=self.spec.spec_id,
            execution_run_id=self.bundle.run_id, control_revision=1,
        )
        self.runner = self.restart()

    def restart(self):
        runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        runner._repository.load_mock_automation_control = lambda _: self.control
        return runner

    def preview(self, **changes):
        arguments = {key: getattr(self.order, key) for key in
                     ('policy', 'target', 'observation', 'assessment')}
        arguments['broker_snapshot'] = self.order.snapshot
        arguments.update(changes)
        return self.runner.preview_unfilled_management(**arguments)

    def test_confirmed_cancel_partial_fill_checkpoint_restart_keeps_one_preview_slot(self):
        first = self.preview()
        self.assertEqual('CANCEL', first['result']['action'])
        self.assertIsNone(first['child_preview'])
        self.order.cancel_and_reconcile(filled=3)
        second = self.preview()
        self.assertEqual(7, second['child_preview']['quantity'])
        self.assertEqual((0, 1, False), (second['native_reprices_used'], second['reserved_preview_slots'], second['order_authorized']))
        self.assertIsNone(self.order.repository.load(second['reserved_child_intent_id']))
        self.runner = self.restart()
        self.assertTrue(self.runner.status['unfilled_management_preflight']['requires_revalidation'])
        self.assertEqual(second, self.preview())
        with patch.object(self.store, 'upsert_documents', wraps=self.store.upsert_documents) as write:
            self.preview()
            write.assert_not_called()
        self.assertEqual((1, 1), (self.order.transport.submit_calls, self.order.transport.cancel_calls))

    def test_ambiguous_checkpoint_commit_preserves_reservation_identity(self):
        self.order.cancel_and_reconcile(filled=3)
        native = self.store.upsert_documents
        def lost_ack(collection, rows):
            native(collection, rows)
            raise OSError('controlled preflight ACK lost after COMMIT')
        with patch.object(self.store, 'upsert_documents', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                self.preview()
        pending = self.runner._unfilled_preflight
        self.assertTrue(self.runner._checkpoint_pending)
        self.runner = self.restart()
        self.assertEqual(pending, self.preview())
        self.assertEqual(1, self.runner._unfilled_preflight['reserved_preview_slots'])

    def test_native_commit_failure_returns_no_plan_and_retries_same_identity(self):
        self.order.cancel_and_reconcile(filled=3)
        write, evidence = _checkpoint_commit_failures(self.store, 1)
        with patch.object(self.store, 'upsert_documents', side_effect=write):
            with self.assertRaisesRegex(OSError, 'COMMIT failure'):
                self.preview()
            pending = self.runner._unfilled_preflight
            self.assertEqual(pending, self.preview())
        self.assertEqual(2, evidence['attempts'])

    def test_anchor_deadline_retry_budget_and_control_cannot_be_reset_after_restart(self):
        self.order.cancel_and_reconcile(filled=3)
        self.preview()
        self.runner = self.restart()
        for changes in ({'maximum_reprices': 99}, {'wait_until': self.order.policy.wait_until + timedelta(seconds=1)},
                        {'protection_deadline': self.order.policy.protection_deadline + timedelta(seconds=1)}):
            with self.assertRaisesRegex(ValueError, 'anchor_or_control_changed'):
                self.preview(policy=replace(self.order.policy, **changes))
        with self.assertRaisesRegex(ValueError, 'native_child_attempt_projection_required'):
            self.preview(assessment=replace(self.order.assessment, reprices_used=1))
        self.control = SimpleNamespace(**{**vars(self.control), 'control_revision': 2})
        with self.assertRaisesRegex(ValueError, 'anchor_or_control_changed'):
            self.preview()

    def test_native_change_during_save_returns_no_acknowledged_plan(self):
        native = self.store.upsert_documents
        def racing_fill(collection, rows):
            native(collection, rows)
            self.order.cancel_and_reconcile(filled=3)
        with patch.object(self.store, 'upsert_documents', side_effect=racing_fill):
            with self.assertRaisesRegex(ValueError, 'native_state_changed'):
                self.preview()
        receipt = self.preview()
        self.assertEqual(7, receipt['child_preview']['quantity'])

    def test_control_stop_during_commit_and_after_restart_never_returns_plan(self):
        native = self.store.upsert_documents
        def stop_during_save(collection, rows):
            native(collection, rows)
            self.control = SimpleNamespace(**{**vars(self.control), 'desired_state': SimpleNamespace(value='STOPPED')})
        with patch.object(self.store, 'upsert_documents', side_effect=stop_during_save):
            with self.assertRaisesRegex(ValueError, 'native_state_changed'):
                self.preview()
        self.runner = self.restart()
        with self.assertRaisesRegex(ValueError, 'control_not_running'):
            self.preview()
        self.assertEqual((1, 0), (self.order.transport.submit_calls, self.order.transport.cancel_calls))

    def test_separate_process_restores_native_checkpoint_without_creating_child_order(self):
        self.order.cancel_and_reconcile(filled=3)
        receipt = self.preview()
        root = Path(__file__).resolve().parents[2]
        command = (
            'import json,sys; from tests.unit.test_mock_automation_runner import inspect_unfilled_preflight_checkpoint; '
            'print(json.dumps(inspect_unfilled_preflight_checkpoint(sys.argv[1],sys.argv[2])))'
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'preflight.sqlite3'
            spec = Path(directory) / 'spec.json'
            spec.write_text(json.dumps(self.spec.to_dict()), encoding='utf-8')
            with self.store._connection() as source, closing(sqlite3.connect(database)) as target:
                source.backup(target)
            environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(root), str(root / 'src'), str(root / 'tests/unit')))}
            result = subprocess.run([sys.executable, '-c', command, str(database), str(spec)],
                                    cwd=root, env=environment, capture_output=True, text=True,
                                    encoding='utf-8', timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            restored = json.loads(result.stdout)
            self.assertNotEqual(os.getpid(), restored['pid'])
            self.assertEqual(receipt, restored['receipt'])
            self.assertTrue(restored['requires_revalidation'])
            self.assertFalse(restored['child_exists'])
            self.assertFalse(restored['order_authorized'])

    def test_unknown_cancel_ack_and_child_collision_never_grant_replacement(self):
        self.order.record = self.order.lifecycle.cancel(self.order.intent.intent_id)
        receipt = self.preview()
        self.assertEqual(('WAIT', None), (receipt['result']['action'], receipt['child_preview']))
        self.order.cancel_and_reconcile(filled=3)
        receipt = self.preview()
        self.order.repository.create(replace(self.order.intent, intent_id=receipt['reserved_child_intent_id']))
        self.runner = self.restart()
        with self.assertRaisesRegex(ValueError, 'native_child_requires_reconciliation'):
            self.preview()
        self.assertEqual(1, self.order.transport.submit_calls)

    def test_corrupt_persisted_receipt_fails_closed_without_bootstrap_budget_reset(self):
        self.order.cancel_and_reconcile(filled=3)
        self.preview()
        row = self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, 'current')
        row['document']['unfilled_management_preflight']['reserved_preview_slots'] = 0
        self.store.upsert_documents(CHECKPOINT_COLLECTION, [row])
        with self.assertRaisesRegex(ValueError, 'integrity_mismatch'):
            self.restart()

    def test_preflight_input_budget_failure_preserves_previous_acknowledged_checkpoint(self):
        first = self.preview()
        with self.assertRaisesRegex(ValueError, 'receipt_budget_exceeded'):
            self.preview(assessment=replace(self.order.assessment, thesis_input_ref='x' * (1024 * 1024)))
        self.runner = self.restart()
        self.assertEqual(first, self.runner._unfilled_preflight)

    def test_protection_clock_hint_is_independent_of_prices_and_never_synthetic_exit(self):
        self.order.setup_order(side='SELL', purpose='PROTECTIVE_EXIT', account_ref=ACCOUNT_REF, run_id=self.run_id)
        self.bundle.repository = self.order.repository
        self.store = self.order.store
        self.store.upsert_documents(
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            [{"owner": self.package.strategy_ref, "key": self.package.package_hash, "document": self.package.to_dict()}],
        )
        self.runner = self.restart()
        self.preview(assessment=replace(self.order.assessment, reprice_evidence=None))
        self.order.now[0] = self.order.policy.protection_deadline
        self.runner._bars = {}
        clock = self.runner.status['unfilled_management_clock']
        self.assertTrue(clock['unfilled_management_due'])
        self.assertTrue(clock['unfilled_risk_path_required'])
        self.assertFalse(clock['exit_required'])
        self.assertFalse(clock['unfilled_order_authorized'])
        self.assertEqual((1, 0), (self.order.transport.submit_calls, self.order.transport.cancel_calls))

    def test_running_closing_stale_and_concurrent_preview_are_rejected(self):
        self.runner._task = object()
        with self.assertRaisesRegex(RuntimeError, 'idle_ready_runner'):
            self.preview()
        self.runner._task = None
        self.runner._unfilled_preflight_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'already_in_progress'):
                self.preview()
        finally:
            self.runner._unfilled_preflight_lock.release()
        self.order.now[0] += timedelta(seconds=21)
        with self.assertRaisesRegex(ValueError, 'cut_stale_or_future'):
            self.preview()


class _FailedCheckpointCommit:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        raise OSError("controlled checkpoint COMMIT failure")


def _checkpoint_commit_failures(store, count):
    native_write, native_connect = store.upsert_documents, store._connect
    evidence = {"remaining": count, "attempts": 0}

    def write(collection, values):
        if collection == CHECKPOINT_COLLECTION:
            evidence["attempts"] += 1
            if evidence["remaining"]:
                evidence["remaining"] -= 1
                with patch.object(store, "_connect", side_effect=lambda: _FailedCheckpointCommit(native_connect())):
                    return native_write(collection, values)
        return native_write(collection, values)
    return write, evidence


def _checkpoint_bar_fields():
    return {
        "revision_id": "bar-revision", "observation_key": "bar-key", "code": "005930",
        "bar_start": "2026-10-21T09:00:00+09:00", "bar_end": "2026-10-21T09:01:00+09:00",
        "available_at": "2026-10-21T09:01:01+09:00", "open": 10000, "high": 10020,
        "low": 9990, "close": 10010, "volume": 100, "trade_value_million_won": 1,
        "session_finalized": False, "capture_quality": "complete", "finalization_source": "native",
        "session_profile": "krx-regular/v1", "research_session": "2026-10-21",
        "market_session": "REGULAR", "market_phase": "CONTINUOUS", "schedule_version": "fixture-v1",
    }


class CheckpointBarDocumentTests(unittest.TestCase):
    def test_declared_fields_subclasses_slots_and_none_preserve_checkpoint_values(self):
        from dataclasses import dataclass, make_dataclass

        expected = _checkpoint_bar_fields()
        normal = KrxMinuteBarFrame(**expected)
        object.__setattr__(normal, "diagnostic_extra", {"ignored": []})

        class Subclass(KrxMinuteBarFrame):
            pass

        @dataclass(frozen=True)
        class Added(KrxMinuteBarFrame):
            added: int = 9

        class EqualAtomic(type):
            __hash__ = type.__hash__

            def __eq__(cls, other):
                return other is str

        class MutablePretender(metaclass=EqualAtomic):
            def __init__(self):
                self.values = ["before"]

            def __deepcopy__(self, memo):
                return {"copied": list(self.values)}

        pretender = MutablePretender()
        slots_type = make_dataclass("SlotsBar", [(name, object) for name in expected], frozen=True, slots=True)
        cases = (
            (normal, expected), (Subclass(**expected), expected),
            (Added(**expected), {**expected, "added": 9}), (slots_type(**expected), expected),
            (KrxMinuteBarFrame(**{**expected, "capture_quality": None}), {**expected, "capture_quality": None}),
            (KrxMinuteBarFrame(**{**expected, "capture_quality": pretender}),
             {**expected, "capture_quality": {"copied": ["before"]}}),
        )
        for frame, document in cases:
            with self.subTest(frame_type=type(frame).__name__, quality=document["capture_quality"]):
                self.assertEqual(document, _checkpoint_bar_document(frame))
        with self.assertRaisesRegex(TypeError, "asdict.*dataclass instances"):
            _checkpoint_bar_document(None)


class MockAutomationRunnerTests(unittest.TestCase):
    def test_each_prefix_ack_preserves_bar_fields_and_detaches_restored_mutable_values(self):
        runner = MockAutomationRunner(self.store, self.bundle, self.spec, now_provider=lambda: NOW)
        normal_fields = _checkpoint_bar_fields()
        normal = KrxMinuteBarFrame(**normal_fields)
        object.__setattr__(normal, "diagnostic_extra", {"ignored": []})
        restored_fields = {**normal_fields, "revision_id": "restored-revision", "observation_key": "restored-key",
                           "capture_quality": ["restored-value"]}
        restored = KrxMinuteBarFrame(**json.loads(json.dumps(restored_fields)))
        runner._bars = {(normal.code, normal.observation_key): normal,
                        (restored.code, restored.observation_key): restored}
        initial_cursor = runner._cursor
        with patch.object(self.store, "upsert_documents", wraps=self.store.upsert_documents) as write:
            for prefix in range(1, 21):
                runner._cursor = initial_cursor + prefix
                runner._save_checkpoint()
                self.assertFalse(runner._checkpoint_pending)
            self.assertEqual(20, write.call_count)
            saved = self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
            self.assertEqual([normal_fields, restored_fields], saved["bars"])
            self.assertEqual(initial_cursor + 20, saved["input_cursor"])
            runner._save_checkpoint()
            self.assertEqual(20, write.call_count)
            acknowledged = runner._last_checkpoint_content
            restored.capture_quality.append("changed-after-ack")
            self.assertEqual(["restored-value"], acknowledged["bars"][1]["capture_quality"])
            runner._save_checkpoint()
            self.assertEqual(21, write.call_count)
            runner._save_checkpoint(force=True)
            self.assertEqual(22, write.call_count)
        saved = self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
        self.assertEqual(["restored-value", "changed-after-ack"], saved["bars"][1]["capture_quality"])

    def test_checkpoint_noop_preserves_document_and_forced_close_boundary_can_write(self):
        runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        before = self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")
        revision = runner._checkpoint_revision
        with patch.object(self.store, "upsert_documents", wraps=self.store.upsert_documents) as write:
            runner._save_checkpoint()
            write.assert_not_called()
            self.assertEqual(before, self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current"))
            self.assertEqual(revision, runner._checkpoint_revision)
            runner._save_checkpoint(force=True)
            self.assertEqual(1, write.call_count)
            self.assertEqual(revision + 1, runner._checkpoint_revision)
            runner._status["reason"] = "changed_same_owner_status"
            runner._save_checkpoint()
            self.assertEqual(2, write.call_count)
        saved = self.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
        self.assertEqual("changed_same_owner_status", saved["status"]["reason"])

    def test_supervisor_propagates_native_checkpoint_failure_and_retains_failed_owner(self):
        import asyncio
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor

        async def exercise():
            runner = MockAutomationRunner(self.store, self.bundle, self.spec)
            before = self.store.load_documents("execution_mock_automation_runner_current")
            runner._bootstrap_pending = False  # Exercise the existing ready-runner final checkpoint.
            runner._task = asyncio.create_task(asyncio.Event().wait())
            supervisor = MockAutomationSupervisor(self.store, SimpleNamespace())
            supervisor._runners[ACCOUNT_REF] = runner
            failure = OSError("controlled checkpoint failure")
            with patch.object(self.store, "upsert_documents", side_effect=failure) as write:
                with self.assertRaises(OSError) as first:
                    await supervisor.close()
                with self.assertRaises(OSError) as second:
                    await supervisor.close()
                self.assertIs(failure, first.exception)
                self.assertIs(first.exception, second.exception)
                self.assertEqual(1, write.call_count)
                self.assertIs(runner, supervisor._runners[ACCOUNT_REF])
            self.assertEqual(before, self.store.load_documents("execution_mock_automation_runner_current"))
        asyncio.run(exercise())

    def test_supervisor_repeat_close_joins_actual_checkpoint_after_waiter_cancellation(self):
        import asyncio
        import threading
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor

        async def exercise():
            runner = MockAutomationRunner(self.store, self.bundle, self.spec)
            runner._bootstrap_pending = False
            runner._task = asyncio.create_task(asyncio.Event().wait())
            supervisor = MockAutomationSupervisor(self.store, SimpleNamespace())
            supervisor._runners[ACCOUNT_REF] = runner
            entered, release, committed = threading.Event(), threading.Event(), threading.Event()
            native_write = self.store.upsert_documents
            def blocked_write(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test checkpoint not released")
                native_write(*args, **kwargs)
                committed.set()
            tasks = []
            try:
                with patch.object(self.store, "upsert_documents", side_effect=blocked_write) as write:
                    first = asyncio.create_task(supervisor.close())
                    tasks.append(first)
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    joined = asyncio.create_task(supervisor.close())
                    tasks.append(joined)
                    for _ in range(3):
                        await asyncio.sleep(0)
                    self.assertFalse(joined.done(), "repeat close returned while checkpoint thread was writing")
                    self.assertFalse(committed.is_set())
                    release.set()
                    await asyncio.wait_for(joined, 2)
                    await supervisor.close()
                    self.assertTrue(committed.is_set())
                    self.assertEqual(1, write.call_count)
                    self.assertEqual({}, supervisor._runners)
                    document = self.store.load_documents("execution_mock_automation_runner_current")[0]["document"]
                    self.assertEqual(self.run_id, document["execution_run_id"])
                    self.assertEqual(self.spec.spec_id, document["spec_id"])
                    self.assertEqual(0, document["input_cursor"])
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(exercise())

    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.addCleanup(self.store.close)
        self.package = _package()
        self.store.upsert_documents(
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            [{"owner": self.package.strategy_ref, "key": self.package.package_hash,
              "document": self.package.to_dict()}],
        )
        self.run_id = "mock_auto_run_" + "c" * 64
        self.spec = MockAutomationOperatingSpec(
            strategy_ref=self.package.strategy_ref,
            candidate_package_hash=self.package.package_hash,
            final_result_hash=self.package.source_final_result_hash,
            final_batch_id=self.package.source_final_batch_id,
            final_run_id=self.package.source_final_run_id,
            forward_profile_id="profile-1",
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            credential_profile_id="profile-a", binding_revision=1,
            binding_verified_at=NOW, frozen_at=NOW,
            shadow_evidence_ref="shadow-1", maximum_concurrent_strategies=1,
            maximum_concurrent_positions=1, maximum_capital_won=1_000_000,
            maximum_daily_loss_won=100_000, maximum_data_gap_seconds=5,
            maximum_submission_unknown_count=0, maximum_reconnect_count=1,
            maximum_balance_mismatch_count=0, supported_venue="KRX",
            session_profile="krx-regular/v1", data_path="nas",
            candidate_transition_policy=MOCK_CANDIDATE_TRANSITION_POLICY,
            stop_policy=MOCK_STOP_POLICY, recovery_policy=MOCK_RECOVERY_POLICY,
        )
        self.events = _Events()
        self.bundle = SimpleNamespace(
            account_ref=ACCOUNT_REF, run_id=self.run_id,
            automation_context=SimpleNamespace(
                execution_run_id=self.run_id, spec_id=self.spec.spec_id,
            ),
            repository=self.events,
        )

    def event(self, sequence, execution_id, side, quantity, price):
        return AccountExecutionEvent(
            accepted_sequence=sequence, source_event_id=f"event-{sequence}",
            intent_id=f"intent-{sequence}", run_id=self.run_id,
            decision_id=f"decision-{sequence}", account_ref=ACCOUNT_REF,
            environment="mock", symbol="005930", venue="KRX", side=side,
            event_type="FILL", state="FILLED", occurred_at=NOW + timedelta(seconds=sequence),
            received_at=NOW + timedelta(seconds=sequence), broker_execution_id=execution_id,
            quantity=quantity, price=price,
        )

    def test_checkpoint_restores_fill_state_and_duplicate_fill_is_not_reapplied(self):
        runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        runner._state = StrategyState(
            status="candidate", symbol="005930", candidate_key="candidate-1",
            candidate_expires_at=(NOW + timedelta(minutes=1)).isoformat(),
            emitted_candidate_keys=("candidate-1",),
        )
        self.events.values.append(self.event(1, "fill-1", "BUY", 2, 10_000))
        runner._apply_new_fills()
        self.assertEqual("open", runner._state.status)
        self.assertEqual(2, runner._state.position_quantity)
        runner._save_checkpoint()

        restarted = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual("open", restarted._state.status)
        self.assertEqual(1, restarted._fill_cursor)
        self.events.values.append(self.event(2, "fill-1", "BUY", 2, 10_000))
        restarted._apply_new_fills()
        self.assertEqual(2, restarted._state.position_quantity)
        self.events.values.append(self.event(3, "fill-2", "SELL", 2, 10_500))
        restarted._apply_new_fills()
        self.assertEqual("cooldown", restarted._state.status)
        self.assertEqual(3, restarted._fill_cursor)

    def test_checkpoint_from_other_spec_is_never_reused(self):
        first = MockAutomationRunner(self.store, self.bundle, self.spec)
        first._cursor = 99
        first._save_checkpoint()
        changed = SimpleNamespace(**{
            **self.bundle.__dict__,
            "automation_context": SimpleNamespace(
                execution_run_id=self.run_id, spec_id="different-spec",
            ),
        })
        with self.assertRaisesRegex(ValueError, "CONTEXT_MISMATCH"):
            MockAutomationRunner(self.store, changed, self.spec)


class MockAutomationRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_iteration_excludes_preflight_until_native_work_is_drained(self):
        case, runner = self.checkpoint_case()
        entered, release = threading.Event(), threading.Event()
        native = runner._apply_new_fills
        def blocked_fill_read():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('controlled native read not drained')
            return native()
        runner._repository.load_mock_automation_control = lambda _: None
        pending = None
        try:
            with patch.object(runner, '_apply_new_fills', side_effect=blocked_fill_read):
                pending = asyncio.create_task(runner.run_once())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                with self.assertRaisesRegex(RuntimeError, 'already_in_progress'):
                    runner.preview_unfilled_management(policy=None, broker_snapshot=None, target=None,
                                                       observation=None, assessment=None)
                release.set()
                self.assertEqual(0, await asyncio.wait_for(pending, 2))
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
        self.assertFalse(runner._unfilled_preflight_lock.locked())

    def checkpoint_case(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.doCleanups)
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=case.spec.spec_id, execution_run_id=case.run_id,
            desired_state=SimpleNamespace(value="RUNNING"),
        )
        return case, runner

    async def test_worker_retries_two_failed_native_commits_without_new_observations(self):
        from tests.unit.test_candidate_monitor import _rank

        case, runner = self.checkpoint_case()
        rows = [_rank(1)]
        case.store.load_observation_revisions_after = lambda cursor, *_args: [
            row for row in rows if row["accepted_sequence"] > cursor]
        native_poll, completed, polls = runner.run_once, asyncio.Event(), []

        async def poll():
            try:
                return await native_poll()
            finally:
                polls.append(1)
                if len(polls) >= 3:
                    completed.set()

        runner.run_once, runner._poll_seconds = poll, 0.01
        write, evidence = _checkpoint_commit_failures(case.store, 2)
        with patch.object(case.store, "upsert_documents", side_effect=write):
            runner._task = asyncio.create_task(runner._run())
            try:
                await asyncio.wait_for(completed.wait(), 2)
                document = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
                self.assertEqual(1, document["input_cursor"], evidence)
                self.assertEqual(1, runner._cursor)
                self.assertFalse(runner._checkpoint_pending)
                self.assertEqual([], case.events.values)
            finally:
                await runner.close()

    async def test_failed_stop_or_block_checkpoint_retries_even_when_status_is_unchanged(self):
        for state in ("STOPPED", "BLOCKED"):
            with self.subTest(state=state):
                case, runner = self.checkpoint_case()
                control = None if state == "BLOCKED" else SimpleNamespace(
                    active_spec_id=case.spec.spec_id, execution_run_id=case.run_id,
                    desired_state=SimpleNamespace(value="STOPPED"),
                )
                runner._repository.load_mock_automation_control = lambda _account: control
                before = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")
                write, evidence = _checkpoint_commit_failures(case.store, 1)
                with patch.object(case.store, "upsert_documents", side_effect=write):
                    with self.assertRaisesRegex(OSError, "checkpoint COMMIT failure"):
                        await runner.run_once()
                    self.assertEqual(before, case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current"))
                    self.assertEqual(0, await runner.run_once())
                document = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
                self.assertEqual(state, document["status"]["state"], evidence)
                self.assertFalse(runner._checkpoint_pending)
                self.assertEqual(0, runner._cursor)

    async def test_commit_ack_loss_retries_prefix_without_consuming_the_input_again(self):
        from tests.unit.test_candidate_monitor import _rank

        case, runner = self.checkpoint_case()
        case.store.load_observation_revisions_after = lambda cursor, *_args: [_rank(1)] if cursor == 0 else []
        native_write, attempts = case.store.upsert_documents, []

        def lose_first_ack(collection, values):
            native_write(collection, values)
            if collection == CHECKPOINT_COLLECTION:
                attempts.append(1)
                if len(attempts) == 1:
                    raise OSError("controlled checkpoint ACK loss after COMMIT")

        with patch.object(case.store, "upsert_documents", side_effect=lose_first_ack), patch.object(
            runner, "_consume", wraps=runner._consume,
        ) as consume:
            with self.assertRaisesRegex(OSError, "ACK loss after COMMIT"):
                await runner.run_once()
            committed = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
            self.assertEqual(1, committed["input_cursor"])
            self.assertTrue(runner._checkpoint_pending)
            self.assertEqual(0, await runner.run_once())
            self.assertEqual(1, consume.await_count)
            self.assertEqual(2, len(attempts))
        acknowledged = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
        self.assertGreater(acknowledged["checkpoint_revision"], committed["checkpoint_revision"])
        self.assertEqual({k: v for k, v in committed.items() if k not in {"checkpoint_revision", "saved_at"}},
                         {k: v for k, v in acknowledged.items() if k not in {"checkpoint_revision", "saved_at"}})
        self.assertFalse(runner._checkpoint_pending)
        restarted = MockAutomationRunner(case.store, case.bundle, case.spec)
        self.assertEqual(1, restarted._cursor)
        self.assertEqual(runner._state, restarted._state)

    async def test_failed_prefix_retry_blocks_later_observations_until_ack(self):
        from tests.unit.test_candidate_monitor import _rank

        case, runner = self.checkpoint_case()
        rows, loaded_after = [_rank(1)], []

        def load(cursor, *_args):
            loaded_after.append(cursor)
            return [row for row in rows if row["accepted_sequence"] > cursor]

        case.store.load_observation_revisions_after = load
        write, evidence = _checkpoint_commit_failures(case.store, 2)
        with patch.object(case.store, "upsert_documents", side_effect=write), patch.object(
            runner, "_consume", wraps=runner._consume,
        ) as consume:
            with self.assertRaises(OSError):
                await runner.run_once()
            rows.append(_rank(2))
            with self.assertRaises(OSError):
                await runner.run_once()
            self.assertEqual([0], loaded_after, evidence)
            self.assertEqual(1, consume.await_count)
            self.assertEqual(1, runner._cursor)
            self.assertEqual(1, await runner.run_once())
            self.assertEqual([0, 1], loaded_after)
            self.assertEqual(2, consume.await_count)
        self.assertEqual(2, case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]["input_cursor"])

    async def test_close_joins_started_checkpoint_before_forced_final_write(self):
        await self.exercise_close_during_checkpoint()

    async def test_close_joins_bootstrap_checkpoint_before_forced_final_write(self):
        await self.exercise_close_during_checkpoint(bootstrap=True)

    async def test_close_joins_fill_state_read_before_final_checkpoint(self):
        case, runner = self.checkpoint_case()
        case.events.values.append(case.event(1, "close-fill", "BUY", 2, 10_000))
        entered, release, read_returned = (threading.Event() for _ in range(3))
        native_read = case.events.account_events

        def blocked_read(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("controlled fill read not released")
            result = native_read(*args, **kwargs)
            read_returned.set()
            return result

        closing = None
        try:
            with patch.object(case.events, "account_events", side_effect=blocked_read):
                runner._task = asyncio.create_task(runner._run())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                closing = asyncio.create_task(runner.close())
                await asyncio.sleep(0.05)
                self.assertFalse(closing.done(), "close completed while checkpoint fill-state input was still owned")
                release.set()
                await asyncio.wait_for(closing, 2)
                saved = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
                self.assertEqual(1, saved["fill_cursor"])
                self.assertEqual("open", saved["strategy_state"]["status"])
                self.assertEqual(2, saved["strategy_state"]["position_quantity"])
        finally:
            release.set()
            await asyncio.to_thread(read_returned.wait, 2)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runner.close()

    async def exercise_close_during_checkpoint(self, *, bootstrap=False):
        from tests.unit.test_candidate_monitor import _rank

        case, runner = self.checkpoint_case()
        if bootstrap:
            runner._bootstrap_pending = True
            case.store.load_observation_bootstrap = lambda *_args, **_kwargs: ObservationRevisionPage(
                rows=(_rank(1),), safe_through=1,
            )
        case.store.load_observation_revisions_after = lambda cursor, *_args: [_rank(1)] if cursor == 0 else []
        entered, release, first_ack, second_started = (threading.Event() for _ in range(4))
        native_write, calls, lock = case.store.upsert_documents, [], threading.Lock()

        def write(collection, values):
            with lock:
                calls.append(1)
                ordinal = len(calls)
            if ordinal == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("controlled checkpoint not released")
                native_write(collection, values)
                first_ack.set()
            else:
                second_started.set()
                native_write(collection, values)

        closing = None
        try:
            with patch.object(case.store, "upsert_documents", side_effect=write):
                runner._task = asyncio.create_task(runner._run())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                closing = asyncio.create_task(runner.close())
                self.assertFalse(await asyncio.to_thread(second_started.wait, 0.2),
                                 "final checkpoint started while prefix write was owned: "
                                 f"close_done={closing.done()}, first_ack={first_ack.is_set()}")
                self.assertFalse(closing.done())
                release.set()
                await asyncio.wait_for(closing, 2)
                self.assertTrue(first_ack.is_set())
                self.assertTrue(second_started.is_set())
                document = case.store.load_document(CHECKPOINT_COLLECTION, ACCOUNT_REF, "current")["document"]
                self.assertEqual(runner._checkpoint_revision, document["checkpoint_revision"])
                self.assertEqual(1, document["input_cursor"])
        finally:
            release.set()
            await asyncio.to_thread(first_ack.wait, 2)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runner.close()

    async def test_pending_bootstrap_preserves_fills_and_never_dispatches_history(self):
        from tests.unit.test_candidate_monitor import _rank, _bar
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        pending = ObservationRevisionPage(ready=False, reason="pending_sequence_commit")
        with patch.object(case.store, "load_observation_bootstrap", return_value=pending) as loader:
            runner = MockAutomationRunner(case.store, case.bundle, case.spec)
            runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                active_spec_id=case.spec.spec_id, desired_state=SimpleNamespace(value="RUNNING"))
            case.events.values.append(case.event(1, "fill-1", "BUY", 2, 10000))
            self.assertEqual(0, await runner.run_once())
            self.assertEqual(1, runner._fill_cursor)
            self.assertEqual(2, runner._state.position_quantity)
            runner._save_checkpoint()
            self.assertEqual([], case.store.load_documents(
                "execution_mock_automation_runner_current", ACCOUNT_REF, 1))
            loader.return_value = ObservationRevisionPage(
                rows=(_rank(1), _bar(2, 0, 1000, 1010)), safe_through=999)
            with patch.object(runner, "_consume", side_effect=AssertionError("historical dispatch")):
                self.assertEqual(0, await runner.run_once())
            saved = case.store.load_documents(
                "execution_mock_automation_runner_current", ACCOUNT_REF, 1)[0]["document"]
            self.assertEqual(2, saved["input_cursor"])
            self.assertEqual(1, saved["fill_cursor"])
            self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved["delivery_protocol"])
            restarted = MockAutomationRunner(case.store, case.bundle, case.spec)
            self.assertEqual(1, restarted._fill_cursor)
            self.assertEqual(2, restarted._state.position_quantity)
            self.assertEqual(2, restarted._cursor)
            self.assertEqual(3, loader.call_count)

    async def test_stopped_pending_runner_does_not_bootstrap_or_write_ready_checkpoint(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        with patch.object(case.store, "load_observation_bootstrap",
                          return_value=ObservationRevisionPage(ready=False)) as loader:
            runner = MockAutomationRunner(case.store, case.bundle, case.spec)
            runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                active_spec_id=case.spec.spec_id, desired_state=SimpleNamespace(value="STOPPED"))
            self.assertEqual(0, await runner.run_once())
            self.assertEqual(1, loader.call_count)
            self.assertEqual("STOPPED", runner.status["state"])
            self.assertEqual(0, runner._checkpoint_revision)
            self.assertEqual([], case.store.load_documents(
                "execution_mock_automation_runner_current", ACCOUNT_REF, 1))

    async def test_failed_bootstrap_save_retries_seed_before_any_strategy_processing(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        with patch.object(case.store, "load_observation_bootstrap",
                          return_value=ObservationRevisionPage(ready=False)) as loader:
            runner = MockAutomationRunner(case.store, case.bundle, case.spec)
            runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                active_spec_id=case.spec.spec_id, desired_state=SimpleNamespace(value="RUNNING"))
            loader.return_value = ObservationRevisionPage()
            with patch.object(case.store, "upsert_documents", side_effect=OSError("seed save failed")):
                with self.assertRaisesRegex(OSError, "seed save failed"):
                    await runner.run_once()
            self.assertTrue(runner._bootstrap_pending)
            self.assertEqual(0, await runner.run_once())
            self.assertFalse(runner._bootstrap_pending)
            saved = case.store.load_documents(
                "execution_mock_automation_runner_current", ACCOUNT_REF, 1)[0]["document"]
            self.assertEqual(0, saved["input_cursor"])
            self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved["delivery_protocol"])

    async def test_stop_restart_resume_consumes_only_unseen_observations(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        control_state = {"value": "RUNNING"}
        observations = [
            {"kind": "top20_membership", "accepted_sequence": sequence}
            for sequence in (1, 2, 3)
        ]
        loaded_after = []
        consumed = []

        def configure(runner):
            runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                active_spec_id=case.spec.spec_id,
                desired_state=SimpleNamespace(value=control_state["value"]),
            )

            async def consume(observation):
                consumed.append(observation["accepted_sequence"])

            runner._consume = consume

        def load_after(cursor, _kinds, _limit):
            loaded_after.append(cursor)
            return [value for value in observations if value["accepted_sequence"] > cursor]

        case.store.load_observation_revisions_after = load_after
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        configure(runner)
        observations[:] = observations[:2]
        self.assertEqual(2, await runner.run_once())
        self.assertEqual([1, 2], consumed)
        self.assertEqual(2, runner.status["input_cursor"])

        control_state["value"] = "STOPPED"
        observations.append({"kind": "top20_membership", "accepted_sequence": 3})
        reads_before_stop = len(loaded_after)
        self.assertEqual(0, await runner.run_once())
        self.assertEqual(reads_before_stop, len(loaded_after))
        self.assertEqual([1, 2], consumed)

        restarted = MockAutomationRunner(case.store, case.bundle, case.spec)
        configure(restarted)
        self.assertEqual("STOPPED", restarted.status["state"])
        self.assertEqual(2, restarted.status["input_cursor"])
        self.assertEqual(0, await restarted.run_once())
        self.assertEqual(reads_before_stop, len(loaded_after))

        control_state["value"] = "RUNNING"
        self.assertEqual(1, await restarted.run_once())
        self.assertEqual(2, loaded_after[-1])
        self.assertEqual([1, 2, 3], consumed)
        self.assertEqual(3, restarted.status["input_cursor"])
        self.assertEqual(0, await restarted.run_once())
        self.assertEqual([1, 2, 3], consumed)

    async def test_idle_blocked_runner_does_not_rewrite_checkpoint_each_poll(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        await runner.run_once()
        revision = runner._checkpoint_revision
        await runner.run_once()
        self.assertEqual(revision, runner._checkpoint_revision)

    async def test_status_measures_observation_age_without_idle_checkpoint_writes(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=case.spec.spec_id,
            desired_state=SimpleNamespace(value="RUNNING"),
        )
        observation = {
            "kind": "minute_bar", "accepted_sequence": 1,
            "available_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        }
        case.store.load_observation_revisions_after = lambda *_args: [observation]

        async def consume(_observation):
            return None

        runner._consume = consume
        self.assertEqual(1, await runner.run_once())
        latency = runner.status["latency"]
        self.assertEqual(1, latency["observation_count"])
        self.assertEqual(1, latency["minute_bar_count"])
        self.assertGreaterEqual(latency["last_bar_age_ms"], 2000)
        self.assertEqual(0, latency["future_or_invalid_bar_time_count"])
        self.assertGreaterEqual(latency["last_poll_duration_ms"], latency["load_duration_ms"])
        revision = runner._checkpoint_revision
        case.store.load_observation_revisions_after = lambda *_args: []
        self.assertEqual(0, await runner.run_once())
        self.assertEqual(revision, runner._checkpoint_revision)
        self.assertEqual(latency["last_bar_processed_at"], runner.status["latency"]["last_bar_processed_at"])


if __name__ == "__main__":
    unittest.main()
