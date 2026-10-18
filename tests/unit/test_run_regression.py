from __future__ import annotations

import json
import copy
import os
import sys
import subprocess
import shutil
import time
import tempfile
import unittest
import yaml
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import check_required_ci_results, run_regression, select_ci_groups


class ProcessProbe:
    """Keep a handle to a specific process, independent of runner bookkeeping."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateProcess.restype = wintypes.BOOL
        self.api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        self.api.GetProcessTimes.restype = wintypes.BOOL
        self.handle = self.api.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.api.GetProcessTimes(self.handle, *(ctypes.byref(value) for value in times)):
            self.api.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self.creation_time = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def exited(self, milliseconds: int = 0) -> bool:
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result not in (0, 258):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return result == 0

    def close(self) -> None:
        if not self.exited():
            self.api.TerminateProcess(self.handle, 99)  # Test failure cleanup, using the retained handle.
            self.exited(5000)
        self.api.CloseHandle(self.handle)


class RunRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        task_temp_root = run_regression.ROOT / "tmp"
        task_temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=task_temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.fixture_count = 0

    def _run_fixture(
        self, mode: str, *, timeout: int = 5, module: str | None = None,
        modules: list[str] | None = None,
    ):
        self.fixture_count += 1
        directory = self.directory / str(self.fixture_count)
        directory.mkdir()
        module_names = modules or [module or "tests.unit.regression_worker_fixtures"]
        with patch.dict(os.environ, {"REGRESSION_FIXTURE_MODE": mode,
                                   "REGRESSION_FIXTURE_DIR": str(directory)}):
            return run_regression._run_process(
                modules=module_names,
                result_path=directory / "result.json",
                log_path=directory / "worker.log",
                timeout_seconds=timeout,
            )

    @contextmanager
    def _windows_shard_fixture(self):
        modules = ["tests.unit.test_kiwoom_rest_client", "tests.unit.test_central_rest_broker",
                   "tests.unit.test_ranking_service", "tests.unit.test_ranking_schedule",
                   "tests.unit.test_ranking_execution", "tests.unit.test_realtime_subscription"]
        batches = [{"name": "core", "modules": modules[:2], "isolate_modules": False},
                   {"name": "isolated", "modules": modules[2:5], "isolate_modules": True},
                   {"name": "tail", "modules": modules[5:], "isolate_modules": False}]
        source = {"files": 6, "lf_sha256": "f" * 64, "raw_sha256": "f" * 64}
        dependencies = dict(line.split("==", 1) for line in
            (run_regression.ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#"))
        environment = {"platform": "Windows-fixture", "python": "3.13 fixture",
                       "timezone": ["UTC", "UTC"], "locked_dependencies": dependencies}
        git = {"commit": "test-commit", "working_tree_dirty": False}
        membership = {name: set(modules) if name == "ci-required-windows" else set()
                      for name in run_regression.CI_WINDOWS_PROFILES}
        with ExitStack() as stack:
            for name, value in (("_load_manifest", {}), ("_load_ci_groups", {}),
                                ("_planned_batches", batches), ("_ci_membership", membership),
                                ("_code_tree_identity", source), ("_git_metadata", git),
                                ("_locked_environment", environment), ("_check_test_catalog", 0),
                                ("_sha256", "a" * 64)):
                stack.enter_context(patch.object(run_regression, name, return_value=value))
            workers = run_regression._profile_workers(batches)
            reports = []
            for index in (0, 1):
                shard = run_regression._all_local_shard_plan(workers, index=index, count=2)
                selected = workers[shard["start"]:shard["end"]]
                results = []
                for number, (name, current_modules) in enumerate(selected, start=shard["start"] + 1):
                    ids = [module + ".Fixture.test_ok" for module in current_modules]
                    results.append({"name": name, "modules": list(current_modules), "status": "passed",
                        "exit_code": 0, "worker_exit_confirmed": True, "process_tree_exit_confirmed": True,
                        "source": {"verified": True}, "timeout": False, "interrupted": False,
                        "leaked_descendants": False, "zero_test_modules": [],
                        "failures": [], "errors": [], "skipped": [], "expected_failures": [],
                        "unexpected_successes": [], "test_count": len(ids), "planned_test_count": len(ids),
                        "planned_test_ids": ids, "executed_test_ids": ids,
                        "module_test_counts": [{"module": module, "test_count": 1} for module in current_modules],
                        "log": f"old/worker-{number:03}.log", "result": f"old/worker-{number:03}.json"})
                reports.append({"schema_version": 1, "profile": "all-local-shard", "status": "passed_shard",
                    "shard": shard, "git": git, "environment": environment,
                    "missing_test_files": [], "unrun_batches": [], "interrupted": False,
                    "source_modified_during_run": False, "code_tree_start": source, "code_tree_end": source,
                    "profile_manifest_sha256": "a" * 64, "ci_catalog_sha256": "a" * 64,
                    "runner_sha256": "a" * 64,
                    "test_file_sha256": {module: "a" * 64 for _, group in selected for module in group},
                    "started_at": f"2026-10-18T01:00:0{index}+00:00",
                    "finished_at": f"2026-10-18T01:00:1{index}+00:00",
                    "planned_batches": [{"name": name, "modules": list(group)} for name, group in selected],
                    "batch_results": results})
            yield reports, workers, batches

    def test_windows_shards_merge_only_complete_current_disjoint_evidence(self) -> None:
        with self._windows_shard_fixture() as (reports, workers, _):
            merged = check_required_ci_results.merge_windows_shards(list(reversed(reports)), "test-commit")
            self.assertEqual("passed", merged["status"])
            self.assertEqual("all-local", merged["profile"])
            self.assertNotIn("shard", merged)
            self.assertEqual([name for name, _ in workers], [w["name"] for w in merged["batch_results"]])
            self.assertEqual("passed", run_regression.summarize_run(merged)["status"])
            with self.assertRaises(ValueError):
                check_required_ci_results.validate_windows(reports[0], "test-commit")
            cases = [
                ("missing", lambda rows: rows.pop()),
                ("duplicate-index", lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0]))),
                ("bool-index", lambda rows: rows[0]["shard"].__setitem__("index", False)),
                ("range", lambda rows: rows[0]["shard"].__setitem__("end", 3)),
                ("full-plan", lambda rows: rows[0]["shard"].__setitem__("plan_sha256", "bad")),
                ("commit", lambda rows: rows[0]["git"].__setitem__("commit", "old")),
                ("dirty", lambda rows: rows[0]["git"].__setitem__("working_tree_dirty", True)),
                ("stale-source", lambda rows: rows[0].__setitem__("code_tree_end", {"lf_sha256": "e" * 64})),
                ("environment", lambda rows: rows[0].__setitem__("environment", {"platform": "different"})),
                ("manifest", lambda rows: rows[0].__setitem__("profile_manifest_sha256", "bad")),
                ("runner", lambda rows: rows[0].__setitem__("runner_sha256", "bad")),
                ("test-hash", lambda rows: rows[0].__setitem__("test_file_sha256", {})),
                ("global-log-index", lambda rows: rows[1]["batch_results"][0].__setitem__("log", "worker-001.log")),
                ("unrun", lambda rows: rows[0].__setitem__("unrun_batches", ["missing"])),
                ("plan-order", lambda rows: rows[0]["planned_batches"].reverse()),
                ("worker-order", lambda rows: rows[0]["batch_results"].reverse()),
                ("IDs", lambda rows: rows[0]["batch_results"][0].__setitem__("executed_test_ids", [])),
                ("discovery", lambda rows: rows[0]["batch_results"][0]["module_test_counts"].pop()),
            ]
            for field, value in (("status", "failed"), ("skipped", ["skipped"]),
                                 ("process_tree_exit_confirmed", False), ("worker_exit_confirmed", False),
                                 ("timeout", True), ("interrupted", True), ("leaked_descendants", True)):
                cases.append((field, lambda rows, key=field, item=value:
                              rows[0]["batch_results"][0].__setitem__(key, item)))
            def duplicate_id(rows):
                worker = rows[1]["batch_results"][0]
                worker["planned_test_ids"] = worker["executed_test_ids"] = [
                    rows[0]["batch_results"][0]["executed_test_ids"][0]]
            cases.append(("duplicate-global-ID", duplicate_id))
            def wrong_dependencies(rows):
                for row in rows:
                    row["environment"]["locked_dependencies"] = {"invented-package": "1"}
            cases.append(("same-wrong-lock-map", wrong_dependencies))
            for name, mutate in cases:
                with self.subTest(mutation=name):
                    changed = copy.deepcopy(reports)
                    mutate(changed)
                    with self.assertRaises(ValueError):
                        check_required_ci_results.merge_windows_shards(changed, "test-commit")
            with patch.object(run_regression, "_git_metadata", return_value={"commit": "different"}):
                with self.assertRaises(ValueError):
                    check_required_ci_results.merge_windows_shards(reports, "test-commit")

    def test_windows_shard_merge_cli_refuses_partial_stale_and_existing_output(self) -> None:
        with self._windows_shard_fixture() as (reports, _, _):
            paths = [self.directory / f"shard-{index}.json" for index in (0, 1)]
            for path, report in zip(paths, reports):
                path.write_text(json.dumps(report), encoding="utf-8")
            output = self.directory / "merged/run.json"
            argv = ["check_required_ci_results", "--windows-shard", str(paths[0]),
                    "--windows-shard", str(paths[1]), "--merge-windows", str(output)]
            with patch.dict(os.environ, {"GITHUB_SHA": "test-commit"}), \
                    patch.object(sys, "argv", argv), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(0, check_required_ci_results.main())
                saved = output.read_bytes()
                merged = json.loads(saved)
                self.assertEqual(str(paths[0].parent / "worker-001.log"), merged["batch_results"][0]["log"])
                self.assertEqual(1, check_required_ci_results.main())
                self.assertEqual(saved, output.read_bytes())
                output.unlink()
                reports[1]["code_tree_end"] = {"lf_sha256": "bad"}
                paths[1].write_text(json.dumps(reports[1]), encoding="utf-8")
                self.assertEqual(1, check_required_ci_results.main())
                self.assertFalse(output.exists())
            with patch.object(sys, "argv", argv[0:3] + argv[-2:]), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    check_required_ci_results.main()

    def test_all_local_shard_plan_and_cli_preserve_the_unsharded_contract(self) -> None:
        workers = [("core", ["a", "b"]), ("one", ["c"]), ("two", ["d"]),
                   ("three", ["e"]), ("four", ["f"])]
        left = run_regression._all_local_shard_plan(workers, index=0, count=2)
        right = run_regression._all_local_shard_plan(workers, index=1, count=2)
        self.assertEqual((0, 2, 2, 5), (left["start"], left["end"], right["start"], right["end"]))
        self.assertEqual(left["plan_sha256"], right["plan_sha256"])
        for index, count in ((True, 2), (-1, 2), (2, 2), (0, True), (0, 1), (0, 3)):
            with self.subTest(index=index, count=count), self.assertRaises(ValueError):
                run_regression._all_local_shard_plan(workers, index=index, count=count)
        with self.assertRaises(ValueError):
            run_regression._all_local_shard_plan([], index=0, count=2)
        pair = ["--shard-index", "1", "--shard-count", "2"]
        with patch.object(run_regression, "_execute_profile", return_value=0) as execute:
            self.assertEqual(0, run_regression.main(["--profile", "all-local", *pair]))
            self.assertEqual(1, execute.call_args.kwargs["shard_index"])
            self.assertEqual(2, execute.call_args.kwargs["shard_count"])
            execute.reset_mock()
            self.assertEqual(0, run_regression.main(["--profile", "core"]))
            self.assertNotIn("shard_index", execute.call_args.kwargs)
        for args in (["--profile", "all-local", "--shard-index", "0"],
                     ["--profile", "core", *pair], ["--list", *pair],
                     ["--selection", "fake.json", *pair], ["--summarize", "fake.json", *pair],
                     ["--check-test-catalog", *pair], ["--check-new-test-modules", *pair],
                     ["--_worker", *pair], ["--profile", "all-local", "--base-ref", "HEAD", *pair]):
            with self.subTest(args=args), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                run_regression.main(args)

    def test_shard_execution_keeps_global_workers_and_existing_lifecycle_gates(self) -> None:
        with self._windows_shard_fixture() as (reports, _, _):
            for mode in ("pass", "failure", "timeout", "interrupt", "unsafe", "source-change"):
                output = self.directory / mode
                observed = []
                def run(**kwargs):
                    observed.append(kwargs["result_path"].name)
                    worker = copy.deepcopy(reports[1]["batch_results"][len(observed) - 1])
                    if len(observed) == 1:
                        if mode == "failure": worker["status"] = "failed"
                        if mode == "timeout": worker.update(status="incomplete", timeout=True)
                        if mode == "interrupt": worker.update(status="incomplete", interrupted=True)
                        if mode == "unsafe": worker["process_tree_exit_confirmed"] = False
                    return worker
                before = reports[0]["code_tree_start"]
                after = {**before, "lf_sha256": "e" * 64} if mode == "source-change" else before
                with self.subTest(mode=mode), patch.object(run_regression, "_run_process", side_effect=run), \
                        patch.object(run_regression, "_code_tree_identity", side_effect=(before, after)), \
                        redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    code = run_regression._execute_profile("all-local", 5, output, shard_index=1, shard_count=2)
                saved = json.loads((output / "run.json").read_text(encoding="utf-8"))
                stopped = mode in {"timeout", "interrupt", "unsafe"}
                self.assertEqual(["worker-003.json"] if stopped else
                                 ["worker-003.json", "worker-004.json", "worker-005.json"], observed)
                self.assertEqual(2 if stopped else 0, len(saved["unrun_batches"]))
                self.assertEqual("all-local-shard", saved["profile"])
                self.assertEqual(reports[1]["planned_batches"], saved["planned_batches"])
                self.assertEqual(reports[1]["test_file_sha256"], saved["test_file_sha256"])
                self.assertEqual("passed_shard" if mode == "pass" else
                                 "failed" if mode == "failure" else "incomplete", saved["status"])
                self.assertEqual(0 if mode == "pass" else 130 if mode == "interrupt" else 1, code)
                self.assertNotEqual("passed", run_regression.summarize_run(saved)["status"])

    def test_profile_plan_runs_registered_modules_once_and_keeps_lifecycle_coverage(self) -> None:
        manifest = run_regression._load_manifest()
        core = run_regression._planned_batches(manifest, "core")
        modules = [module for batch in core for module in batch["modules"]]

        self.assertEqual(["desktop-and-server", "journal-and-news"],
                         [batch["name"] for batch in core])
        self.assertEqual([module for batch in manifest["core_batches"] for module in batch["modules"]], modules)
        self.assertEqual(len(modules), len(set(modules)))
        self.assertEqual(1, modules.count("tests.unit.test_theme_color_repository"))
        for module in ("tests.unit.test_dataset_batch_postgres",
                       "tests.unit.test_postgres_scope_locks",
                       "tests.unit.test_diagnostic_replay_sampling"):
            self.assertEqual(1, modules.count(module))
        pc_modules = [module for batch in run_regression._planned_batches(
            manifest, "pc-lifecycle",
        ) for module in batch["modules"]]
        self.assertIn("tests.unit.test_app_controller", pc_modules)
        self.assertIn("tests.unit.test_main_window", pc_modules)
        self.assertIn("tests.unit.test_market_cache_writer", pc_modules)
        self.assertIn("tests.unit.test_realtime_gap_quality", pc_modules)
        runner_modules = [module for batch in run_regression._planned_batches(
            manifest, "runner",
        ) for module in batch["modules"]]
        self.assertEqual(["tests.unit.test_run_regression", "tests.unit.test_architecture_guardrails", "tests.unit.test_test_change_review",
                          "tests.unit.test_selective_test_tools",
                          "tests.unit.test_run_postgres_access_integration"], runner_modules)

        all_local = run_regression._planned_batches(manifest, "all-local")
        all_modules = [module for batch in all_local for module in batch["modules"]]
        self.assertEqual(len(all_modules), len(set(all_modules)))
        self.assertEqual(1, all_modules.count("tests.unit.test_market_cache_writer"))
        self.assertEqual(1, all_modules.count("tests.unit.test_realtime_gap_quality"))
        self.assertIn("tests.unit.test_top20_program_shutdown", all_modules)
        self.assertIn("tests.unit.test_top20_execution_boundaries", all_modules)

    def test_list_mode_does_not_require_a_profile(self) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(0, run_regression.main(["--list"]))

    def test_invalid_manifest_module_is_rejected(self) -> None:
        self.assertFalse(run_regression._valid_module("tests.unit..bad"))

    def test_empty_named_profile_is_rejected_before_any_worker_starts(self) -> None:
        manifest = run_regression._load_manifest()
        manifest["profiles"]["empty-for-test"] = []
        path = self.directory / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(run_regression, "MANIFEST", path):
            with self.assertRaisesRegex(ValueError, "Invalid named regression profile"):
                run_regression._load_manifest()

        output = self.directory / "empty-result"
        with patch.object(run_regression, "_load_manifest", return_value=manifest):
            with self.assertRaisesRegex(ValueError, "no runnable tests"):
                run_regression._execute_profile("empty-for-test", 5, output)
        self.assertFalse(output.exists())

    def test_ci_routes_preserve_core_and_cover_each_current_test_file(self) -> None:
        manifest = run_regression._load_manifest()
        groups = run_regression._load_ci_groups()
        unit = run_regression._test_modules_under("unit")
        integration = run_regression._test_modules_under("integration")
        self.assertEqual([], run_regression._catalog_errors(manifest, groups, unit, integration))
        membership = run_regression._ci_membership(manifest, groups)
        self.assertEqual({
            "tests.integration.test_recorded_account_postgres",
            "tests.integration.test_replay_activity_operator",
        }, membership["nas-operator-owned"])
        self.assertFalse(membership["nas-operator-owned"] & set().union(*(
            members for group, members in membership.items() if group != "nas-operator-owned"
        )))
        self.assertTrue(all(membership[name] for name in run_regression.CI_WINDOWS_PROFILES))
        required = run_regression._planned_batches(manifest, "ci-required-windows")
        self.assertEqual(
            [batch["modules"] for batch in run_regression._planned_batches(manifest, "core")],
            [batch["modules"] for batch in required[:2]],
        )
        all_local = run_regression._planned_batches(manifest, "all-local")
        combined = set().union(*(membership[name] for name in run_regression.CI_WINDOWS_PROFILES))
        self.assertEqual({module for batch in all_local for module in batch["modules"]}, combined)

    def test_catalog_rejects_missing_old_registration_new_integration_and_removed_files(self) -> None:
        manifest = run_regression._load_manifest()
        groups = run_regression._load_ci_groups()
        unit = run_regression._test_modules_under("unit")
        integration = run_regression._test_modules_under("integration")
        cases = (
            (unit | {"tests.unit.test_new_contract"}, integration, "Unrouted unit"),
            (unit, integration | {"tests.integration.test_new_contract"}, "Unrouted integration"),
            (unit - {"tests.unit.test_order_lifecycle"}, integration, "Missing unit files"),
            (unit, integration - {"tests.integration.test_postgres_access_postgres"},
             "Missing integration files"),
        )
        for current_unit, current_integration, message in cases:
            with self.subTest(message=message):
                self.assertIn(message, "\n".join(run_regression._catalog_errors(
                    manifest, groups, current_unit, current_integration,
                )))

    def test_catalog_rejects_duplicate_integration_route(self) -> None:
        manifest = run_regression._load_manifest()
        groups = run_regression._load_ci_groups()
        groups["postgres_extended"] = [*groups["postgres_extended"],
                                        groups["postgres_required"][0]]
        with self.assertRaisesRegex(ValueError, "multiple CI groups"):
            run_regression._ci_membership(manifest, groups)

    def test_code_tree_identity_distinguishes_line_endings_from_source_changes(self) -> None:
        source = self.directory / "src" / "example.py"
        source.parent.mkdir()
        source.write_bytes(b"value = 1\n")
        dockerfile = self.directory / "tests" / "ci_runtime.Dockerfile"
        dockerfile.parent.mkdir()
        dockerfile.write_bytes(b"FROM python:3.13-slim\n")
        gitkeep = self.directory / "tests" / "unit" / ".gitkeep"
        gitkeep.parent.mkdir()
        gitkeep.write_bytes(b"\n")
        deployment = self.directory / "deploy" / "synology"
        deployment.mkdir(parents=True)
        shell = deployment / "kiwoom-nas"
        example = deployment / ".env.example"
        readme = deployment / "README.md"
        shell.write_bytes(b"#!/bin/sh\nexit 0\n")
        example.write_bytes(b"FIXTURE=1\n")
        readme.write_bytes(b"deployment fixture\n")
        with patch.object(run_regression, "ROOT", self.directory):
            before = run_regression._code_tree_identity()
            source.write_bytes(b"value = 1\r\n")
            dockerfile.write_bytes(b"FROM python:3.13-slim\r\n")
            gitkeep.write_bytes(b"\r\n")
            for path in (shell, example, readme):
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            newline_only = run_regression._code_tree_identity()
            shell.write_bytes(b"#!/bin/sh\r\nexit 1\r\n")
            deployment_changed = run_regression._code_tree_identity()
            source.write_bytes(b"value = 2\r\n")
            changed = run_regression._code_tree_identity()
        self.assertEqual(before["lf_sha256"], newline_only["lf_sha256"])
        self.assertNotEqual(before["raw_sha256"], newline_only["raw_sha256"])
        self.assertNotEqual(newline_only["lf_sha256"], deployment_changed["lf_sha256"])
        self.assertNotEqual(before["lf_sha256"], changed["lf_sha256"])

    def test_source_change_during_run_cannot_report_success(self) -> None:
        output = self.directory / "source-changed"
        passed = {"status": "passed", "worker_exit_confirmed": True,
                  "process_tree_exit_confirmed": True, "test_count": 1}
        identity = lambda name: {"files": 1, "lf_sha256": name, "raw_sha256": name}
        with patch.object(run_regression, "_run_process", return_value=passed), \
                patch.object(run_regression, "_code_tree_identity",
                             side_effect=(identity("before"), identity("after"))), \
                redirect_stdout(StringIO()):
            exit_code = run_regression._execute_profile("core", 5, output)
        result = json.loads((output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(1, exit_code)
        self.assertEqual("incomplete", result["status"])
        self.assertTrue(result["source_modified_during_run"])

    def test_required_gate_rejects_skips_missing_modules_and_unclosed_descendants(self) -> None:
        manifest = run_regression._load_manifest()
        catalog = run_regression._load_ci_groups()
        planned = run_regression._planned_batches(manifest, "all-local")
        code = {"files": 1, "lf_sha256": "f" * 64, "raw_sha256": "f" * 64}
        workers = []
        for batch in planned:
            module_sets = ([batch["modules"]] if not batch["isolate_modules"]
                           else [[module] for module in batch["modules"]])
            for modules in module_sets:
                workers.append({
                    "name": batch["name"], "modules": modules, "status": "passed",
                    "exit_code": 0, "worker_exit_confirmed": True,
                    "process_tree_exit_confirmed": True, "source": {"verified": True},
                    "test_count": len(modules), "planned_test_count": len(modules),
                })
        windows = {
            "status": "passed", "profile": "all-local",
            "git": {"commit": "test-commit", "working_tree_dirty": False},
            "planned_batches": planned, "batch_results": workers,
            "unrun_batches": [], "missing_test_files": [],
            "code_tree_start": code, "code_tree_end": code,
            "source_modified_during_run": False,
        }
        linux = {
            "status": "passed", "modules": catalog["linux_required"],
            "planned": 5, "ran": 5, "failures": 0, "errors": 0,
            "skipped": 0, "expected_failures": 0, "unexpected_successes": 0,
            "source_start": code, "source_end": code,
        }
        postgres = {
            **linux, "modules": catalog["postgres_required"],
            "git_commit": "test-commit",
        }
        check_required_ci_results.validate(windows, linux, postgres, "test-commit")
        # Reuse the full-run proof locally; changing current content invalidates it.
        report = self.directory / "local-proof.json"
        report.write_text(json.dumps(windows), encoding="utf-8")
        argv = ["check_required_ci_results", "--windows-only", "--local", "--windows", str(report)]
        with patch.object(sys, "argv", argv), redirect_stdout(StringIO()):
            with patch.object(run_regression, "_code_tree_identity", return_value=code):
                self.assertEqual(0, check_required_ci_results.main())
            with patch.object(run_regression, "_code_tree_identity", return_value={**code, "lf_sha256": "e" * 64}):
                with patch.object(sys, "stderr", StringIO()):
                    self.assertEqual(1, check_required_ci_results.main())
        for target, field, value in (
            ("linux", "skipped", 1),
            ("postgres", "modules", catalog["postgres_required"][:-1]),
            ("windows", "process_tree_exit_confirmed", False),
        ):
            with self.subTest(target=target, field=field):
                w, l, p = copy.deepcopy((windows, linux, postgres))
                if target == "windows":
                    w["batch_results"][0][field] = value
                elif target == "linux":
                    l[field] = value
                else:
                    p[field] = value
                with self.assertRaises(ValueError):
                    check_required_ci_results.validate(w, l, p, "test-commit")

    def test_full_profile_refuses_catalog_gap_before_any_worker(self) -> None:
        output = self.directory / "not-created"
        with patch.object(run_regression, "_check_test_catalog", return_value=1), \
             patch.object(run_regression, "_locked_environment") as environment:
            with self.assertRaisesRegex(ValueError, "complete unit/integration routing"):
                run_regression._execute_profile("all-local", 5, output)
        environment.assert_not_called()
        self.assertFalse(output.exists())

    def test_locked_environment_refuses_drift_without_installing_packages(self) -> None:
        with patch.object(run_regression.importlib.metadata, "version", return_value="drift"):
            with self.assertRaisesRegex(ValueError, "Locked regression dependencies differ"):
                run_regression._locked_environment()

    def test_extended_selection_is_conservative_for_unmapped_changes(self) -> None:
        docs = select_ci_groups.select(["docs/OPEN_ITEMS.md"], "auto")["groups"]
        self.assertFalse(any(docs.values()))
        ui = select_ci_groups.select([
            "src/kiwoom_monitor/presentation/market_news_window.py",
        ], "auto")["groups"]
        self.assertTrue(ui["windows_fast"])
        self.assertFalse(any(value for name, value in ui.items() if name != "windows_fast"))
        database = select_ci_groups.select([
            "src/kiwoom_monitor/central_server/database_market_bars.py",
        ], "auto")["groups"]
        self.assertTrue(all(database[name] for name in (
            "windows_fast", "windows_long", "postgres", "sealed",
        )))
        unknown = select_ci_groups.select(["scripts/new_shared_runner.py"], "auto")["groups"]
        self.assertTrue(all(unknown.values()))
        self.assertEqual(
            {"windows_fast", "postgres"},
            {name for name, value in select_ci_groups.select([], "nightly")["groups"].items() if value},
        )
        self.assertTrue(all(select_ci_groups.select([], "weekly")["groups"].values()))

    def test_related_selection_follows_imports_and_keeps_linux_tests_separate(self) -> None:
        root = self.directory / "related-source"
        files = {
            "src/sample/value.py": "VALUE = 1\n",
            "src/sample/reader.py": "from .value import VALUE\n",
            "tests/unit/test_consumer.py": "from sample.reader import VALUE\n",
            "tests/unit/test_unrelated.py": "import unittest\n",
            "tests/unit/test_linux.py": "from sample.reader import VALUE\n",
            "tests/ci_groups.json": json.dumps({"linux_required": ["tests.unit.test_linux"]}),
        }
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.assertEqual(["tests.unit.test_consumer"], select_ci_groups.related_test_modules(
            ["src/sample/value.py"], root))
        self.assertEqual(["tests.unit.test_unrelated"], select_ci_groups.related_test_modules(
            ["tests/unit/test_unrelated.py"], root))
        self.assertEqual([], select_ci_groups.related_test_modules(["docs/README.md"], root))
        (root / "src/sample/value.py").unlink()
        self.assertEqual(["tests.unit.test_consumer"], select_ci_groups.related_test_modules(
            ["src/sample/value.py"], root))

    def test_related_plan_keeps_existing_batch_order_and_worker_isolation(self) -> None:
        manifest = {
            "core_batches": [{"name": "core", "modules": ["tests.unit.test_a", "tests.unit.test_b"]}],
            "profiles": {"extra": ["tests.unit.test_c", "tests.unit.test_d"]},
        }
        with patch.object(select_ci_groups, "related_modules_since",
                          return_value=["tests.unit.test_d", "tests.unit.test_b"]):
            self.assertEqual([
                {"name": "core", "modules": ["tests.unit.test_b"], "isolate_modules": False},
                {"name": "extra", "modules": ["tests.unit.test_d"], "isolate_modules": True},
            ], run_regression._planned_batches(manifest, "related", base_ref="parent-commit"))
        with patch.object(select_ci_groups, "related_modules_since",
                          return_value=["tests.unit.test_unregistered"]):
            with self.assertRaisesRegex(ValueError, "missing from the regression manifest"):
                run_regression._planned_batches(manifest, "related", base_ref="parent-commit")

    def test_related_report_requires_its_exact_selected_modules_and_completed_workers(self) -> None:
        modules = ["tests.unit.test_run_regression"]
        windows = {
            "status": "passed", "profile": "related", "base_ref": "parent-commit",
            "git": {"commit": "test-commit", "working_tree_dirty": False},
            "planned_batches": [{"modules": modules}],
            "batch_results": [{"modules": modules, "status": "passed", "exit_code": 0,
                "worker_exit_confirmed": True, "process_tree_exit_confirmed": True,
                "source": {"verified": True}, "test_count": 1, "planned_test_count": 1}],
            "code_tree_start": {"lf_sha256": "f" * 64},
            "code_tree_end": {"lf_sha256": "f" * 64},
        }
        with patch.object(select_ci_groups, "related_modules_since", return_value=modules):
            check_required_ci_results.validate_windows(windows, "test-commit")
            missing = copy.deepcopy(windows)
            missing["batch_results"][0]["modules"] = []
            with self.assertRaisesRegex(ValueError, "plan/result"):
                check_required_ci_results.validate_windows(missing, "test-commit")
            unfinished = copy.deepcopy(windows)
            unfinished["batch_results"][0]["process_tree_exit_confirmed"] = False
            with self.assertRaisesRegex(ValueError, "worker incomplete"):
                check_required_ci_results.validate_windows(unfinished, "test-commit")
        catalog = run_regression._load_ci_groups()
        linux = {"status": "passed", "modules": catalog["linux_required"], "planned": 1, "ran": 1,
                 "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0,
                 "unexpected_successes": 0, "source_start": {"lf_sha256": "f" * 64},
                 "source_end": {"lf_sha256": "f" * 64}}
        postgres = {**linux, "modules": catalog["postgres_required"], "git_commit": "test-commit"}
        with patch.object(select_ci_groups, "related_modules_since", return_value=[]):
            check_required_ci_results.validate(None, linux, postgres, "test-commit", base_ref="parent-commit")
        with patch.object(select_ci_groups, "related_modules_since", return_value=modules):
            with self.assertRaisesRegex(ValueError, "Windows tests were not executed"):
                check_required_ci_results.validate(None, linux, postgres, "test-commit", base_ref="parent-commit")

    def _assert_architecture_ci_gate(self, text: str) -> None:
        # BaseLoader preserves GitHub's "on" key and scalar expressions. This
        # validates this existing workflow contract, not arbitrary Actions YAML.
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        for event in ('push', 'pull_request'):
            self.assertIn(event, workflow['on'])
            self.assertIn(workflow['on'][event], ('', None, {}))
        job = workflow['jobs']['all-local']
        self.assertEqual('Windows all-local regression', job['name'])
        self.assertEqual('${{ always() }}', job['if'])
        self.assertEqual(['windows-shards'], job['needs'])
        self.assertEqual('false', job.get('continue-on-error', 'false'))
        shard_job = workflow['jobs']['windows-shards']
        self.assertNotIn('if', shard_job)
        self.assertEqual('false', shard_job.get('continue-on-error', 'false'))
        self.assertEqual('false', shard_job['strategy']['fail-fast'])
        self.assertEqual(['0', '1'], shard_job['strategy']['matrix']['shard'])
        runners = [step for step in shard_job['steps']
                   if 'python -m scripts.run_regression --profile all-local ' in step.get('run', '')]
        self.assertEqual(1, len(runners))
        self.assertNotIn('if', runners[0])
        self.assertEqual('false', runners[0].get('continue-on-error', 'false'))
        self.assertIn('--shard-count 2 --shard-index ${{ matrix.shard }}', runners[0]['run'])
        self.assertIn('if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }', runners[0]['run'].splitlines())
        merges = [step for step in job['steps'] if '--merge-windows ' in step.get('run', '')]
        self.assertEqual(1, len(merges))
        self.assertNotIn('if', merges[0])
        self.assertEqual('false', merges[0].get('continue-on-error', 'false'))
        self.assertEqual('${{ needs.windows-shards.result }}', merges[0]['env']['WINDOWS_SHARDS_RESULT'])
        self.assertIn("if ($env:WINDOWS_SHARDS_RESULT -ne 'success') { exit 1 }", merges[0]['run'].splitlines())
        self.assertEqual(2, merges[0]['run'].count('--windows-shard '))
        self.assertIn('--merge-windows tmp/regression/ci-all-local/run.json', merges[0]['run'])
        self.assertIn('if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }', merges[0]['run'].splitlines())
        steps = [step for step in job['steps']
                 if 'python -m scripts.check_architecture ' in step.get('run', '')]
        self.assertEqual(1, len(steps))
        self.assertEqual(1, sum('python -m scripts.check_architecture ' in step.get('run', '')
                                for item in workflow['jobs'].values() for step in item.get('steps', [])))
        step = steps[0]
        self.assertNotIn('if', step)
        self.assertEqual('false', step.get('continue-on-error', 'false'))
        self.assertEqual('pwsh', step['shell'])
        self.assertIn('if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }', step['run'].splitlines())
        required = workflow['jobs']['required-regression']
        self.assertIn('all-local', required['needs'])
        self.assertEqual('${{ always() }}', required['if'])
        self.assertEqual('false', required.get('continue-on-error', 'false'))
        self.assertEqual("${{ needs['all-local'].result }}", required['env']['WINDOWS_JOB'])
        upstream = required['steps'][0]
        self.assertNotIn('if', upstream)
        self.assertEqual('false', upstream.get('continue-on-error', 'false'))
        self.assertIn('test "$WINDOWS_JOB" = success', upstream['run'].splitlines())

    def test_architecture_ci_gate_rejects_disabled_or_ignored_failure_mutations(self) -> None:
        original = (run_regression.ROOT / '.github/workflows/dependency-regression.yml').read_text(encoding='utf-8')
        self._assert_architecture_ci_gate(original)
        marker = '      - name: Check architecture deltas without product imports\n'
        mutations = {
            'disabled_step': original.replace(marker, marker + '        if: false\n'),
            'ignored_failure': original.replace(marker, marker + '        continue-on-error: true\n'),
            'zero_exit': original.replace('if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }', 'if ($LASTEXITCODE -ne 0) { exit 0 }'),
            'filtered_pr': original.replace('  pull_request:\n', '  pull_request:\n    paths: ["docs/**"]\n'),
            'missing_guard': original.replace('python -m scripts.check_architecture ', 'python -m scripts.other_check '),
            'ignored_required': original.replace('test "$WINDOWS_JOB" = success\n', 'test "$WINDOWS_JOB" = success || true\n'),
            'missing_partition': original.replace('shard: [0, 1]', 'shard: [0]'),
            'cancel_other_partition': original.replace('fail-fast: false', 'fail-fast: true'),
            'ignored_partition_job': original.replace("if ($env:WINDOWS_SHARDS_RESULT -ne 'success') { exit 1 }",
                                                     "if ($env:WINDOWS_SHARDS_RESULT -ne 'success') { exit 0 }"),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(original, mutated)
                with self.assertRaises(AssertionError):
                    self._assert_architecture_ci_gate(mutated)

    def test_github_ci_keeps_full_regression_without_duplicate_related_run(self) -> None:
        workflow = (run_regression.ROOT / ".github/workflows/dependency-regression.yml").read_text(encoding="utf-8")
        self._assert_architecture_ci_gate(workflow)
        full = workflow.split("  all-local:\n", 1)[1].split("\n  postgres-integration:", 1)[0]
        shards = workflow.split("  windows-shards:\n", 1)[1].split("\n  all-local:", 1)[0]
        self.assertIn("python -m scripts.run_regression --profile all-local", shards)
        self.assertNotIn("python -m scripts.run_regression --profile all-local", full)
        self.assertNotIn("  windows-related:", workflow)
        self.assertNotIn("--profile related", workflow)
        self.assertIn("WINDOWS_JOB: ${{ needs['all-local'].result }}", workflow)
        self.assertIn('test "$WINDOWS_JOB" = success\n', workflow)
        self.assertIn("name: all-local-regression-${{ github.run_id }}", workflow)
        # Multiple upload roots would nest run.json under ci-all-local/ and
        # break the unchanged required job's windows/run.json download path.
        self.assertIn("path: tmp/regression/ci-all-local/\n", full)
        self.assertIn("--windows tmp/ci-required/windows/run.json", workflow)
        self.assertNotIn("--report tmp/regression/ci-all-local/", full)
        self.assertIn('--query --base-ref "$env:REVIEW_BASE" --limit 5', full)
        self.assertIn("$selection.status -ne 'full_required'", full)
        self.assertIn('--summarize tmp/ci-required/windows/run.json', workflow)
        self.assertNotIn('--selection ', workflow)
        self.assertEqual(1, full.count('python -m scripts.check_architecture '))
        self.assertIn('--base-ref "$comparison" --report tmp/regression/architecture.json', full)
        self.assertIn('tmp/regression/architecture.json', full.split('if: always()', 1)[1])
        sealed_build = workflow.split("      - name: Build exact checkout runtime image for isolated v2\n", 1)[1].split("      - name: Confirm installed", 1)[0]
        self.assertIn("tee tmp/regression/ci-sealed/build.log", sealed_build)
        self.assertNotIn("ci-operator/", sealed_build)
        # A changed native sealed test must reach the same execution-ID check;
        # successful upstream job status alone cannot supply its discovery receipt.
        import yaml
        jobs = yaml.load(workflow, Loader=yaml.BaseLoader)['jobs']
        installs = [step for step in jobs['windows-shards']['steps']
                    if step.get('name') == 'Install locked test dependencies']
        self.assertEqual(1, len(installs))
        self.assertEqual('python -m pip install --no-compile -r requirements.lock.txt', installs[0]['run'])
        self.assertFalse(any('pip install' in step.get('run', '') for step in jobs['all-local']['steps']))
        shard_artifact = next(step for step in jobs['windows-shards']['steps']
                              if step.get('uses', '').startswith('actions/upload-artifact'))
        self.assertEqual('windows-regression-shard-${{ matrix.shard }}-${{ github.run_id }}',
                         shard_artifact['with']['name'])
        download = next(step for step in jobs['all-local']['steps']
                        if step.get('uses', '').startswith('actions/download-artifact'))
        self.assertEqual('windows-regression-shard-*-${{ github.run_id }}', download['with']['pattern'])
        self.assertEqual('tmp/ci-shards', download['with']['path'])
        sealed = {step.get('name'): step for step in jobs['sealed-replay']['steps']}
        required = {step.get('name'): step for step in jobs['required-regression']['steps']}
        self.assertIn('--report tmp/regression/ci-sealed/v1-execution.json',
                      sealed['Check v1 recorded execution on sealed DB']['run'])
        artifact = sealed['Preserve replay outcomes without secret files']['with']
        download = required['Download selected sealed execution result']
        self.assertEqual(artifact['name'], download['with']['name'])
        self.assertIn('tmp/regression/ci-sealed/v1-execution.json', artifact['path'].splitlines())
        self.assertEqual("${{ env.SEALED_SELECTED == 'true' }}", download['if'])
        self.assertEqual('tmp/ci-required/sealed', download['with']['path'])
        declaration = required['Confirm declared related tests actually ran']['run']
        self.assertIn('if [ "$SEALED_SELECTED" = true ]; then', declaration)
        self.assertIn('reports+=(--execution tmp/ci-required/sealed/v1-execution.json)', declaration)
        self.assertIn('"${reports[@]}"', declaration)

    def test_new_test_modules_must_be_registered_but_existing_exclusions_are_unchanged(self) -> None:
        base_paths = [
            "tests/unit/test_existing_registered.py",
            "tests/unit/test_existing_unprofiled.py",
        ]
        current_paths = [*base_paths, "tests/unit/test_new_feature.py"]
        manifest = {
            "core_batches": [{"modules": ["tests.unit.test_existing_registered"]}],
            "profiles": {"all-local": ["tests.unit.test_existing_registered"]},
        }

        self.assertEqual(
            ["tests.unit.test_new_feature"],
            run_regression._unregistered_new_test_modules(
                base_paths, current_paths, manifest,
            ),
        )

    def test_registered_new_module_and_non_unit_files_pass_coverage(self) -> None:
        base_paths = ["tests/unit/test_existing.py"]
        current_paths = [
            *base_paths,
            "tests/unit/test_new_feature.py",
            "tests/unit/helper.py",
            "tests/integration/test_database.py",
        ]
        manifest = {
            "core_batches": [],
            "profiles": {"trading": ["tests.unit.test_new_feature"]},
        }

        self.assertEqual(
            [],
            run_regression._unregistered_new_test_modules(
                base_paths, current_paths, manifest,
            ),
        )

    def test_source_identity_flags_a_module_outside_this_worktree(self) -> None:
        foreign = type("Spec", (), {"origin": "C:/elsewhere/kiwoom_monitor.py",
                                     "submodule_search_locations": None})()
        with patch.object(run_regression.importlib.util, "find_spec", return_value=foreign):
            self.assertFalse(run_regression._source_identity()["verified"])

    def test_worker_reports_a_successful_test_and_real_worktree_source(self) -> None:
        result = self._run_fixture("pass")
        self.assertEqual("passed", result["status"])
        self.assertEqual(1, result["test_count"])
        self.assertEqual(1, result["planned_test_count"])
        self.assertEqual(
            [{"module": "tests.unit.regression_worker_fixtures", "test_count": 1}],
            result["module_test_counts"],
        )
        self.assertTrue(result["source"]["verified"])

    def test_real_worker_isolates_fixture_temp_from_workspace_and_other_workers(self) -> None:
        first = self._run_fixture("temp-env")
        second = self._run_fixture("temp-env")
        self.assertEqual("passed", first["status"])
        self.assertEqual("passed", second["status"])
        self.assertNotEqual(first["temporary_directory"], second["temporary_directory"])
        for index, result in enumerate((first, second), 1):
            values = json.loads((self.directory / str(index) / "temp-env.json").read_text())
            self.assertEqual({result["temporary_directory"]}, set(values.values()))
            self.assertFalse(Path(result["temporary_directory"]).is_relative_to(run_regression.ROOT))
            self.assertTrue(result["process_tree_exit_confirmed"])
            self.assertTrue(result["source"]["verified"])
            self.assertEqual("removed", result["temporary_state"])
            self.assertFalse(Path(result["temporary_directory"]).exists())

    def test_temp_isolation_does_not_turn_failed_worker_into_success(self) -> None:
        result = self._run_fixture("fail")
        self.assertEqual("failed", result["status"])
        self.assertTrue(result["failures"])
        self.assertEqual("retained", result["temporary_state"])
        self.assertTrue(Path(result["temporary_directory"]).is_dir())
        self.addCleanup(shutil.rmtree, result["temporary_directory"])

    def test_observed_checkout_cannot_be_selected_as_fixture_temp(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": str(run_regression.ROOT / "tmp"),
                                     "RUNNER_TEMP": str(run_regression.ROOT / "tmp")}):
            result = self._run_fixture("pass")
        self.assertEqual("failed", result["status"])
        self.assertIn("outside the observed checkout", result["execution_error"])
        self.assertIsNone(result["worker_pid"])

    def test_github_worker_uses_runner_scratch_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="github-scratch-") as directory:
            scratch = Path(directory) / "runner-scratch"
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(scratch)}):
                result = self._run_fixture("pass")
            self.assertEqual("passed", result["status"])
            self.assertEqual(scratch / "kiwoom-regression", Path(result["temporary_directory"]).parent)
            self.assertEqual("removed", result["temporary_state"])
            self.assertTrue(result["process_tree_exit_confirmed"])

    def test_windows_child_directory_reader_keeps_native_publication_fail_closed(self) -> None:
        import ctypes
        from ctypes import wintypes
        from scripts import nas_source_runtime
        from tests.unit.test_nas_source_runtime import SourceRuntimeTests

        fixture = SourceRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.publish()
        nas_source_runtime.atomic_json(fixture.store / "active.json", {
            "format": 1, "release_id": first["release_id"],
        })
        (fixture.source / nas_source_runtime.BUILD_FILE).write_text(
            'SERVER_BUILD = "2026.10.01-test-v2"\n', encoding="utf-8",
        )
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        rename = os.rename

        def rename_while_reader_is_open(source, destination):
            handle = api.CreateFileW(str(Path(source) / "tests"), 0x80, 7, None, 3, 0x02000000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return rename(source, destination)
            finally:
                api.CloseHandle(handle)

        with patch.object(nas_source_runtime.os, "rename", side_effect=rename_while_reader_is_open) as observed:
            with self.assertRaises(PermissionError) as failure:
                fixture.publish()
        self.assertEqual(5, failure.exception.winerror)
        self.assertEqual(1, observed.call_count)  # No retry or relaxed publication contract.
        self.assertEqual(first["release_id"], nas_source_runtime.release(fixture.store)[1]["release_id"])
        self.assertEqual(1, len(list((fixture.store / "releases").iterdir())))

    def test_mixed_batch_with_empty_module_is_incomplete_in_either_order(self) -> None:
        empty = "regression_empty_fixture"
        passing = "tests.unit.regression_worker_fixtures"
        for modules in ([empty, passing], [passing, empty]):
            with self.subTest(modules=modules):
                result = self._run_fixture("pass", modules=list(modules))
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(1, result["test_count"])
                self.assertEqual(1, result["planned_test_count"])
                self.assertEqual(list(modules), [item["module"] for item in result["module_test_counts"]])
                self.assertEqual([empty], result["zero_test_modules"])
                self.assertIn("no tests", result["load_error"].lower())

    def test_duplicate_requested_module_has_ordered_discovery_evidence(self) -> None:
        module = "tests.unit.regression_worker_fixtures"
        result = self._run_fixture("pass", modules=[module, module])
        self.assertEqual("failed", result["status"])
        self.assertEqual(2, result["test_count"])
        self.assertEqual(2, result["planned_test_count"])
        identity = "tests.unit.regression_worker_fixtures.RegressionWorkerFixtureTests.test_selected_worker_outcome"
        self.assertEqual([identity, identity], result["executed_test_ids"])
        self.assertEqual(result["planned_test_ids"], result["executed_test_ids"])
        self.assertIn("Duplicate", result["load_error"])
        self.assertEqual(
            [{"module": module, "test_count": 1}, {"module": module, "test_count": 1}],
            result["module_test_counts"],
        )

    def test_worker_success_requires_complete_per_module_discovery_evidence(self) -> None:
        modules = ["first", "second"]
        valid = {
            "module_test_counts": [
                {"module": "first", "test_count": 2},
                {"module": "second", "test_count": 1},
            ],
            "zero_test_modules": [],
            "planned_test_count": 3,
        }
        self.assertIsNone(run_regression._discovery_evidence_error(valid, modules, 3))
        invalid = [
            ({**valid, "module_test_counts": valid["module_test_counts"][:1]}, modules, 2),
            ({**valid, "module_test_counts": [
                {"module": "first", "test_count": 0}, valid["module_test_counts"][1]]}, modules, 1),
            ({**valid, "module_test_counts": [
                {"module": "first", "test_count": True}, valid["module_test_counts"][1]]}, modules, 2),
            ({**valid, "module_test_counts": list(reversed(valid["module_test_counts"]))}, modules, 3),
            ({**valid, "zero_test_modules": ["second"]}, modules, 3),
            ({**valid, "planned_test_count": 4}, modules, 3),
        ]
        for result, requested, executed in invalid:
            with self.subTest(result=result):
                self.assertIsNotNone(
                    run_regression._discovery_evidence_error(result, requested, executed),
                )

    def test_failure_import_error_empty_suite_and_skip_are_not_passes(self) -> None:
        failed = self._run_fixture("fail")
        missing_module = self._run_fixture(
            "pass", module="tests.unit.module_that_does_not_exist_for_regression",
        )
        empty = self._run_fixture("empty")
        skipped = self._run_fixture("skip")

        self.assertEqual("failed", failed["status"])
        self.assertTrue(failed["failures"])
        self.assertEqual("failed", missing_module["status"])
        self.assertIn("ModuleNotFoundError", missing_module["load_error"])
        self.assertEqual("incomplete", empty["status"])
        self.assertEqual(0, empty["test_count"])
        self.assertEqual("incomplete", skipped["status"])
        self.assertTrue(skipped["skipped"])

    def test_expected_failure_and_unexpected_success_are_never_green(self) -> None:
        expected = self._run_fixture("expected-failure")
        unexpected = self._run_fixture("unexpected-success")

        self.assertEqual("incomplete", expected["status"])
        self.assertEqual(1, len(expected["expected_failures"]))
        self.assertEqual("failed", unexpected["status"])
        self.assertEqual(1, len(unexpected["unexpected_successes"]))

    def test_timeout_stops_worker_and_reports_incomplete(self) -> None:
        result = self._run_fixture("timeout", timeout=1)
        self.assertEqual("incomplete", result["status"])
        self.assertTrue(result["timeout"])
        self.assertTrue(result["worker_exit_confirmed"])
        self.assertTrue(result["process_tree_exit_confirmed"])

    def _open_tree_probes(self, directory: Path) -> list[ProcessProbe]:
        probes = []
        deadline = time.monotonic() + 5
        for role in ("worker", "child", "grandchild"):
            path = directory / f"{role}.json"
            while True:
                try:
                    pid = json.loads(path.read_text(encoding="utf-8"))["pid"]
                    break
                except (OSError, json.JSONDecodeError):
                    if time.monotonic() >= deadline:
                        self.fail(f"The {role} fixture did not start")
                    time.sleep(0.02)
            probe = ProcessProbe(pid)
            self.addCleanup(probe.close)
            self.assertFalse(probe.exited(), role)
            self.assertGreater(probe.creation_time, 0)
            probes.append(probe)
        return probes

    def _run_tree_fixture(self, mode: str, *, interrupt: bool = False):
        original_wait = run_regression._JobProcess.wait
        probes = []
        first_wait = True

        def wait(process, timeout):
            nonlocal first_wait
            if first_wait:
                first_wait = False
                probes.extend(self._open_tree_probes(self.directory / str(self.fixture_count)))
                if interrupt:
                    raise KeyboardInterrupt
            return original_wait(process, timeout)

        with patch.object(run_regression._JobProcess, "wait", wait):
            result = self._run_fixture(mode, timeout=5 if mode == "tree-leak" else 1)
        self.assertEqual(3, len(probes))
        self.assertTrue(all(probe.exited() for probe in probes))
        self.assertTrue(result["worker_exit_confirmed"])
        self.assertTrue(result["process_tree_exit_confirmed"])
        self.assertGreaterEqual(result["process_tree_count"], 3)
        return result

    def test_timeout_terminates_live_child_and_grandchild(self) -> None:
        result = self._run_tree_fixture("tree-timeout")
        self.assertEqual("incomplete", result["status"])
        self.assertTrue(result["timeout"])

    def test_interrupt_terminates_live_child_and_grandchild(self) -> None:
        result = self._run_tree_fixture("tree-interrupt", interrupt=True)
        self.assertEqual("incomplete", result["status"])
        self.assertTrue(result["interrupted"])

    def test_worker_success_with_live_descendants_is_incomplete_and_cleans_them(self) -> None:
        result = self._run_tree_fixture("tree-leak")
        self.assertEqual("incomplete", result["status"])
        self.assertTrue(result["leaked_descendants"])
        self.assertEqual(0, result["exit_code"])

    def test_runner_death_kills_worker_child_and_grandchild(self) -> None:
        directory = self.directory / "parent-death"
        directory.mkdir()
        code = (
            "from pathlib import Path; from scripts import run_regression as r; "
            "import os; os.environ['REGRESSION_FIXTURE_RUNNER_PID']=str(os.getpid()); "
            "p=Path(__import__('os').environ['REGRESSION_FIXTURE_DIR']); "
            "r._run_process(modules=['tests.unit.regression_worker_fixtures'], "
            "result_path=p/'result.json', log_path=p/'worker.log', timeout_seconds=30)"
        )
        environment = {**os.environ, "REGRESSION_FIXTURE_MODE": "tree-timeout",
                       "REGRESSION_FIXTURE_DIR": str(directory)}
        parent = subprocess.Popen([sys.executable, "-c", code], env=environment,
                                  cwd=run_regression.ROOT, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: parent.wait(timeout=5))
        self.addCleanup(lambda: parent.kill() if parent.poll() is None else None)
        probes = self._open_tree_probes(directory)
        # The worker records the actual runner PID (venv launchers can have another PID).
        runner_pid = json.loads((directory / "runner.json").read_text(encoding="utf-8"))["pid"]
        runner = ProcessProbe(runner_pid)
        self.addCleanup(runner.close)
        runner.api.TerminateProcess(runner.handle, 98)
        self.assertTrue(runner.exited(5000))
        self.assertTrue(all(probe.exited(5000) for probe in probes))

    def test_containment_launch_failure_does_not_run_tests(self) -> None:
        with patch.object(run_regression._WindowsJob, "start", side_effect=OSError("job rejected")):
            result = self._run_fixture("tree-timeout")
        self.assertEqual("failed", result["status"])
        self.assertIn("job rejected", result["execution_error"])
        self.assertFalse((self.directory / "1" / "worker.json").exists())

    def test_unconfirmed_tree_exit_prevents_the_next_batch(self) -> None:
        output = self.directory / "unconfirmed"
        failed_cleanup = {"status": "failed", "worker_exit_confirmed": True,
                          "process_tree_exit_confirmed": False, "test_count": 1}
        with patch.object(run_regression, "_run_process", return_value=failed_cleanup) as run, \
                redirect_stdout(StringIO()):
            exit_code = run_regression._execute_profile("core", 5, output)
        summary = json.loads((output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(1, run.call_count)
        self.assertNotEqual(0, exit_code)
        self.assertEqual("incomplete", summary["status"])
        self.assertEqual(["journal-and-news"], summary["unrun_batches"])

    def test_safely_closed_nonpassing_batches_continue_but_timeout_and_interrupt_stop(self) -> None:
        for status, cause, stop in (("failed", {}, False),
                ("incomplete", {"skipped": [{"test": "fixture"}]}, False),
                ("incomplete", {"zero_test_modules": ["fixture"]}, False),
                ("incomplete", {"timeout": True}, True),
                ("incomplete", {"interrupted": True}, True)):
            with self.subTest(status=status, cause=cause):
                output = self.directory / ("continue-" + str(len(list(self.directory.iterdir()))))
                first = {"status": status, "worker_exit_confirmed": True,
                    "process_tree_exit_confirmed": True, "test_count": 1, **cause}
                passed = {"status": "passed", "worker_exit_confirmed": True,
                    "process_tree_exit_confirmed": True, "test_count": 1}
                console = StringIO()
                summary_output = StringIO()
                with patch.object(run_regression, "_run_process", side_effect=[first, passed]) as run, \
                        patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                        redirect_stderr(console), redirect_stdout(summary_output):
                    self.assertNotEqual(0, run_regression._execute_profile("core", 5, output))
                events = [json.loads(line) for line in console.getvalue().splitlines()]
                self.assertEqual(["start", "complete"] * run.call_count,
                                 [event["phase"] for event in events if "phase" in event])
                self.assertEqual(first["status"], events[1]["status"])
                result = json.loads((output / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(result['status'], json.loads(summary_output.getvalue())['status'])
                self.assertEqual(1 if stop else 2, run.call_count)
                self.assertEqual(["journal-and-news"] if stop else [], result["unrun_batches"])
                self.assertEqual(first["status"], result["batch_results"][0]["status"])

    def test_result_written_before_nonzero_exit_does_not_count_as_passed(self) -> None:
        result = self._run_fixture("crash-after-result")
        self.assertEqual("failed", result["status"])
        self.assertEqual(23, result["exit_code"])
        self.assertEqual("passed", json.loads(
            Path(result["result"]).read_text(encoding="utf-8"),
        )["status"])

    def test_output_directory_refuses_overwrite(self) -> None:
        with self.assertRaises(FileExistsError):
            run_regression._execute_profile("core", 1, self.directory)

    def test_run_summary_records_pending_work_before_start_and_after_each_batch(self) -> None:
        output = self.directory / "active-run"
        observations: list[list[str]] = []

        def completed_batch(**_kwargs):
            summary = json.loads((output / "run.json").read_text(encoding="utf-8"))
            observations.append(summary["unrun_batches"])
            return {"status": "failed" if len(observations) == 1 else "passed", "worker_exit_confirmed": True,
                    "process_tree_exit_confirmed": True, "test_count": 1}

        with patch.object(run_regression, "_run_process", side_effect=completed_batch), \
                redirect_stdout(StringIO()):
            exit_code = run_regression._execute_profile("core", 5, output)

        final = json.loads((output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(1, exit_code)
        self.assertEqual(2, len(observations[0]))
        self.assertEqual(1, len(observations[1]))
        self.assertEqual("failed", final["status"])
        self.assertEqual([], final["unrun_batches"])



if __name__ == "__main__":
    unittest.main()
