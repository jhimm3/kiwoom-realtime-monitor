"""Public selected Leader management preserves its first stop and original plan."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.market_session_schedule import KST


async def leader_management_source(case):
    from scripts.preserve_selected_order_evidence import (
        capture_selected_order_evidence, capture_selected_order_management_bundle,
    )
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
    from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence, validate_management_closure
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.domain.order_contract import AccountEnvironment
    from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
    from tests.unit.test_leader_order_management import current_leader
    from tests.unit.test_selected_order_recovery import FakeReadOnlyBroker, KEY, NOW, dated_orders, identity_owner

    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    path = root / 'original-leader.sqlite3'
    identity = await KiwoomAccountIdentityReader(FakeReadOnlyBroker(), environment=AccountEnvironment.MOCK,
        hmac_key=KEY, now_provider=lambda: NOW).verify()
    register = SQLiteQueryStore.register_account_identity
    def native_identity(store, value):
        return register(store, {**value, 'identity_fingerprint': identity.identity_fingerprint})
    with patch.object(SQLiteQueryStore, 'register_account_identity', native_identity):
        buy = await current_leader(case, lambda: SQLiteQueryStore(path))
    case.clock += timedelta(milliseconds=100)
    case.book.fill(buy.broker_order_id, 2, 'original-leader-partial-two')
    await case.bundle.monitor.refresh_recovery()
    episode = case.runner.episode
    episode._episode_position.apply_new_fills(case.execution, leader=True, config=episode._config,
        position_time=episode._position_state_time)
    case.clock += timedelta(milliseconds=100)
    case.runtime.heartbeat()
    payload = dict(code=buy.intent.symbol, market='KRX', current_price=940,
        trade_time=case.clock.astimezone(KST).strftime('%H%M%S'), cumulative_trade_value=100)
    episode.observe_leader_trade(payload, case.clock, source='actual-0B-fixture:1', approved=True)
    notice = deepcopy(episode._protection_snapshot())
    case.assertIsNotNone(notice, episode.status)
    case.assertEqual(('PENDING', 2), (notice['state'], notice['risk_trigger']['stop_loss']['quantity']))
    case.assertIn('planned_leader_stop', notice)
    episode._save_checkpoint(force=True)
    emergency_stop_mock_automation(case.repository, case.runtime, account_ref=case.runtime.account_ref,
        spec_id=case.spec.spec_id, stopped_at=case.runtime.server_now(), reason='test-owned stopped Leader export')
    await case.runner.close()
    await case.bundle.gateway.close()
    await case.bundle.monitor.close()
    case.runtime.stop()
    case.assertEqual([], case.book.mutations)
    selection = dict(source=path, source_instance='original-leader-source', operation_id='selected-original-leader',
        account_ref=case.runtime.account_ref, run_id=case.runtime.run_id, intent_id=buy.intent.intent_id)
    original, _ = capture_selected_order_evidence(**selection)
    profile = case.spec.credential_profile_id
    orders = dated_orders(original)
    evidence = DatedBrokerEvidence(case.runtime.account_ref, profile, 1, case.spec.binding_revision,
        case.runtime.server_now(), case.runtime.server_now(), tuple(sorted({order.trading_date for order in orders})),
        'FULL', 'fake-original-leader-source-cut', orders)
    owner = identity_owner(case.store, KEY, profile=profile)
    packet, digest, proof = capture_selected_order_management_bundle(**selection, identity_owner=owner,
        credential_profile_id=profile, broker_evidence=evidence)
    spec, package, admission = validate_management_closure(packet)
    case.assertEqual('selected-order-evidence/v4', packet['version'])
    case.assertEqual('leader_breakout/v1', package.candidate_spec['family'])
    case.assertEqual((case.spec, case.run_id), (spec, admission.execution_run_id))
    return SimpleNamespace(root=root, packet=packet, digest=digest, proof=proof, selection=selection,
        source_notice=notice, source_buy=buy, source_spec=spec)


async def selected_leader_target(case):
    from tests.unit.test_selected_order_management import actual_management_target

    data = await actual_management_target(case, source_factory=leader_management_source)
    data.source_bytes = data.source.selection['source'].read_bytes()
    data.management_url = data.target.url.replace('/assessment', '/prepare-management')
    prepared = await data.target.client.post(data.management_url, json=data.request)
    case.assertEqual(200, prepared.status_code, prepared.text)
    case.assertIsNone(data.target.store.load_document('server_operational_settings', 'global', 'current'))
    data.bundle = data.target.owner.bundle('target-profile')
    data.book_context.runtime, data.book_context.execution = data.bundle.runtime, data.bundle.repository
    data.book_context.management_field = 'leader_order_management_claim'
    data.management_context = data.bundle.selected_management_context
    case.assertEqual((1, 3, 3), (data.source.source_spec.binding_revision,
        data.management_context.binding_revision, data.management_context.credential_revision))
    case.assertNotEqual(data.source.source_spec.credential_profile_id, data.management_context.credential_profile_id)
    accepted = await data.target.client.post(data.management_url.replace('/prepare-management', '/accept-management'),
        json={**data.request, 'expected_control_revision': 1})
    recovery_row = data.target.store.load_document('execution_mock_automation_current_recovery',
        data.management_context.account_ref, data.management_context.admission_id)
    case.assertEqual(200, accepted.status_code, (accepted.text, recovery_row))
    case.assertEqual(('MANAGEMENT_READY', True, False), (accepted.json()['receipt']['status'],
        accepted.json()['receipt']['management_ready'], accepted.json()['receipt']['new_orders_enabled']))
    data.runner = data.supervisor._runners[data.bundle.account_ref]
    case.assertEqual(1, len(data.runner.episodes))
    case.assertFalse(data.bundle.runtime._new_orders_enabled)
    return data


class SelectedLeaderManagementTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests
    target = SelectedOrderRecoveryTests.target

    async def test_public_acceptance_cancels_original_partial_buy_then_protects_its_two_actual_fills(self):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs

        data = await selected_leader_target(self)
        bundle, runner = data.bundle, data.runner
        symbol = data.source.source_buy.intent.symbol

        async def deliver_later_quote():
            data.clock[0] += timedelta(milliseconds=10)
            payload = dict(code=symbol, market='KRX', current_price=945,
                trade_time=data.clock[0].astimezone(KST).strftime('%H%M%S'), cumulative_trade_value=100)
            data.supervisor.observe_leader_trade(payload, data.clock[0],
                source='selected-current-leader:1', approved=True)
            await asyncio.sleep(.05)

        async def protection_submitted():
            cancellation_polled = False
            for _ in range(200):
                await deliver_later_quote()
                if data.book.mutations and not cancellation_polled and not bundle.runtime.observation_in_progress:
                    cancellation_polled = True
                    await bundle.monitor.refresh_recovery()
                sells = tuple(record for record in bundle.repository.active_intents('mock', bundle.account_ref, bundle.run_id)
                    if record.intent.side.value == 'SELL' and record.state.value in {'ACCEPTED', 'PARTIALLY_FILLED'})
                if sells:
                    return sells
            self.fail(str((data.book.mutations, runner.status)))

        try:
            sells = await asyncio.wait_for(protection_submitted(), timeout=30)
        except TimeoutError:
            self.fail(str((data.book.mutations, runner.status)))
        self.assertEqual([2], [record.intent.quantity for record in sells])
        sell = sells[0]
        self.assertEqual(('mock-protection-exit/v1', data.source.source_buy.intent.plan.plan_id),
            (sell.intent.policy_version, sell.intent.plan.plan_id))
        self.assertEqual(data.source.source_notice['risk_trigger'], runner.episode._protection_snapshot()['risk_trigger'])
        self.assertEqual([('CANCEL', data.source.source_buy.broker_order_id, 0)], data.book.mutations)
        current_sell = bundle.repository.load(sell.intent.intent_id)
        working = tuple(leg for leg in execution_broker_legs(current_sell) if leg.remaining_quantity)
        self.assertEqual(1, len(working))
        data.clock[0] += timedelta(milliseconds=1)
        data.book.fill(working[0].broker_order_id, 2, 'current-target-original-leader-protection-two')
        await bundle.monitor.refresh_recovery()

        async def confirmed_flat():
            for _ in range(200):
                head = bundle.repository.load_plan_position(bundle.account_ref, bundle.run_id, symbol, sell.intent.plan)
                if head.quantity == 0 and runner.episode._episode_position.state.status == 'flat':
                    return head
                await asyncio.sleep(.05)
            self.fail(str((head, runner.status)))

        head = await asyncio.wait_for(confirmed_flat(), timeout=30)
        self.assertIsNone(runner.episode._episode_position.state.position_quantity)
        self.assertEqual(['SELL'], [row[1] for row in data.transport.calls])
        stored = bundle.repository.load(sell.intent.intent_id)
        self.assertEqual(2, stored.detailed_filled_quantity)
        fill = tuple(event for event in bundle.repository.account_events('mock', bundle.account_ref).events
            if event.intent_id == sell.intent.intent_id and event.event_type == 'FILL')
        self.assertEqual([(2, head.fill_cursor)], [(event.quantity, event.accepted_sequence) for event in fill])
        status = await data.target.client.get(data.management_url.replace('/prepare-management', '/management'))
        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual(data.management_context.to_dict(), status.json()['management']['context'])
        self.assertTrue(status.json()['control']['entry_resume_required'])
        self.assertEqual(data.source_bytes, data.source.selection['source'].read_bytes())
