from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.forward_evaluation import (
    build_forward_report,
    forward_evidence_from_sources,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    ForwardEvidence,
    ForwardReportStatus,
    StrategyLifecycleStage,
    strategy_stage_revision,
)
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


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
        "minimum_adjusted_net_pnl_won": 50_000,
        "maximum_drawdown_ppm": 100_000,
        "maximum_exposure_ppm": 500_000,
    }
    values.update(changes)
    return ForwardCriteria(**values)


def _spec(criteria: ForwardCriteria | None = None) -> ForwardEvaluationSpec:
    return ForwardEvaluationSpec(
        "strategy-1", "family/v1", ("factor/v1",), "policy/v1", "nas",
        "mock-account", "mock", NOW, NOW + timedelta(days=7), NOW - timedelta(days=1),
        criteria or _criteria(),
    )


def _evidence(spec: ForwardEvaluationSpec, **changes: object) -> ForwardEvidence:
    values = {
        "profile_id": spec.profile_id, "as_of": spec.evaluation_end,
        "finalized": True, "comparable_observation_count": 1_000,
        "coverage_ppm": 999_000, "p95_delay_ms": 500, "gap_count": 0,
        "submission_unknown_count": 0, "rejected_order_count": 0,
        "cancel_failure_count": 0, "reconnect_count": 0,
        "balance_mismatch_count": 0, "no_trade_count": 12,
        "active_day_count": 6, "closed_trade_count": 12,
        "broker_net_pnl_won": 100_000, "broker_reported_cost_won": 15_000,
        "additional_unmodeled_cost_won": 20_000,
        "max_drawdown_ppm": 50_000, "exposure_ppm": 200_000,
    }
    values.update(changes)
    return ForwardEvidence(**values)


class ForwardReportTests(unittest.TestCase):
    def test_completed_evidence_passes_without_double_subtracting_broker_cost(self) -> None:
        spec = _spec()
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)

        self.assertEqual(ForwardReportStatus.PASSED, report.status)
        self.assertTrue(report.promotion_eligible)
        self.assertEqual(80_000, report.performance_metrics["adjusted_net_pnl_won"])
        self.assertEqual(15_000, report.performance_metrics["broker_reported_cost_won"])
        self.assertEqual(set(), {gate.status for gate in report.gates} - {"PASSED"})

    def test_tbd_threshold_blocks_promotion(self) -> None:
        spec = _spec(_criteria(maximum_drawdown_ppm=None))
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)

        self.assertEqual(ForwardReportStatus.BLOCKED, report.status)
        self.assertFalse(report.promotion_eligible)
        self.assertIn("criterion_tbd:max_drawdown_ppm", report.reasons)

    def test_incomplete_minimum_is_pending_but_maximum_breach_fails_immediately(self) -> None:
        spec = _spec()
        pending = build_forward_report(
            spec,
            _evidence(
                spec, as_of=NOW + timedelta(days=2), finalized=False,
                active_day_count=2, closed_trade_count=2,
            ),
            computed_at=NOW + timedelta(days=2),
        )
        failed = build_forward_report(
            spec,
            _evidence(
                spec, as_of=NOW + timedelta(days=2), finalized=False,
                submission_unknown_count=1,
            ),
            computed_at=NOW + timedelta(days=2),
        )

        self.assertEqual(ForwardReportStatus.PENDING, pending.status)
        self.assertEqual(ForwardReportStatus.FAILED, failed.status)
        self.assertIn("criterion_not_met:submission_unknown_count", failed.reasons)

    def test_profile_mismatch_is_rejected_and_report_round_trips(self) -> None:
        spec = _spec()
        with self.assertRaisesRegex(ValueError, "different profile"):
            build_forward_report(
                spec, _evidence(spec, profile_id="other"), computed_at=spec.evaluation_end,
            )
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        self.assertTrue(repository.save_report(report))
        self.assertFalse(repository.save_report(report))
        self.assertEqual((report,), repository.load_reports(spec.profile_id))

    def test_v1_summary_and_o1_events_are_adapted_without_duplicate_incidents(self) -> None:
        spec = _spec()
        event = {"event_id": "unknown-1", "event_type": "SUBMISSION_RESPONSE_UNKNOWN"}
        evidence = forward_evidence_from_sources(
            profile_id=spec.profile_id, as_of=spec.evaluation_end, finalized=True,
            validation_summary={
                "comparable_count": 999, "unmatched_count": 1,
                "arrival_gap_ms": {"p95": 499.6},
            },
            execution_events=(
                event, event,
                {"event_id": "reject-1", "event_type": "PRECHECK_REJECTED"},
                {"event_id": "cancel-1", "event_type": "CANCEL_RESPONSE_UNKNOWN"},
                {"event_id": "no-trade-1", "event_type": "NO_TRADE"},
            ),
            performance={
                "active_day_count": 6, "closed_trade_count": 12,
                "broker_net_pnl_won": 100_000, "broker_reported_cost_won": 15_000,
                "additional_unmodeled_cost_won": 20_000,
                "max_drawdown_ppm": 50_000, "exposure_ppm": 200_000,
            },
        )

        self.assertEqual(999_000, evidence.coverage_ppm)
        self.assertEqual(500, evidence.p95_delay_ms)
        self.assertEqual(1, evidence.submission_unknown_count)
        self.assertEqual(1, evidence.rejected_order_count)
        self.assertEqual(1, evidence.cancel_failure_count)
        self.assertEqual(1, evidence.no_trade_count)

    def test_broker_mock_stage_requires_stored_final_pass_for_same_strategy(self) -> None:
        spec = _spec()
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        repository.save_profile(spec)
        stages = (
            (StrategyLifecycleStage.DRAFT, StrategyLifecycleStage.EVALUATED, "research-1"),
            (StrategyLifecycleStage.EVALUATED, StrategyLifecycleStage.VALIDATED, "validation-1"),
            (StrategyLifecycleStage.VALIDATED, StrategyLifecycleStage.SHADOW, "shadow-1"),
        )
        for index, (previous, stage, evidence_ref) in enumerate(stages):
            repository.save_stage_revision(strategy_stage_revision(
                strategy_ref=spec.strategy_ref, previous_stage=previous, stage=stage,
                evidence_refs=(evidence_ref,), reason="reviewed",
                decided_at=NOW + timedelta(minutes=index),
            ))
        promotion = strategy_stage_revision(
            strategy_ref=spec.strategy_ref, previous_stage=StrategyLifecycleStage.SHADOW,
            stage=StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
            evidence_refs=(report.report_id,), reason="forward report reviewed",
            decided_at=NOW + timedelta(minutes=3),
        )
        with self.assertRaisesRegex(ValueError, "stored finalized PASSED"):
            repository.save_stage_revision(promotion)
        repository.save_report(report)
        self.assertTrue(repository.save_stage_revision(promotion))
        self.assertEqual(
            StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
            repository.latest_stage(spec.strategy_ref),
        )


class NativeImmutableDocumentTests(unittest.TestCase):
    def test_native_immutable_write_costs_and_read_only_duplicate_preserve_original_receipt(self):
        import json
        from unittest.mock import Mock, patch

        clock = Mock(return_value=datetime(2030,1,2,10,tzinfo=timezone(timedelta(hours=9))))
        store = SQLiteQueryStore(Path(':memory:'), now_provider=clock)
        store.initialize();self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        statements = []
        native = store._connect

        def connect():
            connection = native();connection.set_trace_callback(statements.append);return connection

        payload = {'z':[1,{'flag':True}], 'a':'original'}
        clock.reset_mock()
        with patch.object(store,'_connect',side_effect=connect) as connections:
            self.assertTrue(repository._save_immutable('test-immutable','owner','key',payload))
        self.assertEqual(1,connections.call_count)
        self.assertEqual(1,sum(s.startswith('SELECT') for s in statements))
        self.assertEqual(1,sum(s.startswith('INSERT') for s in statements))
        self.assertEqual(1,statements.count('COMMIT'))
        clock.assert_called_once_with()
        original = store.load_document('test-immutable','owner','key')
        statements.clear();clock.reset_mock();clock.side_effect=AssertionError('no-op must not sample time')
        with patch.object(store,'_connect',side_effect=connect) as connections:
            self.assertFalse(repository._save_immutable('test-immutable','owner','key',{'a':'original','z':[1,{'flag':True}]}))
            with self.assertRaisesRegex(ValueError,'immutable document conflict'):
                repository._save_immutable('test-immutable','owner','key',{'a':'changed'})
        self.assertEqual(2,connections.call_count)
        self.assertEqual(2,sum(s.startswith('SELECT') for s in statements))
        self.assertFalse(any(s.startswith(('INSERT','UPDATE','BEGIN','COMMIT')) for s in statements))
        clock.assert_not_called()
        self.assertEqual(original,store.load_document('test-immutable','owner','key'))
        self.assertEqual(payload,json.loads(json.dumps(original['document'])))

    def test_two_native_stores_cannot_ack_different_content_for_the_same_identity(self):
        import tempfile,threading
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            for equal in (False,True):
                with self.subTest(equal=equal):
                    path=Path(directory)/f'{equal}.sqlite3'
                    stores=[SQLiteQueryStore(path),SQLiteQueryStore(path)]
                    stores[0].initialize()
                    barrier=threading.Barrier(2);results=[]

                    class Cursor:
                        def __init__(self,raw):self.raw=raw
                        def fetchone(self):
                            row=self.raw.fetchone()
                            if row is None:barrier.wait(5)
                            return row

                    class Connection:
                        def __init__(self,raw):self.raw=raw;self.read=False
                        def __getattr__(self,name):return getattr(self.raw,name)
                        def execute(self,sql,*args):
                            cursor=self.raw.execute(sql,*args)
                            if sql.startswith('SELECT document_json') and not self.read:
                                self.read=True;return Cursor(cursor)
                            return cursor

                    def save(index):
                        try:
                            document={'value':0 if equal else index}
                            result=ForwardEvaluationRepository(stores[index])._save_immutable('race','owner','key',document)
                            results.append((index,result,None))
                        except BaseException as error:results.append((index,None,error))

                    workers=[];native0=stores[0]._connect;native1=stores[1]._connect
                    try:
                        with patch.object(stores[0],'_connect',side_effect=lambda:Connection(native0())), \
                             patch.object(stores[1],'_connect',side_effect=lambda:Connection(native1())):
                            for index in range(2):
                                worker=threading.Thread(target=save,args=(index,));workers.append(worker);worker.start()
                            for worker in workers:
                                worker.join(10);self.assertFalse(worker.is_alive())
                        self.assertEqual(2,len(results))
                        self.assertEqual(1,sum(result is True for _,result,_ in results))
                        if equal:
                            self.assertEqual(1,sum(result is False for _,result,_ in results))
                            self.assertTrue(all(error is None for _,_,error in results))
                        else:
                            self.assertEqual(1,sum(isinstance(error,ValueError) for _,_,error in results))
                        winner=next(index for index,result,_ in results if result is True)
                        self.assertEqual({'value':0 if equal else winner},
                            stores[0].load_document('race','owner','key')['document'])
                    finally:
                        for worker in workers:worker.join(10)
                        for store in stores:store.close()

    def test_native_commit_failure_and_lost_ack_never_allow_overwrite_on_retry(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ack.sqlite3';store=SQLiteQueryStore(path);store.initialize()
            self.addCleanup(store.close)
            repository=ForwardEvaluationRepository(store);native=store._connect
            with self.assertRaises(TypeError):
                repository._save_immutable('ack','owner','invalid',{'value':object()})
            with self.assertRaisesRegex(RuntimeError,'failed to persist immutable document'):
                repository._save_immutable('ack','owner','invalid',{'value':(1,2)})
            self.assertIsNone(store.load_document('ack','owner','invalid'))

            class CommitFailure:
                def __init__(self,raw):self.raw=raw
                def __getattr__(self,name):return getattr(self.raw,name)
                def commit(self):raise OSError('controlled commit failure')

            with patch.object(store,'_connect',side_effect=lambda:CommitFailure(native())):
                with self.assertRaisesRegex(OSError,'controlled commit failure'):
                    repository._save_immutable('ack','owner','key',{'value':'first'})
            self.assertIsNone(store.load_document('ack','owner','key'))
            insert=store.insert_immutable_document

            def lose_ack(*args):
                insert(*args)
                raise OSError('controlled committed ACK loss')

            with patch.object(store,'insert_immutable_document',side_effect=lose_ack):
                with self.assertRaisesRegex(OSError,'committed ACK loss'):
                    repository._save_immutable('ack','owner','key',{'value':'first'})
            reopened=SQLiteQueryStore(path);self.addCleanup(reopened.close)
            retry=ForwardEvaluationRepository(reopened)
            original=reopened.load_document('ack','owner','key')
            self.assertFalse(retry._save_immutable('ack','owner','key',{'value':'first'}))
            with self.assertRaisesRegex(ValueError,'immutable document conflict'):
                retry._save_immutable('ack','owner','key',{'value':'second'})
            self.assertEqual(original,reopened.load_document('ack','owner','key'))


if __name__ == "__main__":
    unittest.main()
