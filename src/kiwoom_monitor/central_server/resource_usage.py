from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any


_STARTED_AT = time.time()


def resource_usage(data_path: str | Path, database_size_bytes: int | None = None) -> dict[str, Any]:
    """Return bounded, read-only server/container resource diagnostics."""
    path = Path(data_path)
    try:
        disk = shutil.disk_usage(path)
    except OSError:
        disk = shutil.disk_usage(Path.cwd())
    process_memory = _process_rss_bytes()
    container_current, container_limit = _container_memory_bytes()
    return {
        "process_memory_bytes": process_memory,
        "container_memory_bytes": container_current,
        "container_memory_limit_bytes": container_limit,
        "data_disk_used_bytes": disk.used,
        "data_disk_free_bytes": disk.free,
        "data_disk_total_bytes": disk.total,
        "database_size_bytes": database_size_bytes,
        "uptime_seconds": max(0, int(time.time() - _STARTED_AT)),
        "measured_at": time.time(),
    }


def _process_rss_bytes() -> int | None:
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        return pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError, AttributeError):
        if os.name == "nt":
            return _windows_process_rss_bytes()
        return None


def _windows_process_rss_bytes() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb,
        ):
            return int(counters.WorkingSetSize)
    except (AttributeError, OSError):
        pass
    return None


def _container_memory_bytes() -> tuple[int | None, int | None]:
    # cgroup v2 (current Synology/Container Manager); v1 fallback for older DSM.
    candidates = (
        (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.max")),
        (Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")),
    )
    for current_path, limit_path in candidates:
        try:
            current = int(current_path.read_text(encoding="ascii").strip())
            raw_limit = limit_path.read_text(encoding="ascii").strip()
            limit = None if raw_limit == "max" else int(raw_limit)
            # Some cgroup v1 hosts report an effectively unlimited, enormous value.
            if limit is not None and limit >= 1 << 60:
                limit = None
            return current, limit
        except (OSError, ValueError):
            continue
    return None, None
