"""Validate the required Windows/Linux/PostgreSQL results for the same source."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

from scripts import run_regression


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid CI report: {path}")
    return value


def _assert_clean_result(report: dict, label: str) -> None:
    if report.get("status") != "passed":
        raise ValueError(f"{label}: report did not pass")
    planned = report.get("planned")
    ran = report.get("ran")
    if not isinstance(planned, int) or isinstance(planned, bool) or planned < 1 or ran != planned:
        raise ValueError(f"{label}: discovery/execution mismatch")
    for field in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        if report.get(field) != 0:
            raise ValueError(f"{label}: {field}={report.get(field)}")
    source_start = report.get("source_start") or {}
    source_end = report.get("source_end") or {}
    if not source_start.get("lf_sha256") or source_start["lf_sha256"] != source_end.get("lf_sha256"):
        raise ValueError(f"{label}: source changed or fingerprint missing")


def validate_windows(windows: dict, commit: str | None) -> None:
    manifest = run_regression._load_manifest()
    catalog = run_regression._load_ci_groups()
    membership = run_regression._ci_membership(manifest, catalog)
    profile = windows.get("profile")
    if windows.get("status") != "passed" or profile not in {"all-local", "related"}:
        raise ValueError("Windows selected regression did not pass")
    if commit is not None and (windows.get("git", {}).get("commit") != commit
                               or windows.get("git", {}).get("working_tree_dirty") is not False):
        raise ValueError("Windows source commit or cleanliness differs")
    if windows.get("missing_test_files") or windows.get("unrun_batches") or windows.get("interrupted"):
        raise ValueError("Windows run omitted work")
    if profile == "related":
        if not windows.get("base_ref"):
            raise ValueError("Related Windows report has no comparison base")
        planned_batches = run_regression._planned_batches(
            manifest, "related", base_ref=windows["base_ref"],
        )
    else:
        planned_batches = run_regression._planned_batches(manifest, "all-local")
    planned = [m for batch in planned_batches for m in batch["modules"]]
    actual = [m for batch in windows.get("batch_results", []) for m in batch.get("modules", [])]
    if actual != planned or [m for batch in windows.get("planned_batches", [])
                              for m in batch.get("modules", [])] != planned:
        raise ValueError("Windows plan/result differs from current manifest")
    if profile == "all-local" and len({m for m in actual}) != len(membership["ci-required-windows"] |
                                     membership["ci-extended-windows"] |
                                     membership["ci-long-windows"]):
        raise ValueError("Windows planned modules are incomplete")
    for worker in windows["batch_results"]:
        if (worker.get("status") != "passed" or worker.get("exit_code") != 0
                or not worker.get("worker_exit_confirmed")
                or not worker.get("process_tree_exit_confirmed")
                or worker.get("leaked_descendants") or worker.get("timeout")
                or worker.get("interrupted") or worker.get("zero_test_modules")
                or not (worker.get("source") or {}).get("verified")
                or worker.get("test_count", 0) < 1
                or worker.get("test_count") != worker.get("planned_test_count")
                or any(worker.get(field) for field in (
                    "failures", "errors", "skipped", "expected_failures", "unexpected_successes",
                ))):
            raise ValueError(f"Windows worker incomplete: {worker.get('name')}")
    if windows.get("source_modified_during_run") or (
        windows.get("code_tree_start", {}).get("lf_sha256")
        != windows.get("code_tree_end", {}).get("lf_sha256")
    ):
        raise ValueError("Windows source changed during regression")


def merge_windows_shards(shards: list[dict], commit: str) -> dict:
    """Accept two complete disjoint worker ranges before publishing full evidence."""
    if len(shards) != 2:
        raise ValueError("Exactly two Windows shards are required")
    current_git = run_regression._git_metadata()
    if (not commit or current_git.get("commit") != commit
            or current_git.get("working_tree_dirty") is not False):
        raise ValueError("Merge checkout commit or cleanliness differs")
    batches = run_regression._planned_batches(run_regression._load_manifest(), "all-local")
    workers = run_regression._profile_workers(batches)
    current_source = run_regression._code_tree_identity()["lf_sha256"]
    hashes = {
        "profile_manifest_sha256": run_regression._sha256(run_regression.MANIFEST),
        "ci_catalog_sha256": run_regression._sha256(run_regression.CI_GROUPS),
        "runner_sha256": run_regression._sha256(Path(run_regression.__file__)),
    }
    locked_dependencies = dict(line.split("==", 1) for line in
        (run_regression.ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#"))
    by_index = {}
    all_ids = []
    test_hashes = {}
    environment = None
    for report in shards:
        shard = report.get("shard", {})
        index = shard.get("index")
        if type(index) is not int or index not in (0, 1) or index in by_index:
            raise ValueError("Missing or duplicate Windows shard index")
        expected = run_regression._all_local_shard_plan(workers, index=index, count=2)
        if any(type(shard.get(key)) is not type(value) or shard[key] != value
               for key, value in expected.items()):
            raise ValueError("Windows shard range or full plan differs")
        if (report.get("profile") != "all-local-shard" or report.get("status") != "passed_shard"
                or report.get("git", {}).get("commit") != commit
                or report.get("git", {}).get("working_tree_dirty") is not False
                or report.get("missing_test_files") != [] or report.get("unrun_batches") != []
                or report.get("interrupted") is not False
                or report.get("source_modified_during_run") is not False
                or any(report.get(key) != value for key, value in hashes.items())
                or any(report.get(key, {}).get("lf_sha256") != current_source
                       for key in ("code_tree_start", "code_tree_end"))):
            raise ValueError("Windows shard failed, omitted work, or used stale source")
        current_environment = report.get("environment")
        if (not isinstance(current_environment, dict) or not current_environment.get("platform")
                or not current_environment.get("python") or not current_environment.get("timezone")
                or current_environment.get("locked_dependencies") != locked_dependencies):
            raise ValueError("Windows shard environment evidence is missing")
        if environment is None:
            environment = current_environment
        elif current_environment != environment:
            raise ValueError("Windows shard environments differ")
        selected = workers[expected["start"]:expected["end"]]
        planned = [{"name": name, "modules": list(modules)} for name, modules in selected]
        if ([{"name": row.get("name"), "modules": row.get("modules")}
             for row in report.get("planned_batches", [])] != planned
                or [{"name": row.get("name"), "modules": row.get("modules")}
                    for row in report.get("batch_results", [])] != planned):
            raise ValueError("Windows shard plan or worker order differs")
        expected_hashes = {module: run_regression._sha256(
            run_regression.ROOT / (module.replace(".", "/") + ".py"))
            for _, modules in selected for module in modules}
        if report.get("test_file_sha256") != expected_hashes:
            raise ValueError("Windows shard test files differ from current checkout")
        test_hashes.update(expected_hashes)
        for number, worker in enumerate(report["batch_results"], start=expected["start"] + 1):
            count = worker.get("test_count")
            ids = worker.get("executed_test_ids")
            if (type(count) is not int or count < 1
                    or run_regression._discovery_evidence_error(worker, worker["modules"], count)
                    or not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids)
                    or len(ids) != count or ids != worker.get("planned_test_ids")
                    or worker.get("source", {}).get("verified") is not True
                    or any(not isinstance(worker.get(key), str) or
                           worker[key].replace("\\", "/").rsplit("/", 1)[-1] != f"worker-{number:03}.{suffix}"
                           for key, suffix in (("log", "log"), ("result", "json")))
                    or any(worker.get(key) is not False for key in (
                        "timeout", "interrupted", "leaked_descendants"))):
                raise ValueError("Windows shard worker discovery or execution IDs differ")
            all_ids.extend(ids)
        by_index[index] = report
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Duplicate executed test IDs across Windows shards")
    merged = copy.deepcopy(by_index[0])
    merged.update(profile="all-local", status="passed", planned_batches=batches,
                  batch_results=[worker for index in (0, 1) for worker in by_index[index]["batch_results"]],
                  test_file_sha256=test_hashes,
                  started_at=min(report["started_at"] for report in shards),
                  finished_at=max(report["finished_at"] for report in shards),
                  shards=[report["shard"] for report in (by_index[0], by_index[1])])
    merged.pop("shard", None)
    validate_windows(merged, commit)
    return merged


def validate(windows: dict | None, linux: dict, postgres: dict, commit: str, *, base_ref: str = "") -> None:
    if windows is not None:
        validate_windows(windows, commit)
    else:
        from scripts.select_ci_groups import related_modules_since
        if not base_ref or related_modules_since(base_ref):
            raise ValueError("Applicable Windows tests were not executed")
    manifest = run_regression._load_manifest()
    membership = run_regression._ci_membership(manifest, run_regression._load_ci_groups())
    _assert_clean_result(linux, "Linux POSIX")
    _assert_clean_result(postgres, "PostgreSQL")
    if set(linux.get("modules", [])) != membership["ci-required-linux"]:
        raise ValueError("Linux required modules differ from catalog")
    if set(postgres.get("modules", [])) != membership["ci-required-postgres"]:
        raise ValueError("PostgreSQL required modules differ from catalog")
    if postgres.get("git_commit") != commit:
        raise ValueError("PostgreSQL source commit differs")
    fingerprints = {
        linux.get("source_start", {}).get("lf_sha256"),
        postgres.get("source_start", {}).get("lf_sha256"),
    }
    if windows is not None:
        fingerprints.add(windows.get("code_tree_start", {}).get("lf_sha256"))
    if None in fingerprints or len(fingerprints) != 1:
        raise ValueError("Required jobs did not use the same normalized source")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=Path)
    parser.add_argument("--windows-shard", type=Path, action="append", default=[])
    parser.add_argument("--merge-windows", type=Path)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--linux", type=Path)
    parser.add_argument("--postgres", type=Path)
    parser.add_argument("--windows-only", action="store_true",
                        help="Validate existing all-local evidence without rerunning any tests")
    parser.add_argument("--local", action="store_true",
                        help="Allow a precommit dirty report; still require exact current source content")
    parser.add_argument("--covered-profile", choices=run_regression.CI_WINDOWS_PROFILES)
    args = parser.parse_args()
    if args.windows_shard or args.merge_windows:
        if (len(args.windows_shard) != 2 or args.merge_windows is None or args.windows
                or args.windows_only or args.local or args.covered_profile or args.base_ref
                or args.linux or args.postgres):
            parser.error("merge requires exactly two --windows-shard paths and --merge-windows only")
        try:
            if args.merge_windows.exists() or args.merge_windows.is_symlink():
                raise FileExistsError("Refusing to overwrite merged Windows report")
            reports = [_read(path) for path in args.windows_shard]
            merged = merge_windows_shards(reports, os.environ["GITHUB_SHA"])
            # Point diagnostic links at the downloaded artifact, retaining global worker names.
            for path, report in zip(args.windows_shard, reports):
                for worker in merged["batch_results"]:
                    if worker["name"] in {row["name"] for row in report["batch_results"]}:
                        for key in ("log", "result"):
                            if worker.get(key):
                                worker[key] = str(path.parent / worker[key].replace("\\", "/").rsplit("/", 1)[-1])
            args.merge_windows.parent.mkdir(parents=True, exist_ok=True)
            with args.merge_windows.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
            print("Complete Windows all-local evidence merged from two validated shards")
            return 0
        except (OSError, KeyError, TypeError, ValueError) as error:
            print(f"Required regression failed: {error}", file=sys.stderr)
            return 1
    if args.windows_only and args.windows is None:
        parser.error("Windows-only validation requires a Windows report")
    if (args.local or args.covered_profile) and not args.windows_only:
        parser.error("local/covered-profile options require --windows-only")
    if not args.windows_only and (args.linux is None or args.postgres is None):
        parser.error("required CI validation requires Linux and PostgreSQL reports")
    try:
        windows = _read(args.windows) if args.windows else None
        if args.windows_only:
            commit = None if args.local else os.environ["GITHUB_SHA"]
            validate_windows(windows, commit)
            current = run_regression._code_tree_identity()["lf_sha256"]
            if windows.get("code_tree_end", {}).get("lf_sha256") != current:
                raise ValueError("Windows report is stale: current source differs from tested source")
            if args.covered_profile:
                membership = run_regression._ci_membership(
                    run_regression._load_manifest(), run_regression._load_ci_groups())
                covered = {m for worker in windows["batch_results"] for m in worker["modules"]}
                if not membership[args.covered_profile].issubset(covered):
                    raise ValueError("Requested Windows profile was not fully executed")
                print(f"{args.covered_profile}: all modules passed in the existing all-local run")
            print("Windows source evidence passed; external CI environments remain separate")
        else:
            validate(windows, _read(args.linux), _read(args.postgres), os.environ["GITHUB_SHA"],
                     base_ref=args.base_ref)
            print("Selected regression passed: applicable Windows, Linux and PostgreSQL results match the source")
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"Required regression failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
