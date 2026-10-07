"""Calendar-first selection must not choose dates by outcomes or later readiness."""

import unittest

from scripts.plan_historical_monthly_case_selection import plan


def _case(day: str, outcome: str, *, ready: bool = True) -> dict:
    return {"selection_date": day, "outcome_date": outcome,
            "candidate_codes_original": 2, "candidate_codes_excluded": 1,
            "candidate_codes_after_exclusion": 1, "excluded_codes": ["000001"],
            "readiness_after_exclusion": "READY" if ready else "BLOCKED"}


def _split() -> dict:
    return {"oos_status": "SEALED", "oos_results_included": False,
            "case_assignments": [
                {"role": "TRAIN", "selection_dates": ["2024-09-02"]},
                {"role": "VALIDATION", "selection_dates": ["2024-10-02"]},
                {"role": "OOS", "selection_dates": ["2026-01-14"]},
            ]}


class MonthlyCaseSelectionTests(unittest.TestCase):
    def test_calendar_first_cases_preserve_exclusions_and_seal_oos(self):
        projection = {"version": "historical_minute_exclusion_projection/v1",
                      "cases": [_case("2024-09-02", "2024-09-03"),
                                _case("2024-09-03", "2024-09-04"),
                                _case("2024-10-02", "2024-10-04")]}
        result = plan(projection, "projection-hash", _split(), "split-hash", train_months=1)
        self.assertEqual(["2024-09-02", "2024-10-02"],
                         [row["selection_date"] for row in result["cases"]])
        self.assertEqual(["TRAIN", "VALIDATION"],
                         [row["role"] for row in result["cases"]])
        self.assertEqual(["000001"], result["cases"][0]["excluded_codes"])
        self.assertEqual({"selection_date": "2026-01-14", "status": "SEALED",
                          "results_included": False}, result["oos"])

    def test_calendar_first_blocked_day_cannot_be_replaced_by_later_day(self):
        projection = {"version": "historical_minute_exclusion_projection/v1",
                      "cases": [_case("2024-09-02", "2024-09-03", ready=False),
                                _case("2024-09-03", "2024-09-04"),
                                _case("2024-10-02", "2024-10-04")]}
        with self.assertRaisesRegex(ValueError, "calendar-first case is not ready"):
            plan(projection, "projection-hash", _split(), "split-hash", train_months=1)


if __name__ == "__main__":
    unittest.main()
