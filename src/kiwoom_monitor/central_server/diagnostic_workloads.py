"""Short-lived NAS diagnostic gates, independent of operational settings."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from uuid import uuid4
from pathlib import Path


WORKLOADS = frozenset({
    "minute_backfill", "top20_after_close", "news_jobs", "news_stock_refresh",
    "news_query_set", "news_market_feed", "external_market", "candidate_monitor",
    "historical_news_archive", "minute_query_metadata",
})


def instance_id() -> str:
    """Identify this container process lifetime, not just the host boot."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        process = Path("/proc/1/stat").read_text(encoding="ascii").split()[21]
        return f"{boot}:{process}"
    except (OSError, IndexError):
        return str(os.getpid())


def control_path() -> Path | None:
    value = os.environ.get("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH", "").strip()
    return Path(value) if value else None


def evaluate_diagnostic_control(payload: object, *, now: float | None = None) -> dict[str, object]:
    """Evaluate parent and children from the same immutable file generation."""
    empty = {"enabled": False, "expires_at": None, "owner": None, "session_id": None}
    result: dict[str, object] = {
        "diagnostic_tool": empty, "metrics_capture": dict(empty),
        "paused": frozenset(), "workload_expiries": {}, "expires_at": None,
        "control_revision": 0,
    }
    if (not isinstance(payload, dict) or payload.get("schema") != 1
            or payload.get("instance_id") != instance_id()):
        return result
    try:
        result["control_revision"] = max(0, int(payload.get("control_revision", 0)))
    except (ValueError, TypeError):
        return result
    current_time = time.time() if now is None else now
    parent = payload.get("diagnostic_tool")
    if not isinstance(parent, dict):
        return result
    try:
        parent_expiry = float(parent.get("expires_at", 0))
        monotonic_deadline = parent.get("monotonic_deadline")
        if (parent_expiry <= current_time or
                (monotonic_deadline is not None
                 and float(monotonic_deadline) <= time.monotonic())):
            return result
        parent_status = {
            "enabled": True, "expires_at": parent_expiry,
            "owner": str(parent.get("owner", "manual")),
            "session_id": parent.get("session_id"),
        }
        result["diagnostic_tool"] = parent_status
        result["expires_at"] = parent_expiry
        leases = payload.get("leases")
        if isinstance(leases, dict):
            valid = {name: min(float(expiry), parent_expiry)
                     for name, expiry in leases.items() if name in WORKLOADS}
            expiries = {name: expiry for name, expiry in valid.items()
                        if expiry > current_time}
        else:
            legacy_expiry = min(float(payload.get("expires_at", 0)), parent_expiry)
            values = payload.get("paused", [])
            expiries = ({name: legacy_expiry for name in values if name in WORKLOADS}
                        if legacy_expiry > current_time and isinstance(values, list) else {})
        result["workload_expiries"] = expiries
        result["paused"] = frozenset(expiries)
        capture = payload.get("capture")
        if isinstance(capture, dict):
            capture_expiry = min(float(capture.get("expires_at", 0)), parent_expiry)
            if capture_expiry > current_time:
                result["metrics_capture"] = {
                    "enabled": True, "expires_at": capture_expiry,
                    "owner": str(capture.get("owner", "manual")),
                    "session_id": parent_status["session_id"],
                }
        return result
    except (ValueError, TypeError, AttributeError):
        # Invalid diagnostic state cannot turn on a pause or a sampler.
        return {"diagnostic_tool": empty, "metrics_capture": dict(empty),
                "paused": frozenset(), "workload_expiries": {}, "expires_at": None,
                "control_revision": 0}


def control_snapshot(path: Path | None = None, *, now: float | None = None) -> dict[str, object]:
    path = path if path is not None else control_path()
    if path is None:
        return evaluate_diagnostic_control(None, now=now)
    try:
        return evaluate_diagnostic_control(json.loads(path.read_text(encoding="utf-8")), now=now)
    except (OSError, ValueError, TypeError):
        return evaluate_diagnostic_control(None, now=now)


def diagnostic_tool_status(path: Path | None = None, *, now: float | None = None) -> dict[str, object]:
    return control_snapshot(path, now=now)["diagnostic_tool"]


def paused_workloads(*, path: Path | None = None, now: float | None = None) -> frozenset[str]:
    return control_snapshot(path, now=now)["paused"]


def diagnostic_expiry(path: Path | None = None) -> float | None:
    return control_snapshot(path)["expires_at"]


def workload_expiries(path: Path | None = None) -> dict[str, float]:
    return control_snapshot(path)["workload_expiries"]


def capture_status(path: Path | None = None, *, now: float | None = None) -> dict[str, object]:
    return control_snapshot(path, now=now)["metrics_capture"]


def is_paused(workload: str) -> bool:
    return workload in paused_workloads()

def _payload(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("schema") == 1 else {}
    except (OSError, ValueError):
        return {}


def _assert_expected(current: dict, *, session: str | None = None,
                     revision: int | None = None,
                     instance: str | None = None) -> None:
    if (instance is not None and instance != instance_id()
            or session is not None and
            (current.get("diagnostic_tool") or {}).get("session_id") != session
            or revision is not None and int(current.get("control_revision", 0)) != revision):
        raise ValueError("diagnostic_control_conflict")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            if hasattr(os, "fchmod"):
                os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def _control_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "diagnostic-workloads.lock").open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            if lock_file.seek(0, 2) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def diagnostic_run_lock(path: Path):
    """Allow only one CLI/API measurement on this shared NAS control root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "diagnostic-run.lock").open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            if lock_file.seek(0, 2) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("diagnostic_run_busy") from error
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("diagnostic_run_busy") from error
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _set(path: Path, workload: str, pause: bool, lease_seconds: int,
         *, owner: str = "manual", expected_session: str | None = None,
         expected_revision: int | None = None,
         expected_instance: str | None = None) -> dict:
    if workload not in WORKLOADS:
        raise ValueError(f"unsupported workload: {workload}")
    with _control_lock(path):
        now = time.time()
        current = _payload(path)
        if current.get("instance_id") != instance_id():
            current = {}
        _assert_expected(current, session=expected_session if owner == "manual" else None,
                         revision=expected_revision, instance=expected_instance)
        tool = evaluate_diagnostic_control(current, now=now)["diagnostic_tool"]
        if expected_session is not None and tool["session_id"] != expected_session:
            return current  # A completed run must not alter a newer session.
        if not tool["enabled"]:
            if not pause and owner != "manual":
                return current  # TTL expiry or tool OFF already restored it.
            raise ValueError("diagnostic tool is OFF; run 'tool on --ttl 10m' first")
        tool_expiry = float(tool["expires_at"])
        leases = {name: min(float(expiry), tool_expiry)
                  for name, expiry in current.get("leases", {}).items()
                  if name in WORKLOADS and float(expiry) > now}
        owners = {name: current.get("owners", {}).get(name, "manual") for name in leases}
        if pause:
            leases[workload] = min(now + lease_seconds, tool_expiry)
            owners[workload] = owner
        elif owner == "manual" or owners.get(workload) == owner:
            leases.pop(workload, None)
            owners.pop(workload, None)
        capture = current.get("capture")
        if not isinstance(capture, dict) or float(capture.get("expires_at", 0)) <= now:
            capture = None
        elif float(capture["expires_at"]) > tool_expiry:
            capture = {**capture, "expires_at": tool_expiry}
        result = {"schema": 1, "instance_id": instance_id(), "leases": leases,
                  "diagnostic_tool": current.get("diagnostic_tool"),
                  "control_revision": int(current.get("control_revision", 0)) + 1,
                  "owners": owners, "paused": sorted(leases), "capture": capture,
                  "expires_at": max([*leases.values(),
                                     float(capture["expires_at"]) if capture else 0]),
                  "updated_at": now}
        _write(path, result)
        return result


def _set_capture(path: Path, enabled: bool, lease_seconds: int,
                 *, owner: str = "manual", expected_session: str | None = None,
                 expected_revision: int | None = None,
                 expected_instance: str | None = None) -> dict:
    with _control_lock(path):
        now = time.time()
        current = _payload(path)
        if current.get("instance_id") != instance_id():
            current = {}
        _assert_expected(current, session=expected_session if owner == "manual" else None,
                         revision=expected_revision, instance=expected_instance)
        tool = evaluate_diagnostic_control(current, now=now)["diagnostic_tool"]
        if expected_session is not None and tool["session_id"] != expected_session:
            return current
        if enabled and not tool["enabled"]:
            raise ValueError("diagnostic tool is OFF; run 'tool on --ttl 10m' first")
        if not tool["enabled"] and owner != "manual":
            return current
        tool_expiry = float(tool["expires_at"]) if tool["enabled"] else 0
        leases = {name: min(float(expiry), tool_expiry)
                  for name, expiry in current.get("leases", {}).items()
                  if name in WORKLOADS and float(expiry) > now and tool["enabled"]}
        owners = {name: current.get("owners", {}).get(name, "manual") for name in leases}
        existing = current.get("capture")
        if not isinstance(existing, dict) or float(existing.get("expires_at", 0)) <= now:
            existing = None
        elif tool["enabled"] and float(existing["expires_at"]) > tool_expiry:
            existing = {**existing, "expires_at": tool_expiry}
        if enabled:
            capture = {"expires_at": min(now + lease_seconds, tool_expiry), "owner": owner}
        elif owner == "manual" or (existing and existing.get("owner") == owner):
            capture = None
        else:
            capture = existing
        result = {"schema": 1, "instance_id": instance_id(), "leases": leases,
                  "diagnostic_tool": current.get("diagnostic_tool"),
                  "control_revision": int(current.get("control_revision", 0)) + 1,
                  "owners": owners, "paused": sorted(leases), "capture": capture,
                  "expires_at": max([*leases.values(),
                                     float(capture["expires_at"]) if capture else 0]),
                  "updated_at": now}
        _write(path, result)
        return result


def _set_tool(path: Path, enabled: bool, lease_seconds: int = 600,
              *, expected_session: str | None = None,
              expected_revision: int | None = None,
              expected_instance: str | None = None) -> dict:
    """Enable the parent diagnostic gate or atomically turn all child switches off."""
    with _control_lock(path):
        now = time.time()
        current = _payload(path)
        if current.get("instance_id") != instance_id():
            current = {}
        _assert_expected(current, session=expected_session,
                         revision=expected_revision, instance=expected_instance)
        active = evaluate_diagnostic_control(current, now=now)["diagnostic_tool"]
        if enabled and active["enabled"] and active["session_id"]:
            return current  # Repeating ON neither clears children nor extends TTL.
        tool = ({"expires_at": now + lease_seconds,
                 "monotonic_deadline": time.monotonic() + lease_seconds,
                 "owner": "manual",
                 "session_id": uuid4().hex}
                if enabled else None)
        result = {
            "schema": 1,
            "instance_id": instance_id(),
            "control_revision": int(current.get("control_revision", 0)) + 1,
            "diagnostic_tool": tool,
            "leases": {},
            "owners": {},
            "paused": [],
            "capture": None,
            "expires_at": float(tool["expires_at"]) if tool else 0,
            "updated_at": now,
        }
        _write(path, result)
        return result


def _history(command: str, *, workload: str = "", detail: dict | None = None) -> None:
    configured = control_path()
    if configured is None:
        raise RuntimeError("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH is not configured")
    path = configured.parent / "diagnostic-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            output.write(json.dumps({"at_utc": datetime.now(UTC).isoformat(),
                                     "command": command, "workload": workload,
                                     "detail": detail or {}}, ensure_ascii=False) + "\n")
    except BaseException:
        raise

