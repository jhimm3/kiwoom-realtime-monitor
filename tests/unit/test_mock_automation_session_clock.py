from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.unit import test_mock_automation_specification as fixture
from kiwoom_monitor.application.breakout_strategy import (
    REGULAR_SESSION_EXIT_POLICY, StrategyState, default_shadow_breakout_config,
)
from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
from kiwoom_monitor.application.mock_automation_execution import (
    emergency_stop_mock_automation, resume_mock_automation,
)
from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
from kiwoom_monitor.application.mock_automation_session_clock import (
    clock_exit_decision, session_clock_assessment,
)
from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
from kiwoom_monitor.central_server.mock_automation_runner import CLOCK_COLLECTION, MockAutomationRunner
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, BrokerFill, BrokerOrderSnapshot, OrderState,
    OrderIntent, OrderSide, OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.application.order_lifecycle import SubmissionUnknown


KST = timezone(timedelta(hours=9))


def guarded_config():
    return replace(default_shadow_breakout_config(), quantity=2,
                   session_exit_policy=REGULAR_SESSION_EXIT_POLICY, session_exit_buffer_seconds=60)


def inspect_checkpoint_in_process(database_path: str, journal_path: str) -> dict:
    """별도 프로세스에서 native 저장본만 복구한다. 주문·계좌 API는 구성하지 않는다."""
    store = fixture.SQLiteQueryStore(Path(database_path))
    store.initialize()
    try:
        repository = fixture.ForwardEvaluationRepository(store)
        spec, = repository.load_mock_automation_specs(fixture.ACCOUNT_REF)
        execution = fixture.ExecutionRepository(store)
        run_id = fixture.execution_run_id_for_spec(spec.spec_id)
        bundle = SimpleNamespace(
            account_ref=fixture.ACCOUNT_REF, run_id=run_id, repository=execution,
            automation_context=SimpleNamespace(spec_id=spec.spec_id, execution_run_id=run_id),
        )
        runner = MockAutomationRunner(store, bundle, spec)
        runner._apply_new_fills()
        before = runner._state.to_dict()
        cursor = runner.status["fill_cursor"]
        runner._apply_new_fills()
        assert runner._state.to_dict() == before, "persisted fills applied twice"
        assert runner.status["fill_cursor"] == cursor, "fill cursor advanced twice"
        journal = fixture.JournalRepository(Path(journal_path))
        scope = spec.account_scope
        now = datetime.fromisoformat(runner._state.opened_at) if runner._state.opened_at else fixture.NOW
        projection = fixture.sync_journal_execution_projection(
            fixture._ExecutionPageSource(execution, fixture.ACCOUNT_REF), journal,
            scope, page_limit=1000, now=now,
        )
        fills = journal.load_execution_fill_projections(scope)
        active = execution.active_intents("mock", fixture.ACCOUNT_REF, run_id)
        return {
            "pid": os.getpid(), "state": runner._state.to_dict(),
            "fill_cursor": runner.status["fill_cursor"],
            "pending_intent_id": runner.status.get("pending_intent_id"),
            "session_clock": runner.status.get("session_clock"),
            "active_intents": [{"intent_id": row.intent.intent_id, "filled_quantity": row.filled_quantity}
                               for row in active],
            "new_journal_fills": projection.detailed_fill_count,
            "journal_fill_ids": sorted(row["broker_execution_id"] for row in fills),
            "journal_buy_quantity": sum(row["quantity"] for row in fills if row["side"] == "BUY"),
            "journal_sell_quantity": sum(row["quantity"] for row in fills if row["side"] == "SELL"),
        }
    finally:
        store.close()


async def inspect_stopped_runtime_in_process(database_path: str, recovery_path: str) -> dict:
    """파일 DB의 새 소유권·복구가 영속 중지를 풀지 않는지 native 경로로 확인한다."""
    payload = json.loads(Path(recovery_path).read_text(encoding="utf-8"))
    now = datetime.fromisoformat(payload["observed_at"])
    store = fixture.SQLiteQueryStore(Path(database_path), now_provider=lambda: now)
    store.initialize()
    repository = fixture.ForwardEvaluationRepository(store)
    execution = fixture.ExecutionRepository(store)
    spec, = repository.load_mock_automation_specs(fixture.ACCOUNT_REF)
    run_id = fixture.execution_run_id_for_spec(spec.spec_id)
    transport = Mock(spec=["submit", "cancel"])
    transport.submit.side_effect = AssertionError("unexpected broker submission")
    transport.cancel.side_effect = AssertionError("unexpected broker cancellation")
    runtime = fixture.ExecutionRuntime(
        fixture.OrderLifecycle(execution, transport, now_provider=lambda: now), execution,
        account_ref=fixture.ACCOUNT_REF, run_id=run_id,
        owner_token="separate-process-owner", now_provider=lambda: now,
    )
    try:
        runtime.start(new_orders_enabled=False)
        initial_control = repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        assert initial_control.desired_state is MockAutomationDesiredState.STOPPED
        peer_repository = fixture.ExecutionRepository(store)
        peer = fixture.ExecutionRuntime(
            fixture.OrderLifecycle(peer_repository, transport), peer_repository,
            account_ref=fixture.ACCOUNT_REF, run_id=run_id, owner_token="competing-process-owner", now_provider=lambda: now,
        )
        try:
            peer.start(new_orders_enabled=False)
        except RuntimeError as error:
            assert "another mock execution runtime" in str(error)
        else:
            raise AssertionError("second owner acquired the same runtime")
        runtime.heartbeat()
        try:
            resume_mock_automation(repository, runtime, account_ref=fixture.ACCOUNT_REF,
                                   spec_id=spec.spec_id, resumed_at=now, reason="stale recovery test")
        except RuntimeError as error:
            assert "fresh broker reconciliation" in str(error)
        else:
            raise AssertionError("stale recovery resumed stopped automation")
        recovery = MockAccountRecovery(AccountSnapshot(
            fixture.ACCOUNT_REF, payload["cash"], 0, payload["positions"], now,
        ), ())
        costs = tuple(fixture.DailyTradeCost(
            date.fromisoformat(row["fill_date"]), date.fromisoformat(row["settlement_date"]),
            row["stock_code"], row["side"], row["gross_amount"], row["settlement_amount"],
            row["commission"], row["tax"], row["total_cost"], origin_scope=spec.account_scope,
        ) for row in payload["costs"])
        risk = build_mock_automation_risk_snapshot(
            recovery, execution.account_events("mock", fixture.ACCOUNT_REF, limit=1000).events,
            costs, execution.active_intents("mock", fixture.ACCOUNT_REF, run_id),
            execution_run_id=run_id, binding_revision=spec.binding_revision,
            reconciliation_revision=payload["risk_revision"], observed_at=now,
            broker_query_started_at=now, broker_query_completed_at=now,
        )
        repository.save_mock_automation_risk_snapshot(risk)
        decision = record_mock_automation_recovery_from_risk(
            repository, runtime, recovery, risk, account_ref=fixture.ACCOUNT_REF, spec_id=spec.spec_id,
        )
        assert repository.load_mock_automation_control(fixture.ACCOUNT_REF) == initial_control
        bundle = SimpleNamespace(
            account_ref=fixture.ACCOUNT_REF, run_id=run_id, repository=execution, runtime=runtime,
            automation_context=SimpleNamespace(spec_id=spec.spec_id, execution_run_id=run_id),
        )
        runner = MockAutomationRunner(store, bundle, spec)
        assert await runner.run_once() == 0
        assert runner.status["state"] == "STOPPED"
        assert runner._state.position_quantity == payload["positions"]["005930"]
        gate, submitted, _ = fixture.dispatch_mock_automation_decision_from_risk(
            repository, runtime, fixture._automation_decision(run_id, "EXIT", now), recovery, risk,
            account_ref=fixture.ACCOUNT_REF, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref,
        )
        assert gate.status.value == "BLOCKED" and "AUTOMATION_CONTROL_NOT_RUNNING" in gate.reasons
        assert submitted is None
        before_resume = repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        assert before_resume == initial_control
        resumed = resume_mock_automation(
            repository, runtime, account_ref=fixture.ACCOUNT_REF, spec_id=spec.spec_id,
            resumed_at=now, reason="explicit isolated-test resume",
        )
        assert resumed.desired_state is MockAutomationDesiredState.RUNNING
        assert resumed.control_revision == initial_control.control_revision + 1
        buy = execution.load(payload["buy_intent_id"])
        raw = replace(buy.intent, intent_id=buy.intent.intent_id + "-restart-probe",
                      expires_at=now + timedelta(seconds=30))
        try:
            runtime.submit(raw, recovery.account)
        except RuntimeError as error:
            assert "MOCK_NEW_ORDERS_DISABLED" in str(error)
        else:
            raise AssertionError("resume opened the raw submission gate")
        assert execution.load(raw.intent_id) is None
        transport.submit.assert_not_called()
        transport.cancel.assert_not_called()
        return {
            "pid": os.getpid(), "restored_position_quantity": runner._state.position_quantity,
            "restored_fill_cursor": runner.status["fill_cursor"],
            "control_before_explicit_resume": before_resume.to_dict(),
            "control_after_explicit_resume": resumed.to_dict(), "recovery_status": decision.status.value,
            "blocked_exit_reasons": list(gate.reasons), "stale_resume_rejected": True,
            "competing_owner_rejected": True, "raw_submission_rejected": True,
            "broker_submission_calls": 0, "broker_cancel_calls": 0,
        }
    finally:
        runtime.stop()
        store.close()


class ClockPriceTests(unittest.TestCase):
    def test_clock_does_not_retimestamp_old_or_future_prices(self):
        now = datetime(2026, 9, 21, 15, 19, 2, tzinfo=KST)
        state = StrategyState(status="open", symbol="005930", entry_price=71000,
                              position_quantity=2, opened_at=(now - timedelta(minutes=5)).isoformat())
        assessment = session_clock_assessment(guarded_config(), state, now)
        bar = SimpleNamespace(code="005930", close=71000, revision_id="actual-1",
                              observation_key="actual-key", bar_end=(now - timedelta(seconds=2)).isoformat(),
                              available_at=now.isoformat())
        decision, snapshot = clock_exit_decision(
            spec_id="spec-1", run_id="run-1", state=state, assessment=assessment,
            bar=bar, now=now, maximum_age_seconds=5,
        )
        self.assertEqual("EXIT", decision.final_action)
        self.assertEqual(state.to_dict(), decision.state_after)
        self.assertEqual(now.isoformat(), snapshot["price_reference"]["available_at"])
        for changes, reason in (
            ({"bar_end": (now - timedelta(minutes=10)).isoformat()}, "stale"),
            ({"available_at": (now + timedelta(seconds=1)).isoformat()}, "future"),
            ({"code": "000660"}, "missing"),
        ):
            changed = SimpleNamespace(**{**vars(bar), **changes})
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                clock_exit_decision(spec_id="spec-1", run_id="run-1", state=state,
                                    assessment=assessment, bar=changed, now=now, maximum_age_seconds=5)


class ClockWorkflowTests(unittest.IsolatedAsyncioTestCase):
    _rank = fixture.MockAutomationWorkflowTests._rank
    _bar = fixture.MockAutomationWorkflowTests._bar
    _poll_bar = fixture.MockAutomationWorkflowTests._poll_bar

    def setUp(self):
        with patch.object(fixture, "default_shadow_breakout_config", return_value=guarded_config()):
            fixture.MockAutomationWorkflowTests.setUp(self)
        self.cancel_calls = []
        self.cancel_unknown = False

        def cancel(intent, order_id, quantity=0):
            self.cancel_calls.append((intent.intent_id, order_id, quantity))
            if self.cancel_unknown:
                raise SubmissionUnknown("controlled cancellation response loss")
        self.transport.cancel = cancel

        async def refresh_account():
            return (await self._refresh_recovery()).account
        self.bundle.gateway = ManualMockOrderGateway(self.runtime, self.execution, refresh_account)
        self.addAsyncCleanup(self.bundle.gateway.close)

    async def _refresh_recovery(self):
        active = self.execution.active_intents("mock", fixture.ACCOUNT_REF, self.run_id)
        orders = tuple(BrokerOrderSnapshot(
            record.broker_order_id, fixture.ACCOUNT_REF, record.intent.symbol,
            record.state, record.filled_quantity, record.intent.quantity - record.filled_quantity,
            self.clock, (),
        ) for record in active if record.broker_order_id)
        for record in active:
            order = next((row for row in orders if row.broker_order_id == record.broker_order_id), None)
            if order is not None:
                self.runtime.reconcile(record.intent.intent_id, order)
        recovery = MockAccountRecovery(
            AccountSnapshot(fixture.ACCOUNT_REF, self.cash, 0, dict(self.positions), self.clock), orders,
        )
        self.risk_revision += 1
        risk = build_mock_automation_risk_snapshot(
            recovery, self.execution.account_events("mock", fixture.ACCOUNT_REF, limit=1000).events,
            tuple(self.costs), self.execution.active_intents("mock", fixture.ACCOUNT_REF, self.run_id),
            execution_run_id=self.run_id, binding_revision=self.binding["binding_revision"],
            reconciliation_revision=self.risk_revision, observed_at=self.clock,
            broker_query_started_at=self.clock, broker_query_completed_at=self.clock,
        )
        self.repository.save_mock_automation_risk_snapshot(risk)
        return recovery

    def _fill(self, intent_id, quantity, price=71000):
        record = self.execution.load(intent_id)
        self.clock += timedelta(seconds=1)
        filled = record.filled_quantity + quantity
        state = OrderState.FILLED if filled == record.intent.quantity else OrderState.PARTIALLY_FILLED
        side = record.intent.side.value
        self.runtime.reconcile(intent_id, BrokerOrderSnapshot(
            record.broker_order_id, fixture.ACCOUNT_REF, "005930", state, filled,
            record.intent.quantity - filled, self.clock,
            (BrokerFill(f"clock-{intent_id}-{side}-{filled}", quantity, price, self.clock),),
        ))
        held = self.positions.get("005930", 0) + (quantity if side == "BUY" else -quantity)
        self.positions = {"005930": held} if held else {}
        self.cash += (-1 if side == "BUY" else 1) * quantity * price
        self.costs.append(fixture.DailyTradeCost(
            self.clock.astimezone(KST).date(), self.clock.date(), "005930",
            "매수" if side == "BUY" else "매도", quantity * price, quantity * price, 0, 0, 0,
            origin_scope=self.spec.account_scope,
        ))

    async def _open(self):
        await self._poll_bar(0, 71000, 71200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        intent_id = self.runner.status["pending_intent_id"]
        self._fill(intent_id, 2)
        return intent_id

    async def _fresh_clock_exit(self):
        await self._open()
        self.clock = self.clock.replace(hour=6, minute=19, second=2)  # 15:19:02 KST
        self._bar(self.clock - timedelta(minutes=1, seconds=2), 71100, 71300)
        await self.runner.run_once()
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        return self.runner.status["pending_intent_id"]

    async def test_empty_feed_stops_at_max_hold_without_synthetic_exit(self):
        await self._open()
        cursor = self.runner._cursor
        self.clock += timedelta(minutes=10)
        self.assertEqual(0, await self.runner.run_once())
        self.assertEqual("STOPPED", self.runner.status["state"], self.runner.status)
        self.assertIn("max_hold_reached", self.runner.status["session_clock"]["assessment"]["reasons"])
        self.assertEqual("open", self.runner._state.status)
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual(cursor, self.runner._cursor)
        self.assertEqual(1, len(self.transport.calls))

    async def test_recent_cached_recovery_before_due_does_not_claim_exit_complete(self):
        await self._open()
        self.clock += timedelta(minutes=10)
        fresh = self._refresh_recovery
        async def cached_recovery():
            actual_now = self.clock
            self.clock -= timedelta(seconds=1)
            try:
                return await fresh()
            finally:
                self.clock = actual_now
        with patch.object(self.bundle.monitor, "refresh_recovery", side_effect=cached_recovery):
            await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"], self.runner.status)
        self.assertIn("predates_exit_due", self.runner.status["reason"])
        self.assertEqual("open", self.runner._state.status)
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual(1, len(self.transport.calls))
        stopped = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        self.assertEqual(MockAutomationDesiredState.STOPPED, stopped.desired_state)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual(1, len(self.transport.calls))

    async def test_clock_exit_uses_real_price_once_and_only_fill_closes_position(self):
        sell_id = await self._fresh_clock_exit()
        clock = self.runner.status["session_clock"]
        self.assertEqual(self.clock.isoformat(), clock["snapshot"]["price_reference"]["available_at"])
        self.assertEqual(["BUY", "SELL"], [row[1] for row in self.transport.calls])
        self.assertEqual("open", self.runner._state.status)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual([], self.cancel_calls)
        self._fill(sell_id, 2, 71100)
        await self.runner.run_once()
        self.assertEqual("cooldown", self.runner._state.status)
        self.assertEqual("COMPLETED", self.runner.status["session_clock"]["phase"])

    async def test_expired_unfilled_buy_is_cancelled_once_even_after_unknown_and_restart(self):
        await self._poll_bar(0, 71000, 71200)
        intent_id = self.runner.status["pending_intent_id"]
        self.clock += timedelta(seconds=5)
        self.cancel_unknown = True
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"], self.runner.status)
        self.assertEqual(OrderState.CANCEL_PENDING, self.execution.load(intent_id).state)
        self.assertEqual(1, len(self.cancel_calls))
        self.assertNotEqual("open", self.runner._state.status)
        # Broker may still report accepted after a lost cancel response.
        self.clock += timedelta(seconds=1)
        self.runtime.reconcile(intent_id, BrokerOrderSnapshot(
            self.execution.load(intent_id).broker_order_id, fixture.ACCOUNT_REF, "005930",
            OrderState.ACCEPTED, 0, 2, self.clock, (),
        ))
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        await self.runner.run_once()
        self.assertEqual(1, len(self.cancel_calls))
        self.assertEqual(1, len(self.transport.calls))

    async def test_partial_buy_and_late_fill_remain_owned_after_cancel(self):
        await self._poll_bar(0, 71000, 71200)
        intent_id = self.runner.status["pending_intent_id"]
        self._fill(intent_id, 1)
        self.clock += timedelta(seconds=5)
        await self.runner.run_once()
        self.assertEqual(1, self.runner._state.position_quantity)
        self.assertEqual(OrderState.CANCEL_PENDING, self.execution.load(intent_id).state)
        self._fill(intent_id, 1)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual(1, len(self.cancel_calls))

    async def test_unknown_submission_is_stopped_without_cancel_or_resubmission(self):
        with patch.object(self.transport, "submit", side_effect=SubmissionUnknown("response lost")) as submit:
            await self._poll_bar(0, 71000, 71200)
            intent_id = self.runner.status["pending_intent_id"]
            self.clock += timedelta(seconds=5)
            await self.runner.run_once()
            self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
            await self.runner.run_once()
            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, self.execution.load(intent_id).state)
            self.assertEqual("STOPPED", self.runner.status["state"])
            self.assertEqual(1, submit.call_count)
            self.assertEqual([], self.cancel_calls)
            self.assertNotEqual("open", self.runner._state.status)

    async def test_broker_fill_before_cancel_transport_prevents_cancellation(self):
        await self._poll_bar(0, 71000, 71200)
        intent_id = self.runner.status["pending_intent_id"]
        self.clock += timedelta(seconds=5)
        async def filled_account():
            self._fill(intent_id, 2)
            return (await self._refresh_recovery()).account
        with patch.object(self.bundle.gateway, "_account_refresh", side_effect=filled_account):
            await self.runner.run_once()
        await self.runner.run_once()
        self.assertEqual(OrderState.FILLED, self.execution.load(intent_id).state)
        self.assertEqual([], self.cancel_calls)
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual("STOPPED", self.runner.status["state"])

    async def test_stop_write_failure_closes_gate_and_does_not_cancel(self):
        await self._poll_bar(0, 71000, 71200)
        self.clock += timedelta(seconds=5)
        self.runtime.set_new_orders_enabled(True)
        with patch.object(self.runner._repository, "save_mock_automation_control",
                          side_effect=OSError("controlled stop persistence failure")):
            with self.assertRaisesRegex(OSError, "stop persistence"):
                await self.runner.run_once()
        self.assertFalse(self.runtime._new_orders_enabled)
        self.assertEqual([], self.cancel_calls)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual(1, len(self.cancel_calls))

    async def test_resume_requires_fresh_recovery_and_preserves_actual_position(self):
        await self._open()
        self.clock += timedelta(minutes=10)
        await self.runner.run_once()
        with self.assertRaisesRegex(RuntimeError, "fresh broker reconciliation"):
            resume_mock_automation(self.repository, self.runtime, account_ref=fixture.ACCOUNT_REF,
                                   spec_id=self.spec.spec_id, resumed_at=self.clock, reason="explicit resume")
        self.clock = self.clock.replace(hour=6, minute=19, second=2)
        recovery = await self._refresh_recovery()
        risk = self.repository.load_latest_mock_automation_risk(fixture.ACCOUNT_REF)
        record_mock_automation_recovery_from_risk(
            self.repository, self.runtime, recovery, risk,
            account_ref=fixture.ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        resume_mock_automation(self.repository, self.runtime, account_ref=fixture.ACCOUNT_REF,
                               spec_id=self.spec.spec_id, resumed_at=self.clock, reason="explicit resume")
        self._bar(self.clock - timedelta(minutes=1, seconds=2), 71100, 71300)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self.assertEqual("open", self.runner._state.status)
        self.assertEqual(2, self.runner._state.position_quantity)
        self.assertEqual(["BUY", "SELL"], [row[1] for row in self.transport.calls])

    async def test_subsequent_actual_position_gets_a_new_clock_decision(self):
        await self._open()
        self.clock += timedelta(minutes=10)
        self._bar(self.clock.replace(second=0) - timedelta(minutes=1), 71100, 71300)
        await self.runner.run_once()
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        first_decision = self.runner.status["session_clock"]["decision"]["decision_id"]
        self._fill(self.runner.status["pending_intent_id"], 2, 71100)
        await self.runner.run_once()
        self.assertEqual("COMPLETED", self.runner.status["session_clock"]["phase"])
        self.clock += timedelta(minutes=4)
        control = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        # A second actual O1 BUY fill, never a synthetic strategy-state change.
        intent = OrderIntent(
            "fixture-second-entry", self.run_id, "fixture-second-decision", fixture.ACCOUNT_REF,
            "mock", "005930", "KRX", OrderSide.BUY, 2, OrderType.LIMIT, 71100,
            self.clock, self.clock + timedelta(seconds=5), "fixture-policy",
        )
        self.runtime.submit_automation_intent(
            intent, AccountSnapshot(fixture.ACCOUNT_REF, self.cash, 0, {}, self.clock),
            spec_id=self.spec.spec_id, control_revision=control.control_revision,
        )
        self._fill(intent.intent_id, 2, 71100)
        self.clock += timedelta(minutes=10)
        self._bar(self.clock.replace(second=0) - timedelta(minutes=1), 71200, 71400)
        await self.runner.run_once()
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self.assertNotEqual(first_decision, self.runner.status["session_clock"]["decision"]["decision_id"])
        self.assertEqual(["BUY", "SELL", "BUY", "SELL"], [row[1] for row in self.transport.calls])

    async def test_stale_clock_revision_cannot_overwrite_concurrent_user_stop(self):
        control = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=fixture.ACCOUNT_REF,
                                      spec_id=self.spec.spec_id, stopped_at=self.clock, reason="operator stop")
        stopped = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        with self.assertRaisesRegex(RuntimeError, "revision"):
            emergency_stop_mock_automation(
                self.repository, self.runtime, account_ref=fixture.ACCOUNT_REF,
                spec_id=self.spec.spec_id, stopped_at=self.clock, reason="stale clock stop",
                expected_control_revision=control.control_revision,
            )
        self.assertEqual(stopped, self.repository.load_mock_automation_control(fixture.ACCOUNT_REF))

    async def test_pending_exit_is_preserved_until_expiry_then_stopped_and_cancelled(self):
        sell_id = await self._fresh_clock_exit()
        self.clock += timedelta(seconds=2)
        await self.runner.run_once()
        self.assertEqual([], self.cancel_calls)
        self.clock += timedelta(seconds=3)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual(sell_id, self.cancel_calls[0][0])
        self.assertEqual("open", self.runner._state.status)
        self.assertEqual(2, len(self.transport.calls))

    async def test_exact_deadline_does_not_submit_new_exit(self):
        await self._open()
        self.clock = self.clock.replace(hour=6, minute=20, second=0)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertIn("session_deadline_passed", self.runner.status["session_clock"]["assessment"]["reasons"])
        self.assertEqual(1, len(self.transport.calls))

    async def test_clock_operates_during_bootstrap_without_checkpointing_partial_seed(self):
        await self._open()
        checkpoint = self.store.load_documents("execution_mock_automation_runner_current")
        self.runner._bootstrap_pending = True
        self.clock += timedelta(minutes=10)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual(checkpoint, self.store.load_documents("execution_mock_automation_runner_current"))
        self.assertTrue(self.store.load_documents(CLOCK_COLLECTION))

    async def test_user_stop_preserves_existing_no_automatic_cancel_contract(self):
        await self._poll_bar(0, 71000, 71200)
        emergency_stop_mock_automation(self.repository, self.runtime,
                                      account_ref=fixture.ACCOUNT_REF, spec_id=self.spec.spec_id,
                                      stopped_at=self.clock, reason="operator stop")
        self.clock += timedelta(minutes=10)
        await self.runner.run_once()
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual([], self.cancel_calls)
        self.assertEqual([], self.store.load_documents(CLOCK_COLLECTION))

    async def test_stale_stop_cannot_replace_another_run_control(self):
        control = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        replacement = replace(control, active_spec_id="another-spec", execution_run_id="another-run",
                              control_revision=control.control_revision + 1)
        self.assertTrue(self.repository.save_mock_automation_control(replacement,
                                                                    expected_revision=control.control_revision))
        with self.assertRaisesRegex(RuntimeError, "does not belong"):
            emergency_stop_mock_automation(self.repository, self.runtime,
                                          account_ref=fixture.ACCOUNT_REF, spec_id=self.spec.spec_id,
                                          stopped_at=self.clock, reason="stale clock stop")
        self.assertEqual(replacement, self.repository.load_mock_automation_control(fixture.ACCOUNT_REF))

    async def test_receipt_failure_prevents_transport_cancellation(self):
        await self._poll_bar(0, 71000, 71200)
        self.clock += timedelta(seconds=5)
        original = self.store.upsert_documents
        def fail_clock(collection, values):
            if collection == CLOCK_COLLECTION:
                raise OSError("controlled clock persistence failure")
            return original(collection, values)
        with patch.object(self.store, "upsert_documents", side_effect=fail_clock):
            with self.assertRaisesRegex(OSError, "clock persistence"):
                await self.runner.run_once()
        self.assertEqual([], self.cancel_calls)
        await self.runner.run_once()
        self.assertEqual(1, len(self.cancel_calls))

    async def test_shutdown_waits_for_cancel_and_its_clock_receipt(self):
        await self._poll_bar(0, 71000, 71200)
        self.clock += timedelta(seconds=5)
        entered, release = asyncio.Event(), asyncio.Event()
        cancel = self.bundle.gateway.cancel
        async def blocked_cancel(intent_id):
            result = await cancel(intent_id)
            entered.set()
            await release.wait()
            return result
        with patch.object(self.bundle.gateway, "cancel", side_effect=blocked_cancel):
            self.runner._task = asyncio.create_task(self.runner.run_once())
            await asyncio.wait_for(entered.wait(), 3)
            close = asyncio.create_task(self.runner.close())
            try:
                await asyncio.sleep(0.02)
                self.assertFalse(close.done())
            finally:
                release.set()
                await asyncio.wait_for(close, 3)
        self.assertEqual(1, len(self.cancel_calls))
        persisted = self.store.load_documents(CLOCK_COLLECTION)[0]["document"]
        self.assertEqual("STOPPED", persisted["phase"])
        self.assertEqual(1, len(persisted["cancel_attempted_intent_ids"]))

    async def test_partial_clock_exit_restart_finishes_once_and_projects_each_fill_to_journal(self):
        exit_id = await self._fresh_clock_exit()
        self._fill(exit_id, 1, price=71100)
        await self.runner.run_once()
        self.assertEqual({"005930": 1}, self.positions)
        self.assertEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(2, len(self.transport.calls))

        # 실제 checkpoint와 체결 원장에서 부분 청산 상태를 복구한다.
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(2, len(self.transport.calls), "남은 매도 주문을 중복 제출하지 않는다")
        self.assertEqual([], self.cancel_calls)
        self._fill(exit_id, 1, price=71100)
        await self.runner.run_once()
        await self.runner.run_once()
        self.assertEqual({}, self.positions)
        self.assertEqual("cooldown", self.runner.status["strategy_state"])
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual([], self.cancel_calls)
        self.assertEqual((), self.execution.active_intents("mock", fixture.ACCOUNT_REF, self.run_id))

        # 마지막 체결 후 다시 재시작해도 포지션과 주문이 되살아나지 않는다.
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual("cooldown", self.runner.status["strategy_state"])
        self.assertEqual(2, len(self.transport.calls))
        scope = fixture.AccountScope("kiwoom", fixture.AccountEnvironment.MOCK, fixture.ACCOUNT_REF)
        with tempfile.TemporaryDirectory() as directory:
            journal = fixture.JournalRepository(Path(directory) / "journal.sqlite3")
            first = fixture.sync_journal_execution_projection(
                fixture._ExecutionPageSource(self.execution, fixture.ACCOUNT_REF), journal,
                scope, page_limit=1000, now=self.clock,
            )
            second = fixture.sync_journal_execution_projection(
                fixture._ExecutionPageSource(self.execution, fixture.ACCOUNT_REF), journal,
                scope, page_limit=1000, now=self.clock,
            )
            fills = journal.load_execution_fill_projections(scope)
        self.assertEqual(3, first.detailed_fill_count)
        self.assertEqual(0, second.detailed_fill_count)
        self.assertEqual(3, len({row["broker_execution_id"] for row in fills}))
        self.assertEqual(2, sum(row["quantity"] for row in fills if row["side"] == "BUY"))
        self.assertEqual(2, sum(row["quantity"] for row in fills if row["side"] == "SELL"))

    async def test_file_checkpoint_and_journal_restore_across_separate_processes(self):
        exit_id = await self._fresh_clock_exit()
        self._fill(exit_id, 1, price=71100)
        await self.runner.run_once()
        self.assertEqual(1, self.runner._state.position_quantity)
        command = (
            "import json,sys; "
            "from tests.unit.test_mock_automation_session_clock import inspect_checkpoint_in_process; "
            "print(json.dumps(inspect_checkpoint_in_process(sys.argv[1],sys.argv[2])))"
        )
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "execution.sqlite3"
            journal = Path(directory) / "journal.sqlite3"

            def snapshot():
                # SQLite backup을 써서 native checkpoint·체결·시계 문서의 한 벌을 보존한다.
                with self.store._connection() as source, closing(sqlite3.connect(database)) as target:
                    source.backup(target)

            def child():
                environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "src")))}
                result = subprocess.run(
                    [sys.executable, "-c", command, str(database), str(journal)],
                    cwd=root, env=environment, text=True, encoding="utf-8", errors="replace",
                    capture_output=True, timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                value = json.loads(result.stdout)
                self.assertNotEqual(os.getpid(), value["pid"])
                return value

            snapshot()
            partial = await asyncio.to_thread(child)
            self.assertEqual("open", partial["state"]["status"])
            self.assertEqual(1, partial["state"]["position_quantity"])
            self.assertEqual(json.loads(json.dumps(self.runner._state.to_dict())), partial["state"])
            self.assertEqual([{"intent_id": exit_id, "filled_quantity": 1}], partial["active_intents"])
            self.assertEqual(json.loads(json.dumps(self.runner.status["session_clock"])), partial["session_clock"])
            self.assertEqual(2, partial["new_journal_fills"])
            self.assertEqual((2, 1), (partial["journal_buy_quantity"], partial["journal_sell_quantity"]))

            self._fill(exit_id, 1, price=71100)
            await self.runner.run_once()
            snapshot()
            completed = await asyncio.to_thread(child)
            repeated = await asyncio.to_thread(child)
            self.assertEqual("cooldown", completed["state"]["status"])
            self.assertEqual(json.loads(json.dumps(self.runner._state.to_dict())), completed["state"])
            self.assertEqual([], completed["active_intents"])
            self.assertEqual(1, completed["new_journal_fills"])
            self.assertEqual(0, repeated["new_journal_fills"])
            self.assertEqual(3, len(set(completed["journal_fill_ids"])))
            self.assertEqual((2, 2), (completed["journal_buy_quantity"], completed["journal_sell_quantity"]))
            self.assertEqual(completed["state"], repeated["state"])
            self.assertEqual(completed["fill_cursor"], repeated["fill_cursor"])
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual([], self.cancel_calls)

    async def test_new_process_owner_and_fresh_recovery_preserve_stop_until_explicit_resume(self):
        buy_id = await self._open()
        self.clock += timedelta(seconds=1)
        emergency_stop_mock_automation(
            self.repository, self.runtime, account_ref=fixture.ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=self.clock, reason="operator stopped before restart",
        )
        await self.runner.run_once()
        control = self.repository.load_mock_automation_control(fixture.ACCOUNT_REF)
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.runtime.stop()
        self.assertFalse(self.runtime._active)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "execution.sqlite3"
            recovery = Path(directory) / "fake-broker-recovery.json"
            with self.store._connection() as source, closing(sqlite3.connect(database)) as target:
                source.backup(target)
            recovery.write_text(json.dumps({
                "observed_at": (self.clock + timedelta(seconds=1)).isoformat(),
                "cash": self.cash, "positions": self.positions, "buy_intent_id": buy_id,
                "risk_revision": self.risk_revision + 1,
                "costs": [{
                    "fill_date": cost.fill_date.isoformat(), "settlement_date": cost.settlement_date.isoformat(),
                    **{name: getattr(cost, name) for name in (
                        "stock_code", "side", "gross_amount", "settlement_amount", "commission", "tax", "total_cost",
                    )},
                } for cost in self.costs],
            }), encoding="utf-8")
            root = Path(__file__).resolve().parents[2]
            command = (
                "import asyncio,json,sys; "
                "from tests.unit.test_mock_automation_session_clock import inspect_stopped_runtime_in_process; "
                "print(json.dumps(asyncio.run(inspect_stopped_runtime_in_process(sys.argv[1],sys.argv[2]))))"
            )
            result = await asyncio.to_thread(
                subprocess.run, [sys.executable, "-c", command, str(database), str(recovery)],
                cwd=root, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "src")))},
                text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            restored = json.loads(result.stdout)
            self.assertNotEqual(os.getpid(), restored["pid"])
            self.assertEqual(2, restored["restored_position_quantity"])
            self.assertEqual(control.to_dict(), restored["control_before_explicit_resume"])
            self.assertEqual("RUNNING", restored["control_after_explicit_resume"]["desired_state"])
            self.assertEqual(0, restored["broker_submission_calls"])
            self.assertEqual(0, restored["broker_cancel_calls"])
            with fixture.SQLiteQueryStore(database)._connection() as connection:
                cursor = connection.execute("SELECT COUNT(*) FROM central_execution_runtime_leases")
                self.assertEqual(0, cursor.fetchone()[0], "child owner released its lease")
        self.assertEqual(1, len(self.transport.calls))
        self.assertEqual([], self.cancel_calls)
