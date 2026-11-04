"""Run the offline Linux operator suites and publish checkout-bound CI evidence."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4

from scripts.run_regression import ROOT, _IdentityTestResult, _code_tree_identity, _suite_test_ids


MODULES = (
    "tests.unit.test_nas_operator",
    "tests.unit.test_nas_source_runtime",
    "tests.integration.test_nas_operator_linux",
)
COUNTERS = {
    "failures": "failures",
    "errors": "errors",
    "skipped": "skipped",
    "expected_failures": "expectedFailures",
    "unexpected_successes": "unexpectedSuccesses",
}
# Match the canonical code-tree fingerprint without mounting a whole checkout.
SOURCE_PATHS = (
    "src", "scripts", "tests", "deploy", ".github/workflows",
    "requirements.lock.txt", "pyproject.toml", "KiwoomMonitor.spec",
)
CONTAINER_ROOT = Path("/app/candidate")
FIXTURE = "/op-fixture"


def _collect_suites() -> dict:
    source_start = _code_tree_identity()
    module_counts, planned_ids, suites = [], [], []
    loader = unittest.TestLoader()
    for module in MODULES:
        suite = loader.loadTestsFromName(module)
        ids = _suite_test_ids(suite)
        module_counts.append({"module": module, "test_count": suite.countTestCases()})
        planned_ids.extend(ids)
        suites.append(suite)
    result = unittest.TextTestRunner(verbosity=2, resultclass=_IdentityTestResult).run(unittest.TestSuite(suites))
    return {
        "source_start": source_start, "source_end": _code_tree_identity(),
        "planned": len(planned_ids), "ran": result.testsRun,
        "planned_test_ids": planned_ids, "executed_test_ids": result.executed_test_ids,
        "passed_test_ids": result.passed_test_ids, "module_test_counts": module_counts,
        **{name: len(getattr(result, attribute)) for name, attribute in COUNTERS.items()},
    }


def _complete(report: dict) -> bool:
    if report["source_start"] != report["source_end"] or any(report[name] != 0 for name in COUNTERS):
        return False
    ids = report["planned_test_ids"]
    if (not ids or report["planned"] != len(ids) or report["ran"] != len(ids)
            or report["executed_test_ids"] != ids or report["passed_test_ids"] != ids
            or len(set(ids)) != len(ids)):
        return False
    rows = report["module_test_counts"]
    if tuple(row["module"] for row in rows) != MODULES:
        return False
    offset = 0
    for row in rows:
        count = row["test_count"]
        if (type(count) is not int or count <= 0 or offset + count > len(ids)
                or not all(identity.startswith(row["module"] + ".") for identity in ids[offset:offset + count])):
            return False
        offset += count
    return offset == len(ids)


def _container_main() -> int:
    expected_cpus = {int(cpu) for cpu in os.environ["KIWOOM_OPERATOR_CPUSET"].split(",")}
    if os.sched_getaffinity(0) != expected_cpus:
        raise RuntimeError("Operator container CPU affinity differs")
    memory = Path("/sys/fs/cgroup/memory.max")
    if not memory.exists():
        memory = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if not 0 < int(memory.read_text()) <= 256 * 1024**2:
        raise RuntimeError("Operator container memory limit differs")

    # Only the evidence goes to stdout. Test output remains in the existing log.
    with redirect_stdout(sys.stderr):
        import kiwoom_monitor

        mounted = CONTAINER_ROOT / "src/kiwoom_monitor"
        installed = Path(kiwoom_monitor.__file__).parent
        files = list(mounted.rglob("*.py"))
        if not files or any(
            not (installed / path.relative_to(mounted)).is_file()
            or path.read_bytes() != (installed / path.relative_to(mounted)).read_bytes()
            for path in files
        ):
            raise RuntimeError("Installed Python sources differ from the operator checkout")
        print(f"installed_source_parity=true python_files={len(files)}")
        report = _collect_suites()
    if not _complete(report):
        print("Operator discovery/execution evidence did not pass", file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


def _docker_output(docker: list[str], *args: str) -> str:
    return subprocess.run([*docker, *args], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def _remove_owned_container(docker: list[str], owner: str) -> None:
    listing = ("container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"label={owner}")
    owned = _docker_output(docker, *listing).splitlines()
    if not owned:
        return
    # --rm normally handles this. Also reap the owned container after an interrupted run.
    subprocess.run([*docker, "container", "rm", "--force", *owned], stdout=subprocess.DEVNULL)
    if _docker_output(docker, *listing):
        raise RuntimeError("Owned operator container removal could not be confirmed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_id", help="existing pinned local runtime image ID")
    parser.add_argument("output", type=Path, help="new CI execution receipt")
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id):
        parser.error("a pinned local runtime image ID is required")
    if args.output.exists() or args.output.is_symlink():
        parser.error("the execution receipt path already exists")
    executable = shutil.which("docker", path="/usr/local/bin:/usr/bin:/bin")
    if sys.platform != "linux" or not executable:
        parser.error("the offline operator gate requires Linux and the local Docker daemon")
    docker = [executable, "--host", "unix:///var/run/docker.sock"]
    if _docker_output(docker, "image", "inspect", "--format", "{{.Id}}", args.image_id) != args.image_id:
        raise RuntimeError("Local image identity mismatch")
    cpus = sorted(os.sched_getaffinity(0))[:2]
    if len(cpus) != 2:
        raise RuntimeError("The operator gate requires two available CPUs")
    cpuset = ",".join(str(cpu) for cpu in cpus)
    owner = f"com.kiwoom.ci-operator={uuid4().hex}"
    mounts = []
    for path in SOURCE_PATHS:
        mounts.extend(("--mount", f"type=bind,src={ROOT / path},dst={CONTAINER_ROOT / path},readonly"))

    source_start = _code_tree_identity()
    try:
        raw_report = _docker_output(
            docker, "run", "--rm", "--pull", "never", "--label", owner,
            "--network", "none", "--user", "0:0", "--read-only",
            "--memory", "256m", "--memory-swap", "256m", "--cpuset-cpus", cpuset,
            "--pids-limit", "64", "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "--tmpfs", f"{FIXTURE}:rw,nosuid,nodev,size=32m,mode=0755",
            "--env", f"KIWOOM_OPERATOR_FS_GATE_ROOT={FIXTURE}",
            "--env", f"TMPDIR={FIXTURE}", "--env", f"TEMP={FIXTURE}", "--env", f"TMP={FIXTURE}",
            "--env", f"KIWOOM_OPERATOR_CPUSET={cpuset}", *mounts,
            "--entrypoint", "python", args.image_id, "-I", "-B", "-c",
            "import sys; sys.path.insert(0, '/app/candidate'); "
            "from scripts.run_linux_operator_ci import _container_main; sys.exit(_container_main())",
        )
    finally:
        _remove_owned_container(docker, owner)
    source_end = _code_tree_identity()
    report = json.loads(raw_report)
    if (not _complete(report) or report["source_start"] != source_start
            or report["source_end"] != source_end):
        raise RuntimeError("Operator evidence or checkout fingerprint differs")
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({**report, "status": "passed", "container_removed": True}, indent=2) + "\n")
    print(f"operator_execution=passed tests={report['ran']} modules={len(MODULES)} container_removed=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
