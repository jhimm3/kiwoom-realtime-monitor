from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.research_process import (
    execute_process_request,
    load_research_process_request,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from scripts.run_research import ResearchRunCancelled
from scripts.run_research import LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash
from research_test_support import (
    research_request_document as _request_document,
    write_empty_dataset as _write_empty_dataset,
)


class ResearchProcessTests(unittest.TestCase):
    def test_generic_cli_rejects_request_aliases_before_parsing_or_execution(self) -> None:
        from io import StringIO
        from kiwoom_monitor.research_process import _run_research_process
        from research_test_support import write_campaign_request

        for mode in ("invalid-json", "invalid-search", "single_run", "rank_comparison", "limited_search"):
            for output in ("result", "cancel"):
                with self.subTest(mode=mode, output=output), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    if mode == "limited_search":
                        request, _ = write_campaign_request(root, max_trials=1)
                    else:
                        _write_empty_dataset(root)
                        request = root / "request.json"
                        document = (_request_document() if mode not in {"invalid-json", "invalid-search"}
                                    else {"mode": "limited_search", "audit_source_marker": "preserve-this-input"})
                        if mode in {"single_run", "rank_comparison"}:
                            document["mode"] = mode
                        request.write_text("{invalid json" if mode == "invalid-json" else json.dumps(document), encoding="utf-8")
                    source = request.read_bytes()
                    result, cancel = root / "result.json", root / "cancel.request"
                    # Different spellings resolving to the source must be rejected too.
                    alias = root / "alias" / ".." / request.name
                    arguments = ["--request", str(request), "--result", str(alias if output == "result" else result),
                                 "--cancel", str(alias if output == "cancel" else cancel)]
                    rejected = False
                    with patch("sys.stdout", new=StringIO()), patch("sys.stderr", new=StringIO()):
                        try:
                            _run_research_process(arguments)
                        except SystemExit as error:
                            self.assertEqual(2, error.code)
                            rejected = True
                    self.assertEqual(source, request.read_bytes())
                    self.assertTrue(rejected, "an input alias must be rejected before publishing any result")
                    self.assertFalse(result.exists())
                    self.assertFalse(cancel.exists())
                    self.assertFalse((root / "output").exists())

    def test_generic_cli_protects_declared_database_dataset_and_distinct_output_channels(self) -> None:
        from io import StringIO
        import sqlite3
        from contextlib import closing
        from kiwoom_monitor.research_process import _run_research_process

        for valid in (False, True):
            for output, target in (("result", "database"), ("cancel", "database"),
                                   ("result", "dataset"), ("cancel", "dataset"), ("both", "result")):
                with self.subTest(valid=valid, output=output, target=target), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    _write_empty_dataset(root)
                    document = _request_document()
                    if not valid:
                        document.pop("strategy")
                    request = root / "request.json"
                    request.write_text(json.dumps(document), encoding="utf-8")
                    database = root / "output/research.sqlite3"
                    database.parent.mkdir()
                    with closing(sqlite3.connect(database)) as connection, connection:
                        connection.execute("CREATE TABLE original_input(marker TEXT)")
                        connection.execute("INSERT INTO original_input VALUES('preserve native database')")
                    dataset = root / "dataset/observations.jsonl"
                    protected = {path: path.read_bytes() for path in (request, database, dataset, root / "dataset/manifest.json")}
                    result, cancel = root / "result.json", root / "cancel.request"
                    target_path = database if target == "database" else dataset if target == "dataset" else result
                    if output == "result":
                        result = target_path
                    elif output == "cancel":
                        cancel = target_path
                    else:
                        cancel = result
                    rejected = False
                    with patch("sys.stdout", new=StringIO()), patch("sys.stderr", new=StringIO()):
                        try:
                            _run_research_process(["--request", str(request), "--result", str(result), "--cancel", str(cancel)])
                        except SystemExit as error:
                            self.assertEqual(2, error.code)
                            rejected = True
                    self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                    self.assertTrue(rejected, "declared inputs and output channels must not alias")
                    if target == "result":
                        self.assertFalse(result.exists())
                    self.assertFalse((root / "output/runs").exists())

    def test_generic_cli_separate_failure_result_keeps_atomic_publication_and_original_input(self) -> None:
        from io import StringIO
        from kiwoom_monitor.research_process import _run_research_process

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text('{"mode":"limited_search","audit_source_marker":"preserve-this-input"}', encoding="utf-8")
            source = request.read_bytes()
            result = root / "result.json"
            old_result = b'{"status":"previous-result"}\n'
            result.write_bytes(old_result)
            native_replace = Path.replace
            replacements = []
            def publish(temporary, target):
                self.assertEqual(result, target)
                self.assertEqual(old_result, target.read_bytes())
                envelope = json.loads(temporary.read_text(encoding="utf-8"))
                self.assertEqual("failed", envelope["status"])
                replacements.append((temporary, target))
                return native_replace(temporary, target)
            with patch("sys.stdout", new=StringIO()), patch.object(Path, "replace", publish):
                code = _run_research_process(["--request", str(request), "--result", str(result)])
            self.assertEqual(1, code)
            self.assertEqual(source, request.read_bytes())
            self.assertEqual(1, len(replacements))
            self.assertIn("research request strategy must be an object", json.loads(result.read_text())["reason"])
            self.assertFalse(replacements[0][0].exists())
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_decimal_cost_request_round_trips_without_float_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["execution"]["cost_model"].update(
                version="fixed_bps/v2", commission_bps="1.5",
                sell_tax_bps="20", slippage_bps="5",
            )
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            cost = load_research_process_request(path).execution.cost_model
            self.assertEqual(cost.to_dict(), document["execution"]["cost_model"])

    def test_relative_paths_and_all_locked_settings_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            request = load_research_process_request(path)
            self.assertEqual(root / "dataset", request.dataset)
            self.assertEqual(root / "output/research.sqlite3", request.database)
            self.assertEqual(root / "output/runs", request.runs_dir)
            original = _request_document()
            self.assertEqual(original["strategy"], request.strategy.to_dict())
            self.assertEqual(original["execution"]["cost_model"], request.execution.cost_model.to_dict())
            self.assertEqual(original["evaluation"], {key: request.evaluation.to_dict()[key] for key in original["evaluation"]})
        self.assertEqual("rank_comparison", request.mode)
        self.assertTrue(request.strategy.rank_persistence_required)
        self.assertEqual("locked fixture", request.execution.cost_model.source)
        self.assertEqual("train", request.evaluation.folds[0].name)

    def test_empty_request_executes_in_process_contract_and_writes_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            result = execute_process_request(load_research_process_request(path))
            manifest = Path(result["manifest"])
            self.assertTrue(manifest.is_file())
            document = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertTrue(document)
            self.assertEqual(result["comparison"], document["comparison"])
            for name in ("baseline_manifest", "variant_manifest"):
                child = Path(document[name])
                self.assertTrue(child.is_file())
                self.assertTrue(json.loads(child.read_text(encoding="utf-8")))
        self.assertEqual("ok", result["status"])
        self.assertEqual("rank_comparison", result["kind"])
        self.assertEqual("INELIGIBLE", result["comparison_status"])
        self.assertTrue(manifest.name == "manifest.json")

    def test_output_inside_frozen_dataset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["database"] = "dataset/research.sqlite3"
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                load_research_process_request(path)

    def test_cancel_marker_stops_before_empty_dataset_is_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            cancel = root / "cancel.request"
            cancel.write_text("cancel\n", encoding="ascii")
            with self.assertRaises(ResearchRunCancelled):
                execute_process_request(
                    load_research_process_request(path), cancel_path=cancel,
                )
            self.assertEqual([], list((root / "output/runs").rglob("manifest.json")))

    def test_limited_search_runs_to_budget_and_resume_does_not_duplicate_trials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "limited_search"
            document["search"] = {
                "version": "limited_search/v2",
                "hypothesis_refs": ["hypothesis-1"],
                "dataset_id": "empty",
                "dataset_hash": hashlib.sha256(b"").hexdigest(),
                "family_allowlist": ["krx_bar_close_breakout/v1"],
                "factor_allowlist": ["rolling_high_breakout/v1", "rank_persistence/v1"],
                "parameter_space": {"target_bps": [400, 500]},
                "objective": {"net_pnl_won": "maximize", "max_drawdown_won": "minimize"},
                "constraints": {"minimum_closed_trades": 1},
                "split_version": "chronological_holdout/v1",
                "max_trials": 4, "max_seconds": 60, "seed": 11,
                "ablations": ["rank_persistence"],
                "cost_stress_multipliers_ppm": [2_000_000],
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            first = execute_process_request(request)
            second = execute_process_request(request)
        self.assertEqual("limited_search", first["kind"])
        self.assertEqual(4, first["budget"]["used_trials"])
        self.assertEqual(
            ["baseline", "no_trade_baseline", "ablation", "cost_stress"],
            [card["variant"] for card in first["candidate_cards"]],
        )
        self.assertEqual(0, second["attempted_now"])
        self.assertEqual(first["candidate_cards"], second["candidate_cards"])

    def test_interrupted_trial_is_retried_without_cancelled_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "limited_search"
            document["search"] = {
                "version": "limited_search/v2", "hypothesis_refs": ["h1"],
                "dataset_id": "empty", "dataset_hash": hashlib.sha256(b"").hexdigest(),
                "family_allowlist": ["krx_bar_close_breakout/v1"],
                "factor_allowlist": ["rolling_high_breakout/v1"],
                "parameter_space": {}, "objective": {"net_pnl_won": "maximize"},
                "constraints": {}, "split_version": "chronological_holdout/v1",
                "max_trials": 1, "max_seconds": 60, "seed": 1,
                "include_no_trade_baseline": False,
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            with patch(
                "kiwoom_monitor.research_process.execute_research",
                side_effect=ResearchRunCancelled("fixture interruption"),
            ):
                interrupted = execute_process_request(request)
            repository = ResearchRepository(request.database)
            self.assertEqual("cancelled", interrupted["job_status"])
            self.assertEqual((), repository.load_search_trials(request.search.experiment_id))
            completed = execute_process_request(request)
            attempts = repository.load_trial_attempts(request.search.experiment_id)
            results = repository.load_search_trials(request.search.experiment_id)
        self.assertEqual("completed", completed["job_status"])
        self.assertEqual(["INTERRUPTED", "INELIGIBLE"], [row["status"] for row in attempts])
        self.assertEqual(1, len(results))

    def test_search_identity_includes_strategy_execution_and_actual_fold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)

            def load(document: dict, name: str):
                document["mode"] = "limited_search"
                document["search"] = {
                    "version": "limited_search/v2", "hypothesis_refs": ["h1"],
                    "dataset_id": "empty", "dataset_hash": hashlib.sha256(b"").hexdigest(),
                    "family_allowlist": ["krx_bar_close_breakout/v1"],
                    "factor_allowlist": ["rolling_high_breakout/v1"],
                    "parameter_space": {"target_bps": [400]},
                    "objective": {"net_pnl_won": "maximize"}, "constraints": {},
                    "split_version": "chronological_holdout/v1",
                    "max_trials": 1, "max_seconds": 60, "seed": 1,
                }
                path = root / f"{name}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                return load_research_process_request(path).search

            baseline = load(_request_document(), "baseline")
            strategy_document = _request_document()
            strategy_document["strategy"]["target_bps"] = 900
            strategy = load(strategy_document, "strategy")
            execution_document = _request_document()
            execution_document["execution"]["initial_cash_won"] = 2_000_000
            execution = load(execution_document, "execution")
            fold_document = _request_document()
            fold_document["evaluation"]["folds"][0]["end"] = "2026-09-12T02:00:00+00:00"
            fold = load(fold_document, "fold")
        assert baseline and strategy and execution and fold
        self.assertNotEqual(baseline.experiment_id, strategy.experiment_id)
        self.assertNotEqual(baseline.experiment_id, execution.experiment_id)
        self.assertNotEqual(baseline.experiment_id, fold.experiment_id)

    def test_second_registered_family_is_parsed_and_runs_in_same_process_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "single_run"
            document["family"] = "krx_pullback_reacceleration/v1"
            document["strategy"] = {
                "strategy_version": "v1", "pullback_factor_version": "v1",
                "rank_factor_version": "v1", "lookback_bars": 3,
                "minimum_pullback_bps": 500, "minimum_reacceleration_bps": 100,
                "rank_persistence_enabled": False, "rank_persistence_required": False,
                "rank_top_k": 20, "rank_window_seconds": 60,
                "rank_max_gap_seconds": 30, "rank_min_residency_seconds": 10,
                "stop_loss_bps": 300, "target_bps": 500, "max_hold_minutes": 10,
                "quantity": 1, "capital_won": 1_000_000,
                "signal_valid_seconds": 60, "cooldown_seconds": 30,
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            result = execute_process_request(request)
        self.assertEqual("krx_pullback_reacceleration/v1", request.family)
        self.assertEqual("single_run", result["kind"])

    def test_explicit_session_profile_separates_run_identity_and_missing_request_stays_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            legacy_document = _request_document()
            legacy_document["mode"] = "single_run"
            explicit_document = _request_document()
            explicit_document["mode"] = "single_run"
            explicit_document["session_profile"] = "krx-regular/v1"
            legacy_path = root / "legacy.json"
            explicit_path = root / "explicit.json"
            legacy_path.write_text(json.dumps(legacy_document), encoding="utf-8")
            explicit_path.write_text(json.dumps(explicit_document), encoding="utf-8")
            legacy_request = load_research_process_request(legacy_path)
            explicit_request = load_research_process_request(explicit_path)
            legacy = execute_process_request(legacy_request)
            explicit = execute_process_request(explicit_request)
            explicit_manifest = json.loads(Path(explicit["manifest"]).read_text(encoding="utf-8"))
        self.assertIsNone(legacy_request.session_profile)
        self.assertEqual(LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash())
        self.assertNotEqual(legacy["run_id"], explicit["run_id"])
        self.assertEqual("krx-regular/v1", explicit_manifest["spec"]["session_profile"]["profile"])

    def test_unknown_session_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["session_profile"] = "nxt-main/v1"
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported research session profile"):
                load_research_process_request(path)


    def test_cli_reuses_parsed_request_with_native_input_and_result_oracles(self) -> None:
        from io import StringIO
        from kiwoom_monitor import research_process
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        from research_test_support import write_campaign_request

        modes = ("single_run", "rank_comparison", "limited_search", "leader_breakout",
                 "invalid-json", "nonobject", "read-error")
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if mode == "limited_search":
                    request_path, _ = write_campaign_request(root, max_trials=1)
                else:
                    _write_empty_dataset(root)
                    request_path = root / "request.json"
                    if mode == "leader_breakout":
                        document = {"mode": mode, "dataset": "dataset", "session_profile": "krx-regular/v1",
                                    "policy": document_leader_breakout_policy().to_dict()}
                    else:
                        document = {**_request_document(), "mode": mode}
                    if mode != "read-error":
                        request_path.write_text(
                            "{invalid-json" if mode == "invalid-json" else "[]" if mode == "nonobject"
                            else json.dumps(document), encoding="utf-8",
                        )
                contents = request_path.read_text(encoding="utf-8") if request_path.exists() else None
                protected = {path: path.read_bytes() for path in (
                    root / "dataset/manifest.json", root / "dataset/observations.jsonl",
                    *((request_path,) if request_path.exists() else ()),
                )}
                result_path = root / "result.json"
                old_result = b'{"status":"previous-result"}\n'
                result_path.write_bytes(old_result)
                counts = {"read": 0, "parse": 0}
                requests, replacements = [], []
                native_read, native_parse, native_replace = Path.read_text, json.loads, Path.replace
                native_execute = research_process.execute_process_request

                def read(path, *args, **kwargs):
                    if path.resolve() == request_path.resolve():
                        counts["read"] += 1
                    return native_read(path, *args, **kwargs)

                def parse(encoded, *args, **kwargs):
                    if isinstance(encoded, str) and encoded == contents:
                        counts["parse"] += 1
                    return native_parse(encoded, *args, **kwargs)

                def execute(request, *args, **kwargs):
                    requests.append(request)
                    return native_execute(request, *args, **kwargs)

                def publish(temporary, target):
                    if target == result_path:
                        self.assertEqual(old_result, target.read_bytes())
                        self.assertIn("status", native_parse(native_read(temporary, encoding="utf-8")))
                        replacements.append((temporary, target))
                    return native_replace(temporary, target)

                with patch.object(Path, "read_text", read), patch.object(json, "loads", parse), \
                        patch.object(Path, "replace", publish), patch("sys.stdout", new=StringIO()), \
                        patch.object(research_process, "execute_process_request", side_effect=execute):
                    code = research_process._run_research_process([
                        "--request", str(request_path), "--result", str(result_path),
                    ])
                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                self.assertEqual(1, len(replacements))
                self.assertFalse(replacements[0][0].exists())
                self.assertEqual([], list(root.glob("*.tmp")))
                if mode in {"invalid-json", "nonobject", "read-error"}:
                    self.assertEqual((1, "failed", []), (code, result["status"], requests))
                    self.assertEqual(
                        "ValueError: research request must be a JSON object" if mode == "nonobject"
                        else "ValueError: research request cannot be read", result["reason"],
                    )
                    self.assertEqual({"read": 2, "parse": 0 if mode == "read-error" else 2}, counts)
                    continue
                self.assertEqual((0, "ok", mode), (code, result["status"], result["kind"]))
                self.assertEqual(1, len(requests))
                converted = requests[0]
                self.assertEqual((mode, (root / "dataset").resolve()), (converted.mode, converted.dataset))
                if mode == "leader_breakout":
                    self.assertIsInstance(converted, research_process.LeaderBreakoutProcessRequest)
                    self.assertEqual("krx-regular/v1", converted.session_profile)
                    self.assertEqual(("1m", 0, False, False), (
                        result["input_mode"], result["bar_count"], result["performance_verified"], result["order_authorized"],
                    ))
                else:
                    self.assertIsInstance(converted, research_process.ResearchProcessRequest)
                    self.assertEqual((root / "output/research.sqlite3").resolve(), converted.database)
                    self.assertEqual((2, 1_000_000, "chronological_holdout/v1"), (
                        converted.strategy.lookback_bars, converted.execution.initial_cash_won, converted.evaluation.version,
                    ))
                    if mode == "limited_search":
                        self.assertEqual((1, "completed", 1), (
                            converted.search.max_trials, result["job_status"], result["budget"]["used_trials"],
                        ))
                    else:
                        self.assertTrue(Path(result["manifest"]).is_file())
                self.assertEqual({"read": 1, "parse": 1}, counts)


if __name__ == "__main__":
    unittest.main()
