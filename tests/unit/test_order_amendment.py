"""Physical-order facts stay on the original intent across one-shot amendment."""
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import json
import asyncio
from unittest.mock import patch

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import (
    BrokerAmendmentSubmission, BrokerFill, BrokerOrderSnapshot, OrderSide, OrderState, order_event,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import (
    AmendmentResponseUnknown, SubmissionRejected, SubmissionUnknown,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails, MockAccountRecovery
from tests.unit.test_order_lifecycle import NOW, _Transport, _account, _intent


class AmendmentTransport(_Transport):
    def __init__(self, repository, *, accepted_quantity=3, error=None):
        super().__init__()
        self.repository = repository
        self.accepted_quantity = accepted_quantity
        self.error = error
        self.amend_calls = []

    def amend(self, intent, broker_order_id, quantity, limit_price):
        durable = self.repository.load(intent.intent_id)
        assert durable.amendment.status == "PENDING"
        assert (durable.amendment.broker_order_id, durable.amendment.quantity, durable.amendment.limit_price) == (
            broker_order_id, quantity, limit_price)
        assert self.repository.events(intent.intent_id)[-1]["event_type"] == "AMENDMENT_STARTED"
        self.amend_calls.append((broker_order_id, quantity, limit_price))
        if self.error:
            raise self.error
        return BrokerAmendmentSubmission("broker-2", NOW, broker_order_id, self.accepted_quantity)


def amendment_fixture(case, factory=None, side=OrderSide.BUY):
    if factory is None:
        directory = case.enterContext(tempfile.TemporaryDirectory())
        factory = lambda: SQLiteQueryStore(Path(directory) / "execution.sqlite3")
    store = factory()
    store.initialize()
    case.addCleanup(store.close)
    repository = ExecutionRepository(store)
    transport = AmendmentTransport(repository)
    clock = [NOW]
    lifecycle = OrderLifecycle(repository, transport, now_provider=lambda: clock[0])
    intent = replace(_intent(), quantity=6, side=side)
    lifecycle.queue(intent)
    lifecycle.submit(intent.intent_id, replace(_account(), positions={"005930": 6} if side is OrderSide.SELL else {}))
    return factory, store, repository, transport, clock, lifecycle, intent


def broker_snapshot(intent, order, filled, remaining, seconds, fills=(), state=None):
    return BrokerOrderSnapshot(order, intent.account_ref, intent.symbol,
        state or (OrderState.PARTIALLY_FILLED if filled else OrderState.ACCEPTED),
        filled, remaining, NOW + timedelta(seconds=seconds), tuple(fills))


def verify_physical_fills(case, factory=None, *, sides=(OrderSide.BUY, OrderSide.SELL)):
    for side in sides:
        with case.subTest(side=side):
            factory_, store, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory, side)
            # PostgreSQL factories create a fresh schema for each fixture invocation.
            lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 2, 4, 1,
                (BrokerFill("same-fill", 2, 70_000, NOW),)))
            clock[0] = NOW + timedelta(seconds=2)
            accepted = lifecycle.amend(intent.intent_id, 4, 70_100)
            case.assertEqual(intent, accepted.intent)
            case.assertEqual("broker-1", accepted.broker_order_id)
            case.assertEqual((3, 3), tuple(leg.ordered_quantity - leg.transferred_quantity for leg in accepted.broker_legs))
            case.assertEqual((1, 0, [("broker-1", 4, 70_100)]),
                (transport.submit_calls, transport.cancel_calls, transport.amend_calls))
            # Mixed detailed/aggregate quantities must sum max(detail, report) per order.
            lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-2", 2, 1, 4))
            old = lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 3, 0, 2,
                (BrokerFill("old-late", 1, 70_000, NOW + timedelta(seconds=1)),)))
            case.assertEqual((5, 3, 5), (old.filled_quantity, old.detailed_filled_quantity, old.broker_reported_filled_quantity))
            # Older child cut still contains a previously unseen execution identity.
            child = broker_snapshot(intent, "broker-2", 2, 1, 3,
                (BrokerFill("same-fill", 2, 70_100, NOW + timedelta(seconds=2)),))
            detailed = lifecycle.reconcile(intent.intent_id, child)
            case.assertEqual((5, 5, 5), (detailed.filled_quantity, detailed.detailed_filled_quantity,
                                      detailed.broker_reported_filled_quantity))
            case.assertEqual(NOW + timedelta(seconds=4), detailed.broker_legs[1].last_broker_as_of)
            count = len(repository.events(intent.intent_id))
            case.assertEqual(detailed, lifecycle.reconcile(intent.intent_id, child))
            case.assertEqual(count, len(repository.events(intent.intent_id)))
            # A separate repository after reopen reads exactly the same physical identities.
            reopened = factory_()
            reopened.initialize()
            case.addCleanup(reopened.close)
            recovered = ExecutionRepository(reopened)
            for number in ("broker-1", "broker-2"):
                case.assertEqual(detailed, recovered.find_by_broker_order_id("mock", intent.account_ref, intent.run_id, number))
            case.assertIsNone(recovered.find_by_broker_order_id("mock", "other-account", intent.run_id, "broker-2"))
            restarted = OrderLifecycle(recovered, transport, now_provider=lambda: NOW + timedelta(seconds=5))
            final = restarted.reconcile(intent.intent_id, broker_snapshot(intent, "broker-2", 3, 0, 5,
                (BrokerFill("child-last", 1, 70_100, NOW + timedelta(seconds=4)),), OrderState.FILLED))
            case.assertEqual((OrderState.FILLED, 6, 6), (final.state, final.filled_quantity, final.detailed_filled_quantity))
            facts = [event for event in recovered.events(intent.intent_id) if event["event_type"] == "FILL"]
            case.assertEqual([("broker-1", "same-fill", 2), ("broker-1", "old-late", 1),
                              ("broker-2", "same-fill", 2), ("broker-2", "child-last", 1)],
                [(event["broker_order_id"], event["broker_execution_id"], event["quantity"]) for event in facts])
            case.assertEqual(6, sum(event.quantity for event in recovered.account_events("mock", intent.account_ref).events
                                    if event.event_type == "FILL"))


def verify_unknown_and_durable_claim(case, factory=None):
    _, _, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory)
    transport.error = AmendmentResponseUnknown("lost response", original_broker_order_id="broker-1",
                                               reported_broker_order_id="broker-2")
    unknown = lifecycle.amend(intent.intent_id, 3, 70_100)
    case.assertEqual(("UNKNOWN", "broker-2"), (unknown.amendment.status, unknown.amendment.reported_broker_order_id))
    clock[0] += timedelta(seconds=2)
    old = lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 1, 5, 1,
        (BrokerFill("late-on-unknown", 1, 70_000, NOW),)))
    restarted_transport = AmendmentTransport(repository)
    restarted = OrderLifecycle(repository, restarted_transport, now_provider=lambda: clock[0])
    case.assertEqual(old, restarted.amend(intent.intent_id, 3, 70_200))
    case.assertEqual([], restarted_transport.amend_calls)
    case.assertEqual("UNKNOWN", old.amendment.status)
    case.assertEqual(OrderState.SUBMISSION_UNKNOWN, old.state)
    with case.assertRaisesRegex(ValueError, "must be reconciled"):
        restarted.cancel(intent.intent_id)
    # An unknown number is preserved as an observation, never accepted as an owned leg merely by symbol.
    with case.assertRaisesRegex(ValueError, "known physical order"):
        restarted.reconcile(intent.intent_id, broker_snapshot(intent, "broker-2", 1, 2, 3))
    case.assertEqual(1, len([event for event in repository.events(intent.intent_id) if event["event_type"] == "FILL"]))
    complete_old = restarted.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 6, 0, 4,
        (BrokerFill("old-rest", 5, 70_000, NOW + timedelta(seconds=1)),), OrderState.FILLED))
    case.assertEqual((6, OrderState.SUBMISSION_UNKNOWN), (complete_old.filled_quantity, complete_old.state))
    case.assertEqual((complete_old,), repository.active_intents("mock", intent.account_ref, intent.run_id))


def verify_claim_crash_and_cas(case, factory=None):
    _, store, repository, transport, _, lifecycle, intent = amendment_fixture(case, factory)
    stale = repository.load(intent.intent_id)
    def crash():
        raise RuntimeError("crash after commit before network")
    with case.assertRaisesRegex(RuntimeError, "crash after commit"):
        lifecycle.amend(intent.intent_id, 3, 70_100, after_amendment_claim=crash)
    durable = repository.load(intent.intent_id)
    case.assertEqual("PENDING", durable.amendment.status)
    case.assertEqual([], transport.amend_calls)
    case.assertEqual(durable, lifecycle.amend(intent.intent_id, 3, 70_200))
    # A stale pre-amendment writer cannot erase the durable command. Event insert rolls back too.
    events = repository.events(intent.intent_id)
    with case.assertRaises((RuntimeError, KeyError)):
        repository.apply(stale, order_event(intent.intent_id, "BROKER_RECONCILED", stale.state,
                                            NOW, NOW + timedelta(seconds=2), broker_order_id="broker-1"))
    case.assertEqual(events, repository.events(intent.intent_id))
    case.assertEqual(durable, repository.load(intent.intent_id))


def verify_partial_transfer_and_cancel(case, factory=None):
    _, _, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory)
    transport.accepted_quantity = 2
    accepted = lifecycle.amend(intent.intent_id, 2, 70_100)
    case.assertEqual((4, 2), tuple(leg.remaining_quantity for leg in accepted.broker_legs))
    with case.assertRaisesRegex(ValueError, "one explicit"):
        lifecycle.cancel(intent.intent_id)
    lifecycle.cancel(intent.intent_id, broker_order_id="broker-2")
    clock[0] += timedelta(seconds=1)
    child_cancelled = lifecycle.reconcile(intent.intent_id,
        broker_snapshot(intent, "broker-2", 0, 0, 1, state=OrderState.CANCELLED))
    case.assertEqual(OrderState.ACCEPTED, child_cancelled.state)
    # One physical cancellation cannot release the other physical order/whole plan.
    lifecycle.cancel(intent.intent_id, broker_order_id="broker-1")
    final = lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 1, 0, 2,
        (BrokerFill("cancel-race", 1, 70_000, NOW),), OrderState.CANCELLED))
    case.assertEqual((OrderState.CANCELLED, 1), (final.state, final.filled_quantity))
    case.assertEqual((1, 2, 1), (transport.submit_calls, transport.cancel_calls, len(transport.amend_calls)))
    case.assertEqual(intent, repository.load(intent.intent_id).intent)
    from kiwoom_monitor.application.position_target import reconcile_position_account_observation
    recovery = MockAccountRecovery(replace(_account(), as_of=NOW + timedelta(seconds=2), positions={intent.symbol: 1}),
        (broker_snapshot(intent, "broker-2", 0, 0, 1, state=OrderState.CANCELLED),
         broker_snapshot(intent, "broker-1", 1, 0, 2, state=OrderState.CANCELLED)))
    active, complete = repository.account_active_intents(intent.account_ref)
    observed = reconcile_position_account_observation(recovery, (accepted,), active,
        input_ref="cancelled-family", observed_at=recovery.account.as_of, ledger_complete=complete,
        runtime_stable=True, reconciled_records=(final,))
    case.assertTrue(observed.open_orders_complete, observed.reasons)
    case.assertEqual((1, (), ()), (observed.account.positions[intent.symbol], observed.open_orders, observed.broker_open_orders))


def verify_rejected_and_invalid_response(case, factory=None):
    _, _, repository, transport, _, lifecycle, intent = amendment_fixture(case, factory)
    transport.error = SubmissionRejected("broker rejection")
    rejected = lifecycle.amend(intent.intent_id, 3, 70_100)
    case.assertEqual(("REJECTED", OrderState.ACCEPTED, 6),
        (rejected.amendment.status, rejected.state, rejected.broker_legs[0].remaining_quantity))
    transport.error = SubmissionUnknown("timeout")
    unknown = lifecycle.amend(intent.intent_id, 3, 70_100)
    case.assertEqual("UNKNOWN", unknown.amendment.status)
    case.assertEqual((1, 0, 2), (transport.submit_calls, transport.cancel_calls, len(transport.amend_calls)))
    # Invalid inputs never spend a mutation or create a command in a fresh intent.
    for quantity, price in ((0, 70_100), (True, 70_100), (7, 70_100), (3, 0), (3, True)):
        other = replace(intent, intent_id=f"invalid-{quantity}-{price}", decision_id=f"invalid-{quantity}-{price}")
        lifecycle.queue(other)
        lifecycle.submit(other.intent_id, _account())
        before = repository.events(other.intent_id)
        with case.assertRaises(ValueError):
            lifecycle.amend(other.intent_id, quantity, price)
        case.assertEqual(before, repository.events(other.intent_id))
    case.assertEqual(2, len(transport.amend_calls))


def verify_expired_owner(case, factory=None):
    _, store, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory)
    store._execution_wall_now = lambda: clock[0]
    case.assertTrue(repository.claim_runtime("mock", intent.account_ref, intent.run_id, "owner", NOW, 10))
    repository.bind_runtime_owner(intent.account_ref, intent.run_id, "owner")
    before = repository.events(intent.intent_id)
    clock[0] += timedelta(seconds=11)
    with case.assertRaisesRegex(RuntimeError, "EXECUTION_OWNERSHIP_LOST"):
        lifecycle.amend(intent.intent_id, 3, 70_100)
    case.assertEqual(before, repository.events(intent.intent_id))
    case.assertEqual([], transport.amend_calls)
    case.assertEqual((), repository.load(intent.intent_id).broker_legs)


def verify_planned_admission_is_not_reused(case, factory=None):
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests
    if factory is None:
        directory = case.enterContext(tempfile.TemporaryDirectory())
        factory = lambda: SQLiteQueryStore(Path(directory) / "planned.sqlite3")
    store = factory()
    store.initialize()
    case.addCleanup(store.close)
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store)
    case.addCleanup(context.runtime.stop)
    before = context.execution.events(context.buy.intent.intent_id)
    # The existing new-order approval is not a current-condition amendment approval.
    with case.assertRaisesRegex(RuntimeError, "PLANNED_AMENDMENT_ASSESSMENT_REQUIRED"):
        context.runtime._lifecycle.amend(context.buy.intent.intent_id, 2, 105)
    case.assertEqual(before, context.execution.events(context.buy.intent.intent_id))
    case.assertEqual((), context.execution.load(context.buy.intent.intent_id).broker_legs)


def verify_recovered_amendment(case, factory=None, *, crash=False, overfill=False):
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.infrastructure.persistence.execution_repository import broker_fill_key
    factory_, store, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory)
    lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 2, 4, 1,
        (BrokerFill("shared", 2, 70_000, NOW),)))
    clock[0] += timedelta(seconds=2)
    transport.error = SubmissionUnknown("lost amendment ACK")
    lifecycle.amend(intent.intent_id, 4, 70_100)
    store._execution_wall_now = lambda: clock[0]
    runtime = ExecutionRuntime(lifecycle, repository, account_ref=intent.account_ref, run_id=intent.run_id,
                               owner_token="recovery-owner", now_provider=lambda: clock[0])
    runtime.start(new_orders_enabled=False)
    case.addCleanup(runtime.stop)
    clock[0] += timedelta(seconds=1)
    early = broker_snapshot(intent, "broker-2", 1, 2, 3,
        (BrokerFill("shared", 1, 70_100, NOW + timedelta(seconds=2)),))
    observed = runtime.observe_amendment_fill(early, original_broker_order_id="broker-1", side=OrderSide.BUY)
    case.assertEqual((2, 2, OrderState.SUBMISSION_UNKNOWN),
        (observed.filled_quantity, observed.detailed_filled_quantity, observed.state))
    case.assertEqual((broker_fill_key("broker-2", "shared"),), observed.amendment.observed_fills)
    case.assertEqual(1, len([event for event in repository.events(intent.intent_id) if event["event_type"] == "FILL"]))
    case.assertEqual(observed, runtime.observe_amendment_fill(early, original_broker_order_id="broker-1", side=OrderSide.BUY))
    clock[0] += timedelta(seconds=1)
    recovery = MockAccountRecovery(replace(_account(), as_of=clock[0], positions={"005930": 3},
        reserved_open_buy_won=70_000 + 2 * 70_100),
        (broker_snapshot(intent, "broker-1", 2, 1, 4), broker_snapshot(intent, "broker-2", 1, 2, 4)),
        (BrokerOrderDetails("broker-1", 6, OrderSide.BUY, 70_000, "KRX"),
         BrokerOrderDetails("broker-2", 3, OrderSide.BUY, 70_100, "KRX", original_broker_order_id="broker-1")))
    # Rejected/ambiguous/stale data cannot resolve the command or consume the observation.
    invalids = [replace(recovery, order_details=(replace(recovery.order_details[1], **change),)) for change in (
        {"original_broker_order_id": "other"}, {"side": OrderSide.SELL}, {"limit_price": 70_200},
        {"ordered_quantity": 5}, {"ordered_quantity": True}, {"venue": "NXT"})]
    invalids += [replace(recovery, order_details=()), replace(recovery, orders=()),
        replace(recovery, order_details=(*recovery.order_details, replace(recovery.order_details[1], broker_order_id="ambiguous"))),
        replace(recovery, account=replace(recovery.account, as_of=clock[0] + timedelta(seconds=1)))]
    for invalid in invalids:
        runtime.recover_amendments(invalid)
        case.assertEqual(observed, repository.load(intent.intent_id))
    apply = repository.apply
    def changed_native_proof(record, event, **kwargs):
        if event.event_type == "AMENDMENT_RECOVERED":
            kwargs["_amendment_recovery"] = replace(recovery, order_details=(
                replace(recovery.order_details[1], limit_price=70_200),))
        return apply(record, event, **kwargs)
    with patch.object(repository, "apply", side_effect=changed_native_proof):
        with case.assertRaisesRegex(RuntimeError, "EXECUTION_AMENDMENT_RECOVERY_CHANGED"):
            runtime.recover_amendments(recovery)
    case.assertEqual(observed, repository.load(intent.intent_id))
    if overfill:
        runtime.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 6, 0, 4,
            (BrokerFill("original-rest", 4, 70_000, NOW + timedelta(seconds=3)),), OrderState.FILLED))
        recovery = replace(recovery, account=replace(recovery.account, positions={"005930": 7}), orders=(
            broker_snapshot(intent, "broker-1", 6, 0, 4, state=OrderState.FILLED), recovery.orders[1]))
        with case.assertRaisesRegex(ValueError, "fills exceed"):
            runtime.recover_amendments(recovery)
        blocked = repository.load(intent.intent_id)
        case.assertEqual((6, OrderState.SUBMISSION_UNKNOWN, 1),
            (blocked.detailed_filled_quantity, blocked.state, len(blocked.amendment.observed_fills)))
        case.assertEqual((blocked,), repository.active_intents("mock", intent.account_ref, intent.run_id))
        case.assertEqual(1, len([event for event in repository.events(intent.intent_id)
                                if event["event_type"] == "AMENDMENT_FILL_OBSERVED"]))
        case.assertFalse(any(event["event_type"] == "FILL" and event["broker_order_id"] == "broker-2"
                             for event in repository.events(intent.intent_id)))
        return
    if crash:
        with patch.object(lifecycle, "_replay_amendment_fills", side_effect=RuntimeError("crash after binding")):
            with case.assertRaisesRegex(RuntimeError, "crash after binding"):
                runtime.recover_amendments(recovery)
        bound = repository.load(intent.intent_id)
        case.assertEqual(("ACCEPTED", OrderState.SUBMISSION_UNKNOWN, 2),
            (bound.amendment.status, bound.state, bound.detailed_filled_quantity))
        case.assertEqual((bound,), repository.active_intents("mock", intent.account_ref, intent.run_id))
    else:
        runtime.recover_amendments(recovery)
    runtime.stop()
    reopened = factory_(); reopened.initialize(); case.addCleanup(reopened.close)
    reopened._execution_wall_now = lambda: clock[0]
    restored = ExecutionRepository(reopened)
    restarted = ExecutionRuntime(OrderLifecycle(restored, None, now_provider=lambda: clock[0]), restored,
        account_ref=intent.account_ref, run_id=intent.run_id, owner_token="new-recovery-owner", now_provider=lambda: clock[0])
    restarted.start(new_orders_enabled=False); case.addCleanup(restarted.stop)
    restarted.recover_amendments(recovery)
    for order in recovery.orders:
        restarted.reconcile_by_broker_order(order)
    final = restored.load(intent.intent_id)
    case.assertEqual((intent, 3, 3, "ACCEPTED", ()),
        (final.intent, final.filled_quantity, final.detailed_filled_quantity, final.amendment.status, final.amendment.observed_fills))
    from kiwoom_monitor.application.position_target import reconcile_position_account_observation
    observation = reconcile_position_account_observation(recovery, (observed,), (final,), input_ref="recovered-family",
        observed_at=clock[0], ledger_complete=True, runtime_stable=True, reconciled_records=(final,))
    case.assertTrue(observation.open_orders_complete, observation.reasons)
    case.assertEqual([("broker-1", intent.intent_id, 70_000, 1), ("broker-2", intent.intent_id, 70_100, 2)],
        [(row.broker_order_id, row.intent_ref, row.limit_price, row.remaining_quantity) for row in observation.broker_open_orders])
    facts = [event for event in restored.events(intent.intent_id) if event["event_type"] == "FILL"]
    case.assertEqual([("broker-1", "shared", 2), ("broker-2", "shared", 1)],
        [(event["broker_order_id"], event["broker_execution_id"], event["quantity"]) for event in facts])
    before = restored.events(intent.intent_id)
    restarted.recover_amendments(recovery)
    for order in recovery.orders:
        restarted.reconcile_by_broker_order(order)
    case.assertEqual(before, restored.events(intent.intent_id))
    case.assertEqual((1, 0, 1), (transport.submit_calls, transport.cancel_calls, len(transport.amend_calls)))
    return recovery, final


async def verify_amendment_monitor(case, factory=None):
    from tests.unit.test_mock_account_monitor import _Reader
    from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor, MockAccountRealtimeCollector
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    _, store, repository, transport, clock, lifecycle, intent = amendment_fixture(case, factory)
    lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 2, 4, 1,
        (BrokerFill("shared", 2, 70_000, NOW),)))
    clock[0] += timedelta(seconds=2)
    transport.error = SubmissionUnknown("lost amendment ACK")
    lifecycle.amend(intent.intent_id, 4, 70_100)
    store._execution_wall_now = lambda: clock[0]
    reader = _Reader(MockAccountRecovery(replace(_account(), as_of=clock[0], positions={"005930": 2}),
        (broker_snapshot(intent, "broker-1", 2, 4, 2),)))
    runtime = ExecutionRuntime(lifecycle, repository, account_ref=intent.account_ref, run_id=intent.run_id,
                               owner_token="monitor-owner", now_provider=lambda: clock[0])
    monitor = MockAccountMonitor(reader, runtime, repository, now_provider=lambda: clock[0], new_orders_enabled_on_start=False)
    monitor.set_account_order_observation_enabled(True)
    try:
        await monitor.start()
        clock[0] += timedelta(seconds=1)
        collector = MockAccountRealtimeCollector(lambda: "fake-token", monitor.handle_realtime, lambda: clock[0])
        collector._dispatch_realtime({"trnm": "REAL", "data": [{"type": "00", "item": "A005930", "values": {
            "913": "체결", "914": "70100", "915": "1", "905": "+매수정정", "9203": "broker-2", "904": "broker-1",
            "909": "shared", "908": "090003", "900": "3", "902": "2", "2135": "KRX"}}]})
        await asyncio.wait_for(monitor._queue.join(), 5)
        retained = repository.load(intent.intent_id)
        case.assertEqual((2, OrderState.SUBMISSION_UNKNOWN), (retained.detailed_filled_quantity, retained.state))
        case.assertEqual(1, len(retained.amendment.observed_fills))
        clock[0] += timedelta(seconds=1)
        reader.recovery = MockAccountRecovery(replace(_account(), as_of=clock[0], positions={"005930": 3},
            reserved_open_buy_won=70_000 + 2 * 70_100),
            (broker_snapshot(intent, "broker-1", 2, 1, 4), broker_snapshot(intent, "broker-2", 1, 2, 4)),
            (BrokerOrderDetails("broker-1", 6, OrderSide.BUY, 70_000, "KRX"),
             BrokerOrderDetails("broker-2", 3, OrderSide.BUY, 70_100, "KRX", original_broker_order_id="broker-1")))
        monitor.handle_realtime("order_changed", None)
        await asyncio.wait_for(monitor._queue.join(), 5)
        final = repository.load(intent.intent_id)
        case.assertEqual((3, (), "ACCEPTED"), (final.detailed_filled_quantity, final.amendment.observed_fills, final.amendment.status))
        observation = monitor.latest_position_observation
        case.assertIsNotNone(observation)
        case.assertTrue(observation.open_orders_complete, observation.reasons)
        case.assertEqual(2, len(observation.broker_open_orders))
        case.assertEqual(3, reader.calls)
        case.assertEqual((1, 0, 1), (transport.submit_calls, transport.cancel_calls, len(transport.amend_calls)))
    finally:
        await monitor.close()



class OrderAmendmentTests(unittest.TestCase):
    def test_advancing_clock_persists_the_exact_amendment_start_instant(self):
        _, _, repository, transport, _, _, intent = amendment_fixture(self)
        ticks = iter(NOW + timedelta(microseconds=index) for index in range(100))
        lifecycle = OrderLifecycle(repository, transport, now_provider=lambda: next(ticks))
        result = lifecycle.amend(intent.intent_id, 3, 70_100)
        started = next(row for row in repository.events(intent.intent_id) if row['event_type'] == 'AMENDMENT_STARTED')
        self.assertEqual(result.amendment.started_at.isoformat(), started['received_at'])
        self.assertEqual([('broker-1', 3, 70_100)], transport.amend_calls)

    def test_conflicting_excess_fill_stays_durable_and_unresolved_without_weakening_quantity_cap(self):
        verify_recovered_amendment(self, overfill=True)

    def test_account_monitor_retains_early_child_00_and_recovers_using_existing_reads(self):
        asyncio.run(verify_amendment_monitor(self))

    def test_known_ack_preserves_a_fill_observed_during_the_network_call(self):
        _, _, repository, transport, clock, lifecycle, intent = amendment_fixture(self)
        original = transport.amend
        def observed_during_request(*args):
            response = original(*args)
            snapshot = broker_snapshot(intent, "broker-2", 1, 2, 0, (BrokerFill("early", 1, 70_100, NOW),))
            lifecycle.observe_amendment_fill(intent.intent_id, snapshot, original_broker_order_id="broker-1", side=OrderSide.BUY)
            return response
        with patch.object(transport, "amend", side_effect=observed_during_request):
            final = lifecycle.amend(intent.intent_id, 3, 70_100)
        self.assertEqual((1, (), "ACCEPTED"),
            (final.detailed_filled_quantity, final.amendment.observed_fills, final.amendment.status))
        self.assertEqual(["AMENDMENT_FILL_OBSERVED", "AMENDMENT_ACCEPTED", "FILL"],
            [event["event_type"] for event in repository.events(intent.intent_id)][-3:])

    def test_unknown_ack_recovers_from_parent_details_and_retained_fill_without_transmission(self):
        verify_recovered_amendment(self)

    def test_restart_after_binding_replays_durable_observation_exactly_once(self):
        verify_recovered_amendment(self, crash=True)

    def test_official_00_parent_preserves_leading_zeros_and_missing_parent_is_unknown(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_order_executions
        for original, expected in (("0000139", "0000139"), ("0000000", None), (None, None)):
            message = {"trnm": "REAL", "data": [{"type": "00", "item": "A005930", "values": {
                "913": "체결", "914": "70100", "915": "1", "905": "+매수정정", "9203": "0000140",
                "909": "same-fill", "908": "090002", "904": original}}]}
            result, = parse_order_executions(message)
            self.assertEqual(("0000140", expected, "same-fill", "매수", 1),
                (result.order_no, result.original_broker_order_id, result.execution_no, result.side, result.quantity))

    def test_legacy_wire_and_indexed_lookup_cost_and_offline_input_roundtrip(self):
        from kiwoom_monitor.application.position_target import _json_value, _position_account_orders_from_dict
        _, store, repository, transport, _, lifecycle, intent = amendment_fixture(self)
        legacy = store.load_execution_intent(intent.intent_id)
        self.assertTrue({"broker_legs", "broker_revision", "amendment"}.isdisjoint(legacy))
        lifecycle.amend(intent.intent_id, 3, 70_100)
        sql = []
        connect = store._connect
        def traced():
            connection = connect()
            connection.set_trace_callback(sql.append)
            return connection
        with patch.object(store, "_connect", side_effect=traced):
            original = repository.find_by_broker_order_id("mock", intent.account_ref, intent.run_id, "broker-1")
            self.assertEqual(1, len([text for text in sql if text.startswith(("SELECT", "WITH"))]))
            sql.clear()
            child = repository.find_by_broker_order_id("mock", intent.account_ref, intent.run_id, "broker-2")
            self.assertEqual(1, len([text for text in sql if text.startswith(("SELECT", "WITH"))]))
        self.assertEqual(original, child)
        account, orders = json.loads(json.dumps([asdict(_account()), [asdict(child)]], default=_json_value))
        decoded = _position_account_orders_from_dict(account, orders)
        self.assertEqual((_account(), (child,)), decoded)

    def test_original_and_amended_unique_fills_keep_physical_identity_after_reopen(self):
        verify_physical_fills(self)

    def test_lost_ack_keeps_command_and_original_late_fills_without_retransmission(self):
        verify_unknown_and_durable_claim(self)

    def test_pre_network_crash_and_stale_writer_preserve_one_durable_command(self):
        verify_claim_crash_and_cas(self)

    def test_partial_amendment_cancels_each_leg_without_releasing_other_exposure(self):
        verify_partial_transfer_and_cancel(self)

    def test_rejected_unknown_and_invalid_amendments_never_fall_back_to_new_order(self):
        verify_rejected_and_invalid_response(self)

    def test_expired_native_owner_cannot_claim_or_transmit_an_amendment(self):
        verify_expired_owner(self)

    def test_existing_planned_entry_approval_cannot_authorize_a_new_amendment(self):
        verify_planned_admission_is_not_reused(self)
