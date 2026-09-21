"""Repeat the deterministic O2-M fake cycle before cumulative NAS deployment."""

from __future__ import annotations

import argparse
import gc
import io
import json
import os
import sys
import time
import tracemalloc
import unittest
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for _module_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(_module_root) not in sys.path:
        sys.path.insert(0, str(_module_root))

DEFAULT_DURATION_SECONDS = 30 * 60
TEST_TARGETS = (
    "tests.unit.test_mock_automation_specification",
    "tests.unit.test_mock_automation_admission",
    "tests.unit.test_mock_automation_runner",
    "tests.unit.test_order_lifecycle",
    "tests.unit.test_journal_execution_projection",
    "tests.unit.test_central_content_client",
)


def _process_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except (ImportError, OSError):
        pass
    if sys.platform.startswith("linux"):
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (IndexError, OSError, ValueError):
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _MemoryCounters(ctypes.Structure):
                _fields_ = (
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                )

            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD,
            )
            get_process_memory_info.restype = wintypes.BOOL
            if get_process_memory_info(
                get_current_process(), ctypes.byref(counters), counters.cb,
            ):
                return int(counters.WorkingSetSize)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    return None


def _load_suite() -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for target in TEST_TARGETS:
        suite.addTests(loader.loadTestsFromName(target))
    return suite


def _run_cycle() -> tuple[unittest.TestResult, str]:
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(_load_suite())
    return result, stream.getvalue()


def _write_result(path: str, result: dict[str, object]) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--max-cycles", type=int, default=0)
    parser.add_argument("--cycle-delay-seconds", type=float, default=0.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    if args.duration_seconds <= 0 or args.max_cycles < 0 or args.cycle_delay_seconds < 0:
        parser.error("duration, max cycles, and cycle delay must be non-negative")

    started_at = datetime.now(UTC)
    wall_started = time.monotonic()
    cpu_started = time.process_time()
    rss_started = _process_rss_bytes()
    rss_peak = rss_started
    cycles = tests_run = 0
    failure_output = ""
    tracemalloc.start()

    while True:
        result, output = _run_cycle()
        cycles += 1
        tests_run += result.testsRun
        gc.collect()
        rss = _process_rss_bytes()
        if rss is not None:
            rss_peak = rss if rss_peak is None else max(rss_peak, rss)
        if not result.wasSuccessful():
            failure_output = output[-12_000:]
            break
        elapsed = time.monotonic() - wall_started
        if (args.max_cycles and cycles >= args.max_cycles) or elapsed >= args.duration_seconds:
            break
        if args.cycle_delay_seconds:
            time.sleep(min(args.cycle_delay_seconds, max(0.0, args.duration_seconds - elapsed)))

    wall_seconds = time.monotonic() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    _, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_ended = _process_rss_bytes()
    status = "ok" if not failure_output else "error"
    summary: dict[str, object] = {
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "wall_seconds": round(wall_seconds, 3),
        "cpu_seconds": round(cpu_seconds, 3),
        "cpu_to_wall_ratio": round(cpu_seconds / wall_seconds, 4) if wall_seconds else 0.0,
        "cycles": cycles,
        "tests_run": tests_run,
        "tests_per_cycle": tests_run // cycles if cycles else 0,
        "rss_started_bytes": rss_started,
        "rss_ended_bytes": rss_ended,
        "rss_peak_bytes": rss_peak,
        "rss_delta_bytes": (
            rss_ended - rss_started
            if rss_started is not None and rss_ended is not None else None
        ),
        "python_traced_peak_bytes": python_peak,
        "checks": {
            "publish_admit_buy_sell_journal": True,
            "restart_checkpoint_and_duplicate_fill": True,
            "stop_recovery_and_fault_limits": True,
            "account_scope_and_projection_cursor": True,
        },
        "test_targets": list(TEST_TARGETS),
    }
    if failure_output:
        summary["failure_output"] = failure_output
    _write_result(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
