"""Bounded, opt-in DB call trace.  No file operation runs in a DB caller."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from .diagnostic_workloads import (
    _set_trace, control_path, diagnostic_run_lock, diagnostic_tool_status, instance_id, trace_status,
)

_LOCK = threading.Lock()
_QUEUE: deque[dict] = deque()
_CAPACITY = 32768
_SESSION: dict | None = None
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_MAX_BYTES = 1_073_741_824
_MAX_SESSIONS = 8
_MAX_STORED_BYTES = 4 * _MAX_BYTES


def _directory() -> Path:
    parent = control_path()
    if parent is None:
        raise ValueError("diagnostic_control_unavailable")
    return parent.parent / "diagnostic-traces"


def start(*, seconds: int) -> dict:
    global _SESSION, _THREAD
    if not 60 <= seconds <= 7200:
        raise ValueError("trace_duration_out_of_bounds")
    tool = diagnostic_tool_status()
    if not tool["enabled"] or float(tool["expires_at"] or 0) - time.time() < seconds + 5:
        raise ValueError("diagnostic_master_ttl_too_short")
    child = trace_status()
    if not child["enabled"] or child["session_id"] != tool["session_id"]:
        raise ValueError("diagnostic_trace_child_off")
    directory = _directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    sessions = [part for part in directory.iterdir()
                if part.is_dir() and not part.is_symlink() and _valid_id(part.name)]
    stored_bytes = sum(file.stat().st_size for part in sessions for file in part.iterdir()
                       if file.is_file() and not file.is_symlink())
    if (len(sessions) >= _MAX_SESSIONS or stored_bytes >= _MAX_STORED_BYTES
            or shutil.disk_usage(directory).free < 256 * 1024 * 1024):
        raise ValueError("trace_storage_quota_exceeded")
    identifier = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid4().hex[:12]
    with _LOCK:
        if (_SESSION is not None and _SESSION["state"] in {"running", "stopping"}
                or _THREAD is not None and _THREAD.is_alive()):
            raise ValueError("trace_already_running")
        run_lock = diagnostic_run_lock(control_path())
        try:
            run_lock.__enter__()
        except RuntimeError as error:
            raise ValueError("diagnostic_run_busy") from error
        _QUEUE.clear()
        _STOP.clear()
        source_root = Path(__file__).resolve().parents[3]
        _SESSION = {"trace_id": identifier, "producer_id": uuid4().hex,
                    "schema_version": 1, "coverage": "observed_paths_only",
                    "source_release": source_root.name if source_root.parent.name == "releases" else "image_or_local",
                    "instance_id": instance_id(),
                    "master_session": tool["session_id"], "state": "running",
                    "started_at": time.time(), "expires_at": time.time() + seconds,
                    "last_seq": 0, "accepted": 0, "written": 0,
                    "known_dropped": 0, "bytes_written": 0, "chunks": [],
                    "chunk_flush_ms_total": 0.0, "chunk_flush_ms_max": 0.0,
                    "chunk_fsync_ms_total": 0.0, "chunk_fsync_ms_max": 0.0,
                    "queue_high_water": 0, "reason": None}
        _THREAD = threading.Thread(target=_drain, args=(_SESSION, run_lock),
                                   name="diagnostic-trace", daemon=True)
        try:
            _THREAD.start()
        except Exception:
            _SESSION["state"] = "failed"
            run_lock.__exit__(None, None, None)
            raise
        return _public(_SESSION)


def _public(session: dict) -> dict:
    return {**session, "queued": len(_QUEUE)}


def status(trace_id: str | None = None) -> dict:
    with _LOCK:
        if _SESSION is not None and (trace_id is None or trace_id == _SESSION["trace_id"]):
            return _public(_SESSION)
    if trace_id is None or not _valid_id(trace_id):
        return {"state": "off"}
    path = _directory() / trace_id / "manifest.json"
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise KeyError(trace_id)
    return json.loads(path.read_text(encoding="utf-8"))


def chunk_bytes(trace_id: str, name: str) -> bytes:
    """Serve only a committed, checksummed chunk named by its manifest."""
    if not _valid_id(trace_id) or len(name) != 12 or not name[:6].isdigit() or name[6:] != ".jsonl":
        raise KeyError("invalid_trace_chunk")
    manifest = status(trace_id)
    expected = next((part for part in manifest.get("chunks", []) if part["name"] == name), None)
    if expected is None or int(expected["bytes"]) > 2_000_000:
        raise KeyError("trace_chunk_not_committed")
    path = _directory() / trace_id / name
    if path.is_symlink() or not path.is_file():
        raise KeyError("trace_chunk_missing")
    content = path.read_bytes()
    if (len(content) != expected["bytes"] or
            hashlib.sha256(content).hexdigest() != expected["sha256"]):
        raise ValueError("trace_chunk_checksum_mismatch")
    return content


def stop(reason: str = "manual", timeout: float = 10) -> dict:
    with _LOCK:
        session = _SESSION
        if session is None:
            return {"state": "off"}
        if session["state"] == "running":
            session["reason"] = reason
            session["state"] = "stopping"
        _STOP.set()
        thread = _THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
    return status()


def _valid_id(value: str) -> bool:
    return len(value) == 29 and all(c.isascii() and (c.isalnum() or c == "-") for c in value)


def recover_interrupted() -> int:
    """Mark prior-process running manifests incomplete; never re-enable capture."""
    if control_path() is None:
        return 0
    root = _directory()
    if not root.is_dir():
        return 0
    recovered = 0
    current_instance = instance_id()
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink() or not _valid_id(directory.name):
            continue
        path = directory / "manifest.json"
        if path.is_symlink() or not path.is_file():
            continue
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
            if (session.get("state") not in {"running", "stopping"}
                    or session.get("instance_id") == current_instance):
                continue
            session["state"] = "interrupted"
            session["reason"] = "producer_restart"
            session["unknown_tail_loss"] = True
            session["recovered_at"] = time.time()
            _manifest(directory, session)
            recovered += 1
        except (OSError, ValueError, KeyError):
            continue
    return recovered


def token() -> str | None:
    # The background worker owns control-file polling.  No file I/O here.
    session = _SESSION
    return session["trace_id"] if session is not None and session["state"] == "running" else None


def emit(trace_id: str | None, event_type: str, fields: dict) -> None:
    if trace_id is None:
        return
    now_wall, now_mono = time.time_ns(), time.monotonic_ns()
    with _LOCK:
        session = _SESSION
        if session is None or session["trace_id"] != trace_id or session["state"] != "running":
            return
        session["last_seq"] += 1
        if len(_QUEUE) >= _CAPACITY:
            session["known_dropped"] += 1
            return
        event = {"seq": session["last_seq"], "producer_id": session["producer_id"],
                 "wall_ns": now_wall, "mono_ns": now_mono, "event_type": event_type}
        event.update(fields)
        _QUEUE.append(event)
        session["accepted"] += 1
        session["queue_high_water"] = max(session["queue_high_water"], len(_QUEUE))


def _manifest(directory: Path, session: dict) -> None:
    target = directory / "manifest.json"
    temporary = directory / "manifest.partial"
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(_public(session), file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)


def _drain(session: dict, run_lock) -> None:
    directory = None
    try:
        directory = _directory() / session["trace_id"]
        directory.mkdir(mode=0o700, exist_ok=False)
        _manifest(directory, session)
        next_flush = time.monotonic() + 5
        while True:
            _STOP.wait(0.5)
            if not _STOP.is_set():
                master = diagnostic_tool_status()
                child = trace_status()
                if (not master["enabled"] or master["session_id"] != session["master_session"]
                        or not child["enabled"] or time.time() >= session["expires_at"]):
                    with _LOCK:
                        session["state"] = "stopping"
                        session["reason"] = "master_off_or_expired" if not master["enabled"] else "expired"
                    _STOP.set()
            if not _STOP.is_set() and time.monotonic() < next_flush:
                continue
            next_flush = time.monotonic() + 5
            batch: list[dict] = []
            with _LOCK:
                while _QUEUE and len(batch) < 4096:
                    batch.append(_QUEUE.popleft())
            if batch:
                flush_started = time.perf_counter()
                content = b"".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                                               default=str).encode("utf-8") + b"\n" for row in batch)
                if session["bytes_written"] + len(content) > _MAX_BYTES:
                    raise OSError("trace_file_limit")
                chunk_id = len(session["chunks"]) + 1
                name = f"{chunk_id:06d}.jsonl"
                temporary = directory / f"{name}.partial"
                with temporary.open("wb") as file:
                    file.write(content)
                    file.flush()
                    fsync_started = time.perf_counter()
                    os.fsync(file.fileno())
                    fsync_ms = (time.perf_counter() - fsync_started) * 1000
                os.replace(temporary, directory / name)
                flush_ms = (time.perf_counter() - flush_started) * 1000
                session["chunk_flush_ms_total"] += flush_ms
                session["chunk_flush_ms_max"] = max(session["chunk_flush_ms_max"], flush_ms)
                session["chunk_fsync_ms_total"] += fsync_ms
                session["chunk_fsync_ms_max"] = max(session["chunk_fsync_ms_max"], fsync_ms)
                session["chunks"].append({"name": name, "first_seq": batch[0]["seq"],
                                          "last_seq": batch[-1]["seq"], "count": len(batch),
                                          "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
                session["written"] += len(batch)
                session["bytes_written"] += len(content)
                _manifest(directory, session)
            if _STOP.is_set():
                with _LOCK:
                    if not _QUEUE:
                        break
        with _LOCK:
            session["state"] = "complete" if not session["known_dropped"] else "incomplete"
            session["finished_at"] = time.time()
        _manifest(directory, session)
    except Exception as error:
        with _LOCK:
            session["state"] = "failed"
            session["reason"] = type(error).__name__
            session["finished_at"] = time.time()
        try:
            if directory is not None:
                _manifest(directory, session)
        except OSError:
            pass
    finally:
        try:
            path = control_path()
            if path is not None:
                _set_trace(path, False, 60, expected_session=session["master_session"])
        except (OSError, ValueError):
            pass
        run_lock.__exit__(None, None, None)
