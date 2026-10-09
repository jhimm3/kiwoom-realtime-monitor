from __future__ import annotations

import json
import copy
import os
import sys
import subprocess
import time
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def test_profile_plan_preserves_legacy_batches_and_adds_missing_lifecycle_modules(self) -> None:
        manifest = run_regression._load_manifest()
        core = run_regression._planned_batches(manifest, "core")
        modules = [module for batch in core for module in batch["modules"]]

        self.assertEqual(["desktop-and-server", "journal-and-news"],
                         [batch["name"] for batch in core])
        self.assertEqual(143, len(modules))
        self.assertEqual(142, len(set(modules)))
        self.assertEqual("36dae599f514ec9356b7dc3eab9c7b4f1033c4a27076eb8a7fcd931fbeb1810a",
                         run_regression._core_order_sha256(core))
        self.assertEqual("tests.unit.test_main_window_layout", modules[83])
        self.assertEqual("tests.unit.test_theme_color_repository", modules[84])
        self.assertEqual("tests.unit.test_theme_color_repository", modules[142])
        pc_modules = [module for batch in run_regression._planned_batches(
            manifest, "pc-lifecycle",
        ) for module in batch["modules"]]
        self.assertIn("tests.unit.test_app_controller", pc_modules)
        self.assertIn("tests.unit.test_main_window", pc_modules)
        self.assertIn("tests.unit.test_market_cache_writer", pc_modules)
        runner_modules = [module for batch in run_regression._planned_batches(
            manifest, "runner",
        ) for module in batch["modules"]]
        self.assertEqual(["tests.unit.test_run_regression"], runner_modules)

        all_local = run_regression._planned_batches(manifest, "all-local")
        all_modules = [module for batch in all_local for module in batch["modules"]]
        self.assertEqual(1, all_modules.count("tests.unit.test_market_cache_writer"))
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
        self.assertEqual((274, 132, 2), tuple(len(membership[name]) for name in
                         run_regression.CI_WINDOWS_PROFILES))
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
        with patch.object(run_regression, "ROOT", self.directory):
            before = run_regression._code_tree_identity()
            source.write_bytes(b"value = 1\r\n")
            dockerfile.write_bytes(b"FROM python:3.13-slim\r\n")
            gitkeep.write_bytes(b"\r\n")
            newline_only = run_regression._code_tree_identity()
            source.write_bytes(b"value = 2\r\n")
            changed = run_regression._code_tree_identity()
        self.assertEqual(before["lf_sha256"], newline_only["lf_sha256"])
        self.assertNotEqual(before["raw_sha256"], newline_only["raw_sha256"])
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
        self.assertEqual("passed", result["status"])
        self.assertEqual(2, result["test_count"])
        self.assertEqual(2, result["planned_test_count"])
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
            return {"status": "passed", "worker_exit_confirmed": True,
                    "process_tree_exit_confirmed": True, "test_count": 1}

        with patch.object(run_regression, "_run_process", side_effect=completed_batch), \
                redirect_stdout(StringIO()):
            exit_code = run_regression._execute_profile("core", 5, output)

        final = json.loads((output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(0, exit_code)
        self.assertEqual(2, len(observations[0]))
        self.assertEqual(1, len(observations[1]))
        self.assertEqual("passed", final["status"])
        self.assertEqual([], final["unrun_batches"])



if __name__ == "__main__":
    unittest.main()
