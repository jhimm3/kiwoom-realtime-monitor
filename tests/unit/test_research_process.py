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
    def test_headless_cli_executes_without_desktop_news_sources_or_site_packages(self) -> None:
        import importlib.resources
        import os
        import shutil
        import sqlite3
        import subprocess
        import sys
        from contextlib import closing

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            package = source / "kiwoom_monitor"
            omitted = {
                "presentation", "__pycache__", "news_ai.py", "naver_news.py",
                "news_ai_backup.py", "news_ai_repository.py", "news_schema.py",
                "settings_backup.py", "kiwoom_rest",
            }
            shutil.copytree(
                Path(__file__).resolve().parents[2] / "src" / "kiwoom_monitor",
                package,
                ignore=lambda _, names: [name for name in names if name in omitted],
            )
            # Keep real KST timezone data while excluding every installed package.
            timezone_data = root / "timezone-data"
            shutil.copytree(importlib.resources.files("tzdata") / "zoneinfo", timezone_data)
            environment = {
                **os.environ, "PYTHONPATH": str(source), "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONTZPATH": str(timezone_data),
            }
            environment.pop("KIWOOM_DESKTOP_DATA_DIR", None)
            children = []
            def run_child(command, *, timeout):
                process = subprocess.Popen(
                    command, cwd=root, env=environment, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                )
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    children.append({"pid": process.pid, "exit_code": process.returncode})
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
            proof = run_child(
                [sys.executable, "-S", "-c", "\n".join((
                    "import importlib.util,json,sys",
                    "from kiwoom_monitor import research_process",
                    "from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv",
                    "print(json.dumps({'file':research_process.__file__,",
                    "'qt':importlib.util.find_spec('PySide6') is not None,",
                    "'news':importlib.util.find_spec('kiwoom_monitor.infrastructure.news_ai') is not None,",
                    "'loaded_restore':'kiwoom_monitor.infrastructure.persistence.strict_restore' in sys.modules,",
                    "'loaded_delivery':'kiwoom_monitor.research_result_delivery' in sys.modules,",
                    "'loaded_kiwoom_rest':[name for name in sys.modules if name == 'kiwoom_monitor.infrastructure.kiwoom_rest'",
                    "or name.startswith('kiwoom_monitor.infrastructure.kiwoom_rest.')],",
                    "'site_paths':[p for p in sys.path if 'site-packages' in p]}))",
                ))], timeout=15,
            )
            self.assertEqual(0, proof.returncode, proof.stderr)
            provenance = json.loads(proof.stdout)
            self.assertEqual(package / "research_process.py", Path(provenance["file"]))
            self.assertEqual((False, False, False, []), (
                provenance["qt"], provenance["news"], provenance["loaded_restore"], provenance["site_paths"],
            ))
            self.assertFalse(provenance["loaded_delivery"])
            self.assertEqual([], provenance["loaded_kiwoom_rest"])
            self.assertFalse((package / "infrastructure/kiwoom_rest").exists())
            self.assertFalse((package / "presentation").exists())
            self.assertFalse((package / "infrastructure/persistence/news_ai_backup.py").exists())
            for mode in ("success", "invalid", "cancel"):
                with self.subTest(mode=mode):
                    case = root / mode
                    case.mkdir()
                    _write_empty_dataset(case)
                    document = _request_document()
                    document["mode"] = "single_run"
                    request = case / "request.json"
                    request.write_text("{" if mode == "invalid" else json.dumps(document), encoding="utf-8")
                    result_path = case / "result.json"
                    cancel_path = case / "cancel.request"
                    if mode == "cancel":
                        cancel_path.write_text("cancel\n", encoding="ascii")
                    protected = {path: path.read_bytes() for path in (request, *sorted((case / "dataset").iterdir()))}
                    child = run_child(
                        [sys.executable, "-S", "-m", "kiwoom_monitor.research_process",
                         "--request", str(request), "--result", str(result_path), "--cancel", str(cancel_path)],
                        timeout=15,
                    )
                    self.assertEqual({"success": 0, "invalid": 1, "cancel": 2}[mode], child.returncode, child.stderr)
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    self.assertEqual(result, json.loads(child.stdout))
                    self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                    self.assertEqual([], list(case.rglob("*.tmp")))
                    database = case / "output/research.sqlite3"
                    if mode == "success":
                        self.assertEqual(("ok", "single_run"), (result["status"], result["kind"]))
                        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
                        self.assertEqual((result["run_id"], "empty", hashlib.sha256(b"").hexdigest()), (
                            manifest["run_id"], manifest["input_dataset_id"], manifest["input_revision_ids_hash"],
                        ))
                        with closing(sqlite3.connect(database)) as connection:
                            self.assertEqual(("completed", manifest["logical_result_hash"]), connection.execute(
                                "SELECT status,logical_result_hash FROM research_runs WHERE run_id=?", (result["run_id"],),
                            ).fetchone())
                            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0])
                    else:
                        self.assertEqual("failed" if mode == "invalid" else "cancelled", result["status"])
                        if mode == "invalid":
                            self.assertEqual("ValueError: research request cannot be read", result["reason"])
                        self.assertFalse(database.exists())
            print(json.dumps({"headless_absence_provenance": provenance, "children": children}))

    def test_desktop_main_keeps_native_child_restore_lease_until_cli_returns(self) -> None:
        import os
        import subprocess
        import sys
        from kiwoom_monitor import research_process

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = "\n".join((
                "import sys",
                "from pathlib import Path",
                "from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease,StrictRestoreError",
                "try:",
                "    with AppDataLease(Path(sys.argv[1]),exclusive=True,timeout=0.1): pass",
                "except StrictRestoreError:",
                "    sys.exit(17)",
            ))
            def exclusive_probe():
                return subprocess.run(
                    [sys.executable, "-c", probe, str(root)], capture_output=True,
                    text=True, encoding="utf-8", timeout=10,
                )
            def cli(argv):
                self.assertEqual(["unchanged"], argv)
                result = exclusive_probe()
                self.assertEqual(17, result.returncode, result.stderr)
                return 23
            with patch.dict(os.environ, {"KIWOOM_DESKTOP_DATA_DIR": str(root)}), \
                    patch.object(research_process, "_run_research_process", side_effect=cli):
                self.assertEqual(23, research_process.main(["unchanged"]))
            result = exclusive_probe()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((root / "monitor.sqlite3").exists())
            self.assertFalse((root / "news.sqlite3").exists())

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

    def test_single_run_uses_pinned_calendar_at_each_original_receipt(self) -> None:
        from kiwoom_monitor import research_process

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            dates = [f"2026-10-16T09:0{i}:00+09:00" for i in range(3)]
            rows = [{"ordinal": 1, "revision_id": "members", "kind": "top20_membership",
                     "available_at": dates[0], "accepted_sequence": 1, "payload": {"codes": ["005930"]}}]
            for i in range(2):
                rows.append({
                    "ordinal": i + 2, "accepted_sequence": i + 2, "revision_id": f"bar-{i}",
                    "kind": "minute_bar", "venue": "KRX", "subject": "005930:KRX", "observation_key": dates[i],
                    "available_at": dates[i + 1], "completeness": "complete", "value_kind": "actual",
                    "payload": {
                        "market": "KRX", "code": "005930", "bar_start": dates[i], "bar_end": dates[i + 1],
                        "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 10,
                        "trade_value_million_won": 1, "window_closed": True, "capture_quality": "complete",
                    },
                })
            encoded = "\n".join(json.dumps(row) for row in rows).encode()
            (root / "dataset/observations.jsonl").write_bytes(encoded)
            manifest_path = root / "dataset/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update({
                "revision_count": len(rows), "end": dates[-1],
                "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
                "revision_ids_hash": hashlib.sha256(b"members\nbar-0\nbar-1").hexdigest(),
            })
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            known_open = {
                "venue": "KRX", "trading_date": "2026-10-16", "available_at": dates[0],
                "revision_id": "opening", "evidence_ref": "fixture-opening-notice", "sessions": [{
                    "session": "KRX_REGULAR", "phase": "CONTINUOUS", "support": "SUPPORTED",
                    "starts_at": dates[0], "ends_at": "2026-10-16T15:20:00+09:00",
                    "schedule_version": "fixture/v1",
                }],
            }
            correction = {**known_open, "available_at": dates[-1], "revision_id": "closure", "sessions": []}
            reference_bytes = json.dumps({"version": "strategy-reference/v1", "listings": [],
                                          "calendar": [known_open, correction]}).encode()
            reference_path = root / "references.json"
            reference_path.write_bytes(reference_bytes)
            digest = hashlib.sha256(reference_bytes).hexdigest()
            document = {**_request_document(), "mode": "single_run", "session_profile": "krx-regular/v1"}
            request_path = root / "request.json"
            request_path.write_text(json.dumps(document), encoding="utf-8")
            baseline = execute_process_request(load_research_process_request(request_path))
            document["strategy_references"] = {"path": reference_path.name, "sha256": digest}
            request_path.write_text(json.dumps(document), encoding="utf-8")
            pinned_request = load_research_process_request(request_path)
            pinned = execute_process_request(pinned_request)
            baseline_manifest = json.loads(Path(baseline["manifest"]).read_text(encoding="utf-8"))
            pinned_manifest = json.loads(Path(pinned["manifest"]).read_text(encoding="utf-8"))
            self.assertNotEqual(baseline["run_id"], pinned["run_id"])
            self.assertEqual((2, 1), (baseline_manifest["decision_count"], pinned_manifest["decision_count"]))
            self.assertEqual({"version": "strategy-reference/v1", "sha256": digest},
                             pinned_manifest["spec"]["strategy_references"])
            self.assertEqual(pinned["run_id"], execute_process_request(pinned_request)["run_id"])
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                research_process._run_research_process(["--request", str(request_path), "--result", str(reference_path)])
            self.assertEqual(reference_bytes, reference_path.read_bytes())
            reference_path.write_bytes(reference_bytes + b" ")
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                execute_process_request(pinned_request)


    def test_section12_normal_request_rejects_cross_fold_reviewed_event_before_result_storage(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            rows = [
                {"kind": "top20_membership", "ordinal": index, "accepted_sequence": index,
                 "revision_id": revision_id, "observation_key": stamp, "available_at": stamp,
                 "payload": {"codes": ["005930"]}}
                for index, (revision_id, stamp) in enumerate((
                    ("train-revision", "2026-09-10T09:00:00+09:00"),
                    ("validation-revision", "2026-09-11T09:00:00+09:00"),
                ), 1)
            ]
            encoded = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
            (root / "dataset/observations.jsonl").write_bytes(encoded)
            manifest_path = root / "dataset/manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest.update(revision_count=2, observations_file_hash=hashlib.sha256(encoded).hexdigest(),
                            revision_ids_hash=hashlib.sha256(b"train-revision\nvalidation-revision").hexdigest())
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            references = b'{"version":"strategy-reference/v1","calendar":[],"listings":[]}'
            (root / "references.json").write_bytes(references)
            request = _request_document()
            request.update(mode="single_run", family="breakout_study/v1", session_profile="krx-regular/v1",
                           strategy_references={"path": "references.json", "sha256": hashlib.sha256(references).hexdigest()},
                           strategy={"capital_won": 1000, "policy": {"variant": "B0", "quantity": 1,
                               "exit_at_kst": "15:18", "theme_id": None, "theme_role": None,
                               "exit_on_dependency_loss": False}})
            request["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request["evaluation"]["folds"] = [
                {"name": name, "role": name.upper(), "start": f"2026-09-{day}T09:00:00+09:00",
                 "end": f"2026-09-{day}T15:00:00+09:00"}
                for name, day in (("train", 10), ("validation", 11))
            ]
            links = [
                {"event_id": "review:case-1", "source_occurrence_refs": [f"source:{number}:case-1"],
                 "revision_ids": [revision]}
                for number, revision in enumerate(("train-revision", "validation-revision"), 1)
            ]
            request["evaluation"]["source_event_links"] = links
            path = root / "request.json"

            def execute():
                path.write_text(json.dumps(request), encoding="utf-8")
                return execute_process_request(load_research_process_request(path))

            with self.assertRaisesRegex(ValueError, "eligible evaluation fold"):
                execute()
            self.assertFalse((root / request["database"]).exists())
            self.assertFalse((root / request["runs_dir"]).exists())
            links[1]["event_id"] = "review:case-2"
            result = execute()
            self.assertEqual("ok", result["status"])
            self.assertEqual(2, result["section12"]["source_event_group_count"])
            repository = ResearchRepository(root / request["database"])
            spec = repository.load_run(result["run_id"])["spec"]
            self.assertEqual(links, spec["evaluation_spec"]["source_event_links"])
            with self.assertRaisesRegex(ValueError, "Section 12 development single_run"):
                replace(load_research_process_request(path), mode="rank_comparison")
            request["evaluation"]["folds"][1]["role"] = "OOS"
            with self.assertRaisesRegex(ValueError, "without final holdout"):
                execute()

    def test_section12_linked_development_refuses_marked_final_before_observation_open(self):
        from kiwoom_monitor.infrastructure.research_data_source import BUNDLE_VERSION

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            sealed = dataset / "sealed"
            sealed.mkdir(parents=True)
            request = _request_document()
            request.update(mode="single_run", family="breakout_study/v1", session_profile="krx-regular/v1",
                           strategy={"capital_won": 1000, "policy": {"variant": "B0", "quantity": 1,
                               "exit_at_kst": "15:18", "theme_id": None, "theme_role": None,
                               "exit_on_dependency_loss": False}})
            request["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request["evaluation"]["source_event_links"] = [{
                "event_id": "review:case-1", "source_occurrence_refs": ["source:01:case-1"],
                "revision_ids": ["linked-fixture-revision"],
            }]
            path = root / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            parsed = load_research_process_request(path)
            final = {"runtime_input_version": "independent_final_holdout_input/v1",
                     "observations_file": "not-present.jsonl"}
            (sealed / "manifest.json").write_text(json.dumps(final), encoding="utf-8")
            bundle = {"bundle_version": BUNDLE_VERSION, "children": [{"path": "sealed"}]}
            for manifest in (final, bundle):
                with self.subTest(manifest=manifest):
                    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    with patch("kiwoom_monitor.research_process.load_research_input", side_effect=AssertionError) as loader:
                        with self.assertRaisesRegex(ValueError, "cannot open final holdout input"):
                            execute_process_request(parsed)
                    loader.assert_not_called()
                    # The actual read still rejects the marker if the manifest
                    # was replaced after the separate resource preflight.
                    with patch("kiwoom_monitor.research_process.research_input_encoded_bytes", return_value=0):
                        with self.assertRaisesRegex(ValueError, "cannot open final holdout input"):
                            execute_process_request(parsed)
                    self.assertFalse((root / request["database"]).exists())
                    self.assertFalse((root / request["runs_dir"]).exists())

    def test_section12_close_and_ipo_requests_preserve_actual_filled_quantity(self) -> None:
        from datetime import datetime, timedelta

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            friday = "2026-10-16"
            monday = "2026-10-19"

            def stamp(day, clock):
                return f"{day}T{clock}:00+09:00"

            def session(day, start, end, name, phase):
                return {"session": name, "phase": phase, "support": "SUPPORTED", "schedule_version": "fixture/v1",
                        "starts_at": stamp(day, start), "ends_at": stamp(day, end)}

            rows = [{"revision_id": "universe", "kind": "top20_membership", "available_at": stamp(friday, "09:00"),
                     "payload": {"codes": ["005930"]}}]
            bars = [(friday, clock, price, price) for clock, price in (
                ("15:14", 100), ("15:15", 100), ("15:16", 105), ("15:17", 105),
                ("15:18", 106), ("15:19", 106), ("15:29", 110),
            )] + [(monday, clock, open_price, close) for clock, open_price, close in (
                ("10:00", 103, 103), ("10:01", 102, 102), ("10:02", 101, 90), ("10:03", 89, 89),
            )]
            for day, clock, open_price, close in bars:
                start = stamp(day, clock)
                end = (datetime.fromisoformat(start) + timedelta(minutes=1)).isoformat()
                rows.append({"revision_id": f"{day}-{clock}", "kind": "minute_bar", "venue": "KRX", "subject": "005930:KRX",
                             "observation_key": start, "available_at": end, "completeness": "complete", "value_kind": "actual",
                             "payload": {"market": "KRX", "code": "005930", "bar_start": start, "bar_end": end,
                                         "open": open_price, "high": max(open_price, close), "low": 1, "close": close,
                                         "volume": 20, "trade_value_million_won": 1, "window_closed": True,
                                         "capture_quality": "complete"}})
            for index, row in enumerate(rows, 1):
                row.update(ordinal=index, accepted_sequence=index)
            encoded = "\n".join(json.dumps(row) for row in rows).encode()
            (root / "dataset/observations.jsonl").write_bytes(encoded)
            (root / "dataset/manifest.json").write_text(json.dumps({
                "dataset_id": "section12-close-fixture", "revision_count": len(rows), "end": stamp(monday, "10:04"),
                "observations_file": "observations.jsonl", "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
                "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
            }), encoding="utf-8")
            calendar = [{"venue": "KRX", "trading_date": day, "available_at": stamp(friday, "08:00"),
                         "revision_id": day, "evidence_ref": "fixture-calendar", "sessions": windows} for day, windows in (
                (friday, [session(friday, "09:00", "15:20", "KRX_REGULAR", "CONTINUOUS"),
                          session(friday, "15:29", "15:30", "KRX_CLOSING_AUCTION", "AUCTION_ORDER_ENTRY")]),
                ("2026-10-17", []), ("2026-10-18", []),
                (monday, [session(monday, "10:00", "15:20", "KRX_REGULAR", "CONTINUOUS")]),
            )]
            listing = {"symbol": "005930", "listing_date": friday, "listing_type": "ipo", "available_at": stamp(friday, "08:00"),
                       "source": "fixture-listing", "revision": "listed", "evidence_id": "listing-1", "completeness": "complete"}
            references = json.dumps({"version": "strategy-reference/v1", "calendar": calendar, "listings": [listing]}).encode()
            (root / "references.json").write_bytes(references)
            request = _request_document()
            request.update(mode="single_run", session_profile="krx-regular/v1",
                           strategy_references={"path": "references.json", "sha256": hashlib.sha256(references).hexdigest()})
            request["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request["execution"]["cost_model"].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
                                                       valid_from=stamp(friday, "08:00"), valid_to=stamp(monday, "16:00"))
            request["evaluation"]["folds"][0].update(start=stamp(friday, "09:00"), end=stamp(monday, "16:00"))
            close = {"quantity": 4, "entry_window_minutes": 10, "lookback_bars": 2, "minimum_close_return_bps": 200,
                     "adverse_gap_bps": 500, "protection_loss_bps": 1000, "exit_minutes_after_open": 8,
                     "ipo_reduction_quantity": None, "listing_phases": []}
            breakout = {"quantity": 4, "lookback_bars": 2, "breakout_buffer_bps": 100, "protection_loss_bps": 1000,
                        "exit_minutes_before_close": 1, "listing_phases": ["listing_day"]}
            cases = (
                ("close_betting/v1", close, [("BUY", 4, 105), ("SELL", 4, 102)]),
                ("ipo_close/v1", {**close, "ipo_reduction_quantity": 2, "listing_phases": ["listing_day"]},
                 [("BUY", 4, 105), ("SELL", 2, 102), ("SELL", 2, 89)]),
                ("ipo_breakout/v1", breakout, [("BUY", 4, 105), ("SELL", 4, 106)]),
            )
            repository = ResearchRepository(root / request["database"])
            for family, policy, expected in cases:
                with self.subTest(family=family):
                    request.update(family=family, strategy={"capital_won": 1000, "policy": policy})
                    path = root / "request.json"
                    path.write_text(json.dumps(request), encoding="utf-8")
                    result = execute_process_request(load_research_process_request(path))
                    events = repository.load_execution_events(result["run_id"])
                    fills = [(event["side"], event["quantity"], event["price"]) for event in events if event["event_type"] == "FILL"]
                    self.assertEqual(expected, fills)
                    self.assertNotIn("RISK_FILL", {event["event_type"] for event in events})
            request["shadow_only"] = True
            request.update(family="close_betting/v1", strategy={"capital_won": 1000, "policy": close})
            path.write_text(json.dumps(request), encoding="utf-8")
            with patch("kiwoom_monitor.research_run.PaperExecutionEngine.process_decision", side_effect=AssertionError), \
                    patch("kiwoom_monitor.research_run.PaperExecutionEngine.process_bar", side_effect=AssertionError):
                result = execute_process_request(load_research_process_request(path))
            self.assertTrue(result["shadow_only"])
            self.assertFalse(result["order_authorized"])
            self.assertEqual((), repository.load_execution_events(result["run_id"]))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertGreater(manifest["candidate_count"], 0)

    def test_section12_panic_request_uses_each_variant_entry_cut_and_completed_close_exit(self) -> None:
        from datetime import datetime, timedelta

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            opening = datetime.fromisoformat("2026-09-10T09:00:00+09:00")
            at = lambda minute: (opening + timedelta(minutes=minute)).isoformat()
            rows = [{"kind": "top20_membership", "revision_id": "members", "available_at": at(0),
                     "payload": {"codes": ["123456"]}}]
            for minute, (open_price, close) in enumerate(((1000, 1000), (990, 990), (980, 980), (900, 900),
                                                            (905, 910), (915, 915), (920, 950), (940, 940))):
                rows.append({"kind": "minute_bar", "venue": "KRX", "subject": "123456:KRX", "revision_id": f"bar-{minute}",
                             "observation_key": at(minute), "available_at": at(minute + 1), "completeness": "complete",
                             "value_kind": "actual", "payload": {"code": "123456", "market": "KRX", "bar_start": at(minute),
                             "bar_end": at(minute + 1), "open": open_price, "close": close, "high": max(open_price, close),
                             "low": 800 if minute == 5 else min(open_price, close) - 5, "volume": 20,
                             "trade_value_million_won": 1, "window_closed": True, "capture_quality": "complete"}})
            for ordinal, row in enumerate(rows, 1):
                row.update(ordinal=ordinal, accepted_sequence=ordinal)
            encoded = "\n".join(json.dumps(row) for row in rows).encode()
            (root / "dataset/observations.jsonl").write_bytes(encoded)
            (root / "dataset/manifest.json").write_text(json.dumps({
                "dataset_id": "section12-panic-fixture", "revision_count": len(rows), "end": at(8),
                "observations_file": "observations.jsonl", "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
                "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
            }), encoding="utf-8")
            references = json.dumps({"version": "strategy-reference/v1", "listings": [], "calendar": [{
                "venue": "KRX", "trading_date": "2026-09-10", "available_at": at(-15), "revision_id": "krx-session",
                "evidence_ref": "fixture-calendar", "sessions": [{"session": "KRX_REGULAR", "phase": "CONTINUOUS",
                "support": "SUPPORTED", "schedule_version": "fixture/v1", "starts_at": at(0), "ends_at": at(380)}],
            }]}).encode()
            (root / "references.json").write_bytes(references)
            proof = {"symbol": "123456", "source": "reviewed-fixture", "assessment_id": "assessment", "revision": "first",
                     "effective_at": at(2), "available_at": at(3), "disposition": "supported_nonfundamental",
                     "evidence_refs": ["article-r1", "body-r1", "rule-r1"]}
            adverse = {**proof, "revision": "adverse", "available_at": "2026-09-10T09:04:30+09:00",
                       "disposition": "fundamental_adverse"}
            request = _request_document()
            request.update(mode="single_run", family="panic_rebound/v1", session_profile="krx-regular/v1",
                           strategy_references={"path": "references.json", "sha256": hashlib.sha256(references).hexdigest()})
            request["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request["execution"]["cost_model"].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
                                                       valid_from=at(-10), valid_to=at(380))
            request["evaluation"]["folds"][0].update(start=at(0), end=at(8))
            repository = ResearchRepository(root / request["database"])
            for variant, proofs, expected in (("P0", [proof, adverse], [("BUY", 905), ("SELL", 940)]),
                                               ("P1", [proof], [("BUY", 915), ("SELL", 940)]),
                                               ("P1", [proof, adverse], [])):
                with self.subTest(variant=variant, count=len(proofs)):
                    request["strategy"] = {"quantity": 1, "capital_won": 2000, "news_proofs": proofs,
                                           "policy": {"variant": variant, "lookback_minutes": 3, "decline_bps": 1000,
                                           "minimum_turnover_won": 3_000_000, "max_input_age_seconds": 10,
                                           "rebound_target_bps": 500, "max_hold_minutes": 5}}
                    path = root / "request.json"
                    path.write_text(json.dumps(request), encoding="utf-8")
                    result = execute_process_request(load_research_process_request(path))
                    events = repository.load_execution_events(result["run_id"])
                    self.assertEqual(expected, [(event["side"], event["price"]) for event in events if event["event_type"] == "FILL"])
                    self.assertNotIn("RISK_FILL", {event["event_type"] for event in events})

    def test_section12_micro_request_pins_receipts_and_persists_the_result(self) -> None:
        from kiwoom_monitor import research_process
        from tests.unit.test_micro_execution_evidence import _entry_market, _event, _policy, _scenario

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario = _scenario(_entry_market() + [
                _event("ORDER_ARRIVED", "arrive", 4, order_id="entry"),
                _event("FILL", "filled", 5, order_id="entry", price_won=104, quantity=2),
                _event("CANCELLED", "cancelled", 6, order_id="entry", filled_quantity=2),
            ])
            evidence = root / "micro.json"
            encoded = json.dumps(scenario).encode()
            evidence.write_bytes(encoded)
            digest = hashlib.sha256(encoded).hexdigest()
            request_path = root / "request.json"
            request_path.write_text(json.dumps({
                "mode": "micro_experiment", "dataset": evidence.name, "dataset_sha256": digest,
                "policy": _policy(), "database": "research.sqlite3", "runs_dir": "runs",
            }), encoding="utf-8")
            result_path = root / "result.json"
            with patch("sys.stdout"):
                self.assertEqual(0, research_process._run_research_process([
                    "--request", str(request_path), "--result", str(result_path),
                ]))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(("OPEN", 2, 2), (result["result"]["status"], result["result"]["position_quantity"],
                                             result["result"]["protected_quantity"]))
            self.assertEqual(digest, manifest["spec"]["dataset_sha256"])
            self.assertFalse(manifest["order_authorized"])
            parsed = load_research_process_request(request_path)
            self.assertEqual(result["run_id"], execute_process_request(parsed)["run_id"])
            evidence.write_bytes(encoded + b" ")
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                execute_process_request(parsed)

    def test_section12_daily_decline_request_records_only_explicit_late_session_fills(self) -> None:
        from dataclasses import asdict
        from tests.unit.test_daily_decline import SPLIT, quote, quote_receipt, synthetic_scenario

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = quote(1000)
            exit_quote = quote(740, session=2)
            rows = []
            for raw, label, price, session in ((entry, "entry", 1000, 1), (exit_quote, "exit", 740, 2)):
                rows.extend((quote_receipt(raw),
                             {"kind": "ACCEPT", "available_at": raw.available_at, "quote_ref": raw.evidence_id, "order_ref": label},
                             {"kind": "FILL", "available_at": raw.available_at, "order_ref": label, "fill_id": f"{label}-1",
                              "quantity": 2, "price_won": price, "fee_won": 0, "session_index": session},
                             {"kind": "TERMINAL", "available_at": raw.available_at, "order_ref": label}))
            evidence = root / "daily.json"
            encoded = json.dumps(synthetic_scenario(*rows)).encode()
            evidence.write_bytes(encoded)
            request_path = root / "request.json"
            request_path.write_text(json.dumps({
                "mode": "daily_decline_experiment", "dataset": evidence.name,
                "dataset_sha256": hashlib.sha256(encoded).hexdigest(), "policy": asdict(SPLIT),
                "database": "research.sqlite3", "runs_dir": "runs",
            }), encoding="utf-8")
            result = execute_process_request(load_research_process_request(request_path))
            trace = result["result"]
            self.assertEqual("QUALIFIED", trace["setup"]["status"])
            self.assertEqual(0, trace["events"][0]["owned_quantity"])
            self.assertEqual(2, trace["events"][2]["owned_quantity"])
            self.assertEqual((0, 0, 2, -520), (trace["summary"]["owned_quantity"], trace["summary"]["reserved_won"],
                                               trace["summary"]["fill_count"], trace["summary"]["realized_pnl_won"]))
            self.assertFalse(result["order_authorized"])
            self.assertEqual("synthetic_evidence", result["section12"]["execution_scope"])

    def test_section12_dd_native_setup_request_consumes_pinned_frozen_daily_rows_only(self) -> None:
        from dataclasses import asdict
        from datetime import datetime, time, timedelta
        from kiwoom_monitor import research_process
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.infrastructure.research_data_source import (
            FINAL_INPUT_VERSION, export_daily_dataset, write_frozen_research_bundle,
        )
        from tests.unit.test_daily_decline import BARS, CUT, HISTORY, SIGNAL_DAY, SINGLE, synthetic_scenario
        from tests.unit.test_research_data_source import _StoreClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "daily-bundle"
            now = CUT
            store = SQLiteQueryStore(root / "native.sqlite3", now_provider=lambda: now)
            store.initialize()
            ingest = MarketDataIngestor(store, now_provider=lambda: now)

            def write(day, row, mode="1"):
                ingest.ingest("ka10081", {"stk_cd": "005930", "upd_stkpc_tp": mode}, {
                    "stk_dt_pole_chart_qry": [{"dt": day.strftime("%Y%m%d"), "open_pric": str(row["open"]),
                                                "high_pric": str(row["high"]), "low_pric": str(row["low"]),
                                                "cur_prc": str(row["close"])}],
                })

            days = (*HISTORY, SIGNAL_DAY)
            for day, row in zip(days, BARS, strict=True):
                now = datetime.combine(day, time(20, 1), KST)
                write(day, row["payload"])
            now = CUT + timedelta(minutes=1)
            write(SIGNAL_DAY, BARS[-1]["payload"], mode="0")
            for day in days:
                start = datetime.combine(day, time(), KST)
                export_daily_dataset(_StoreClient(store), start, start + timedelta(days=1), ("daily_bar",),
                                     "005930:KRX", dataset / day.isoformat(), session_profile="krx-regular/v1",
                                     theme_history={"snapshots": []})
            store.close()
            write_frozen_research_bundle(dataset, tuple(day.isoformat() for day in days))

            fixture = synthetic_scenario()
            manifest_sha = hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest()
            descriptor = {"version": "dd-native-setup-evidence/v1",
                          "frozen_dataset": {"path": dataset.name, "manifest_sha256": manifest_sha},
                          "strategy_references": {"version": "strategy-reference/v1", "calendar": fixture["calendar"],
                                                  "listings": []},
                          **{name: fixture[name] for name in ("symbol", "venue", "signal_day", "cut", "basis", "benchmark")}}
            evidence = root / "dd-setup.json"
            request_path = root / "request.json"

            def request(changes):
                encoded = json.dumps({**descriptor, **changes}).encode()
                evidence.write_bytes(encoded)
                request_path.write_text(json.dumps({
                    "mode": "daily_decline_experiment", "dataset": evidence.name,
                    "dataset_sha256": hashlib.sha256(encoded).hexdigest(), "policy": asdict(SINGLE),
                    "database": "research.sqlite3", "runs_dir": "runs",
                }), encoding="utf-8")
                return load_research_process_request(request_path)

            prepared = request({})
            result = execute_process_request(prepared)
            trace = result["result"]
            self.assertEqual("dd-native-setup-result/v1", trace["version"])
            self.assertEqual(("QUALIFIED", 2_500, 1_618), (trace["setup"]["status"], trace["setup"]["drawdown_bps"],
                                                         trace["setup"]["relative_decline_bps"]))
            self.assertNotIn("events", trace)
            self.assertNotIn("summary", trace)
            self.assertFalse(result["order_authorized"])
            self.assertEqual("frozen_native_daily_observations", result["section12"]["source_kind"])
            self.assertEqual("native_setup_assessment", result["section12"]["execution_scope"])
            self.assertEqual(manifest_sha, result["section12"]["frozen_manifest_sha256"])
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(prepared.dataset_sha256, manifest["spec"]["dataset_sha256"])
            self.assertEqual(manifest_sha, manifest["spec"]["frozen_manifest_sha256"])
            self.assertEqual(result["run_id"], execute_process_request(prepared)["run_id"])

            cli_result = root / "cli-result.json"
            cli_args = ["--request", str(request_path), "--result", str(cli_result)]
            with patch("sys.stdout"):
                self.assertEqual(0, research_process._run_research_process(cli_args))
            self.assertEqual(result["run_id"], json.loads(cli_result.read_text(encoding="utf-8"))["run_id"])
            frozen_manifest = dataset / "manifest.json"
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                research_process._run_research_process(["--request", str(request_path), "--result", str(frozen_manifest)])
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                research_process._run_research_process([*cli_args, "--cancel", str(frozen_manifest)])
            self.assertEqual(manifest_sha, hashlib.sha256(frozen_manifest.read_bytes()).hexdigest())

            later = execute_process_request(request({"cut": now.isoformat()}))
            self.assertEqual("daily_window_unavailable", later["result"]["setup"]["reason"])
            self.assertNotEqual(result["run_id"], later["run_id"])
            with self.assertRaisesRegex(ValueError, "frozen manifest SHA256"):
                execute_process_request(request({"frozen_dataset": {"path": dataset.name, "manifest_sha256": "0" * 64}}))

            child_path = dataset / SIGNAL_DAY.isoformat() / "manifest.json"
            child_manifest = json.loads(child_path.read_text(encoding="utf-8"))
            child_manifest.update(runtime_input_version=FINAL_INPUT_VERSION, observations_file="missing.jsonl")
            child_path.write_text(json.dumps(child_manifest), encoding="utf-8")
            with patch.object(research_process, "load_research_input") as load:
                with self.assertRaisesRegex(ValueError, "final holdout input"):
                    execute_process_request(request({}))
                load.assert_not_called()

    def test_section12_breakout_and_theme_studies_share_entry_but_close_on_their_own_evidence(self) -> None:
        from datetime import datetime, timedelta
        from kiwoom_monitor import research_process
        from kiwoom_monitor.application.market_session_schedule import research_session_profile_document

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            first = datetime.fromisoformat("2026-09-11T09:00:00+09:00")
            at = lambda minute: (first + timedelta(minutes=minute)).isoformat()
            rows = [{"kind": "top20_membership", "revision_id": "members", "available_at": at(0),
                     "payload": {"codes": ["123456"]}}]
            prices = [(100, 100)] * 10 + [(100, 110), (105, 107), (106, 99), (98, 98)]
            for minute, (opening, close) in enumerate(prices):
                rows.append({"kind": "minute_bar", "venue": "KRX", "subject": "123456:KRX", "revision_id": f"bar-{minute}",
                             "observation_key": at(minute), "available_at": at(minute + 1), "completeness": "complete",
                             "value_kind": "actual", "payload": {"code": "123456", "market": "KRX", "bar_start": at(minute),
                             "bar_end": at(minute + 1), "open": opening, "close": close, "high": max(opening, close),
                             "low": min(opening, close), "volume": 20, "trade_value_million_won": 1,
                             "window_closed": True, "capture_quality": "complete", "finalization_source": "fixture-native-timer"}})
            for ordinal, row in enumerate(rows, 1):
                row.update(ordinal=ordinal, accepted_sequence=ordinal)

            def write_rows(**source_contract):
                encoded = "\n".join(json.dumps(row) for row in rows).encode()
                (root / "dataset/observations.jsonl").write_bytes(encoded)
                (root / "dataset/manifest.json").write_text(json.dumps({
                    "dataset_id": "section12-breakout-fixture", "revision_count": len(rows), "end": at(14),
                    "observations_file": "observations.jsonl", "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
                    "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
                    **source_contract,
                }), encoding="utf-8")

            write_rows()
            references = json.dumps({"version": "strategy-reference/v1", "listings": [], "calendar": [{
                "venue": "KRX", "trading_date": "2026-09-11", "available_at": at(-15), "revision_id": "session",
                "evidence_ref": "fixture-calendar", "sessions": [{"session": "KRX_REGULAR", "phase": "CONTINUOUS",
                "support": "SUPPORTED", "schedule_version": "fixture/v1", "starts_at": at(0), "ends_at": at(380)}],
            }]}).encode()
            (root / "references.json").write_bytes(references)
            request = _request_document()
            request.update(mode="single_run", session_profile="krx-regular/v1",
                           strategy_references={"path": "references.json", "sha256": hashlib.sha256(references).hexdigest()})
            request["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request["execution"]["cost_model"].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
                                                       valid_from=at(-10), valid_to=at(380))
            request["evaluation"]["folds"][0].update(start=at(0), end=at(14))
            base = {"variant": "B0", "quantity": 1, "exit_at_kst": "15:18", "theme_id": None,
                    "theme_role": None, "exit_on_dependency_loss": False}
            first_role = {"symbol": "123456", "theme_id": "theme", "role": "dependent_follower", "leader_symbol": "654321",
                          "effective_at": at(1), "available_at": at(1), "source": "fixture", "revision": "known",
                          "evidence_refs": ["membership-r1", "turnover-r1"], "basis": "point_in_time", "dependency_valid": True}
            loss = {**first_role, "effective_at": at(12), "available_at": at(12), "revision": "lost", "dependency_valid": False}
            theme = {**base, "variant": "TH", "theme_id": "theme", "theme_role": "dependent_follower", "exit_on_dependency_loss": True}
            path = root / "request.json"
            repository = ResearchRepository(root / request["database"])

            def run(family, policy, **data):
                request.update(family=family, strategy={"capital_won": 1000, "policy": policy, **data})
                path.write_text(json.dumps(request), encoding="utf-8")
                result = execute_process_request(load_research_process_request(path))
                return repository.load_execution_events(result["run_id"])

            for events, expected in (
                (run("breakout_study/v1", base), [("BUY", 105), ("SELL", 98)]),
                (run("theme_leader_study/v1", theme, theme_proofs=[first_role, loss]), [("BUY", 105), ("SELL", 106)]),
            ):
                self.assertEqual(expected, [(event["side"], event["price"]) for event in events if event["event_type"] == "FILL"])
            rows[12]["payload"].update(open=100, low=100)
            write_rows()
            events = run("breakout_study/v1", base)
            self.assertFalse(any(event["event_type"] == "FILL" for event in events))
            self.assertIn("next_open_has_no_positive_initial_risk_distance", {event["reason"] for event in events})

            rows[12]["payload"].update(open=105, low=105)
            write_rows()
            b1 = {"minimum_price_progress_bps": 100, "minimum_symbol_relative_turnover_bps": 10_000}
            b2 = {**b1, "market_scope": "KRX", "minimum_market_relative_turnover_bps": 15_000}
            market = {"kind": "market_relative_turnover", "scope": "KRX", "effective_at": at(11), "available_at": at(11),
                      "source": "fixture", "revision": "turnover", "basis": "point_in_time", "evidence_refs": ["market-r1"],
                      "value": {"observed_turnover_won": 2_000_000, "baseline_median_turnover_won": 1_000_000}}
            request.update(mode="breakout_filter_comparison", breakout_variants=[
                {"capital_won": 1000, "policy": {**base, "variant": "B1", "entry_filters": b1}},
                {"capital_won": 1000, "policy": {**base, "variant": "B2", "entry_filters": b2}, "entry_proofs": [market]},
                {"capital_won": 1000, "policy": {**base, "variant": "B3", "entry_filters": {
                    **b2, "minimum_daily_headroom_bps": None, "require_catalyst": True}}, "entry_proofs": [market]},
            ])
            path.write_text(json.dumps(request), encoding="utf-8")
            comparison = execute_process_request(load_research_process_request(path))
            self.assertEqual([("B0", 1), ("B1", 1), ("B2", 1), ("B3", 0)],
                             [(run["variant"], run["candidate_count"]) for run in comparison["runs"]])
            folds = [pair["comparison"]["fold_comparisons"][0] for pair in comparison["pairs"]]
            self.assertEqual([1, 1, 0], [fold["shared_candidate_count"] for fold in folds])
            self.assertEqual(7, folds[2]["avoided_loss_won"])
            self.assertFalse(comparison["order_authorized"])
            comparison_manifest = Path(comparison["pairs"][-1]["manifest"])
            original_bytes = comparison_manifest.read_bytes()
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                research_process._run_research_process(["--request", str(path), "--result", str(comparison_manifest)])
            self.assertEqual(original_bytes, comparison_manifest.read_bytes())
            request["breakout_variants"][1]["policy"]["entry_filters"]["minimum_price_progress_bps"] += 1
            path.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "previously selected filter"):
                load_research_process_request(path)
            request["breakout_variants"][1]["policy"]["entry_filters"]["minimum_price_progress_bps"] -= 1
            request["evaluation"]["folds"][0]["end"] = at(13)
            path.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside its development fold"):
                execute_process_request(load_research_process_request(path))
            request["evaluation"]["folds"][0]["end"] = at(14)
            del request["strategy_references"]
            path.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "require pinned references"):
                execute_process_request(load_research_process_request(path))
            rows[0]["kind"] = "historical_candidate_population"
            for row in rows[1:]:
                row["payload"]["finalization_source"] = "daishin_completed_history_job"
            write_rows(runtime_input_version="historical_reconstruction_strategy/v1", not_contemporaneous_top20=True,
                       research_session_profile=research_session_profile_document("krx-regular/v1"))
            reconstructed = execute_process_request(load_research_process_request(path))
            self.assertEqual([1, 1, 1, 0], [run["candidate_count"] for run in reconstructed["runs"]])
            b1_manifest = json.loads(Path(reconstructed["runs"][1]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual("reconstructed_completed_bar_end_proxy", b1_manifest["spec"]["observation_clock_basis"])

    def test_section12_account_risk_request_compares_equal_capital_reservations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [{"sequence": index, "kind": "PROPOSE", "order_id": order_id, "plan_id": plan,
                       "symbol": "005930", "side": "BUY", "tranche": tranche, "quantity": quantity,
                       "limit_price_won": 10, "fee_budget_won": 0}
                      for index, (order_id, plan, tranche, quantity) in enumerate((
                          ("a0", "a", 0, 6), ("a1", "a", 1, 4), ("b0", "b", 0, 4)), 1)]
            encoded = json.dumps({"version": "account-risk-scenario/v1", "initial_cash_won": 100,
                                  "plan_budget_won": 100, "events": events}).encode()
            (root / "risk.json").write_bytes(encoded)
            request = {"mode": "account_risk_experiment", "dataset": "risk.json",
                       "dataset_sha256": hashlib.sha256(encoded).hexdigest(), "database": "research.sqlite3", "runs_dir": "runs"}
            run_ids = []
            for tranches, accepted in (([10000], ["a0", "b0"]), ([6000, 4000], ["a0", "a1"])):
                request["policy"] = {"version": "account-risk-allocation/v1", "tranche_bps": tranches}
                path = root / "request.json"
                path.write_text(json.dumps(request), encoding="utf-8")
                result = execute_process_request(load_research_process_request(path))
                trace = result["result"]
                self.assertEqual(accepted, [order["order_id"] for order in trace["orders"] if order["reserved_cash_won"]])
                self.assertEqual((100, 100, 0, []), (trace["plan_budget_won"], trace["reserved_cash_won"],
                                                    trace["available_cash_won"], trace["positions"]))
                run_ids.append(result["run_id"])
            self.assertNotEqual(*run_ids)

    def test_account_risk_marked_equity_reaches_the_immutable_result(self) -> None:
        from tests.unit.test_account_risk_research import _buy, _fill, _mark, _policy, _timed

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario = _timed(_mark("opening", 12, 0), _buy("entry", "a", 2), _fill("entry", "f1", 2, 10),
                              _mark("later", 8, 3))
            encoded = json.dumps(scenario).encode()
            (root / "risk.json").write_bytes(encoded)
            request_path = root / "request.json"
            request_path.write_text(json.dumps({
                "mode": "account_risk_experiment", "dataset": "risk.json", "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
                "policy": _policy(10_000), "database": "research.sqlite3", "runs_dir": "runs",
            }), encoding="utf-8")
            result = execute_process_request(load_research_process_request(request_path))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            valuation = manifest["result"]["valuation"]
            self.assertEqual(("COMPLETE", 96, 8, 769), (valuation["status"], valuation["equity_won"],
                                                       valuation["maximum_drawdown_won"], valuation["maximum_drawdown_bps"]))
            self.assertFalse(manifest["order_authorized"])

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
