"""Physical-order facts stay on the original intent across one-shot amendment."""
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import json
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


class OrderAmendmentTests(unittest.TestCase):
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
