"""Validate the required Windows/Linux/PostgreSQL results for the same source."""

from __future__ import annotations

import argparse
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


def validate(windows: dict, linux: dict, postgres: dict, commit: str) -> None:
    manifest = run_regression._load_manifest()
    catalog = run_regression._load_ci_groups()
    membership = run_regression._ci_membership(manifest, catalog)
    if windows.get("status") != "passed" or windows.get("profile") != "all-local":
        raise ValueError("Windows full regression did not pass")
    if windows.get("git", {}).get("commit") != commit or windows.get("git", {}).get("working_tree_dirty") is not False:
        raise ValueError("Windows source commit or cleanliness differs")
    if windows.get("missing_test_files") or windows.get("unrun_batches") or windows.get("interrupted"):
        raise ValueError("Windows run omitted work")
    planned_batches = run_regression._planned_batches(manifest, "all-local")
    planned = [m for batch in planned_batches for m in batch["modules"]]
    actual = [m for batch in windows.get("batch_results", []) for m in batch.get("modules", [])]
    if actual != planned or [m for batch in windows.get("planned_batches", [])
                              for m in batch.get("modules", [])] != planned:
        raise ValueError("Windows plan/result differs from current manifest")
    if len({m for m in actual}) != len(membership["ci-required-windows"] |
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

    _assert_clean_result(linux, "Linux POSIX")
    _assert_clean_result(postgres, "PostgreSQL")
    if set(linux.get("modules", [])) != membership["ci-required-linux"]:
        raise ValueError("Linux required modules differ from catalog")
    if set(postgres.get("modules", [])) != membership["ci-required-postgres"]:
        raise ValueError("PostgreSQL required modules differ from catalog")
    if postgres.get("git_commit") != commit:
        raise ValueError("PostgreSQL source commit differs")
    fingerprints = {
        windows.get("code_tree_start", {}).get("lf_sha256"),
        linux.get("source_start", {}).get("lf_sha256"),
        postgres.get("source_start", {}).get("lf_sha256"),
    }
    if None in fingerprints or len(fingerprints) != 1:
        raise ValueError("Required jobs did not use the same normalized source")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", required=True, type=Path)
    parser.add_argument("--linux", required=True, type=Path)
    parser.add_argument("--postgres", required=True, type=Path)
    args = parser.parse_args()
    try:
        commit = os.environ["GITHUB_SHA"]
        validate(_read(args.windows), _read(args.linux), _read(args.postgres), commit)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"Required regression failed: {error}", file=sys.stderr)
        return 1
    print("Required regression passed: Windows, Linux and PostgreSQL reports match the source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
