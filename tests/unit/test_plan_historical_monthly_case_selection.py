"""Calendar-first selection must not choose dates by outcomes or later readiness."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.plan_historical_monthly_case_selection import expand_train, main, plan


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


class TrainExpansionTests(unittest.TestCase):
    def setUp(self):
        self.projection = {"version": "historical_minute_exclusion_projection/v1", "cases": [
            _case("2024-09-02", "2024-09-03"),
            _case("2024-09-03", "2024-09-04"),
            _case("2024-09-27", "2024-09-30"),
            _case("2024-09-30", "2024-10-02"),
            _case("2024-10-02", "2024-10-04"),
            _case("2024-10-04", "2024-10-07"),
        ]}
        self.selection = plan(self.projection, "projection-hash", _split(), "split-hash",
                              train_months=1)

    def test_expands_every_train_date_and_excludes_cross_boundary_outcome(self):
        before = copy.deepcopy((self.projection, self.selection))
        result = expand_train(self.projection, "projection-hash", self.selection, "selection-hash")
        self.assertEqual(["2024-09-02", "2024-09-03", "2024-09-27"],
                         [row["selection_date"] for row in result["cases"]])
        self.assertEqual(3, result["train_date_count"])
        self.assertEqual(2, result["additional_train_date_count"])
        self.assertEqual("2024-09-30", result["excluded_boundary_cases"][0]["selection_date"])
        self.assertTrue(all(row["role"] == "TRAIN" for row in result["cases"]))
        self.assertEqual(self.selection["oos"], result["oos"])
        self.assertFalse(result["source_readiness_verified"])
        self.assertFalse(result["runner_requests_created"])
        self.assertEqual(before, (self.projection, self.selection))

    def test_hash_seal_and_monthly_assignment_mismatches_reject(self):
        for mutation in (
                lambda value: value.update(source_projection_sha256="wrong"),
                lambda value: value["oos"].update(status="OPEN"),
                lambda value: value["oos"].update(results_included=True),
                lambda value: value.update(train_months=2),
                lambda value: value.update(plan_id="forged"),
                lambda value: value["cases"][0].update(role="VALIDATION"),
                lambda value: value["cases"][0].update(candidate_codes_after_exclusion=2),
                lambda value: value["cases"][0].update(selection_date="2024-09-03")):
            changed = copy.deepcopy(self.selection)
            mutation(changed)
            with self.subTest(selection=changed), self.assertRaises(ValueError):
                expand_train(self.projection, "projection-hash", changed, "selection-hash")

    def test_bad_new_train_rows_reject_without_silently_skipping(self):
        for mutation in (
                lambda row: row.update(readiness_after_exclusion="BLOCKED"),
                lambda row: row.update(candidate_codes_excluded=0),
                lambda row: row.update(candidate_codes_original=True),
                lambda row: row.update(excluded_codes=["000001", "000001"]),
                lambda row: row.update(outcome_date="2024-09-03"),
                lambda row: row.update(outcome_date="2024-9-4")):
            changed = copy.deepcopy(self.projection)
            mutation(changed["cases"][1])
            with self.subTest(projection=changed), self.assertRaises(ValueError):
                expand_train(changed, "projection-hash", self.selection, "selection-hash")
        for rows in (self.projection["cases"][::-1],
                     self.projection["cases"] + [self.projection["cases"][-1]]):
            with self.assertRaises(ValueError):
                expand_train({**self.projection, "cases": rows}, "projection-hash",
                             self.selection, "selection-hash")
        changed = copy.deepcopy(self.projection)
        changed["cases"][2]["outcome_date"] = changed["cases"][3]["outcome_date"]
        with self.assertRaisesRegex(ValueError, "outcome dates"):
            expand_train(changed, "projection-hash", self.selection, "selection-hash")

    def test_changed_outcomes_do_not_select_dates_and_oos_overlap_rejects(self):
        baseline = expand_train(self.projection, "projection-hash", self.selection, "selection-hash")
        altered = copy.deepcopy(self.projection)
        for index, row in enumerate(altered["cases"]):
            row["net_pnl"] = (-1) ** index * 1000000
        with patch("sqlite3.connect", side_effect=AssertionError("no database reads")):
            result = expand_train(altered, "projection-hash", self.selection, "selection-hash")
        self.assertEqual(baseline["plan_id"], result["plan_id"])
        changed = copy.deepcopy(self.selection)
        changed["oos"]["selection_date"] = "2024-10-04"
        with self.assertRaisesRegex(ValueError, "sealed OOS"):
            expand_train(self.projection, "projection-hash", changed, "selection-hash")

    def test_cli_binds_files_preserves_inputs_and_rejects_overwrite_or_new_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection = root / "projection.json"
            selection = root / "selection.json"
            output = root / "expanded.json"
            projection.write_text(json.dumps(self.projection), encoding="utf-8")
            digest = hashlib.sha256(projection.read_bytes()).hexdigest()
            selected = plan(self.projection, digest, _split(), "split-hash", train_months=1)
            selection.write_text(json.dumps(selected), encoding="utf-8")
            before = (projection.read_bytes(), selection.read_bytes())
            arguments = ["planner", "--projection", str(projection),
                         "--expand-train-selection", str(selection), "--output", str(output)]
            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()), patch(
                    "sqlite3.connect", side_effect=AssertionError("no database reads")):
                self.assertEqual(0, main())
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(digest, result["source_projection_sha256"])
            self.assertEqual(hashlib.sha256(before[1]).hexdigest(), result["prior_selection_sha256"])
            self.assertEqual(before, (projection.read_bytes(), selection.read_bytes()))
            with patch("sys.argv", arguments), self.assertRaisesRegex(ValueError, "immutable"):
                main()
            output.unlink()
            with patch("sys.argv", arguments + ["--train-months", "2"]), redirect_stdout(
                    io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                main()
            self.assertFalse(output.exists())

    def test_existing_monthly_cli_and_stdout_contract_remain_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection, split, output = [root / name for name in ("projection.json", "split.json", "monthly.json")]
            projection.write_text(json.dumps(self.projection), encoding="utf-8")
            split.write_text(json.dumps(_split()), encoding="utf-8")
            stream = io.StringIO()
            with patch("sys.argv", ["planner", "--projection", str(projection), "--prior-split", str(split),
                                    "--train-months", "1", "--output", str(output)]), redirect_stdout(stream), patch(
                    "scripts.plan_historical_monthly_case_selection.load_historical_evaluation_plan",
                    return_value=_split()):
                self.assertEqual(0, main())
            summary = json.loads(stream.getvalue())
            self.assertEqual({"output", "plan_id", "train_months", "validation_months", "oos_status"}, set(summary))
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("historical_monthly_case_selection/v1", result["version"])
            self.assertEqual(["TRAIN", "VALIDATION"], [row["role"] for row in result["cases"]])


if __name__ == "__main__":
    unittest.main()
