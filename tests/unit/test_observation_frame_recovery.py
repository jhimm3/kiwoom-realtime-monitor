from __future__ import annotations

import asyncio
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.application.breakout_strategy import StrategyState
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.central_server.database_observation_readers import (
    OBSERVATION_DELIVERY_PROTOCOL, ObservationRevisionPage,
)
from tests.unit.test_candidate_monitor import FakeStore, _config, _rank, _bar
from tests.unit import test_mock_automation_runner as mock_fixtures

ACCOUNT_REF = mock_fixtures.ACCOUNT_REF


class RecoveryFixture:
    def __init__(self, test, mock=False, rows=None, cursor=4):
        self.mock = mock
        self.rows = rows or [_rank(1), _bar(2, 0, 1000, 1010),
                             _bar(3, 1, 1010, 1020), _bar(4, 2, 1030, 1040)]
        self.pending = False
        self.reads = []
        if mock:
            self.case = mock_fixtures.MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
            self.case.setUp()
            test.addCleanup(self.case.store.close)
            self.store = self.case.store
        else:
            self.store = FakeStore()
        runner = self.create()
        runner._cursor = cursor
        runner._state = StrategyState(status="open", symbol="005930", entry_price=10000,
                                      position_quantity=2, opened_at="2026-09-12T00:00:00+00:00",
                                      emitted_candidate_keys=("already-emitted",))
        if mock:
            runner._status = {"state": "WAITING_ORDER", "pending_intent_id": "pending-original",
                              "orders_enabled": False}
            runner._fill_cursor = 3
            runner._seen_fill_ids = {"already-filled"}
        runner._save_checkpoint()
        self.original = copy.deepcopy(self.document())
        self.original.pop("delivery_protocol")
        self.write_document(self.original)
        self.store.load_observation_revision_page = self.page
        self.runner = self.create()

    def create(self):
        if not self.mock:
            return CandidateMonitor(self.store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        runner = MockAutomationRunner(self.store, self.case.bundle, self.case.spec)
        runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=self.case.spec.spec_id, desired_state=SimpleNamespace(value="RUNNING"))
        return runner

    def document(self):
        if not self.mock:
            return self.store.checkpoints[next(iter(self.store.checkpoints))]
        return self.store.load_documents("execution_mock_automation_runner_current", ACCOUNT_REF, 1)[0]["document"]

    def write_document(self, doc):
        if not self.mock:
            self.store.checkpoints[next(iter(self.store.checkpoints))] = doc
        else:
            self.store.upsert_documents("execution_mock_automation_runner_current",
                [{"owner": ACCOUNT_REF, "key": "current", "document": doc}])

    def page(self, after, kinds, limit, *, through_sequence):
        self.reads.append((after, limit, through_sequence))
        if self.pending:
            return ObservationRevisionPage(ready=False, reason="pending_sequence_commit")
        values = [r for r in self.rows if after < r["accepted_sequence"] <= through_sequence
                  and r["kind"] in kinds]
        return ObservationRevisionPage(rows=tuple(values[:limit]), safe_through=through_sequence,
                                       exhausted=len(values) <= limit)

    async def poll(self, limit=1000):
        return await self.runner.run_once(limit=limit) if self.mock else self.runner.run_once(limit=limit)


class ObservationFrameRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_full_prefix_repair_preserves_execution_state_and_never_consumes_history(self):
        rows = [_rank(1), _bar(2, 0, 1000, 1010)] + [_rank(i) for i in range(3, 6002)]
        rows += [_bar(6002, 0, 1300, 1310), _bar(6003, 1, 1400, 1410),
                 _bar(6004, 2, 1500, 1510), _bar(6005, 3, 1600, 1610)]
        rows[-3]["payload"]["market"] = "NXT"
        rows[-2]["payload"]["window_closed"] = False
        for mock in (False, True):
            with self.subTest(mock=mock):
                fixture = RecoveryFixture(self, mock, rows, cursor=6004)
                fixture.pending = True
                with patch.object(fixture.runner, "_consume", side_effect=AssertionError("historical decision")):
                    self.assertEqual(0, await fixture.poll(limit=701))
                    self.assertEqual(fixture.original, fixture.document())
                    fixture.pending = False
                    for _ in range(8):
                        self.assertEqual(0, await fixture.poll(limit=701))
                        self.assertEqual(fixture.original, fixture.document())
                        fixture.runner._save_checkpoint()
                        self.assertEqual(fixture.original, fixture.document())
                    self.assertEqual(0, await fixture.poll(limit=701))
                saved = fixture.document()
                self.assertEqual(6004, saved["input_cursor" if mock else "cursor"])
                self.assertEqual(fixture.original["strategy_state"], saved["strategy_state"])
                self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved["delivery_protocol"])
                self.assertEqual(6004, saved["input_recovery"]["rows_read"])
                self.assertFalse(saved["input_recovery"]["historical_decisions_recomputed"])
                self.assertEqual([1300], [r["close"] for r in saved["bars"]])
                if mock:
                    for key in ("fill_cursor", "seen_fill_ids", "pending_intent_id", "spec_id",
                                "account_ref", "execution_run_id", "candidate_package_hash"):
                        self.assertEqual(fixture.original[key], saved[key])
                # Once saved, restart does not rewind or repair again; >C alone is consumed.
                fixture.runner = fixture.create()
                self.assertIsNone(fixture.runner._frame_recovery)
                future = rows[-1]
                fixture.store.load_observation_revisions_after = lambda *args: [future]
                if mock:
                    from unittest.mock import AsyncMock
                    consumer = AsyncMock()
                else:
                    from unittest.mock import Mock
                    consumer = Mock()
                fixture.runner._consume = consumer
                self.assertEqual(1, await fixture.poll())
                consumer.assert_called_once_with(future)

    async def test_partial_restart_and_final_save_failure_never_publish_scratch(self):
        for mock in (False, True):
            fixture = RecoveryFixture(self, mock)
            await fixture.poll(limit=1)
            self.assertEqual([], list(fixture.runner._bars))
            fixture.runner = fixture.create()  # Scratch is deliberately not a durable partial checkpoint.
            self.assertEqual(0, fixture.runner._frame_recovery.after_sequence)
            method = "upsert_documents" if mock else "save_shadow_monitor_state"
            with patch.object(fixture.store, method, side_effect=OSError("native checkpoint failed")):
                with self.assertRaisesRegex(OSError, "native checkpoint failed"):
                    await fixture.poll()
                fixture.runner._save_checkpoint()
            self.assertEqual(fixture.original, fixture.document())
            self.assertIsNone(fixture.runner._delivery_protocol)
            reads = len(fixture.reads)
            await fixture.poll()
            self.assertEqual(reads, len(fixture.reads))  # Complete scratch retries its commit only.
            self.assertIsNone(fixture.runner._frame_recovery)

    async def test_lost_final_ack_has_only_old_or_fully_repaired_checkpoint(self):
        for mock in (False, True):
            fixture = RecoveryFixture(self, mock)
            method = "upsert_documents" if mock else "save_shadow_monitor_state"
            native = getattr(fixture.store, method)
            def lost_ack(*args, **kwargs):
                native(*args, **kwargs)
                raise OSError("lost COMMIT ack")
            with patch.object(fixture.store, method, side_effect=lost_ack):
                with self.assertRaisesRegex(OSError, "lost COMMIT ack"):
                    await fixture.poll()
            self.assertIsNone(fixture.runner._delivery_protocol)
            saved = fixture.document()
            self.assertEqual(3, len(saved["bars"]))
            self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved["delivery_protocol"])
            fixture.runner = fixture.create()
            self.assertIsNone(fixture.runner._frame_recovery)
            self.assertEqual(fixture.original["strategy_state"], saved["strategy_state"])

    async def test_mock_fills_and_stop_control_continue_without_partial_checkpoint(self):
        fixture = RecoveryFixture(self, True)
        fixture.case.events.values.append(fixture.case.event(4, "new-fill", "BUY", 1, 10000))
        fixture.pending = True
        await fixture.poll()
        self.assertEqual(3, fixture.runner._state.position_quantity)
        self.assertEqual(4, fixture.runner._fill_cursor)
        fixture.runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=fixture.case.spec.spec_id, desired_state=SimpleNamespace(value="STOPPED"))
        reads = len(fixture.reads)
        await fixture.poll()
        self.assertEqual(reads, len(fixture.reads))
        self.assertEqual("STOPPED", fixture.runner.status["state"])
        self.assertEqual("pending-original", fixture.runner.status["pending_intent_id"])
        self.assertEqual(fixture.original, fixture.document())
        fixture.runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=fixture.case.spec.spec_id, desired_state=SimpleNamespace(value="RUNNING"))
        fixture.pending = False
        await fixture.poll()
        saved = fixture.document()
        self.assertEqual(4, saved["fill_cursor"])
        self.assertEqual(3, saved["strategy_state"]["position_quantity"])
        self.assertEqual(["already-filled", "new-fill"], saved["seen_fill_ids"])

    async def test_unknown_protocol_is_rejected_without_resetting_checkpoint(self):
        for mock in (False, True):
            fixture = RecoveryFixture(self, mock)
            document = {**fixture.original, "delivery_protocol": "future/v99"}
            fixture.write_document(document)
            with self.assertRaisesRegex(RuntimeError, "unsupported_.*observation_delivery_protocol"):
                fixture.create()
            self.assertEqual(document, fixture.document())

    async def test_close_and_repeated_cancel_wait_for_actual_native_recovery_commit(self):
        for mock in (False, True):
            fixture = RecoveryFixture(self, mock)
            entered, release = threading.Event(), threading.Event()
            method = "upsert_documents" if mock else "save_shadow_monitor_state"
            native = getattr(fixture.store, method)
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release native commit")
                return native(*args, **kwargs)
            with patch.object(fixture.store, method, side_effect=blocked):
                await fixture.runner.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                closing = asyncio.create_task(fixture.runner.close())
                try:
                    await asyncio.sleep(0.02)
                    self.assertFalse(closing.done())
                    fixture.runner._task.cancel()
                    await asyncio.sleep(0.02)
                    self.assertFalse(closing.done())
                    self.assertEqual(fixture.original, fixture.document())
                finally:
                    release.set()
                await asyncio.wait_for(closing, 5)
            self.assertIsNone(fixture.runner._frame_recovery)
            self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, fixture.document()["delivery_protocol"])

    async def test_mock_close_drains_fill_work_before_discarding_partial_recovery(self):
        fixture = RecoveryFixture(self, True)
        entered, release = threading.Event(), threading.Event()
        native = fixture.runner._apply_new_fills
        def blocked_fill():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release fill reader")
            native()
        with patch.object(fixture.runner, "_apply_new_fills", side_effect=blocked_fill):
            await fixture.runner.start()
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            closing = asyncio.create_task(fixture.runner.close())
            try:
                await asyncio.sleep(0.02)
                self.assertFalse(closing.done())
            finally:
                release.set()
            await asyncio.wait_for(closing, 5)
        self.assertEqual(fixture.original, fixture.document())
        self.assertIsNotNone(fixture.runner._frame_recovery)
