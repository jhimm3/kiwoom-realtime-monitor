from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.theme_leadership import (
    ThemeLeadershipParameters,
    build_entry_thesis,
    evaluate_entry_thesis,
    evaluate_theme_leadership,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    RESEARCH_SCHEMA_VERSION,
    ResearchRepository,
)
from tests.unit.test_theme_leadership import DAY, initial_points, membership, point


PARAMETERS = ThemeLeadershipParameters(display_confirmation_seconds=3)


def first_evaluation():
    return evaluate_theme_leadership(
        membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
        venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
    )


def changed_evaluation(previous):
    switched = (*initial_points(),
        point("005930", 22, 105, 100), point("000660", 22, 102, 1800),
        point("005930", 29, 105, 100), point("000660", 29, 108, 2200),
    )
    candidate = evaluate_theme_leadership(
        membership(), switched, as_of=f"{DAY}T09:00:30+09:00",
        venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        previous_state=previous.state,
    )
    return evaluate_theme_leadership(
        membership(), (*switched,
            point("005930", 33, 105, 100), point("000660", 33, 109, 2300),
        ), as_of=f"{DAY}T09:00:34+09:00", venue="KRX",
        continuity_status="COMPLETE", parameters=PARAMETERS,
        previous_state=candidate.state,
    )


class EntryThesisTests(unittest.TestCase):
    def test_original_leader_is_frozen_and_policies_make_separate_research_actions(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="005930", leadership=first,
            factor_refs=("factor-r1",), hypothesis_refs=("hypothesis-r1",),
            created_at=first.as_of, available_at=first.as_of,
        )
        changed = changed_evaluation(first)
        immediate = evaluate_entry_thesis(thesis, changed, policy_version="IMMEDIATE_EXIT")
        price_only = evaluate_entry_thesis(thesis, changed, policy_version="PRICE_ONLY")
        confirm = evaluate_entry_thesis(thesis, changed, policy_version="CONFIRM_THEN_EXIT")
        self.assertEqual("005930", thesis.leader_id)
        self.assertEqual("000660", changed.displayed_leader_id)
        self.assertEqual("INVALIDATED", immediate.thesis_state)
        self.assertEqual("EXIT", immediate.final_action)
        self.assertEqual("HOLD", price_only.final_action)
        self.assertEqual("EXIT", confirm.final_action)
        self.assertFalse(immediate.order_authorized)

    def test_at_risk_can_wait_or_reduce_without_authorizing_order(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="000660", leadership=first,
            factor_refs=(), hypothesis_refs=(), created_at=first.as_of, available_at=first.as_of,
        )
        weakened = evaluate_theme_leadership(
            membership(), (*initial_points(),
                point("005930", 22, 104, 700), point("000660", 22, 101, 100),
                point("005930", 29, 102, 900), point("000660", 29, 101, 100),
            ), as_of=f"{DAY}T09:00:30+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        wait = evaluate_entry_thesis(thesis, weakened, policy_version="CONFIRM_THEN_EXIT")
        reduce = evaluate_entry_thesis(thesis, weakened, policy_version="REDUCE_ON_RISK")
        self.assertEqual("AT_RISK", wait.thesis_state)
        self.assertEqual("WAIT", wait.final_action)
        self.assertEqual("REDUCE", reduce.final_action)
        self.assertFalse(reduce.order_authorized)

    def test_thesis_rejects_evidence_from_before_entry(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="005930", leadership=first,
            factor_refs=(), hypothesis_refs=(),
            created_at=f"{DAY}T09:00:21+09:00",
            available_at=f"{DAY}T09:00:21+09:00",
        )
        with self.assertRaisesRegex(ValueError, "earlier leadership evidence"):
            evaluate_entry_thesis(thesis, first, policy_version="PRICE_ONLY")

    def test_research_repository_persists_leadership_thesis_and_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.start_run("run-1", {"mode": "fixture"}, {"dataset_id": "fixture"})
            first = first_evaluation()
            thesis = build_entry_thesis(
                run_id="run-1", symbol="005930", leadership=first,
                factor_refs=("factor-r1",), hypothesis_refs=(),
                created_at=first.as_of, available_at=first.as_of,
            )
            changed = changed_evaluation(first)
            decision = evaluate_entry_thesis(
                thesis, changed, policy_version="IMMEDIATE_EXIT",
            )
            self.assertTrue(repository.append_theme_leadership(first))
            self.assertFalse(repository.append_theme_leadership(first))
            self.assertTrue(repository.append_entry_thesis(thesis))
            self.assertTrue(repository.append_theme_leadership(changed))
            self.assertTrue(repository.append_thesis_decision(decision))
            self.assertEqual(RESEARCH_SCHEMA_VERSION, repository.schema_version())
            self.assertEqual("005930", repository.load_entry_theses("run-1")[0]["leader_id"])
            self.assertEqual("EXIT", repository.load_thesis_decisions("run-1")[0]["final_action"])
            self.assertEqual(2, len(repository.load_theme_leadership_revisions(
                theme_id="반도체", as_of=changed.as_of,
            )))


if __name__ == "__main__":
    unittest.main()
