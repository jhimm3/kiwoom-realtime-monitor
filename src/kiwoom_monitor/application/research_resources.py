"""PC 연구의 실제 메모리·계산 batch 운영 한도. 과학 입력과는 분리한다."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable


class ResearchResourceBlocked(RuntimeError):
    """Retryable operating failure; never a terminal scientific trial result."""


@dataclass(frozen=True)
class ResearchResourceLimits:
    memory_mb: int = 512
    cpu_duty_percent: int = 50
    batch_seconds: float = 0.05

    def __post_init__(self):
        if not 128 <= self.memory_mb <= 4096:
            raise ValueError("research memory budget must be between 128MB and 4096MB")
        if not 10 <= self.cpu_duty_percent <= 100:
            raise ValueError("research CPU duty must be between 10 and 100 percent")
        if not 0.01 <= self.batch_seconds <= 0.2:
            raise ValueError("research batch must be between 0.01 and 0.2 seconds")


@lru_cache(maxsize=1)
def _windows_rss_api():
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'peak_pool', 'pool',
                                               'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    return Counters, kernel, psapi


def current_rss_bytes() -> int:
    if sys.platform == 'win32':
        Counters, kernel, psapi = _windows_rss_api()
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), 'GetProcessMemoryInfo failed')
        return int(counters.working)
    if sys.platform.startswith('linux'):
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    raise OSError('RSS measurement unsupported on this platform')


class ResearchResourceGuard:
    def __init__(self, limits: ResearchResourceLimits, *, rss: Callable[[], int] = current_rss_bytes,
                 monotonic: Callable[[], float] = time.monotonic,
                 process_clock: Callable[[], float] = time.process_time,
                 sleeper: Callable[[float], None] = time.sleep):
        self.limits, self.rss, self.monotonic, self.sleeper = limits, rss, monotonic, sleeper
        self.process_clock = process_clock
        self.max_rss_bytes = 0
        self.yield_count = 0
        self._batch_started = monotonic()
        self._cpu_started = process_clock()

    def check(self):
        try:
            used = self.rss()
        except OSError as exc:
            raise ResearchResourceBlocked('rss_measurement_unavailable') from exc
        self.max_rss_bytes = max(self.max_rss_bytes, used)
        if used > self.limits.memory_mb * 1024 * 1024:
            raise ResearchResourceBlocked('memory_rss_limit_exceeded')

    def checkpoint(self):
        self.check()
        now = self.monotonic()
        active = max(0.0, now - self._batch_started)
        if active >= self.limits.batch_seconds:
            cpu_active = max(0.0, self.process_clock() - self._cpu_started)
            pause = max(0.0, cpu_active * 100 / self.limits.cpu_duty_percent - active)
            # Bound cancellation/lease latency even after an unusually long operation.
            self.sleeper(min(pause, 0.2))
            self.yield_count += 1
            self._batch_started = self.monotonic()
            self._cpu_started = self.process_clock()

    def preflight(self, encoded_bytes: int):
        """Estimate decoded input before loading; actual RSS is checked again during loading/run."""
        self.check()
        if self.max_rss_bytes + encoded_bytes * 8 > self.limits.memory_mb * 1024 * 1024:
            raise ResearchResourceBlocked('input_memory_estimate_exceeds_limit')
        return encoded_bytes
