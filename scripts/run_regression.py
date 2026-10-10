"""Run named local regression profiles in isolated Python worker processes."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
MANIFEST = ROOT / "tests" / "regression_profiles.json"
CI_GROUPS = ROOT / "tests" / "ci_groups.json"
DEFAULT_TIMEOUT_SECONDS = 1800


class _WindowsJob:
    """Own a worker's Windows process tree, including cleanup if the runner dies.

    Job assignment is part of CreateProcess itself, so even a venv launcher
    cannot spawn an uncontained child. The job handle is never inherited.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("This local regression runner requires Windows process-tree containment")
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
                        ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
                        ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

        self._ctypes = ctypes
        self._accounting_type = Accounting
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                         wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                           wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "InitializeProcThreadAttributeList": ([wintypes.LPVOID, wintypes.DWORD,
                wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            "UpdateProcThreadAttribute": ([wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t,
                wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL),
            "DeleteProcThreadAttributeList": ([wintypes.LPVOID], None),
            "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID,
                wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID,
                wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self._api, name)
            function.argtypes = arguments
            function.restype = result
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags
            self._check(self._api.SetInformationJobObject(
                self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def _check(self, success: int) -> None:
        if not success:
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def start(self, command: Sequence[str], environment: dict[str, str], log) -> _JobProcess:
        import msvcrt
        from ctypes import wintypes

        ctypes = self._ctypes

        class StartupInfo(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
                ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
                *[(name, wintypes.DWORD) for name in ("x", "y", "x_size", "y_size",
                    "x_chars", "y_chars", "fill", "flags")],
                ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
                ("reserved_data", wintypes.LPVOID), ("stdin", wintypes.HANDLE),
                ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]

        class ExtendedStartupInfo(ctypes.Structure):
            _fields_ = [("startup", StartupInfo), ("attributes", wintypes.LPVOID)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                        ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]

        attribute_size = ctypes.c_size_t()
        self._api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
        attributes = ctypes.create_string_buffer(attribute_size.value)
        self._check(self._api.InitializeProcThreadAttributeList(
            attributes, 2, 0, ctypes.byref(attribute_size)))
        info = ProcessInfo()
        log_handle = msvcrt.get_osfhandle(log.fileno())
        previously_inheritable = os.get_handle_inheritable(log_handle)
        try:
            with open(os.devnull, "rb") as null_input:
                input_handle = msvcrt.get_osfhandle(null_input.fileno())
                os.set_handle_inheritable(log_handle, True)
                os.set_handle_inheritable(input_handle, True)
                inherited = (wintypes.HANDLE * 2)(log_handle, input_handle)
                jobs = (wintypes.HANDLE * 1)(self._handle)
                for key, handles in ((0x20002, inherited), (0x2000D, jobs)):
                    self._check(self._api.UpdateProcThreadAttribute(
                        attributes, 0, key, handles, ctypes.sizeof(handles), None, None))
                startup = ExtendedStartupInfo()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                startup.startup.stdin = input_handle
                startup.startup.stdout = startup.startup.stderr = log_handle
                startup.attributes = ctypes.cast(attributes, wintypes.LPVOID)
                command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
                environment_block = ctypes.create_unicode_buffer("\0".join(
                    f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())
                ) + "\0\0")
                # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
                self._check(self._api.CreateProcessW(command[0], command_line, None, None,
                    True, 0x80000 | 0x400 | 0x08000000, environment_block, str(ROOT),
                    ctypes.byref(startup), ctypes.byref(info)))
            process = _JobProcess(self, info.process, info.pid, list(command))
            info.process = None  # Ownership transferred to the process object.
            return process
        finally:
            os.set_handle_inheritable(log_handle, previously_inheritable)
            self._api.DeleteProcThreadAttributeList(attributes)
            if info.thread:
                self._api.CloseHandle(info.thread)
            if info.process:
                self._api.CloseHandle(info.process)

    def accounting(self) -> dict[str, int]:
        info = self._accounting_type()
        self._check(self._api.QueryInformationJobObject(
            self._handle, 1, self._ctypes.byref(info), self._ctypes.sizeof(info), None))
        return {"active": info.active, "total": info.total}

    def terminate_and_wait(self, seconds: float = 10) -> dict[str, int]:
        handles = []
        self.exit_evidence = []
        try:
            try:
                handles = self._retain_active_processes()
            finally:
                # Terminate even when evidence collection fails; fail the run closed.
                self._check(self._api.TerminateJobObject(self._handle, 1))
            deadline = time.monotonic() + seconds
            info = self.wait_empty(seconds)
            all_exited = True
            for pid, handle in handles:
                exited = False
                while True:
                    state = self._api.WaitForSingleObject(handle, 50)
                    if state == 0:
                        exited = True
                        break
                    if state != 258:
                        raise self._ctypes.WinError(self._ctypes.get_last_error())
                    if time.monotonic() >= deadline:
                        break
                self.exit_evidence.append({"pid": pid, "exit_confirmed": exited})
                all_exited = all_exited and exited
            return {**info, "handles_exit_confirmed": all_exited}
        finally:
            for _, handle in handles:
                self._api.CloseHandle(handle)

    def _retain_active_processes(self) -> list[tuple[int, int]]:
        from ctypes import wintypes

        ctypes = self._ctypes
        capacity = 32
        while True:
            class ProcessIds(ctypes.Structure):
                _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]

            info = ProcessIds()
            if self._api.QueryInformationJobObject(self._handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                break
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, info.assigned)
        handles = []
        try:
            for pid in info.pids[:info.listed]:
                handle = self._api.OpenProcess(0x100000 | 0x1000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:  # Process exited between enumeration and OpenProcess.
                        continue
                    raise ctypes.WinError(error)
                try:
                    belongs = wintypes.BOOL()
                    self._check(self._api.IsProcessInJob(handle, self._handle, ctypes.byref(belongs)))
                    if belongs.value:
                        handles.append((pid, handle))
                        handle = None
                    # A reused PID outside this job is never acted upon.
                finally:
                    if handle:
                        self._api.CloseHandle(handle)
            return handles
        except BaseException:
            for _, handle in handles:
                self._api.CloseHandle(handle)
            raise

    def wait_empty(self, seconds: float) -> dict[str, int]:
        deadline = time.monotonic() + seconds
        while True:
            info = self.accounting()
            if info["active"] == 0 or time.monotonic() >= deadline:
                return info
            time.sleep(0.02)

    def close(self) -> None:
        if self._handle:
            self._api.CloseHandle(self._handle)
            self._handle = None


class _JobProcess:
    """Wait on the actual native process handle, never a PID lookup."""

    def __init__(self, job: _WindowsJob, handle: int, pid: int, command: list[str]) -> None:
        self.job, self._handle, self.pid, self.command = job, handle, pid, command
        self.returncode = None

    def wait(self, timeout: float) -> int:
        from ctypes import wintypes

        deadline = time.monotonic() + timeout
        while True:
            result = self.job._api.WaitForSingleObject(self._handle, 50)
            if result == 0:
                code = wintypes.DWORD()
                self.job._check(self.job._api.GetExitCodeProcess(self._handle, self.job._ctypes.byref(code)))
                self.returncode = code.value
                return self.returncode
            if result != 258:  # WAIT_TIMEOUT
                raise self.job._ctypes.WinError(self.job._ctypes.get_last_error())
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.command, timeout)

    def close(self) -> None:
        if self._handle:
            self.job._api.CloseHandle(self._handle)
            self._handle = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    if value.get("version") != 1 or not isinstance(value.get("core_batches"), list):
        raise ValueError("Unsupported or invalid regression profile manifest")
    if not value["core_batches"]:
        raise ValueError("The core profile must contain at least one batch")
    for batch in value["core_batches"]:
        if not isinstance(batch, dict) or not isinstance(batch.get("modules"), list):
            raise ValueError("Invalid core regression batch")
        if not batch["modules"] or any(not _valid_module(item) for item in batch["modules"]):
            raise ValueError("Core batches must contain valid, nonempty module lists")
    if not isinstance(value.get("core_order_sha256"), str) or (
        _core_order_sha256(value["core_batches"]) != value["core_order_sha256"].lower()
    ):
        raise ValueError("Core regression module order does not match its reviewed fingerprint")
    profiles = value.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Regression profiles are missing")
    for name, modules in profiles.items():
        if not isinstance(name, str) or not name or not isinstance(modules, list) or not modules:
            raise ValueError("Invalid named regression profile")
        if any(not _valid_module(item) for item in modules):
            raise ValueError(f"Profile {name!r} contains an invalid test module")
    return value


def _core_order_sha256(batches: Sequence[dict[str, Any]]) -> str:
    modules = [module for batch in batches for module in batch["modules"]]
    return hashlib.sha256("\n".join(modules).encode("utf-8")).hexdigest()


def _valid_module(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("tests.unit."):
        return False
    return all(part.isidentifier() for part in value.split("."))


CI_WINDOWS_PROFILES = ("ci-required-windows", "ci-extended-windows", "ci-long-windows")
CI_MODULE_GROUPS = (
    "required_windows_additions", "long_windows_modules", "linux_required",
    "postgres_required", "postgres_extended", "sealed_extended", "operator_extended",
    "nas_operator_owned",
)


def _load_ci_groups() -> dict[str, Any]:
    value = json.loads(CI_GROUPS.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported CI test catalog")
    keys = {"version", "required_windows_profiles", *CI_MODULE_GROUPS}
    if set(value) != keys:
        raise ValueError("CI test catalog has missing or unknown groups")
    for name in keys - {"version"}:
        items = value[name]
        if (not isinstance(items, list) or not items
                or any(not isinstance(item, str) for item in items)
                or len(items) != len(set(items))):
            raise ValueError(f"CI group {name!r} must contain distinct entries")
        if name == "required_windows_profiles":
            if any(not isinstance(item, str) or not item for item in items):
                raise ValueError("Invalid CI Windows profile name")
        else:
            prefix = "tests.integration." if name in {
                "postgres_required", "postgres_extended", "sealed_extended", "operator_extended",
                "nas_operator_owned"
            } else "tests.unit."
            if any(not isinstance(item, str) or not item.startswith(prefix)
                   or not all(part.isidentifier() for part in item.split(".")) for item in items):
                raise ValueError(f"Invalid test module in CI group {name!r}")
    return value


def _ci_membership(manifest: dict[str, Any], catalog: dict[str, Any]) -> dict[str, set[str]]:
    unknown_profiles = set(catalog["required_windows_profiles"]) - set(manifest["profiles"])
    if unknown_profiles:
        raise ValueError(f"Unknown required Windows profiles: {sorted(unknown_profiles)}")
    full_plan = _planned_batches(manifest, "all-local")
    all_windows = {module for batch in full_plan for module in batch["modules"]}
    core = {module for batch in manifest["core_batches"] for module in batch["modules"]}
    required = core | {
        module for name in catalog["required_windows_profiles"]
        for module in manifest["profiles"][name]
    } | set(catalog["required_windows_additions"])
    long = set(catalog["long_windows_modules"])
    if not required <= all_windows or not long <= all_windows or required & long:
        raise ValueError("Required/long Windows modules must be distinct registered modules")
    if not all_windows - required - long:
        raise ValueError("Extended Windows group is empty")
    if set(catalog["linux_required"]) & all_windows:
        raise ValueError("A Linux unit module is already routed through Windows")
    integration_groups = [
        set(catalog[name]) for name in (
            "postgres_required", "postgres_extended", "sealed_extended", "operator_extended",
            "nas_operator_owned"
        )
    ]
    integration = set().union(*integration_groups)
    if sum(map(len, integration_groups)) != len(integration):
        raise ValueError("An integration module is assigned to multiple CI groups")
    return {
        "ci-required-windows": required,
        "ci-extended-windows": all_windows - required - long,
        "ci-long-windows": long,
        "ci-required-linux": set(catalog["linux_required"]),
        "ci-required-postgres": integration_groups[0],
        "ci-extended-postgres": integration_groups[1],
        "ci-sealed-replay": integration_groups[2],
        "ci-linux-operator": integration_groups[3],
        # Requires a real disposable operator job; hosted offline CI cannot run these.
        "nas-operator-owned": integration_groups[4],
    }


def _test_modules_under(directory: str) -> set[str]:
    root = ROOT / "tests" / directory
    return {
        ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for path in root.rglob("test_*.py")
    }


def _catalog_errors(
    manifest: dict[str, Any], catalog: dict[str, Any],
    actual_unit: set[str], actual_integration: set[str],
) -> list[str]:
    membership = _ci_membership(manifest, catalog)
    assigned_unit = set().union(*(modules for name, modules in membership.items()
                                  if name.endswith("windows") or name == "ci-required-linux"))
    assigned_integration = set().union(*(modules for name, modules in membership.items()
                                         if name not in CI_WINDOWS_PROFILES and name != "ci-required-linux"))
    errors = []
    for label, actual, assigned in (
        ("unit", actual_unit, assigned_unit),
        ("integration", actual_integration, assigned_integration),
    ):
        if actual - assigned:
            errors.append(f"Unrouted {label} modules: {sorted(actual - assigned)}")
        if assigned - actual:
            errors.append(f"Missing {label} files named by CI: {sorted(assigned - actual)}")
    return errors


def _check_test_catalog() -> int:
    manifest = _load_manifest()
    catalog = _load_ci_groups()
    unit = _test_modules_under("unit")
    integration = _test_modules_under("integration")
    errors = _catalog_errors(manifest, catalog, unit, integration)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"CI test catalog passed: unit={len(unit)} integration={len(integration)}")
    return 0


def _unit_test_modules(paths: Sequence[str | Path]) -> set[str]:
    modules = set()
    for value in paths:
        normalized = str(value).replace("\\", "/").strip("/")
        parts = normalized.split("/")
        if (len(parts) != 3 or parts[:2] != ["tests", "unit"]
                or not parts[2].startswith("test_") or not parts[2].endswith(".py")):
            continue
        module = f"tests.unit.{parts[2][:-3]}"
        if _valid_module(module):
            modules.add(module)
    return modules


def _unregistered_new_test_modules(
    base_paths: Sequence[str | Path], current_paths: Sequence[str | Path],
    manifest: dict[str, Any],
) -> list[str]:
    base_modules = _unit_test_modules(base_paths)
    current_modules = _unit_test_modules(current_paths)
    registered = {
        module
        for batch in manifest["core_batches"]
        for module in batch["modules"]
    }
    registered.update(
        module for modules in manifest["profiles"].values() for module in modules
    )
    return sorted((current_modules - base_modules) - registered)


def _git_unit_test_modules(revision: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", revision, "--", "tests/unit"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited with {result.returncode}"
        raise ValueError(f"Could not inspect regression base {revision!r}: {detail}")
    return result.stdout.splitlines()


def _check_new_test_module_coverage(base_ref: str, fallback_ref: str = "") -> int:
    catalog_status = _check_test_catalog()
    if catalog_status:
        return catalog_status
    base_ref = base_ref.strip()
    if not base_ref or (set(base_ref) == {"0"}):
        base_ref = fallback_ref.strip()
    if not base_ref:
        raise ValueError("A valid base ref or fallback ref is required for module coverage")
    manifest = _load_manifest()
    try:
        base_paths = _git_unit_test_modules(base_ref)
    except ValueError:
        if not fallback_ref.strip() or fallback_ref.strip() == base_ref:
            raise
        print(
            f"Regression base {base_ref!r} is unavailable; using {fallback_ref.strip()!r}.",
            file=sys.stderr,
        )
        base_paths = _git_unit_test_modules(fallback_ref.strip())
    current_paths = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests" / "unit").glob("test_*.py")
    ]
    added_modules = sorted(
        _unit_test_modules(current_paths) - _unit_test_modules(base_paths)
    )
    unregistered = _unregistered_new_test_modules(base_paths, current_paths, manifest)
    if unregistered:
        print(
            "New unit test modules missing from tests/regression_profiles.json:\n"
            + "\n".join(f"  - {module}" for module in unregistered),
            file=sys.stderr,
        )
        return 1
    print(
        f"Regression module coverage passed: {len(added_modules)} new unit test module(s); "
        "all are registered."
    )
    return 0


def _available_profiles(manifest: dict[str, Any]) -> list[str]:
    return ["core", *manifest["profiles"], "all-local", *CI_WINDOWS_PROFILES]


def _planned_batches(manifest: dict[str, Any], profile: str) -> list[dict[str, Any]]:
    available = _available_profiles(manifest)
    if profile not in available:
        raise ValueError(f"Unknown profile {profile!r}; choose from {', '.join(available)}")

    if profile in CI_WINDOWS_PROFILES:
        members = _ci_membership(manifest, _load_ci_groups())[profile]
        selected = []
        for batch in _planned_batches(manifest, "all-local"):
            modules = [module for module in batch["modules"] if module in members]
            if modules:
                selected.append({**batch, "modules": modules})
        if not selected:
            raise ValueError(f"CI profile {profile!r} has no runnable tests")
        return selected

    batches = [
        {"name": str(batch.get("name", f"core-{index + 1}")),
         "modules": list(batch["modules"]), "isolate_modules": False}
        for index, batch in enumerate(manifest["core_batches"])
    ] if profile in {"core", "all-local"} else []
    if profile == "core":
        return batches

    selected = list(manifest["profiles"].items()) if profile == "all-local" else [
        (profile, manifest["profiles"][profile]),
    ]
    already_in_core = {
        module for batch in manifest["core_batches"] for module in batch["modules"]
    } if profile == "all-local" else set()
    seen: set[str] = set()
    for name, modules in selected:
        additions = [module for module in modules if module not in already_in_core and module not in seen]
        seen.update(additions)
        if additions:
            batches.append({"name": name, "modules": additions, "isolate_modules": True})
    return batches


def _source_identity() -> dict[str, Any]:
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    try:
        spec = importlib.util.find_spec("kiwoom_monitor")
        origin = spec.origin if spec else None
        locations = list(spec.submodule_search_locations or ()) if spec else []
    except (ImportError, ValueError) as error:
        return {"verified": False, "origin": None, "error": str(error)}

    candidates = [origin] if origin else locations
    resolved: list[str] = []
    for candidate in candidates:
        try:
            resolved.append(str(Path(candidate).resolve()))
        except OSError:
            continue
    verified = any(_is_within(Path(candidate), SOURCE_ROOT) for candidate in resolved)
    return {"verified": verified, "origin": resolved[0] if resolved else None}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _worker(output_path: Path, modules: Sequence[str]) -> int:
    """Load and run a batch, then persist unittest's structured TestResult."""
    sys.path[:0] = [str(SOURCE_ROOT), str(ROOT), str(ROOT / "tests" / "unit")]
    source = _source_identity()
    result_document: dict[str, Any] = {
        "started_at": _utc_now(), "modules": list(modules),
        "test_count": 0, "planned_test_count": 0, "module_test_counts": [],
        "zero_test_modules": [], "failures": [], "errors": [], "skipped": [],
        "expected_failures": [], "unexpected_successes": [],
        "source": source, "status": "failed",
    }
    try:
        if not source["verified"]:
            raise RuntimeError(f"kiwoom_monitor did not resolve to this worktree src: {source}")
        loaded = []
        for module_name in modules:
            module = importlib.import_module(module_name)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            count = suite.countTestCases()
            result_document["module_test_counts"].append(
                {"module": module_name, "test_count": count},
            )
            if count == 0:
                result_document["zero_test_modules"].append(module_name)
            result_document["planned_test_count"] += count
            loaded.append(suite)
        suite = unittest.TestSuite(loaded)
        if suite.countTestCases() == 0:
            result_document["status"] = "incomplete"
            result_document["load_error"] = "No tests were discovered for this batch"
            return _write_worker_result(output_path, result_document, 2)

        test_result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        has_unexpected_success = bool(test_result.unexpectedSuccesses)
        result_document.update({
            "test_count": test_result.testsRun,
            "failures": [_result_item(test, error) for test, error in test_result.failures],
            "errors": [_result_item(test, error) for test, error in test_result.errors],
            "skipped": [_result_item(test, reason) for test, reason in test_result.skipped],
            "expected_failures": [test.id() for test, _ in test_result.expectedFailures],
            "unexpected_successes": [test.id() for test in test_result.unexpectedSuccesses],
            "status": "passed" if test_result.wasSuccessful() and not has_unexpected_success else "failed",
        })
        if result_document["zero_test_modules"] or (
            test_result.testsRun != result_document["planned_test_count"]
        ):
            result_document["status"] = "incomplete"
            result_document["load_error"] = (
                "One or more requested modules had no tests, or planned and executed counts differed"
            )
        if result_document["skipped"] or result_document["expected_failures"]:
            result_document["status"] = "incomplete"
        return _write_worker_result(
            output_path, result_document,
            0 if result_document["status"] == "passed" else 1,
        )
    except BaseException as error:
        result_document["status"] = "incomplete" if isinstance(error, KeyboardInterrupt) else "failed"
        result_document["load_error"] = f"{type(error).__name__}: {error}"
        return _write_worker_result(output_path, result_document, 2)


def _result_item(test: unittest.case.TestCase, detail: str) -> dict[str, str]:
    return {"test": test.id(), "detail": detail}


def _write_worker_result(path: Path, value: dict[str, Any], exit_code: int) -> int:
    value["finished_at"] = _utc_now()
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exit_code


def _discovery_evidence_error(
    worker_result: dict[str, Any], requested_modules: Sequence[str], executed_tests: int,
) -> str | None:
    counts = worker_result.get("module_test_counts")
    if not isinstance(counts, list) or any(not isinstance(item, dict) for item in counts):
        return "Worker per-module discovery counts are missing or invalid"
    if [item.get("module") for item in counts] != list(requested_modules):
        return "Worker per-module discovery order does not match the requested modules"
    values = [item.get("test_count") for item in counts]
    if any(type(value) is not int or value < 1 for value in values):
        return "Every requested module must discover at least one test"
    if worker_result.get("zero_test_modules") != []:
        return "Worker reports modules with no discovered tests"
    planned = worker_result.get("planned_test_count")
    if type(planned) is not int or planned != sum(values) or planned != executed_tests:
        return "Planned, per-module, and executed test counts do not agree"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _code_tree_identity() -> dict[str, Any]:
    """Fingerprint the actual executable/test tree, normalizing text newlines only."""
    normalized = hashlib.sha256()
    raw = hashlib.sha256()
    count = 0
    text_suffixes = {
        ".py", ".json", ".jsonl", ".yml", ".yaml", ".sh", ".ps1",
        ".bat", ".cmd", ".txt", ".md", ".lock", ".toml", ".cfg",
        ".ini", ".dockerfile", ".sql", ".html", ".css", ".svg",
        ".csv", ".tsv", ".xml",
    }
    paths = [p for relative in ("src", "scripts", "tests", ".github/workflows")
             for p in (ROOT / relative).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths.extend(ROOT / name for name in ("requirements.lock.txt", "pyproject.toml")
                 if (ROOT / name).is_file())
    for path in sorted(paths):
        name = path.relative_to(ROOT).as_posix().encode("utf-8")
        data = path.read_bytes()
        canonical = (data.replace(b"\r\n", b"\n")
                     if path.name == ".gitkeep" or path.suffix.lower() in text_suffixes else data)
        normalized.update(name + b"\0" + hashlib.sha256(canonical).digest())
        raw.update(name + b"\0" + hashlib.sha256(data).digest())
        count += 1
    return {"files": count, "lf_sha256": normalized.hexdigest(), "raw_sha256": raw.hexdigest()}


def _git_metadata() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(ROOT), *args], check=True, capture_output=True,
                text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip()

    commit = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    return {"commit": commit, "working_tree_dirty": bool(dirty) if dirty is not None else None}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="profile declared in tests/regression_profiles.json")
    parser.add_argument("--output", type=Path, help="new or not-yet-created directory for this run")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="print a profile plan without running tests")
    parser.add_argument("--check-new-test-modules", action="store_true",
                        help="check all test routes, then compare new unit modules to a base ref")
    parser.add_argument("--check-test-catalog", action="store_true",
                        help="fail on any unrouted or nonexistent unit/integration test module")
    parser.add_argument("--base-ref", default=os.environ.get("REGRESSION_BASE_REF", ""),
                        help="revision whose tests/unit inventory is the comparison baseline")
    parser.add_argument("--fallback-ref", default=os.environ.get("REGRESSION_FALLBACK_REF", ""),
                        help="fallback comparison revision for a new branch or manual run")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_result-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_modules", nargs="*", default=[], help=argparse.SUPPRESS)
    return parser


def _next_run_path() -> Path:
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    return ROOT / "tmp" / "regression" / name


def _run_process(
    *, modules: Sequence[str], result_path: Path, log_path: Path, timeout_seconds: int,
) -> dict[str, Any]:
    environment = os.environ.copy()
    pythonpath = [str(SOURCE_ROOT), str(ROOT), str(ROOT / "tests" / "unit")]
    inherited = environment.get("PYTHONPATH", "")
    if inherited:
        pythonpath.append(inherited)
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONUTF8"] = "1"
    environment["TEMP"] = str(result_path.parent / "temp")
    environment["TMP"] = environment["TEMP"]
    environment["TMPDIR"] = environment["TEMP"]
    Path(environment["TEMP"]).mkdir(parents=True, exist_ok=True)

    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--_worker",
               "--_result-path", str(result_path), "--_modules", *modules]
    started_at = _utc_now()
    started_clock = time.monotonic()
    timed_out = False
    interrupted = False
    process = None
    job = None
    exit_code = None
    tree_exit_confirmed = False
    worker_exit_confirmed = False
    leaked_descendants = False
    process_count = 0
    execution_error = None
    try:
        if result_path.exists():
            raise FileExistsError(f"Refusing to reuse a worker result: {result_path}")
        job = _WindowsJob()
        with log_path.open("wb") as log:
            process = job.start(command, environment, log)
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            except KeyboardInterrupt:
                interrupted = True
    except (OSError, ValueError) as error:
        execution_error = f"{type(error).__name__}: {error}"
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            if job is not None:
                before = job.accounting()
                if not timed_out and not interrupted and exit_code is not None and before["active"]:
                    # Exit signalling can precede final job accounting (venv/console helpers).
                    before = job.wait_empty(1)
                leaked_descendants = not timed_out and not interrupted and exit_code is not None and before["active"] > 0
                after = job.terminate_and_wait()
                process_count = after["total"]
                tree_exit_confirmed = after["active"] == 0 and after["handles_exit_confirmed"]
            if process is not None:
                exit_code = process.wait(timeout=10)
                worker_exit_confirmed = True
            else:
                worker_exit_confirmed = tree_exit_confirmed = True  # Launch failed before a worker existed.
        except (OSError, subprocess.TimeoutExpired) as error:
            execution_error = f"Cleanup {type(error).__name__}: {error}"
        finally:
            if job is not None:
                job.close()  # Last-handle close also kills the tree if cleanup itself failed.
            if process is not None:
                process.close()

    try:
        worker_result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(worker_result, dict):
            raise ValueError("Worker result must be an object")
        if worker_result.get("modules") != list(modules):
            raise ValueError("Worker result does not identify the requested modules")
        count = worker_result.get("test_count")
        if type(count) is not int or count < 0:
            raise ValueError("Worker test count is invalid")
        if worker_result.get("status") == "passed" and (
            count == 0 or not isinstance(worker_result.get("source"), dict)
            or worker_result["source"].get("verified") is not True
            or any(worker_result.get(key) != [] for key in (
                "failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
        ):
            raise ValueError("Worker success does not contain complete passing evidence")
        if worker_result.get("status") == "passed":
            discovery_error = _discovery_evidence_error(worker_result, modules, count)
            if discovery_error:
                raise ValueError(discovery_error)
    except (OSError, ValueError) as error:
        worker_result = None
        result_error = f"{type(error).__name__}: {error}"
    else:
        result_error = None

    if timed_out or interrupted or not tree_exit_confirmed or not worker_exit_confirmed or leaked_descendants:
        status = "incomplete"
    elif execution_error:
        status = "failed"
    elif exit_code is not None and exit_code < 0:
        status = "incomplete"
    elif worker_result is None:
        status = "failed" if exit_code != 0 else "incomplete"
    elif worker_result.get("status") == "failed":
        status = "failed"
    elif worker_result.get("status") == "incomplete":
        status = "incomplete"
    elif worker_result.get("status") != "passed" or exit_code != 0:
        status = "failed"
    else:
        status = "passed"

    return {
        "status": status, "started_at": started_at, "finished_at": _utc_now(),
        "duration_seconds": time.monotonic() - started_clock,
        "timeout": timed_out, "interrupted": interrupted,
        "worker_exit_confirmed": worker_exit_confirmed,
        "process_tree_exit_confirmed": tree_exit_confirmed,
        "process_tree_count": process_count, "worker_pid": process.pid if process else None,
        "process_exit_evidence": getattr(job, "exit_evidence", []),
        "leaked_descendants": leaked_descendants, "execution_error": execution_error,
        "exit_code": exit_code,
        "log": str(log_path), "result": str(result_path), "result_error": result_error,
        "test_count": worker_result.get("test_count", 0) if worker_result else 0,
        "planned_test_count": worker_result.get("planned_test_count", 0) if worker_result else 0,
        "module_test_counts": worker_result.get("module_test_counts", []) if worker_result else [],
        "zero_test_modules": worker_result.get("zero_test_modules", []) if worker_result else [],
        "failures": worker_result.get("failures", []) if worker_result else [],
        "errors": worker_result.get("errors", []) if worker_result else [],
        "skipped": worker_result.get("skipped", []) if worker_result else [],
        "expected_failures": worker_result.get("expected_failures", []) if worker_result else [],
        "unexpected_successes": worker_result.get("unexpected_successes", []) if worker_result else [],
        "source": worker_result.get("source") if worker_result else None,
        "load_error": worker_result.get("load_error") if worker_result else result_error,
    }


def _execute_profile(profile: str, timeout_seconds: int, output_path: Path) -> int:
    if timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be positive")
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing regression output: {output_path}")

    manifest = _load_manifest()
    batches = _planned_batches(manifest, profile)
    if not batches or any(not batch["modules"] for batch in batches):
        raise ValueError(f"Regression profile {profile!r} has no runnable tests")
    output_path.mkdir(parents=True)
    test_paths = {
        module: ROOT / (module.replace(".", "/") + ".py")
        for batch in batches for module in batch["modules"]
    }
    missing = [str(path) for path in test_paths.values() if not path.is_file()]
    summary: dict[str, Any] = {
        "schema_version": 1, "profile": profile, "status": "incomplete",
        "started_at": _utc_now(), "finished_at": None,
        "repository_root": str(ROOT), "python_executable": sys.executable,
        "python_version": sys.version, "source_root": str(SOURCE_ROOT),
        "git": _git_metadata(),
        "profile_manifest_sha256": _sha256(MANIFEST),
        "ci_catalog_sha256": _sha256(CI_GROUPS),
        "code_tree_start": _code_tree_identity(),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "test_file_sha256": {module: _sha256(path) for module, path in test_paths.items()
                             if path.is_file()},
        "missing_test_files": missing, "timeout_seconds_per_worker": timeout_seconds,
        "planned_batches": [
            {"name": batch["name"], "modules": batch["modules"]}
            for batch in batches
        ], "batch_results": [], "unrun_batches": [],
    }

    if missing:
        summary["status"] = "failed"
        summary["finished_at"] = _utc_now()
        (output_path / "run.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print(json.dumps({"status": "failed", "missing_test_files": missing}, ensure_ascii=False))
        return 1

    workers: list[tuple[str, Sequence[str]]] = []
    for batch in batches:
        if batch["isolate_modules"]:
            workers.extend((f"{batch['name']}:{module}", [module]) for module in batch["modules"])
        else:
            workers.append((batch["name"], batch["modules"]))

    previous_failed = False
    interrupted = False
    summary["unrun_batches"] = [name for name, _ in workers]
    (output_path / "run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    try:
        for index, (name, modules) in enumerate(workers, start=1):
            result_path = output_path / f"worker-{index:03}.json"
            log_path = output_path / f"worker-{index:03}.log"
            result = _run_process(modules=modules, result_path=result_path,
                                  log_path=log_path, timeout_seconds=timeout_seconds)
            result["name"] = name
            result["modules"] = list(modules)
            summary["batch_results"].append(result)
            summary["unrun_batches"] = [value[0] for value in workers[index:]]
            (output_path / "run.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            if (result["status"] == "incomplete" or not result.get("worker_exit_confirmed", False)
                    or not result.get("process_tree_exit_confirmed", False)):
                summary["unrun_batches"] = [value[0] for value in workers[index:]]
                interrupted = bool(result.get("interrupted"))
                break
            previous_failed = previous_failed or result["status"] == "failed"
    except KeyboardInterrupt:
        interrupted = True
        summary["unrun_batches"] = [value[0] for value in workers[len(summary["batch_results"]):]]

    statuses = [item["status"] for item in summary["batch_results"]]
    if interrupted or "incomplete" in statuses or summary["unrun_batches"]:
        summary["status"] = "incomplete"
    elif previous_failed or "failed" in statuses:
        summary["status"] = "failed"
    elif len(summary["batch_results"]) == len(workers):
        summary["status"] = "passed"
    else:
        summary["status"] = "incomplete"
    summary["interrupted"] = interrupted
    summary["code_tree_end"] = _code_tree_identity()
    summary["source_modified_during_run"] = (
        summary["code_tree_start"]["lf_sha256"] != summary["code_tree_end"]["lf_sha256"]
    )
    if summary["source_modified_during_run"]:
        summary["status"] = "incomplete"
    summary["finished_at"] = _utc_now()
    (output_path / "run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "status": summary["status"], "profile": profile,
        "completed_workers": len(summary["batch_results"]),
        "unrun_workers": len(summary["unrun_batches"]),
        "tests_run": sum(item.get("test_count", 0) for item in summary["batch_results"]),
        "output": str(output_path),
    }, ensure_ascii=False))
    return 130 if interrupted else 0 if summary["status"] == "passed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args._worker:
        if args._result_path is None or not args._modules:
            parser.error("worker mode requires a result path and at least one module")
        return _worker(args._result_path.resolve(), args._modules)
    if args._result_path is not None or args._modules:
        parser.error("worker-only options are not available in normal mode")
    if args.check_test_catalog:
        if args.check_new_test_modules or args.profile is not None or args.list_only or args.output is not None:
            parser.error("catalog check cannot be combined with execution or another check")
        try:
            return _check_test_catalog()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Regression runner: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
    if args.check_new_test_modules:
        if args.profile is not None or args.list_only or args.output is not None:
            parser.error("module coverage check cannot be combined with profile/list/output options")
        try:
            return _check_new_test_module_coverage(args.base_ref, args.fallback_ref)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Regression runner: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
    if args.base_ref or args.fallback_ref:
        parser.error("base-ref options require --check-new-test-modules")
    if args.list_only == (args.profile is not None):
        parser.error("choose exactly one of --list or --profile")
    try:
        manifest = _load_manifest()
        if args.list_only:
            plan = {
                profile: _planned_batches(manifest, profile)
                for profile in _available_profiles(manifest)
            }
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        output_path = args.output or _next_run_path()
        return _execute_profile(args.profile, args.timeout_seconds, output_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Regression runner: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
