from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.breakout_strategy import CandidateEvent
from kiwoom_monitor.application.research_evaluation import (
    build_development_evidence,
    build_outcome_labels,
    evaluate_performance,
)
from kiwoom_monitor.application.research_execution import ExecutionEvent
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.application.market_session_schedule import KRX_FULL_DAY_RESEARCH_PROFILE


UTC = timezone.utc


class DevelopmentEvidenceTests(unittest.TestCase):
    def test_projection_copies_only_immutable_development_values(self):
        fold = {
            'name': 'train', 'role': 'TRAIN', 'status': 'ELIGIBLE', 'reasons': ['development_note'],
            'net_realized_pnl_won': 15, 'max_drawdown_won': 4,
            'closed_trade_count': 3, 'active_day_count': 2,
            'event_outcomes': [{'private_raw': 'not_for_generator'}],
        }
        report = {'fold_reports': [fold], 'data_quality': {'private_raw': 'not_for_generator'}}
        evidence = build_development_evidence(report)
        fold['reasons'].append('later_mutation')
        fold['net_realized_pnl_won'] = 999
        self.assertEqual(15, evidence.net_pnl_won)
        self.assertEqual(('development_note',), evidence.reasons)
        self.assertEqual((('train', 'TRAIN'),), evidence.fold_refs)
        self.assertFalse(hasattr(evidence, 'data_quality'))
        self.assertFalse(hasattr(evidence, 'event_outcomes'))
        with self.assertRaises(FrozenInstanceError):
            evidence.status = 'changed'

    def test_no_trade_and_sample_shortage_are_distinct(self):
        no_trade = build_development_evidence({'fold_reports': [{
            'role': 'TRAIN', 'status': 'NOT_APPLICABLE', 'reasons': ['no_closed_simulated_trade'],
        }]})
        shortage = build_development_evidence({'fold_reports': [{
            'role': 'TRAIN', 'status': 'INELIGIBLE', 'reasons': ['minimum_closed_trades_not_met'],
        }]})
        self.assertEqual('NOT_APPLICABLE', no_trade.status)
        self.assertEqual('INELIGIBLE', shortage.status)
        self.assertNotEqual(no_trade.reasons, shortage.reasons)

    def test_eligible_development_fold_does_not_hide_another_development_failure(self):
        evidence = build_development_evidence({'fold_reports': [
            {'role': 'TRAIN', 'status': 'ELIGIBLE'},
            {'role': 'VALIDATION', 'status': 'INELIGIBLE', 'reasons': ['validation_shortage']},
        ]})
        self.assertEqual('INELIGIBLE', evidence.status)
        self.assertEqual(('validation_shortage',), evidence.reasons)


def _candidate() -> CandidateEvent:
    return CandidateEvent(
        event_id="candidate-1", run_id="run-1", dedup_key="dedup", symbol="005930",
        strategy_id="krx_bar_close_breakout", strategy_version="v1",
        setup="rolling_high_breakout", reference_revision_id="bar-1",
        transition="flat_to_candidate", quantity=10, signal_reference_price=1000,
        available_at="2026-09-12T00:03:02+00:00", snapshot_id="snapshot-1",
        decision_id="decision-1",
    )


def _bar(minute: int, close: int) -> KrxMinuteBarFrame:
    start = datetime(2026, 9, 12, 0, minute, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{minute}", observation_key=start.isoformat(), code="005930",
        bar_start=start.isoformat(), bar_end=end.isoformat(),
        available_at=(end + timedelta(seconds=2)).isoformat(), open=close,
        high=close + 10, low=close - 10, close=close, volume=1,
        trade_value_million_won=1, session_finalized=False,
        capture_quality="complete", finalization_source="timer",
    )


def _fill(side: str, *, price: int, cost: int, realized: int) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"{side}-{price}", run_id="run-1", execution_environment="simulation",
        account_ref="research", intent_id=f"intent-{side}", decision_id="decision",
        event_type="FILL", state="FILLED", symbol="005930", side=side,
        occurred_at="2026-09-12T00:04:00+00:00",
        received_at="2026-09-12T00:05:02+00:00", quantity=10, price=price,
        notional_won=price * 10, commission_won=cost, tax_won=0,
        total_cost_won=cost, realized_pnl_won=realized, cash_after_won=0,
        reserved_cash_after_won=0, position_quantity_after=0,
        source_revision_id="bar", optimistic_exit_price=None, reason="",
    )


class ResearchEvaluationTests(unittest.TestCase):
    def test_outcomes_distinguish_unsupported_complete_and_censored(self) -> None:
        labels = build_outcome_labels(
            (_candidate(),), (_bar(4, 1010), _bar(5, 1020), _bar(6, 1030)),
            run_ended=True,
        )
        by_horizon = {label.horizon_seconds: label for label in labels}
        self.assertEqual("UNSUPPORTED", by_horizon[1].status)
        self.assertEqual("COMPLETE", by_horizon[60].status)
        self.assertEqual(100, by_horizon[60].values["return_bps"])
        self.assertEqual("COMPLETE", by_horizon[180].status)
        self.assertEqual("CENSORED", by_horizon[300].status)
        self.assertEqual("CENSORED", by_horizon[600].status)

    def test_unmatured_live_label_is_pending_and_gap_is_censored(self) -> None:
        pending = build_outcome_labels((_candidate(),), (), run_ended=False)
        self.assertEqual("PENDING", next(label for label in pending if label.horizon_seconds == 60).status)
        gap = build_outcome_labels(
            (_candidate(),), (_bar(4, 1010), _bar(6, 1030)), run_ended=True,
        )
        self.assertEqual("CENSORED", next(
            label for label in gap if label.horizon_seconds == 180
        ).status)

    def test_manual_profit_and_no_trade_eligibility(self) -> None:
        summary = evaluate_performance(
            "run-1", (_fill("BUY", price=100, cost=10, realized=0),
                      _fill("SELL", price=110, cost=10, realized=80)),
            initial_cash_won=10_000, cost_model_present=True,
        )
        self.assertEqual("ELIGIBLE", summary.status)
        self.assertEqual(100, summary.gross_realized_pnl_won)
        self.assertEqual(20, summary.total_cost_won)
        self.assertEqual(80, summary.net_realized_pnl_won)
        self.assertEqual(8000, summary.return_ppm)

        no_trade = evaluate_performance(
            "run-2", (), initial_cash_won=10_000, cost_model_present=True,
        )
        self.assertEqual("NOT_APPLICABLE", no_trade.status)
        self.assertIsNone(no_trade.net_realized_pnl_won)
        missing_cost = evaluate_performance(
            "run-3", (), initial_cash_won=10_000, cost_model_present=False,
        )
        self.assertEqual("INELIGIBLE", missing_cost.status)

    def test_session_gap_and_first_future_bar_gap_remain_censored(self) -> None:
        candidate = replace(
            _candidate(), available_at="2026-09-14T06:29:02+00:00",
            reference_revision_id="regular-source",
        )
        source = replace(
            _bar(1, 1000), revision_id="regular-source",
            bar_start="2026-09-14T06:28:00+00:00", bar_end="2026-09-14T06:29:00+00:00",
            available_at="2026-09-14T06:29:02+00:00",
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
            research_session="2026-09-14:KRX_REGULAR", market_phase="CONTINUOUS",
        )
        after = replace(
            _bar(2, 1010), bar_start="2026-09-14T07:00:00+00:00",
            bar_end="2026-09-14T07:01:00+00:00",
            available_at="2026-09-14T07:01:02+00:00",
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
            research_session="2026-09-14:KRX_AFTER", market_phase="CONTINUOUS",
        )
        labels = build_outcome_labels(
            (candidate,), (source, after), run_ended=True,
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        self.assertEqual("CENSORED", next(
            label for label in labels if label.horizon_seconds == 60
        ).status)

        pre_open = replace(
            _candidate(), available_at="2026-09-14T23:49:00+00:00",
            reference_revision_id="missing",
        )
        open_bar = replace(
            _bar(2, 1010), bar_start="2026-09-15T00:00:00+00:00",
            bar_end="2026-09-15T00:01:00+00:00",
            available_at="2026-09-15T00:01:02+00:00",
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
            research_session="2026-09-15:KRX_REGULAR", market_phase="CONTINUOUS",
        )
        gap_labels = build_outcome_labels(
            (pre_open,), (open_bar,), run_ended=True,
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        self.assertEqual("CENSORED", next(
            label for label in gap_labels if label.horizon_seconds == 60
        ).status)

    def test_auction_bar_path_is_not_reported_as_complete(self) -> None:
        candidate = replace(
            _candidate(), available_at="2026-09-14T06:19:02+00:00",
            reference_revision_id="source",
        )
        source = replace(
            _bar(1, 1000), revision_id="source",
            bar_start="2026-09-14T06:18:00+00:00", bar_end="2026-09-14T06:19:00+00:00",
            available_at="2026-09-14T06:19:02+00:00",
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
            research_session="2026-09-14:KRX_REGULAR", market_phase="CONTINUOUS",
        )
        auction = replace(
            _bar(2, 1010), bar_start="2026-09-14T06:20:00+00:00",
            bar_end="2026-09-14T06:21:00+00:00",
            available_at="2026-09-14T06:21:02+00:00",
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
            research_session="2026-09-14:KRX_REGULAR", market_phase="AUCTION_ORDER_ENTRY",
        )
        labels = build_outcome_labels(
            (candidate,), (source, auction), run_ended=True,
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        label = next(item for item in labels if item.horizon_seconds == 60)
        self.assertEqual("UNSUPPORTED", label.status)


if __name__ == "__main__":
    unittest.main()
