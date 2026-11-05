"""Selected minute features can stop, recover and be absent independently."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class MinuteStrategyCodeAbsenceTests(unittest.TestCase):
    def test_other_minute_strategy_runs_native_management_without_unselected_adapter_code(self):
        root = Path(__file__).resolve().parents[2]
        cases = (
            (('panic_mock_execution',), 'close_mock_execution',
             'tests.unit.test_close_order_management.CloseCurrentOrderManagementTests.'
             'test_current_buy_reassesses_after_original_intent_expiry_and_cancels_on_context_change'),
            (('close_mock_execution',), 'panic_mock_execution',
             'tests.unit.test_panic_mock_execution.PanicCurrentOrderManagementTests.'
             'test_native_transaction_rechecks_current_context_before_amendment'),
            (('close_mock_execution', 'panic_mock_execution'), '',
             'tests.unit.test_research_reported_results.ReportedResearchResultTests.'
             'test_fresh_server_physically_missing_producer_and_optional_packages'),
            (('close_mock_execution', 'panic_mock_execution'), '',
             'tests.unit.test_central_realtime_collector.CentralRealtimeCollectorTests.'
             'test_parsed_trade_is_published_only_to_matching_client'),
        )
        script = '''
import json, sys, unittest
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root / 'src'), str(root)]
missing, selected, identity = sys.argv[1:]
missing = json.loads(missing)
for name in missing:
    absent = root / 'src/kiwoom_monitor/application' / (name + '.py')
    assert not absent.exists(), absent
from kiwoom_monitor.central_server.minute_plan_management import MinutePlanManagement
for name in missing:
    family = 'panic_rebound/v1' if name == 'panic_mock_execution' else 'close_betting/v1'
    try:
        MinutePlanManagement(None, None, None, None, parameters={}, price_source=None, now_provider=None, family=family)
    except ModuleNotFoundError as error:
        assert error.name == 'kiwoom_monitor.application.'+name, (error.name, name)
    else:
        raise AssertionError('removed selected adapter was accepted: '+name)
suite = unittest.defaultTestLoader.loadTestsFromName(identity)
result = unittest.TextTestRunner(verbosity=1).run(suite)
assert result.testsRun == 1 and result.wasSuccessful()
assert not (result.skipped or result.expectedFailures or result.unexpectedSuccesses)
for name in missing:
    assert 'kiwoom_monitor.application.' + name not in sys.modules
if selected:
    adapter = sys.modules['kiwoom_monitor.application.' + selected]
    assert Path(adapter.__file__).resolve().is_relative_to(root / 'src')
for name, module in tuple(sys.modules.items()):
    if name.startswith(('kiwoom_monitor.', 'tests.')) and getattr(module, '__file__', None):
        assert Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
print('unselected-strategy-absent-native-management-pass')
'''
        for missing, selected, identity in cases:
            with self.subTest(missing=missing, selected=selected), TemporaryDirectory() as directory:
                target = Path(directory)
                for folder in ('src', 'tests', 'scripts'):
                    shutil.copytree(root / folder, target / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                for name in missing:
                    (target / 'src/kiwoom_monitor/application' / (name + '.py')).unlink()
                completed = subprocess.run([sys.executable, '-I', '-B', '-c', script, json.dumps(missing), selected, identity],
                    cwd=target, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn('unselected-strategy-absent-native-management-pass', completed.stdout)

    def test_absent_selected_code_preserves_pending_cancel_late_fill_and_original_plan_on_reopen(self):
        root = Path(__file__).resolve().parents[2]
        seed = '''
import json, sys, unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
root = Path.cwd()
sys.path[:0] = [str(root / 'src'), str(root)]
from tests.unit.test_panic_mock_execution import prepare_panic_native, panic_entry
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
case = unittest.TestCase()
ctx = prepare_panic_native(case, native_factory=lambda path, now: SQLiteQueryStore(root / 'retained.sqlite3', now_provider=now))
try:
    _, buy, _ = panic_entry(ctx)
    assert buy is not None
    ctx.clock[0] += timedelta(seconds=1)
    first = BrokerFill('retained-first', 2, buy.intent.limit_price, ctx.clock[0])
    ctx.runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(buy.broker_order_id, ctx.runtime.account_ref,
        buy.intent.symbol, OrderState.PARTIALLY_FILLED, 2, 2, ctx.clock[0], (first,)))
    with patch.object(ctx.transport, 'cancel', side_effect=SubmissionUnknown('isolated cancel ACK lost')):
        current = ctx.runtime.cancel(buy.intent.intent_id)
    assert (current.state, current.detailed_filled_quantity) == (OrderState.CANCEL_PENDING, 2)
    value = {'intent_id':buy.intent.intent_id, 'account_ref':ctx.runtime.account_ref, 'run_id':ctx.runtime.run_id,
        'order_id':buy.broker_order_id, 'symbol':buy.intent.symbol, 'price':buy.intent.limit_price,
        'plan_id':buy.intent.plan.plan_id, 'policy_ref':buy.intent.plan.policy_ref,
        'first_at':first.occurred_at.isoformat(), 'next_at':(ctx.clock[0]+timedelta(seconds=1)).isoformat(),
        'control':ctx.repository.load_mock_automation_control(ctx.runtime.account_ref).to_dict()}
    (root / 'retained.json').write_text(json.dumps(value), encoding='utf-8')
finally:
    ctx.runtime.stop()
    case.doCleanups()
'''
        recover = '''
import json, sys
from datetime import datetime
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root / 'src'), str(root)]
for name in ('panic_mock_execution', 'close_mock_execution'):
    assert not (root / 'src/kiwoom_monitor/application' / (name+'.py')).exists()
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
data = json.loads((root/'retained.json').read_text(encoding='utf-8'))
now = datetime.fromisoformat(data['next_at'])
class NoOrderTransport:
    def __getattr__(self, name):
        raise AssertionError('missing strategy attempted transport: '+name)
store = SQLiteQueryStore(root/'retained.sqlite3', now_provider=lambda:now)
store.initialize()
execution, repository = ExecutionRepository(store), ForwardEvaluationRepository(store)
runtime = ExecutionRuntime(OrderLifecycle(execution, NoOrderTransport(), now_provider=lambda:now), execution,
    account_ref=data['account_ref'], run_id=data['run_id'], owner_token='isolated-retained-recovery', now_provider=lambda:now)
try:
    original = execution.load(data['intent_id'])
    assert (original.state, original.detailed_filled_quantity) == (OrderState.CANCEL_PENDING, 2)
    runtime.start(new_orders_enabled=False)
    assert runtime._active
    first = BrokerFill('retained-first', 2, data['price'], datetime.fromisoformat(data['first_at']))
    late = BrokerFill('retained-late', 1, data['price'], now)
    snapshot = BrokerOrderSnapshot(data['order_id'], data['account_ref'], data['symbol'],
        OrderState.CANCELLED, 3, 0, now, (first, late))
    current = runtime.reconcile(data['intent_id'], snapshot)
    assert (current.state, current.detailed_filled_quantity) == (OrderState.CANCELLED, 3)
    head = execution.load_plan_position(data['account_ref'], data['run_id'], data['symbol'], original.intent.plan)
    assert (head.quantity, head.plan.plan_id, head.plan.policy_ref) == (3, data['plan_id'], data['policy_ref'])
    events = execution.events(data['intent_id'])
    fills = [(row['broker_execution_id'], row['quantity']) for row in events if row['event_type']=='FILL']
    assert fills == [('retained-first', 2), ('retained-late', 1)], fills
    runtime.reconcile(data['intent_id'], snapshot)
    assert execution.events(data['intent_id']) == events
    assert repository.load_mock_automation_control(data['account_ref']).to_dict() == data['control']
    assert repository.load_panic_entry_evidence(data['account_ref'], data['plan_id']) is not None
    assert not runtime._new_orders_enabled
    for name in ('panic_mock_execution', 'close_mock_execution'):
        assert 'kiwoom_monitor.application.'+name not in sys.modules
    print('removed-strategy-retained-late-fill-recovery-pass')
finally:
    runtime.stop()
    store.close()
'''
        with TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ('src', 'tests', 'scripts'):
                shutil.copytree(root / folder, target / folder,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            completed = subprocess.run([sys.executable, '-I', '-B', '-c', seed], cwd=target,
                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=60)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            for name in ('panic_mock_execution', 'close_mock_execution'):
                (target / 'src/kiwoom_monitor/application' / (name+'.py')).unlink()
            completed = subprocess.run([sys.executable, '-I', '-B', '-c', recover], cwd=target,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn('removed-strategy-retained-late-fill-recovery-pass', completed.stdout)


class LegacyControlResumeScopeTests(unittest.TestCase):
    def test_unscoped_legacy_resume_avoids_lookup_but_rejects_authority_proofs(self):
        from kiwoom_monitor.central_server.database_execution import _require_planned_resume_control
        value = {'account_ref':'af64a3fa-197f-49df-8e8b-65b71bee02d9', 'desired_state':'RUNNING'}
        current = {'desired_state':'STOPPED'}
        clock = Mock(side_effect=AssertionError('unscoped control used a planned-resume clock'))
        for placeholder in ('?', '%s'):
            cursor = Mock()
            cursor.execute.side_effect = AssertionError('missing scope issued an impossible lookup')
            _require_planned_resume_control(cursor, placeholder, value, current, None, clock)
            for owner, resume in ((None,'forged'), ('forged',None), ('',None), (None,'')):
                with self.subTest(placeholder=placeholder, owner=owner, resume=resume), \
                        self.assertRaisesRegex(RuntimeError, 'PLANNED_RESUME_SCOPE_CHANGED'):
                    _require_planned_resume_control(cursor, placeholder,
                        {**value,'planned_review_resume_id':resume}, current, owner, clock)
            # An actual scope, including an invalid empty identifier, still
            # follows the original lookup and proof validation path.
            for scope in ('original-spec', ''):
                scoped = Mock()
                scoped.fetchone.return_value = None
                with self.assertRaisesRegex(RuntimeError, 'PLANNED_RESUME_SCOPE_CHANGED'):
                    _require_planned_resume_control(scoped, placeholder, value,
                        {**current,'active_spec_id':scope}, 'forged', clock)
                scoped.execute.assert_called_once()
        clock.assert_not_called()


class MinuteStrategyRestoreAbsenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.unit import test_mock_automation_supervisor as native
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        self.native = native
        self.fixture = native.MockAutomationSupervisorTests(
            'test_activate_switches_one_bundle_and_starts_one_runner_fail_closed')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.supervisor, self.owner = self.fixture.supervisor, self.fixture.owner
        self.control = native._Control()
        self.fixture.repository.control = self.control
        self.binding = SimpleNamespace(scope=AccountScope('kiwoom', AccountEnvironment.MOCK, native.ACCOUNT_REF),
                                       credential_profile_id=native.PROFILE_ID)
        self.owner.account_bindings = lambda: (self.binding,)
        self.owner.require_execution_settings = AsyncMock()
        self.settings = Mock(return_value={'revision': 2})
        self.supervisor._store = SimpleNamespace(load_account_settings=self.settings)
        self.clock = [100.0]

    async def test_missing_selected_adapter_waits_for_explicit_activation_without_losing_control(self):
        import httpx
        from fastapi import FastAPI
        from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
        native = self.native
        module = 'kiwoom_monitor.application.panic_mock_execution'
        error = ModuleNotFoundError(f"No module named '{module}'", name=module)
        before = self.control.to_dict()
        monitor = self.owner.current.monitor
        self.owner.current.runtime.enabled = True
        prefix = 'kiwoom_monitor.central_server.mock_automation_supervisor.'
        with patch(prefix+'time', SimpleNamespace(monotonic=lambda: self.clock[0])), \
             patch(prefix+'admit_mock_automation'), patch(prefix+'record_mock_automation_recovery_from_risk'), \
             patch.object(self.supervisor, 'activate', wraps=self.supervisor.activate) as activate:
            with patch.object(self.supervisor, '_assemble_runner', side_effect=error):
                await self.supervisor._restore_once()
                self.clock[0] += 1000
                await self.supervisor._restore_once()
                self.assertEqual(1, activate.await_count)
                self.assertEqual(1, self.settings.call_count)
                self.assertEqual(before, self.fixture.repository.control.to_dict())
                self.assertIs(monitor, self.owner.current.monitor)
                self.assertFalse(self.owner.current.runtime.enabled)
                self.assertIn(module, self.supervisor._restore_errors[native.ACCOUNT_REF])
                app = FastAPI()
                app.include_router(create_mock_automation_router(self.supervisor, lambda:None))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                        base_url='http://isolated.test') as client:
                    response = await client.post('/api/v1/mock-automation/start', json={
                        'account_ref':native.ACCOUNT_REF, 'credential_profile_id':native.PROFILE_ID,
                        'spec_id':native.SPEC_ID, 'expected_settings_revision':2, 'credential_revision':3})
                self.assertEqual(503, response.status_code)
                self.assertEqual({'detail':'MOCK_AUTOMATION_STRATEGY_UNAVAILABLE'}, response.json())
                self.assertEqual(2, activate.await_count)
                self.assertEqual(before, self.fixture.repository.control.to_dict())
            # Existing explicit start can use a restored implementation; no old
            # automatic attempt is silently revived by a timer or copied approval.
            status = await self.supervisor.activate(credential_profile_id=native.PROFILE_ID,
                account_ref=native.ACCOUNT_REF, spec_id=native.SPEC_ID,
                expected_settings_revision=2, credential_revision=3)
            self.assertEqual('WARMUP', status['runner']['state'])
            self.assertEqual(3, activate.await_count)
            self.assertNotIn(native.ACCOUNT_REF, self.supervisor._restore_retry_after)
            self.assertNotIn(native.ACCOUNT_REF, self.supervisor._restore_errors)
            self.assertEqual(before, self.fixture.repository.control.to_dict())

    async def test_unrelated_dependency_failure_keeps_existing_timed_recovery(self):
        module = 'optional_transient_dependency'
        error = ModuleNotFoundError(f"No module named '{module}'", name=module)
        prefix = 'kiwoom_monitor.central_server.mock_automation_supervisor.'
        with patch(prefix+'time', SimpleNamespace(monotonic=lambda: self.clock[0])), \
             patch.object(self.supervisor, 'activate', side_effect=error) as activate:
            await self.supervisor._restore_once()
            self.clock[0] += 1000
            await self.supervisor._restore_once()
        self.assertEqual(2, activate.await_count)
        self.assertEqual(self.clock[0]+30, self.supervisor._restore_retry_after[self.native.ACCOUNT_REF])
        self.assertFalse(self.owner.current.runtime.enabled)
