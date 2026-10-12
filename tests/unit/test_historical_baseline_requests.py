from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
from types import SimpleNamespace
from io import StringIO

from kiwoom_monitor.infrastructure.historical_research_readiness import (
    HistoricalDevelopmentReadiness,
)
from scripts.prepare_historical_baseline_requests import prepare_requests
from scripts import summarize_historical_baseline_results as baseline_review
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from tests.unit.test_research_independent_comparisons import record


def seed_development_baselines(root: Path, *, changed_input=False, missing_report=False):
    """실제 읽기 전용 원장 조회용 네 개발 실행 fixture."""
    repository = ResearchRepository(root / "research.sqlite3")
    for role, family, filename in baseline_review.EXPECTED:
        run_id = role.lower() + "-" + family
        row = record(role.lower(), 0 if role == "TRAIN" else 20, role, -50)
        row["run_id"] = run_id
        row["report"]["run_id"] = run_id
        spec, manifest = row["spec"], row["input_manifest"]
        spec["family"] = baseline_review.BREAKOUT_FAMILY if family == "breakout" else baseline_review.PULLBACK_FAMILY
        spec["session_profile"] = research_session_profile_document("krx-regular/v1")
        manifest["research_session_profile"] = spec["session_profile"]
        if changed_input and role == "TRAIN" and family == "pullback":
            manifest["revision_ids_hash"] = "revised-input"
        request = {"mode": "single_run", "family": spec["family"], "session_profile": "krx-regular/v1",
                   "evaluation": spec["evaluation_spec"], "strategy": spec["parameters"],
                   "execution": spec["execution_model"],
                   "historical_baseline_context": {"version": "historical_baseline_context/v1",
                                                  "oos_included": False, "partition_role": role}}
        for folder, document in (("requests", request), ("results", {"status": "ok", "kind": "single_run", "run_id": run_id,
                                                                   "report": {"fake_pnl": 99999999}})):
            (root / folder).mkdir(exist_ok=True)
            (root / folder / filename).write_text(json.dumps(document), encoding="utf-8")
        repository.start_run(run_id, spec, manifest)
        if not missing_report:
            report = deepcopy(row["report"])
            report.update(report_id="report-" + run_id, status="ELIGIBLE")
            repository.save_research_report(SimpleNamespace(run_id=run_id, report_id=report["report_id"], status="ELIGIBLE",
                                                            to_dict=lambda: report))
        repository.finish_run(run_id, row["logical_result_hash"])
    return repository


class HistoricalBaselineRequestTests(unittest.TestCase):
    def test_prepare_requests_creates_only_train_and_validation_with_rank_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            for name in ("train", "validation"):
                (package / name).mkdir(parents=True)
            evaluation = lambda name, role: {
                "version": "chronological_holdout/v1",
                "folds": [{
                    "name": name, "role": role,
                    "start": "2024-01-02T09:00:00+09:00",
                    "end": "2024-01-02T15:30:00.000001+09:00",
                }],
                "warmup_seconds": 0, "gap_seconds": 0, "purge_seconds": 0,
                "minimum_closed_trades": 0, "minimum_active_days": 1,
                "fold_state_policy": "continuous_state_and_cash/v1",
                "period_end_position_policy": "censor_open_position/v1",
                "fit_policy": "no_fitted_parameters/v1",
                "final_holdout_accessed_at": "", "final_holdout_access_reason": "",
            }
            (package / "manifest.json").write_text(json.dumps({
                "version": "historical_development_inputs/v1",
                "package_id": "package-1", "split_plan_id": "split-1",
                "oos_included": False,
                "partitions": [
                    {"role": "TRAIN", "path": "train", "evaluation": evaluation("train", "TRAIN")},
                    {"role": "VALIDATION", "path": "validation", "evaluation": evaluation("validation", "VALIDATION")},
                ],
            }), encoding="utf-8")

            readiness = HistoricalDevelopmentReadiness(
                version="historical_development_readiness/v1", status="BLOCKED",
                partition_role="TRAIN", candidate_code_count=50, codes_with_bars=4,
                codes_with_continuous_minute_pair=4, bar_coverage_ppm=80_000,
                continuous_pair_coverage_ppm=80_000, missing_bar_codes=("000001",),
                missing_continuous_pair_codes=("000001",),
                reasons=("candidate_codes_without_minute_bars",),
            )
            with patch(
                "scripts.prepare_historical_baseline_requests.load_research_input",
                return_value=object(),
            ), patch(
                "scripts.prepare_historical_baseline_requests.assess_historical_development_readiness",
                return_value=readiness,
            ):
                paths = prepare_requests(package, root / "output", allow_partial=True,
                                         memory_mb=1024)

            self.assertEqual(4, len(paths))
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            self.assertEqual(
                {"TRAIN", "VALIDATION"},
                {item["historical_baseline_context"]["partition_role"] for item in documents},
            )
            self.assertTrue(all(item["historical_baseline_context"]["oos_included"] is False for item in documents))
            self.assertTrue(all(item["strategy"]["rank_persistence_enabled"] is False for item in documents))
            self.assertTrue(all(item["resource_budget"]["memory_mb"] == 1024
                                for item in documents))

    def test_prepare_requests_rejects_package_that_includes_oos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            package.mkdir()
            (package / "manifest.json").write_text(json.dumps({
                "version": "historical_development_inputs/v1",
                "oos_included": True,
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "OOS-free"):
                prepare_requests(package, Path(directory) / "output")

    def test_native_review_is_read_only_and_does_not_trust_external_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = seed_development_baselines(root)
            before = repository.path.read_bytes()
            with (patch("kiwoom_monitor.infrastructure.persistence.research_repository.SQLiteMigrationRunner", side_effect=AssertionError("no migration")),
                  patch("kiwoom_monitor.infrastructure.research_data_source.load_research_input", side_effect=AssertionError("no replay"))):
                review = baseline_review.inspect_development_baselines(root)
            self.assertEqual(before, repository.path.read_bytes())
            self.assertEqual(["COMPLETE", "COMPLETE"], [v["status"] for v in review["comparisons"].values()])
            self.assertEqual([-50] * 4, [v["net_pnl_won"] for v in review["runs"]])
            self.assertFalse(review["oos_queried"])
            self.assertFalse(review["candidate_eligibility_evaluated"])
            self.assertIn("불일치", baseline_review.development_review_text(review))

    def test_oos_request_is_rejected_before_results_or_database_are_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root)
            path = root / "requests" / "train-breakout.json"
            document = json.loads(path.read_text())
            document["evaluation"]["folds"][0]["role"] = "OOS"
            path.write_text(json.dumps(document))
            with patch.object(baseline_review, "ResearchRepository", side_effect=AssertionError("no database")):
                with self.assertRaisesRegex(ValueError, "TRAIN"):
                    baseline_review.inspect_development_baselines(root)

    def test_request_parameter_change_cannot_relabel_old_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root)
            path = root / "requests" / "train-breakout.json"
            document = json.loads(path.read_text())
            document["strategy"]["target_bps"] += 1
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "조건이 다릅니다"):
                baseline_review.inspect_development_baselines(root)

    def test_shared_input_difference_and_current_code_match_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root, changed_input=True)
            with patch.object(baseline_review, "research_implementation_hash", return_value="locked-code"):
                review = baseline_review.inspect_development_baselines(root)
            self.assertEqual(["TRAIN:input_mismatch"], review["paired_input_reasons"])
            self.assertTrue(all(row["current_code_matches"] for row in review["runs"]))
            self.assertFalse(review["candidate_eligibility_evaluated"])

    def test_missing_native_reports_are_kept_as_invalid_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root, missing_report=True)
            review = baseline_review.inspect_development_baselines(root)
            self.assertEqual(4, len(review["runs"]))
            self.assertEqual(["INVALID"] * 4, [row["status"] for row in review["runs"]])

    def test_malformed_or_oversized_request_fails_before_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root)
            path = root / "requests" / "train-breakout.json"
            original = json.loads(path.read_text())
            for encoded in (json.dumps({**original, "historical_baseline_context": []}).encode(), b" " * (4 * 1024 * 1024 + 1)):
                path.write_bytes(encoded)
                with patch.object(baseline_review, "ResearchRepository", side_effect=AssertionError("no database")):
                    with self.assertRaises(ValueError):
                        baseline_review.inspect_development_baselines(root)

    def test_native_oos_reference_is_rejected_before_report_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = seed_development_baselines(root)
            original = ResearchRepository.load_run
            def tainted_run(instance, run_id):
                value = original(instance, run_id)
                value["spec"]["evaluation_spec"]["folds"][0]["role"] = "OOS"
                return value
            with (patch.object(ResearchRepository, "load_run", tainted_run),
                  patch.object(ResearchRepository, "load_independent_development_comparison", side_effect=AssertionError("no OOS projection"))):
                with self.assertRaisesRegex(ValueError, "조건이 다릅니다"):
                    baseline_review.inspect_development_baselines(root)

    def test_missing_database_is_not_created_by_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = seed_development_baselines(root)
            repository.path.unlink()
            with self.assertRaisesRegex(ValueError, "does not exist"):
                baseline_review.inspect_development_baselines(root)
            self.assertFalse(repository.path.exists())

    def test_inspection_cli_prints_review_without_rewriting_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_development_baselines(root)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            stream = StringIO()
            with patch("sys.argv", ["summary", "--root", str(root), "--inspect-development"]), patch("sys.stdout", stream):
                self.assertEqual(0, baseline_review.main())
            self.assertEqual("historical_baseline_review/v1", json.loads(stream.getvalue())["version"])
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_position_summary_traces_native_engine_exit_censor_without_forced_fill(self):
        from tests.unit.test_research_execution import late_exit_trace
        _, events, _, _ = late_exit_trace()
        summary = baseline_review.position_observation_summary(tuple(event.to_dict() for event in events))
        self.assertEqual("OPEN_POSITION_CENSORED", summary["status"])
        self.assertEqual("005930", summary["symbol"])
        self.assertEqual(1, summary["exit_request_count"])
        self.assertEqual({"session_boundary_before_fill": 1}, summary["exit_censored_reasons"])
        self.assertEqual("2026-09-14T06:30:00+00:00", summary["last_source_observation_at"])

    def test_recovered_position_does_not_get_terminal_censor_warning(self):
        from tests.unit.test_research_execution import late_exit_trace
        _, events, _, _ = late_exit_trace(with_followup=True)
        summary = baseline_review.position_observation_summary(tuple(event.to_dict() for event in events))
        self.assertEqual("NO_TERMINAL_POSITION_CENSOR", summary["status"])

    def test_position_summary_compares_timezones_and_excludes_old_symbol_cycle(self):
        from tests.unit.test_research_execution import late_exit_trace
        _, events, _, _ = late_exit_trace()
        documents = [event.to_dict() for event in events]
        # 같은 날 UTC로 기록된 청산과 KST 진입을 문자열 순서로 비교하면 청산을 놓친다.
        for event in documents:
            if event["event_type"] == "FILL" and event["side"] == "BUY":
                event["occurred_at"] = "2026-09-14T15:13:00+09:00"
        old = {**documents[0], "event_type": "ORDER_SUBMITTED", "side": "SELL", "occurred_at": "2026-09-14T00:00:00+00:00"}
        summary = baseline_review.position_observation_summary((old, *documents))
        self.assertEqual(1, summary["exit_request_count"])
        self.assertEqual("2026-09-14T06:13:00+00:00", summary["entry_at"])

    def test_missing_entry_reference_is_reported_and_naive_times_are_rejected(self):
        from tests.unit.test_research_execution import late_exit_trace
        _, events, _, _ = late_exit_trace()
        documents = [event.to_dict() for event in events]
        self.assertEqual("ENTRY_REFERENCE_MISSING", baseline_review.position_observation_summary((documents[-1],))["status"])
        for event in documents:
            if event["event_type"] == "FILL" and event["side"] == "BUY":
                event["occurred_at"] = "2026-09-14T06:13:00"
        with self.assertRaisesRegex(ValueError, "시간대"):
            baseline_review.position_observation_summary(tuple(documents))


class DailyDevelopmentRequestTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        from datetime import date, timedelta, datetime
        from tests.unit.test_research_bundle_execution import rows_for
        from kiwoom_monitor.infrastructure.historical_reconstruction import write_historical_research_input
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / "source", self.root / "results"
        self.selection = self.root / "selection.json"
        cases, rows = [], []
        for selected, day, role, code, late in (
                ("2024-09-02", date(2024, 9, 3), "TRAIN", "005930", True),
                ("2024-10-02", date(2024, 10, 4), "VALIDATION", "000660", False)):
            cases.append({"selection_date": selected, "outcome_date": day.isoformat(), "role": role,
                          "candidate_codes_after_exclusion": 1, "excluded_codes": []})
            for index, row in enumerate(rows_for(day, 11 if late else 8)):
                if late:
                    offset = timedelta(minutes=370)
                    for key in ("available_at", "observation_key"):
                        row[key] = (datetime.fromisoformat(row[key]) + offset).isoformat()
                    if row["kind"] == "minute_bar":
                        for key in ("bar_start", "bar_end"):
                            row["payload"][key] = (datetime.fromisoformat(row["payload"][key]) + offset).isoformat()
                        if row["payload"]["bar_start"][11:16] == "15:20":
                            row["payload"]["bar_end"] = f"{day}T15:30:00+09:00"
                if index == 0:
                    row["kind"] = "historical_candidate_population"
                    row["payload"].update(codes=[code], selection_date=selected)
                else:
                    row["subject"] = code + ":KRX"
                    price = 1000 if index <= 5 else 1020
                    row["payload"].update(code=code, historical_case_date=selected,
                                          open=price, high=price, low=price, close=price)
                    row["available_at"] = row["payload"]["bar_end"]
                rows.append(row)
        self.document = {"version": "historical_monthly_case_selection/v1", "cases": cases,
                         "selection_policy": "first_candidate_date_per_calendar_month_before_outcomes",
                         "oos": {"selection_date": "2026-01-14", "status": "SEALED", "results_included": False}}
        self.selection.write_text(json.dumps(self.document), encoding="utf-8")
        write_historical_research_input(FrozenResearchDataset({
            "schema_version": 3, "runtime_input_version": "historical_reconstruction_strategy/v1",
            "dataset_id": "daily-sparse-cohort", "selected_dates": [case["selection_date"] for case in cases],
            "revision_count": len(rows),
            "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
            "excluded_cases": [], "not_contemporaneous_top20": True,
            "research_session_profile": research_session_profile_document("krx-regular/v1"),
        }, tuple(rows)), self.source)

    def _expanded_training_fixture(self):
        import hashlib
        from scripts.plan_historical_monthly_case_selection import plan, expand_train
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset, load_frozen_research_export
        from kiwoom_monitor.infrastructure.historical_reconstruction import write_historical_research_input
        from tests.unit.test_plan_historical_monthly_case_selection import _split
        projection = {"version": "historical_minute_exclusion_projection/v1", "cases": [
            {**{key: value for key, value in case.items() if key != "role"},
             "candidate_codes_original": 1, "candidate_codes_excluded": 0, "readiness_after_exclusion": "READY"}
            for case in self.document["cases"]]}
        projection["cases"].insert(1, {**projection["cases"][0], "selection_date": "2024-09-03", "outcome_date": "2024-09-04"})
        projection_path, prior_path = self.root / "projection.json", self.root / "prior.json"
        projection_path.write_text(json.dumps(projection), encoding="utf-8")
        digest = hashlib.sha256(projection_path.read_bytes()).hexdigest()
        prior = plan(projection, digest, _split(), "split-hash", train_months=1)
        prior_path.write_text(json.dumps(prior), encoding="utf-8")
        expanded = expand_train(projection, digest, prior, hashlib.sha256(prior_path.read_bytes()).hexdigest())
        expansion_path = self.root / "expansion/fixed.json"
        expansion_path.parent.mkdir()
        expansion_path.write_text(json.dumps(expanded), encoding="utf-8")
        baseline = prepare_daily_requests(self.selection, self.source, self.output, regular_exit_buffer_seconds=60)[0]
        dataset = load_frozen_research_export(self.source)
        rows = [row for row in dataset.observations if row["payload"].get("selection_date", row["payload"].get("historical_case_date")) == "2024-09-02"]
        from datetime import datetime, timedelta
        extra = deepcopy(rows)
        for row in extra:
            row["revision_id"] += "-next-day"
            for key in ("effective_at", "available_at", "observation_key"):
                if key in row:
                    row[key] = (datetime.fromisoformat(row[key]) + timedelta(days=1)).isoformat()
            if row["kind"] == "historical_candidate_population":
                row["payload"]["selection_date"] = "2024-09-03"
            else:
                row["payload"]["historical_case_date"] = "2024-09-03"
                for key in ("bar_start", "bar_end"):
                    row["payload"][key] = (datetime.fromisoformat(row["payload"][key]) + timedelta(days=1)).isoformat()
        rows = [{**row, "accepted_sequence": ordinal} for ordinal, row in enumerate([*rows, *extra], 1)]
        manifest = {**dataset.manifest, "dataset_id": "training-case-source", "selected_dates": ["2024-09-02", "2024-09-03"],
                    "revision_count": len(rows), "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest()}
        source = self.root / "source-train"
        write_historical_research_input(FrozenResearchDataset(manifest, tuple(rows)), source)
        return expansion_path, projection_path, prior_path, baseline, ("2024-09-02", "2024-09-03"), source, self.root / "expanded-results"

    def test_expanded_case_reuses_native_runner_and_preserves_fixed_parameters_and_sources(self):
        from scripts.prepare_historical_baseline_requests import prepare_training_batch_request
        from kiwoom_monitor.research_process import load_development_validation_request, execute_development_validation
        args = self._expanded_training_fixture()
        preserved = {str(path): path.read_bytes() for path in [*args[:4], *args[5].iterdir()]}
        path = prepare_training_batch_request(*args)
        batch = load_development_validation_request(path)
        baseline = load_development_validation_request(args[3])
        self.assertEqual(baseline.request.strategy, batch.request.strategy)
        self.assertEqual(baseline.request.execution, batch.request.execution)
        self.assertEqual(("train-2024-09-03", "train-2024-09-04"), batch.fold_names)
        self.assertEqual(["TRAIN", "TRAIN"], [fold.role for fold in batch.request.evaluation.folds])
        result = execute_development_validation(batch, owner_token="expanded-training-fixture")
        self.assertEqual("COMPLETED", result["batch_status"])
        self.assertEqual(2, len(result["steps"]))
        self.assertEqual("TRAIN", result["steps"][0]["role"])
        self.assertEqual(path, prepare_training_batch_request(*args))
        self.assertEqual(preserved, {str(value): value.read_bytes() for value in map(Path, preserved)})
        self.assertFalse((self.output / "research.sqlite3").exists())

    def test_expanded_case_rejects_changed_bindings_validation_dates_and_old_output_paths(self):
        from scripts.prepare_historical_baseline_requests import prepare_training_batch_request
        args = self._expanded_training_fixture()
        with self.assertRaisesRegex(ValueError, "included TRAIN"):
            prepare_training_batch_request(*args[:4], ("2024-10-02", "2024-10-04"), *args[5:])
        for dates in (("2024-09-02",), ("2024-09-03", "2024-09-02"), ("2024-09-02", "2024-10-02")):
            with self.subTest(dates=dates), self.assertRaises(ValueError):
                prepare_training_batch_request(*args[:4], dates, *args[5:])
        for output in (args[5] / "results", self.output / "new", args[0].parent / "new"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "outside"):
                prepare_training_batch_request(*args[:6], output)
        value = json.loads(args[0].read_bytes())
        value["cases"][0]["role"] = "VALIDATION"
        args[0].write_text(json.dumps(value), encoding="utf-8")
        with patch("scripts.prepare_historical_baseline_requests.load_research_input", side_effect=AssertionError("no source load")), self.assertRaisesRegex(ValueError, "bindings"):
            prepare_training_batch_request(*args)
        self.assertFalse(args[-1].exists())

    def test_expanded_case_rejects_wrong_input_and_changed_baseline_before_publication(self):
        from scripts.prepare_historical_baseline_requests import prepare_training_batch_request
        args = self._expanded_training_fixture()
        with self.assertRaisesRegex(ValueError, "exactly the declared"):
            prepare_training_batch_request(*args[:5], self.source, args[-1])
        value = json.loads(args[3].read_bytes())
        value["request"]["strategy"].pop("session_exit_policy")
        value["request"]["strategy"].pop("session_exit_buffer_seconds")
        args[3].write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "session-exit"):
            prepare_training_batch_request(*args)
        self.assertFalse(args[-1].exists())

    def test_daily_requests_use_existing_runner_reset_state_and_keep_censoring(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        from kiwoom_monitor.research_process import load_development_validation_request, execute_development_validation
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        self.assertEqual(2, len(paths))
        for path in paths:
            batch = load_development_validation_request(path)
            result = execute_development_validation(batch, owner_token="daily-fixture")
            self.assertEqual("COMPLETED", result["batch_status"])
            self.assertEqual(["COMPLETED", "COMPLETED"], [step["state"] for step in result["steps"]])
            self.assertEqual("INCOMPLETE", result["comparison"]["status"])
            self.assertTrue(all(row["status"] == "INSUFFICIENT_SAMPLE" for row in result["comparison"]["partitions"]))
            self.assertEqual(2, len(result["run_ids"]))
        repository = ResearchRepository(self.output / "research.sqlite3", read_only=True)
        first, second = result["run_ids"]  # Both registered families used the same fixed days.
        self.assertTrue(repository.load_evaluations(second))
        breakout = execute_development_validation(load_development_validation_request(paths[0]), owner_token="cache-check")
        self.assertEqual(2, breakout["cached_count"])
        first, second = breakout["run_ids"]
        events = repository.load_execution_events(first)
        self.assertTrue(any(event["event_type"] == "POSITION_CENSORED" for event in events))
        self.assertFalse(any(event["side"] == "SELL" and event["state"] == "FILLED" for event in events))
        decisions = repository.load_evaluations(second)
        self.assertTrue(any(row["final_action"] == "ENTER" for row in decisions))
        self.assertFalse(any("different_symbol_position_managed_elsewhere" in row["reasons"] for row in decisions))
        # Independent day results are not a continuous account equity curve.
        self.assertFalse((self.output / "requests" / "daily-breakout.csv").exists())

    def test_plan_only_preserves_dates_roles_costs_and_does_not_read_input(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_plan
        with patch("scripts.prepare_historical_baseline_requests.load_research_input", side_effect=AssertionError("no input")):
            path = prepare_daily_plan(self.selection, self.output)
        plan = json.loads(path.read_text())
        self.assertEqual(["TRAIN", "VALIDATION"], [fold["role"] for fold in plan["evaluation"]["folds"]])
        self.assertEqual("reset_state_and_cash_per_partition/v1", plan["state_policy"])
        self.assertEqual("2024-09-03T15:30:00.000001+09:00", plan["evaluation"]["folds"][0]["end"])
        self.assertFalse((self.output / "requests").exists())
        self.assertFalse((self.output / "research.sqlite3").exists())

    def test_oos_and_partitioned_source_are_rejected_before_payload_load(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        path = self.source / "manifest.json"
        original = json.loads(path.read_text())
        for changes in ({"selected_dates": ["2024-09-02", "2026-01-14"]},
                        {"development_partition": {}}, {"final_holdout_partition": {}},
                        {"runtime_input_version": "independent_development_input/v1"},
                        {"research_session_profile": research_session_profile_document("krx-full-day/v1")},
                        {"theme_snapshots_file": "themes.json"}):
            with self.subTest(changes=changes):
                path.write_text(json.dumps({**original, **changes}))
                with patch("scripts.prepare_historical_baseline_requests.load_research_input", side_effect=AssertionError("no payload")):
                    with self.assertRaises(ValueError):
                        prepare_daily_requests(self.selection, self.source, self.output)
        self.assertFalse((self.output / "requests").exists())

    def test_declared_missing_days_and_excluded_candidates_do_not_publish_requests(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        for change in ("count", "exclusion"):
            changed = deepcopy(self.document)
            if change == "count":
                changed["cases"][1]["candidate_codes_after_exclusion"] = 2
            else:
                changed["cases"][1]["excluded_codes"] = ["000660"]
            self.selection.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                prepare_daily_requests(self.selection, self.source, self.root / change)
            self.assertFalse((self.root / change / "requests").exists())

    def test_plan_rejects_oos_reordered_duplicates_and_changed_existing_selection(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_plan
        for change in ("role", "boundary", "reverse", "duplicate"):
            changed = deepcopy(self.document)
            if change == "role": changed["cases"][1]["role"] = "OOS"
            elif change == "boundary": changed["oos"]["selection_date"] = "2024-10-04"
            elif change == "reverse": changed["cases"].reverse()
            else: changed["cases"][1]["selection_date"] = "2024-09-02"
            self.selection.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): prepare_daily_plan(self.selection, self.output)
        self.selection.write_text(json.dumps(self.document))
        path = prepare_daily_plan(self.selection, self.output)
        before = path.read_bytes()
        changed = deepcopy(self.document)
        changed["cases"][1]["candidate_codes_after_exclusion"] = 2
        self.selection.write_text(json.dumps(changed))
        with self.assertRaises(ValueError): prepare_daily_plan(self.selection, self.output)
        self.assertEqual(before, path.read_bytes())


    def test_day_without_continuous_pair_is_not_silently_skipped(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        from kiwoom_monitor.infrastructure.historical_reconstruction import write_historical_research_input
        from kiwoom_monitor.infrastructure.research_data_source import load_research_input, FrozenResearchDataset
        import hashlib
        dataset = load_research_input(self.source)
        rows = tuple(row for row in dataset.observations if row["kind"] != "minute_bar"
                     or row["payload"]["historical_case_date"] != "2024-10-02"
                     or row["payload"]["bar_start"][11:16] == "09:00")
        source = self.root / "missing-pairs"
        manifest = {**dataset.manifest, "revision_count": len(rows),
                    "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest()}
        write_historical_research_input(FrozenResearchDataset(manifest, rows), source)
        with self.assertRaisesRegex(ValueError, "daily partition is not ready"):
            prepare_daily_requests(self.selection, source, self.output)
        self.assertFalse((self.output / "requests").exists())

    def test_daily_plan_keeps_verified_delayed_session_schedule(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_plan
        changed = deepcopy(self.document)
        changed["cases"][0].update(selection_date="2024-11-13", outcome_date="2024-11-14")
        changed["cases"][1].update(selection_date="2024-12-02", outcome_date="2024-12-03")
        self.selection.write_text(json.dumps(changed))
        plan = json.loads(prepare_daily_plan(self.selection, self.output).read_text())
        first = plan["evaluation"]["folds"][0]
        self.assertEqual("2024-11-14T10:00:00+09:00", first["start"])
        self.assertEqual("2024-11-14T16:30:00.000001+09:00", first["end"])

    def test_atomic_request_publication_preserves_concurrent_winner(self):
        from scripts.prepare_historical_baseline_requests import _write_immutable_json
        path = self.root / "concurrent.json"
        def publish_other_file(temporary, target):
            target.write_text("foreign request", encoding="utf-8")
            raise FileExistsError
        with patch("scripts.prepare_historical_baseline_requests.os.link", side_effect=publish_other_file):
            with self.assertRaises(ValueError):
                _write_immutable_json(path, {"request": "ours"})
        self.assertEqual("foreign request", path.read_text())
        self.assertEqual({"source", "selection.json", "concurrent.json"}, {value.name for value in self.root.iterdir()})

    def test_results_cannot_be_written_inside_the_frozen_source(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        before = {value.name: value.read_bytes() for value in self.source.iterdir()}
        with self.assertRaisesRegex(ValueError, "outside the frozen source"):
            prepare_daily_requests(self.selection, self.source, self.source / "results")
        self.assertEqual(before, {value.name: value.read_bytes() for value in self.source.iterdir()})


if __name__ == "__main__":
    unittest.main()
