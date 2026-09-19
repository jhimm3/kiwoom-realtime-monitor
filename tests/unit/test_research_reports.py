from __future__ import annotations

import hashlib
import unittest

from kiwoom_monitor.application.research_evaluation import build_research_report
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec


def _event(
    event_id: str, event_type: str, side: str, occurred_at: str, *, price: int,
    notional: int, cost: int, realized: int, cash: int, position: int,
    intent_id: str = "",
) -> dict:
    return {
        "event_id": event_id, "event_type": event_type, "state": "FILLED",
        "symbol": "005930", "side": side, "occurred_at": occurred_at,
        "intent_id": intent_id,
        "quantity": 10, "price": price, "notional_won": notional,
        "total_cost_won": cost, "realized_pnl_won": realized,
        "cash_after_won": cash, "position_quantity_after": position,
    }


def _inputs():
    observations = ({
        "revision_id": "rank", "kind": "top20_membership",
        "available_at": "2026-09-11T23:49:00+00:00", "payload": {"codes": ["005930"]},
    }, {
        "revision_id": "bar", "kind": "minute_bar", "venue": "KRX",
        "available_at": "2026-09-12T00:01:02+00:00", "completeness": "complete",
        "value_kind": "actual", "payload": {
            "window_closed": True, "capture_quality": "complete",
        },
    })
    ids = [row["revision_id"] for row in observations]
    manifest = {
        "revision_count": len(ids),
        "revision_ids_hash": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
    }
    spec = ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (
            ResearchFoldSpec("train", "TRAIN", "2026-09-12T00:00:00+00:00", "2026-09-12T01:00:00+00:00"),
            ResearchFoldSpec("final", "OOS", "2026-09-12T02:00:00+00:00", "2026-09-12T03:00:00+00:00"),
        ),
        600, 3600, 60, 1, 1,
    )
    return observations, manifest, spec


class ResearchReportTests(unittest.TestCase):
    def test_report_separates_trade_metrics_event_study_and_sealed_holdout(self) -> None:
        observations, manifest, spec = _inputs()
        events = (
            _event("submit-buy", "ORDER_SUBMITTED", "BUY", "2026-09-12T00:09:00+00:00", price=0, notional=0, cost=0, realized=0, cash=10_000, position=0, intent_id="buy-intent"),
            _event("buy", "FILL", "BUY", "2026-09-12T00:10:00+00:00", price=100, notional=1000, cost=1, realized=0, cash=8999, position=10, intent_id="buy-intent"),
            _event("mark", "MARK", "", "2026-09-12T00:15:00+00:00", price=90, notional=900, cost=0, realized=0, cash=8999, position=10),
            _event("submit-sell", "ORDER_SUBMITTED", "SELL", "2026-09-12T00:19:00+00:00", price=0, notional=0, cost=0, realized=0, cash=8999, position=10, intent_id="sell-intent"),
            _event("sell", "FILL", "SELL", "2026-09-12T00:20:00+00:00", price=110, notional=1100, cost=1, realized=98, cash=10098, position=0, intent_id="sell-intent"),
        )
        report = build_research_report(
            "run-1", input_manifest=manifest, observations=observations,
            candidate_events=({
                "event_id": "candidate", "available_at": "2026-09-12T00:05:00+00:00",
            },),
            decisions=({
                "decided_at": "2026-09-12T00:30:00+00:00", "final_action": "NO_TRADE",
                "reasons": ["rank_persistence_missing"],
            },),
            execution_events=events,
            outcome_labels=({
                "candidate_event_id": "candidate", "horizon_seconds": 60,
                "horizon_end": "2026-09-12T00:06:00+00:00", "status": "COMPLETE",
                "values": {"return_bps": 100},
            },),
            initial_cash_won=10_000,
            cost_model={
                "version": "fixed_bps/v1", "rate_basis": "model_estimate",
                "source": "locked fixture", "valid_from": "2026-09-11T00:00:00+00:00",
                "valid_to": "2026-09-13T00:00:00+00:00",
            },
            spec=spec, logical_result_hash="verified",
        )
        train, final = report.fold_reports
        self.assertEqual("ELIGIBLE_WITH_SEALED_HOLDOUT", report.status)
        self.assertEqual("ELIGIBLE", train.status)
        self.assertEqual(98, train.net_realized_pnl_won)
        self.assertEqual(100, train.gross_realized_pnl_won)
        self.assertEqual(2, train.total_cost_won)
        self.assertEqual(2100, train.turnover_won)
        self.assertEqual(600, train.exposure_seconds)
        self.assertEqual(101, train.max_drawdown_won)
        self.assertEqual(2, train.submitted_order_count)
        self.assertEqual(2, train.filled_order_count)
        self.assertEqual(1_000_000, train.fill_rate_ppm)
        self.assertEqual(1, train.no_trade_reasons["rank_persistence_missing"])
        self.assertEqual(100, train.event_outcomes[0]["average_return_bps"])
        self.assertEqual("SEALED", final.status)
        self.assertIsNone(final.net_realized_pnl_won)

    def test_missing_cost_provenance_and_samples_are_ineligible(self) -> None:
        observations, manifest, spec = _inputs()
        report = build_research_report(
            "run-2", input_manifest=manifest, observations=observations,
            candidate_events=(), decisions=(), execution_events=(), outcome_labels=(),
            initial_cash_won=10_000,
            cost_model={"rate_basis": "unspecified", "source": "", "valid_from": "", "valid_to": ""},
            spec=spec, logical_result_hash="verified",
        )
        self.assertEqual("INELIGIBLE", report.status)
        self.assertIn("cost_model_provenance_missing", report.reasons)
        self.assertIn("minimum_closed_trades_not_met", report.fold_reports[0].reasons)


if __name__ == "__main__":
    unittest.main()
