"""A member recovery keeps the full native account cut and peer ownership."""
from dataclasses import asdict, fields, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def account_recovery_inputs(case, store):
    from tests.unit.test_mock_automation_account_execution import grouped_inputs, group_runtime, admit_group
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.application.position_target import reconcile_position_account_observation
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    data = grouped_inputs(case, store)
    owner = group_runtime(case, store, data)
    data.admissions, _, _ = admit_group(data, owner)
    data.owner = owner
    data.revision = 0

    def cut(*, positions=None, orders=(), reserved=0, costs=()):
        data.revision += 1
        owner.clock[0] += timedelta(milliseconds=1)
        account = AccountSnapshot(owner.runtime.account_ref, 250_000, reserved, positions or {}, owner.clock[0])
        recovery = MockAccountRecovery(account, tuple(orders))
        active, complete = owner.execution.account_active_intents(owner.runtime.account_ref)
        case.assertTrue(complete)
        events = owner.execution.account_events('mock', owner.runtime.account_ref).events
        risk = build_mock_automation_risk_snapshot(recovery, events, costs, active,
            execution_run_id=owner.run_id, binding_revision=data.selection.binding_revision,
            reconciliation_revision=data.revision, observed_at=owner.clock[0],
            broker_query_started_at=owner.clock[0], broker_query_completed_at=owner.clock[0])
        data.repository.save_mock_automation_risk_snapshot(risk)
        observation = reconcile_position_account_observation(recovery, active, active,
            input_ref='actual-native-account-cut:' + str(data.revision), observed_at=owner.clock[0],
            ledger_complete=True, runtime_stable=True, reconciled_records=active)
        observation = replace(observation, risk_snapshot=risk)
        return dict(risk=risk, observation=observation, recovery=recovery)
    data.cut = cut
    return data


def record_members(data, cut):
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut
    return record_mock_automation_account_recovery_from_cut(data.repository, data.owner.runtime, cut,
        account_ref=data.owner.runtime.account_ref)


def submit_first_member(case, data, cut):
    from tests.unit.test_mock_automation_specification import _automation_decision
    from kiwoom_monitor.application.mock_automation_execution import dispatch_mock_automation_decision_from_risk
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    spec = data.specs[0]
    gate, record, _ = dispatch_mock_automation_decision_from_risk(data.repository, data.owner.runtime,
        _automation_decision(data.owner.run_id, 'ENTER', data.owner.clock[0]), cut['recovery'], cut['risk'],
        account_ref=data.owner.runtime.account_ref, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref,
        plan=OrderPlanRef('native-recovery-member-plan', 'explicit-recovery-test-plan/v1', 0))
    case.assertIsNotNone(record, gate.reasons)
    return record


def verify_account_recovery_peer_order(case, store):
    from kiwoom_monitor.application.mock_automation_recovery import (
        MockAutomationRecoveryStatus, MockAutomationAccountRecoveryDecision, mock_automation_recovery_decision_from_dict)
    from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState
    data = account_recovery_inputs(case, store)
    initial = data.cut()
    values = record_members(data, initial)
    case.assertTrue(all(isinstance(value, MockAutomationAccountRecoveryDecision) for value in values))
    case.assertTrue(all(value.status is MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED for value in values))
    buy = submit_first_member(case, data, initial)
    broker = BrokerOrderSnapshot(buy.broker_order_id, data.owner.runtime.account_ref, buy.intent.symbol,
        OrderState.ACCEPTED, 0, 1, data.owner.clock[0])
    data.owner.runtime.reconcile(buy.intent.intent_id, broker)
    cut = data.cut(orders=(broker,), reserved=buy.intent.limit_price)
    before = (cut['risk'].to_dict(), cut['recovery'], cut['observation'])
    own, peer = record_members(data, cut)
    case.assertEqual((MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED,
                      MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED), (own.status, peer.status))
    case.assertEqual((buy.broker_order_id,), own.own_open_order_ids)
    case.assertEqual((buy.broker_order_id,), peer.other_member_open_order_ids)
    case.assertEqual((), peer.own_open_order_ids)
    case.assertEqual(before, (cut['risk'].to_dict(), cut['recovery'], cut['observation']))
    for value in (own, peer):
        case.assertEqual((1, buy.intent.limit_price, 0),
                         (value.open_order_count, value.reserved_open_buy_won, value.position_count))
        case.assertEqual(cut['risk'].snapshot_id, value.risk_snapshot_id)
        case.assertEqual(data.selection.selection_id, value.account_selection_id)
        case.assertEqual(value, mock_automation_recovery_decision_from_dict(value.to_dict()))
    case.assertFalse(data.owner.runtime._new_orders_enabled)
    case.assertEqual(1, len(data.owner.transport.calls))
    return data, cut, buy


class AccountRecoveryTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = SQLiteQueryStore(root / 'account-recovery.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_verified_peer_broker_order_is_preserved_in_full_cut_and_only_self_is_occupied(self):
        verify_account_recovery_peer_order(self, self.store)

    def test_native_fill_keeps_peer_position_without_relabeling_or_zeroing_canonical_risk(self):
        from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
        from kiwoom_monitor.application.mock_automation_recovery import MockAutomationRecoveryStatus
        data, _, buy = verify_account_recovery_peer_order(self, self.store)
        data.owner.clock[0] += timedelta(milliseconds=1)
        filled = BrokerOrderSnapshot(buy.broker_order_id, data.owner.runtime.account_ref, buy.intent.symbol,
            OrderState.FILLED, 1, 0, data.owner.clock[0],
            (BrokerFill('actual-recovery-fill', 1, buy.intent.limit_price, data.owner.clock[0]),))
        data.owner.runtime.reconcile(buy.intent.intent_id, filled)
        from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
        day = filled.fills[0].occurred_at.date()
        cost = DailyTradeCost(day, day, buy.intent.symbol, '매수', buy.intent.limit_price,
                             buy.intent.limit_price, 0, 0, 0, origin_scope=data.selection.account_scope)
        cut = data.cut(positions={buy.intent.symbol: 1}, costs=(cost,))
        own, peer = record_members(data, cut)
        self.assertEqual((MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED,
                          MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED), (own.status, peer.status))
        self.assertEqual(((buy.intent.symbol, 1),), own.own_position_quantities)
        self.assertEqual((), peer.own_position_quantities)
        self.assertEqual((1, 1), (own.position_count, peer.position_count))
        self.assertEqual(((buy.intent.symbol, 1),), cut['risk'].owned_position_quantities)
        self.assertEqual(own.risk_snapshot_id, peer.risk_snapshot_id)

    def test_missing_incomplete_unmapped_and_stripped_member_observations_cannot_clear(self):
        from kiwoom_monitor.application.mock_automation_recovery import MockAutomationRecoveryStatus
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord
        data, cut, _ = verify_account_recovery_peer_order(self, self.store)
        observation = cut['observation']
        original, = observation.open_orders
        stripped = ExecutionRecord(**{field.name: getattr(original, field.name) for field in fields(ExecutionRecord)})
        for invalid in (None, replace(observation, open_orders_complete=False),
                        replace(observation, broker_open_orders=None), replace(observation, broker_open_orders=()),
                        replace(observation, unmapped_broker_order_ids=('unknown-physical-order',)),
                        replace(observation, open_orders=(stripped,)), replace(observation, risk_snapshot=None)):
            with self.subTest(invalid=type(invalid).__name__):
                values = record_members(data, {**cut, 'observation': invalid})
                self.assertTrue(all(value.status is MockAutomationRecoveryStatus.BLOCKED for value in values))
                self.assertTrue(all(not value.other_member_open_order_ids for value in values))

    def test_unknown_extra_reservation_and_current_binding_revision_cannot_clear(self):
        from kiwoom_monitor.application.mock_automation_recovery import MockAutomationRecoveryStatus
        data, _, buy = verify_account_recovery_peer_order(self, self.store)
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState
        order = BrokerOrderSnapshot(buy.broker_order_id, data.owner.runtime.account_ref, buy.intent.symbol,
            OrderState.ACCEPTED, 0, 1, data.owner.clock[0])
        cut = data.cut(orders=(order,), reserved=buy.intent.limit_price + 1)
        values = record_members(data, cut)
        self.assertTrue(all(value.status is MockAutomationRecoveryStatus.BLOCKED for value in values))
        self.assertIn('ACCOUNT_MEMBER_RESERVED_BUY_UNCONFIRMED', values[1].reasons)
        self.store.append_account_binding(dict(credential_profile_id=data.selection.credential_profile_id,
            broker='kiwoom', environment='mock', account_ref=data.owner.runtime.account_ref,
            verified_at=data.owner.clock[0].isoformat(), verification_method='ka00001'))
        with self.assertRaisesRegex(ValueError, 'current verified mock binding'):
            record_members(data, cut)

    def test_account_codec_binds_metadata_and_standalone_asdict_shape_stays_unchanged(self):
        from kiwoom_monitor.application.mock_automation_recovery import (
            MockAutomationRecoveryDecision, MOCK_AUTOMATION_RECOVERY_VERSION,
            mock_automation_recovery_decision_from_dict, _content_id)
        data = account_recovery_inputs(self, self.store)
        value, _ = record_members(data, data.cut())
        changed = value.to_dict()
        changed['member_spec_ids'].reverse()
        with self.assertRaisesRegex(ValueError, 'content does not match'):
            mock_automation_recovery_decision_from_dict(changed)
        document = {field.name: getattr(value, field.name) for field in fields(MockAutomationRecoveryDecision)}
        document['version'] = MOCK_AUTOMATION_RECOVERY_VERSION
        legacy = MockAutomationRecoveryDecision(**document)
        raw = legacy.to_dict()
        raw.pop('decision_id')
        legacy = replace(legacy, decision_id=_content_id('mock_automation_recovery', raw))
        self.assertEqual(legacy, mock_automation_recovery_decision_from_dict(legacy.to_dict()))
        self.assertEqual(set(document), set(asdict(legacy)))
        self.assertNotIn('account_selection_id', asdict(legacy))

    def test_submission_unknown_stays_blocked_until_real_late_fill_and_current_cost_evidence(self):
        from kiwoom_monitor.application.mock_automation_recovery import MockAutomationRecoveryStatus
        from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
        data = account_recovery_inputs(self, self.store)
        initial = data.cut()
        record_members(data, initial)
        with patch.object(data.owner.transport, 'submit', side_effect=SubmissionUnknown('broker response lost')):
            buy = submit_first_member(self, data, initial)
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, buy.state)
        uncertain = data.cut()
        values = record_members(data, uncertain)
        self.assertTrue(all(value.status is MockAutomationRecoveryStatus.BLOCKED for value in values))
        self.assertEqual((1, 1), tuple(value.submission_unknown_count for value in values))
        data.owner.clock[0] += timedelta(milliseconds=1)
        fact = BrokerFill('late-actual-fill', 1, buy.intent.limit_price, data.owner.clock[0])
        late = BrokerOrderSnapshot('broker-late-original-order', data.owner.runtime.account_ref,
            buy.intent.symbol, OrderState.FILLED, 1, 0, data.owner.clock[0], (fact,))
        data.owner.runtime.reconcile(buy.intent.intent_id, late)
        day = fact.occurred_at.date()
        cost = DailyTradeCost(day, day, buy.intent.symbol, '매수', fact.price, fact.price, 0, 0, 0,
                             origin_scope=data.selection.account_scope)
        current = data.cut(positions={buy.intent.symbol: 1}, costs=(cost,))
        own, peer = record_members(data, current)
        self.assertEqual((MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED,
                          MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED), (own.status, peer.status))
        self.assertEqual(0, current['risk'].submission_unknown_count)
        self.assertEqual(1, data.owner.execution.load(buy.intent.intent_id).detailed_filled_quantity)
        with self.assertRaisesRegex(RuntimeError, 'latest mock automation risk snapshot changed'):
            record_members(data, uncertain)
