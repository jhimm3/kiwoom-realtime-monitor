"""Windows test-only registry ownership for regression workers and Qt children.

The external runner owns one UUID key. Each Python process explicitly remaps
HKCU before Qt imports; this mapping is not inherited by a child. Native
UserScope writes are isolated. HKLM/OS/application fallback reads remain outside
this scope; no native SystemScope writer is introduced by this test support.
Mappings and handles live through interpreter/Qt teardown. Only the external
owner deletes its key, after the Windows Job confirms that every process exited.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


SCOPE_ENV = "KIWOOM_REGRESSION_REGISTRY_SCOPE"
HELPER_ENV = "KIWOOM_REGRESSION_REGISTRY_HELPER"
_PATH = re.compile(r"Software\\KiwoomMonitorRegression-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_KEEPALIVE = "_kiwoom_regression_registry_override"


def _validate(description: dict) -> None:
    if (not isinstance(description, dict) or set(description) != {"key", "nonce", "temporary_directory", "owner_pid"}
            or not isinstance(description["key"], str) or not _PATH.fullmatch(description["key"])
            or not isinstance(description["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", description["nonce"])
            or not isinstance(description["temporary_directory"], str)
            or type(description["owner_pid"]) is not int or description["owner_pid"] <= 0):
        raise ValueError("Invalid regression registry ownership")
    directory = Path(description["temporary_directory"])
    if (not directory.is_absolute() or directory.is_symlink() or not directory.is_dir()
            or directory.resolve() != directory or directory.parent.name != "kiwoom-regression"
            or not re.fullmatch(r"[0-9a-f]{32}", directory.name)):
        raise ValueError("Registry owner must identify its existing worker temporary directory")


def _api():
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.RegOverridePredefKey.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.RegOverridePredefKey.restype = wintypes.LONG
    api.RegCloseKey.argtypes = [wintypes.HANDLE]
    api.RegCloseKey.restype = wintypes.LONG
    return api


def _override(api, key, handle) -> None:
    # Predefined handles are sign-extended on 64-bit Windows.
    value = ctypes.c_ssize_t(ctypes.c_int32(int(key)).value).value
    code = api.RegOverridePredefKey(value, handle.value if handle is not None else None)
    if code:
        raise OSError(code, "RegOverridePredefKey(test-owned)")


def _close(handle) -> None:
    code = _api().RegCloseKey(handle)
    if code:
        raise OSError(code, "RegCloseKey(test-owned)")


def _open(parent, path):
    import winreg
    from ctypes import wintypes
    # Detach transfers native ownership; retain ctypes HANDLE until explicit
    # controller close or process exit, never construct a PyHKEY from raw data.
    return wintypes.HANDLE(winreg.OpenKey(parent, path, 0, winreg.KEY_ALL_ACCESS).Detach())


def _check_marker(handle, description: dict) -> None:
    import winreg
    actual, kind = winreg.QueryValueEx(handle, "regression_owner")
    if kind != winreg.REG_SZ or actual != json.dumps(description, sort_keys=True):
        raise ValueError("Regression registry owner marker mismatch")


class RegistryScope:
    def __init__(self, description: dict, root, *, owner: bool):
        self.description, self._root_handle, self.owner = description, root, owner
        self.root = root.value
        self.owner_pid = os.getpid()

    @classmethod
    def create(cls, directory: Path, *, result_path: Path):
        import winreg
        description = {"key": "Software\\KiwoomMonitorRegression-" + str(uuid.uuid4()),
                       "nonce": uuid.uuid4().hex, "temporary_directory": str(directory.resolve()),
                       "owner_pid": os.getpid()}
        _validate(description)
        # Preserve the exact generated path before the first registry operation,
        # including any setup failure before the owner marker can be written.
        intent = {"scope": description, "worker_result": str(result_path)}
        for journal_path in (result_path.with_name(result_path.name + ".registry-owner.json"),
                             directory / "registry-owner.json"):
            with journal_path.open("x", encoding="utf-8") as journal:
                json.dump(intent, journal, sort_keys=True)
                journal.flush()
                os.fsync(journal.fileno())
        # RegCreateKeyEx disposition prevents taking ownership of an existing key.
        from ctypes import wintypes
        api = _api()
        api.RegCreateKeyExW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
            wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.DWORD)]
        api.RegCreateKeyExW.restype = wintypes.LONG
        handle, disposition = wintypes.HANDLE(), wintypes.DWORD()
        hkcu = ctypes.c_ssize_t(ctypes.c_int32(int(winreg.HKEY_CURRENT_USER)).value).value
        # Qt creates nonvolatile descendants, so its owned root is nonvolatile too.
        code = api.RegCreateKeyExW(hkcu, description["key"], 0, None, 0,
                                  winreg.KEY_ALL_ACCESS, None, ctypes.byref(handle), ctypes.byref(disposition))
        if code:
            raise OSError(code, "RegCreateKeyExW(exact regression UUID)")
        try:
            if disposition.value != 1:
                raise FileExistsError("Refusing existing regression registry root")
            scope = cls(description, handle, owner=True)
            winreg.SetValueEx(scope.root, "regression_owner", 0, winreg.REG_SZ, json.dumps(description, sort_keys=True))
            for name in ("User",):
                with winreg.CreateKeyEx(scope.root, name, 0, winreg.KEY_ALL_ACCESS):
                    pass
            return scope
        except BaseException:
            _close(handle)
            if disposition.value == 1:
                # The native creation disposition is the ownership proof when
                # marker setup itself fails; never remove an existing key.
                api.RegDeleteTreeW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
                api.RegDeleteTreeW.restype = wintypes.LONG
                cleanup = api.RegDeleteTreeW(hkcu, description["key"])
                if cleanup:
                    raise OSError(cleanup, "Registry setup failed and exact UUID cleanup failed")
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, description["key"]):
                        raise RuntimeError("Registry setup failure left its owned UUID key")
                except FileNotFoundError:
                    pass
            raise

    def prepare_environment(self, environment: dict, directory: Path) -> None:
        environment[SCOPE_ENV] = json.dumps(self.description, sort_keys=True)
        environment[HELPER_ENV] = str(Path(__file__).resolve())
        bootstrap = directory / "python-bootstrap"
        bootstrap.mkdir()
        # sitecustomize errors normally do not abort Python: fail closed explicitly.
        code = ("import os, runpy, traceback\ntry:\n"
                "    runpy.run_path(os.environ['" + HELPER_ENV + "'])['join_from_environment']()\n"
                "except BaseException:\n    traceback.print_exc()\n    os._exit(86)\n")
        (bootstrap / "sitecustomize.py").write_text(code, encoding="utf-8")
        environment["PYTHONPATH"] = str(bootstrap) + os.pathsep + environment.get("PYTHONPATH", "")

    def finish(self, *, processes_exited: bool) -> dict:
        result = {"key": self.description["key"], "owner_pid": self.description["owner_pid"],
                  "scope": ["HKCU"], "HKLM_fallback": "outside_scope",
                  "state": "borrowed" if not self.owner else "retained"}
        if not self.owner:
            return result
        if os.getpid() != self.owner_pid or self.owner_pid != self.description["owner_pid"]:
            raise ValueError("Only the creating runner may delete its registry scope")
        try:
            _validate(self.description)
            _check_marker(self.root, self.description)
            if not processes_exited:
                return result
            result["contents"] = self.metadata()
            import winreg
            api = _api()
            from ctypes import wintypes
            api.RegDeleteTreeW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
            api.RegDeleteTreeW.restype = wintypes.LONG
            hkcu = ctypes.c_ssize_t(ctypes.c_int32(int(winreg.HKEY_CURRENT_USER)).value).value
            _close(self._root_handle)
            self.root = None
            self._root_handle = None
            code = api.RegDeleteTreeW(hkcu, self.description["key"])
            if code:
                raise OSError(code, "RegDeleteTreeW(exact regression UUID)")
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.description["key"]):
                    raise RuntimeError("Owned regression registry key remained after deletion")
            except FileNotFoundError:
                result["state"] = "removed"
            return result
        finally:
            if self.root is not None:
                _close(self._root_handle)
                self.root = None
                self._root_handle = None

    def metadata(self) -> dict:
        """Counts and digest only; enumerate only this authenticated owned subtree."""
        import winreg
        digest = hashlib.sha256()
        keys = values = 0
        def visit(handle, relative):
            nonlocal keys, values
            keys += 1
            subkeys, value_count, _ = winreg.QueryInfoKey(handle)
            for index in range(value_count):
                name, value, kind = winreg.EnumValue(handle, index)
                data = repr((relative, name, kind, value)).encode("utf-8")
                digest.update(hashlib.sha256(data).digest())
                values += 1
            for index in range(subkeys):
                name = winreg.EnumKey(handle, index)
                with winreg.OpenKey(handle, name) as child:
                    visit(child, relative + "\\" + name)
        visit(self.root, "")
        return {"keys": keys, "values": values, "sha256": digest.hexdigest()}


def join_from_environment():
    if os.name != "nt":
        return None
    description = json.loads(os.environ[SCOPE_ENV])
    _validate(description)
    if Path(os.environ[HELPER_ENV]).resolve() != Path(__file__).resolve():
        raise ValueError("Regression registry helper source mismatch")
    existing = getattr(sys, _KEEPALIVE, None)
    if existing is not None:
        if existing.description != description:
            raise ValueError("A process cannot change its regression registry scope")
        return existing
    import winreg
    root = _open(winreg.HKEY_CURRENT_USER, description["key"])
    handles = []
    try:
        _check_marker(root.value, description)
        for name in ("User",):
            handles.append(_open(root.value, name))
        api = _api()
        _override(api, winreg.HKEY_CURRENT_USER, handles[0])
        scope = RegistryScope(description, root, owner=False)
        scope.override_handles, scope.override_api = handles, api
        # Keep through all Python/Qt destructors. OS process exit closes handles.
        setattr(sys, _KEEPALIVE, scope)
        return scope
    except BaseException:
        for handle in handles:
            _close(handle)
        _close(root)
        raise


def prepare_scope(environment: dict, directory: Path, *, result_path: Path):
    if os.name != "nt":
        return None
    scope = (join_from_environment() if SCOPE_ENV in environment
             else RegistryScope.create(directory, result_path=result_path))
    try:
        scope.prepare_environment(environment, directory)
    except BaseException:
        # No worker exists yet. A nested borrower must leave its parent's scope.
        if scope.owner:
            scope.finish(processes_exited=True)
        raise
    return scope
