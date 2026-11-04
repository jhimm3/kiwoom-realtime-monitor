from __future__ import annotations

import ctypes
import re
import uuid
from ctypes import wintypes


class WindowsShutdownEvent:
    """One owned start's non-inherited Local event; no settings or process owner."""

    def __init__(self, kernel32, handle, name: str) -> None:
        self._kernel32, self._handle, self.name = kernel32, handle, name

    @staticmethod
    def _kernel():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        return kernel32

    @classmethod
    def create(cls):
        kernel32 = cls._kernel()
        name = "Local\\KiwoomMonitor-CentralStop-" + uuid.uuid4().hex
        ctypes.set_last_error(0)
        handle = kernel32.CreateEventW(None, True, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        event = cls(kernel32, handle, name)
        if ctypes.get_last_error() == 183:
            event.close()
            raise RuntimeError("owned server shutdown event already exists")
        return event

    @classmethod
    def open(cls, name: str):
        if re.fullmatch(r"Local\\KiwoomMonitor-CentralStop-[0-9a-f]{32}", name) is None:
            raise ValueError("invalid owned server shutdown event")
        kernel32 = cls._kernel()
        handle = kernel32.OpenEventW(0x00100000, False, name)  # SYNCHRONIZE only.
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return cls(kernel32, handle, name)

    def signal(self) -> None:
        if not self._kernel32.SetEvent(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, milliseconds: int) -> bool:
        result = self._kernel32.WaitForSingleObject(self._handle, milliseconds)
        if result == 0:
            return True
        if result == 0x00000102:
            return False
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        raise RuntimeError(f"unexpected owned server event wait: {result}")

    def close(self) -> None:
        if self._handle is not None:
            if not self._kernel32.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None
