from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.context_candidates import (
    CarryoverEvidence,
    ContextEvidence,
    IntradayResponse,
    TradingSession,
    advance_context_hypothesis,
    build_carryover_hypothesis,
    build_context_hypothesis,
    build_delayed_market_context_evidence,
    build_intraday_discovery_hypothesis,
    build_news_hypotheses,
    merge_context_hypotheses,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    RESEARCH_SCHEMA_VERSION,
    ResearchRepository,
)


AVAILABLE = "2026-09-14T08:40:00+09:00"
EXPIRES = "2026-09-14T15:30:00+09:00"


def news_rule(*, role: str = "CAUSE") -> dict[str, object]:
    return {
        "role": role,
        "event_type": "SUPPLY_CONTRACT",
        "certainty": "CONFIRMED",
        "amount_won": 50_000_000_000,
        "counterparty": "삼성전자",
        "event_key": "supply:customer:50000000000",
        "rule_version": "supply-contract-rule-v1",
        "targets": ({
            "target_id": "005930", "direction": "POSITIVE", "directness": "DIRECT",
        },),
    }


def news_hypothesis():
    return build_news_hypotheses(
        news_rule(), article_revision_id="article-r1", event_revision_id="event-r1",
        available_at=AVAILABLE, expires_at=EXPIRES,
    )[0]


class ContextCandidateTests(unittest.TestCase):
    def test_news_keeps_fact_and_inference_separate_and_skips_reaction(self) -> None:
        hypothesis = news_hypothesis()
        self.assertEqual(("STOCK_NEWS",), hypothesis.source_types)
        self.assertEqual("SUPPLY_CONTRACT", hypothesis.facts[0]["event_type"])
        self.assertNotIn("direction", hypothesis.facts[0])
        self.assertEqual("POSITIVE", hypothesis.inferred_impacts[0]["direction"])
        self.assertFalse(hypothesis.entry_eligible)
        self.assertEqual((), build_news_hypotheses(
            news_rule(role="REACTION"), article_revision_id="a", event_revision_id="e",
            available_at=AVAILABLE, expires_at=EXPIRES,
        ))

    def test_duplicate_sources_merge_before_response_without_double_counting(self) -> None:
        first = news_hypothesis()
        evidence = ContextEvidence(
            evidence_ref="disclosure-r1", source_type="STOCK_NEWS", target_id="005930",
            event_group_id=first.event_group_id, available_at="2026-09-14T08:45:00+09:00",
            relation_version="disclosure-link/v1", facts=first.facts[0],
            inferred_impact=first.inferred_impacts[0],
        )
        second = build_context_hypothesis((evidence,), expires_at=EXPIRES)
        merged = merge_context_hypotheses((first, second))
        self.assertEqual(2, len(merged.source_refs))
        self.assertEqual(1, len(merged.facts))
        self.assertEqual(1, len(merged.inferred_impacts))
        advanced = advance_context_hypothesis(merged, self._response("flow", flow=True))
        with self.assertRaisesRegex(ValueError, "before intraday responses"):
            merge_context_hypotheses((advanced, second))

    def test_carryover_requires_explicit_normal_session_predecessor(self) -> None:
        evidence = CarryoverEvidence(
            evidence_ref="daily:2026-09-11:005930", target_id="005930",
            trading_session_id="KRX:2026-09-11",
            finalized_at="2026-09-11T15:30:00+09:00",
            available_at="2026-09-11T15:31:00+09:00",
            strong_move_confirmed=True, change_bps=1800,
            new_high_kind="N_DAY_HIGH", upper_limit_status="TOUCHED",
        )
        monday = TradingSession(
            session_id="KRX:2026-09-14", opens_at="2026-09-14T09:00:00+09:00",
            closes_at=EXPIRES, session_kind="NORMAL_KRX",
            previous_session_id="KRX:2026-09-11",
        )
        hypothesis = build_carryover_hypothesis(evidence, next_session=monday)
        self.assertEqual("KRX:2026-09-11", hypothesis.facts[0]["previous_session_id"])
        self.assertEqual("TOUCHED", hypothesis.facts[0]["upper_limit_status"])
        special = TradingSession(
            session_id="KRX:2026-09-14", opens_at="2026-09-14T10:00:00+09:00",
            closes_at=EXPIRES, session_kind="SPECIAL_UNKNOWN",
            previous_session_id="KRX:2026-09-11",
        )
        with self.assertRaisesRegex(ValueError, "normal KRX"):
            build_carryover_hypothesis(evidence, next_session=special)

    def test_flow_then_leadership_confirmation_preserves_observations(self) -> None:
        hypothesis = news_hypothesis()
        flow = advance_context_hypothesis(hypothesis, self._response("flow", flow=True))
        self.assertEqual("FLOW_CONFIRMED", flow.status)
        leader = advance_context_hypothesis(
            flow,
            self._response(
                "leader", observed="2026-09-14T09:06:00+09:00",
                available="2026-09-14T09:06:01+09:00", leadership=True,
            ),
        )
        self.assertEqual("LEADERSHIP_CONFIRMED", leader.status)
        self.assertEqual(("flow", "leader"), leader.response_refs)
        self.assertTrue(leader.response_observations[0]["flow_response"])

    def test_no_response_expires_without_rewriting_source_fact(self) -> None:
        hypothesis = news_hypothesis()
        after_close = self._response(
            "close-1", observed="2026-09-14T15:31:00+09:00",
            available="2026-09-14T15:31:01+09:00", flow=False, leadership=False,
        )
        no_response = advance_context_hypothesis(hypothesis, after_close)
        self.assertEqual("NO_RESPONSE", no_response.status)
        closed = advance_context_hypothesis(
            no_response,
            self._response(
                "close-2", observed="2026-09-14T15:32:00+09:00",
                available="2026-09-14T15:32:01+09:00", flow=False, leadership=False,
            ),
        )
        self.assertEqual("EXPIRED", closed.status)
        self.assertEqual(hypothesis.facts, closed.facts)

    def test_contradiction_rejects_inference_but_keeps_news_fact(self) -> None:
        hypothesis = news_hypothesis()
        rejected = advance_context_hypothesis(
            hypothesis, self._response("opposite", contradiction=True),
        )
        self.assertEqual("REJECTED", rejected.status)
        self.assertEqual("market_response_contradicted_hypothesis", rejected.status_reason)
        self.assertEqual(hypothesis.facts, rejected.facts)

    def test_intraday_discovery_does_not_require_news(self) -> None:
        hypothesis = build_intraday_discovery_hypothesis({
            "event_id": "candidate-1", "symbol": "A005930",
            "dedup_key": "005930:BREAKOUT:1", "available_at": "2026-09-14T09:05:00+09:00",
            "setup": "BREAKOUT", "reference_revision_id": "bar-r1",
            "signal_reference_price": 80000, "strategy_version": "strategy/v1",
        }, expires_at=EXPIRES)
        self.assertEqual("005930", hypothesis.target_id)
        self.assertEqual(("INTRADAY_DISCOVERY",), hypothesis.source_types)

    def test_delayed_external_bar_keeps_cadence_and_roll_contract(self) -> None:
        bar = {
            "provider": "yahoo_delayed", "instrument": "WTI_FUTURES",
            "contract": "CLV26.NYM", "timeframe": "5m",
            "bar_time": "2026-09-14T00:00:00Z", "close": 91.5, "volume": 300,
        }
        roll = {
            "active_contract": "CLV26.NYM", "change_pct": 1.2,
            "change_basis": "previous_daily_close", "updated_at": "2026-09-14T00:06:00Z",
        }
        evidence = build_delayed_market_context_evidence(
            bar, roll, target_id="005930", event_group_id="oil-up",
            relation_version="oil-beneficiary/v1", inferred_direction="POSITIVE",
            available_at="2026-09-14T00:06:00Z",
        )
        self.assertTrue(evidence.facts["delayed"])
        self.assertEqual("5m", evidence.facts["timeframe"])
        self.assertFalse(evidence.inferred_impact["simultaneous_second_observation"])
        with self.assertRaisesRegex(ValueError, "5m/1d"):
            build_delayed_market_context_evidence(
                {**bar, "timeframe": "1m"}, roll, target_id="005930",
                event_group_id="oil-up", relation_version="oil-beneficiary/v1",
                inferred_direction="POSITIVE", available_at="2026-09-14T00:06:00Z",
            )

    @staticmethod
    def _response(
        reference: str, *, observed: str = "2026-09-14T09:05:00+09:00",
        available: str = "2026-09-14T09:05:01+09:00",
        flow: bool | None = None, leadership: bool | None = None,
        contradiction: bool = False,
    ) -> IntradayResponse:
        return IntradayResponse(
            response_ref=reference, target_id="005930", observed_at=observed,
            available_at=available, quality="COMPLETE", price_response=True,
            flow_response=flow, leadership_response=leadership,
            contradiction=contradiction, reasons=("fixture",),
        )


class ContextCandidateRepositoryTests(unittest.TestCase):
    def test_revision_ledger_is_idempotent_and_supports_as_of_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            initial = news_hypothesis()
            flow = advance_context_hypothesis(initial, ContextCandidateTests._response(
                "flow", flow=True,
            ))
            self.assertTrue(repository.append_context_hypothesis(initial))
            self.assertFalse(repository.append_context_hypothesis(initial))
            self.assertTrue(repository.append_context_hypothesis(flow))
            self.assertEqual(RESEARCH_SCHEMA_VERSION, repository.schema_version())
            before = repository.load_latest_context_hypotheses(
                as_of="2026-09-14T08:59:59+09:00",
            )
            after = repository.load_latest_context_hypotheses(
                target_id="005930", as_of="2026-09-14T09:06:00+09:00",
            )
            self.assertEqual("UNCONFIRMED", before[0]["status"])
            self.assertEqual("FLOW_CONFIRMED", after[0]["status"])
            self.assertEqual(2, len(repository.load_context_hypothesis_revisions(
                hypothesis_id=initial.hypothesis_id,
            )))


if __name__ == "__main__":
    unittest.main()
