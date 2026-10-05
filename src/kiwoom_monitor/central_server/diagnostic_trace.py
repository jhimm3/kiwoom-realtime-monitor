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
_WAKE = threading.Event()
_COPY_GATE = threading.BoundedSemaphore(2)
_COPY_RESERVATION = 8 * 1024 * 1024
_MEMORY_LIMIT = 64 * 1024 * 1024
_SCALAR_RESERVE = 8 * 1024 * 1024
_MAX_BLOB_BYTES = 16 * 1024 * 1024
_WORKER_RESERVE = 16 * 1024 * 1024  # Encoding, hashing, and chunk buffers stay off the caller.
_MAX_CHUNK_BYTES = 1_048_576
_MAX_DOWNLOAD_CHUNK_BYTES = 2_000_000  # Keep older, readable chunks compatible.
_MAX_WINDOW_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_COLLECTOR_PREFIX_SECONDS = 900
_MAX_BYTES = 1_073_741_824
_MAX_SESSIONS = 8
_MAX_STORED_BYTES = 4 * _MAX_BYTES


def _directory() -> Path:
    parent = control_path()
    if parent is None:
        raise ValueError("diagnostic_control_unavailable")
    return parent.parent / "diagnostic-traces"


def start(*, seconds: int, store_inputs: bool = False, collector_inputs: bool = False) -> dict:
    global _SESSION, _THREAD
    if (not 60 <= seconds <= 7200 or type(store_inputs) is not bool
            or type(collector_inputs) is not bool):
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
        _WAKE.clear()
        from .diagnostic_replay_contract import reset_actor_sequences
        reset_actor_sequences()
        source_root = Path(__file__).resolve().parents[3]
        _SESSION = {"trace_id": identifier, "producer_id": uuid4().hex,
                    "schema_version": 2 if store_inputs or collector_inputs else 1,
                    "coverage": "observed_paths_only",
                    "payload_capture": {"store_inputs": store_inputs, "collector_inputs": collector_inputs},
                    "source_release": source_root.name if source_root.parent.name == "releases" else "image_or_local",
                    "instance_id": instance_id(),
                    "master_session": tool["session_id"], "state": "running",
                    "started_at": time.time(), "started_mono_ns": time.monotonic_ns(),
                    "expires_at": time.time() + seconds,
                    "last_seq": 0, "accepted": 0, "written": 0,
                    "known_dropped": 0, "bytes_written": 0, "chunks": [],
                    "chunk_flush_ms_total": 0.0, "chunk_flush_ms_max": 0.0,
                    "chunk_fsync_ms_total": 0.0, "chunk_fsync_ms_max": 0.0,
                    "queue_high_water": 0, "pending_events": 0, "reason": None,
                    "charged_bytes": 0, "scalar_charged_bytes": 0, "copy_reserved_bytes": 0,
                    "memory_high_water": 0, "blobs": {}, "payload_accepted": 0,
                    "worker_reserved_bytes": _WORKER_RESERVE,
                    "input_rejected": 0, "input_rejected_reasons": {}, "input_coverage": {}}
        _SESSION["input_capture_censored"] = False
        _SESSION["storage_limit_bytes"] = min(_MAX_BYTES, _MAX_STORED_BYTES - stored_bytes)
        _SESSION.update(copy_ms_total=0.0, copy_ms_max=0.0, payload_fsync_ms_total=0.0, payload_fsync_ms_max=0.0)
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
    return {**session, "payload_capture": dict(session.get("payload_capture", {})),
            "chunks": [dict(part) for part in session.get("chunks", [])],
            "input_coverage": {key: dict(value) for key, value in session.get("input_coverage", {}).items()},
            "input_rejected_reasons": dict(session.get("input_rejected_reasons", {})),
            "blobs": {key: dict(value) for key, value in session.get("blobs", {}).items()},
            "queued": len(_QUEUE) + session.get("pending_events", 0)}


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
    if expected is None or int(expected["bytes"]) > _MAX_DOWNLOAD_CHUNK_BYTES:
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
        _WAKE.set()
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


def input_token(kind: str) -> str | None:
    session = _SESSION
    return (session["trace_id"] if session is not None and session["state"] == "running"
            and session.get("payload_capture", {}).get(kind) is True else None)


def _field_charge(value, depth=0) -> int:
    if depth > 12:
        return _MEMORY_LIMIT + 1
    if type(value) is str:
        return 128 + 4 * len(value)
    if type(value) is dict:
        return 128 + sum(80 + _field_charge(key, depth + 1) + _field_charge(item, depth + 1)
                         for key, item in value.items())
    if type(value) in (list, tuple):
        return 128 + sum(16 + _field_charge(item, depth + 1) for item in value)
    return 64


def _enqueue(session: dict, event: dict, charge: int, *, payload: bool) -> bool:
    session["last_seq"] += 1
    scalar = 0 if payload else charge
    payload_bytes = session["charged_bytes"] - session["scalar_charged_bytes"]
    if (len(_QUEUE) + session["pending_events"] >= _CAPACITY
            or session["charged_bytes"] + session["copy_reserved_bytes"] + charge > _MEMORY_LIMIT - _WORKER_RESERVE
            or payload and payload_bytes + session["copy_reserved_bytes"] + charge > _MEMORY_LIMIT - _SCALAR_RESERVE):
        session["known_dropped"] += 1
        return False
    event["seq"] = session["last_seq"]
    event["producer_id"] = session["producer_id"]
    event["_memory_charge"] = charge
    event["_scalar_charge"] = scalar
    _QUEUE.append(event)
    session["charged_bytes"] += charge
    session["scalar_charged_bytes"] += scalar
    session["accepted"] += 1
    session["queue_high_water"] = max(session["queue_high_water"], len(_QUEUE) + session["pending_events"])
    session["memory_high_water"] = max(session["memory_high_water"],
                                        session["charged_bytes"] + session["copy_reserved_bytes"])
    if session["charged_bytes"] >= _MEMORY_LIMIT // 4 or len(_QUEUE) >= 4096:
        _WAKE.set()
    return True


def emit(trace_id: str | None, event_type: str, fields: dict) -> None:
    if trace_id is None:
        return
    now_wall, now_mono = time.time_ns(), time.monotonic_ns()
    charge = 1024 + _field_charge(fields)
    with _LOCK:
        session = _SESSION
        if session is None or session["trace_id"] != trace_id or session["state"] != "running":
            return
        event = {"wall_ns": now_wall, "mono_ns": now_mono, "event_type": event_type}
        event.update(fields)
        _enqueue(session, event, charge, payload=False)


def reject_input(trace_id: str, fields: dict) -> None:
    with _LOCK:
        session = _SESSION
        if session is None or session["trace_id"] != trace_id or session["state"] != "running":
            return
        reason = fields.get("reason", "capture_error")
        session["input_rejected"] += 1
        reasons = session["input_rejected_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1
        group = fields.get("workload_id", "unsupported")
        bucket = session["input_coverage"].setdefault(group, {"accepted": 0, "rejected": 0})
        bucket["rejected"] += 1
    emit(trace_id, "input_rejected", fields)


def emit_payload(trace_id: str, event_type: str, fields: dict, value) -> bool:
    """Try a bounded immutable copy; all encoding/hash/file work stays in the worker."""
    if not _COPY_GATE.acquire(blocking=False):
        reject_input(trace_id, {**fields, "reason": "capture_copy_busy"})
        return False
    reservation = False
    try:
        with _LOCK:
            session = _SESSION
            if session is None or session["trace_id"] != trace_id or session["state"] != "running":
                if session is not None and session["trace_id"] == trace_id and session["state"] == "stopping":
                    session["input_capture_censored"] = True
                return False
            if session["charged_bytes"] + session["copy_reserved_bytes"] + _COPY_RESERVATION > _MEMORY_LIMIT - _WORKER_RESERVE - _SCALAR_RESERVE:
                budget_available = False
            else:
                budget_available = True
                session["copy_reserved_bytes"] += _COPY_RESERVATION
                session["memory_high_water"] = max(session["memory_high_water"],
                                                    session["charged_bytes"] + session["copy_reserved_bytes"])
                reservation = True
        if not budget_available:
            reject_input(trace_id, {**fields, "reason": "capture_memory_full"})
            return False
        from .diagnostic_replay_contract import freeze_payload, InputRejected
        copy_started = time.perf_counter()
        try:
            frozen = freeze_payload(value)
        except Exception as error:
            reason = str(error) if isinstance(error, InputRejected) else "capture_copy_error"
            reject_input(trace_id, {**fields, "reason": reason})
            return False
        with _LOCK:
            # Charge remains reserved if stop/worker is draining; its finally waits for copies.
            session["copy_reserved_bytes"] -= _COPY_RESERVATION
            copy_ms = (time.perf_counter() - copy_started) * 1000
            session["copy_ms_total"] += copy_ms
            session["copy_ms_max"] = max(session["copy_ms_max"], copy_ms)
            reservation = False
            if session is not _SESSION or session["state"] != "running":
                session["input_capture_censored"] = True
                return False
            event = {"wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(),
                     "event_type": event_type, **fields, "payload": frozen.value}
            accepted = _enqueue(session, event, frozen.charge + 1024 + _field_charge(fields), payload=True)
            if accepted:
                session["payload_accepted"] += 1
                group = fields.get("workload_id", "unsupported")
                bucket = session["input_coverage"].setdefault(group, {"accepted": 0, "rejected": 0})
                bucket["accepted"] += 1
            return accepted
    finally:
        if reservation:
            with _LOCK:
                session["copy_reserved_bytes"] -= _COPY_RESERVATION
            _WAKE.set()
        _COPY_GATE.release()


def payload_bytes(trace_id: str, digest: str) -> bytes:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise KeyError("invalid_payload_hash")
    manifest = status(trace_id)
    part = manifest.get("blobs", {}).get(digest)
    if not part or not 0 < part["bytes"] <= _MAX_BLOB_BYTES:
        raise KeyError("payload_not_committed")
    path = _directory() / trace_id / f"payload-{digest}.json"
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BLOB_BYTES:
        raise KeyError("payload_missing_or_oversized")
    content = path.read_bytes()
    if len(content) != part["bytes"] or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("payload_checksum_mismatch")
    return content


def recorded_events(trace_id: str) -> tuple[dict, list[dict]]:
    manifest = status(trace_id)
    if (manifest.get("schema_version") != 2 or manifest.get("state") != "complete"
            or manifest.get("known_dropped") or manifest.get("input_capture_censored")):
        raise ValueError("recorded_capture_incomplete_or_old_schema")
    # Internal bounded reader; the future public window reader must stream a long capture.
    if manifest.get("written", 0) > 10_000:
        raise ValueError("recorded_window_reader_required")
    rows = []
    loaded_bytes = 0
    for part in manifest["chunks"]:
        for line in chunk_bytes(trace_id, part["name"]).splitlines():
            row = json.loads(line)
            digest = row.pop("payload_ref", None)
            if digest is not None:
                content = payload_bytes(trace_id, digest)
                loaded_bytes += len(content)
                if loaded_bytes > 32 * 1024 * 1024:
                    raise ValueError("recorded_window_reader_required")
                row["payload"] = json.loads(content)
            rows.append(row)
    if len(rows) != manifest["written"] or [row["seq"] for row in rows] != list(range(1, manifest["last_seq"] + 1)):
        raise ValueError("recorded_capture_sequence_invalid")
    return manifest, rows


def recorded_window_events(trace_id: str, *, window_start_seconds: float,
                           window_end_seconds: float, mode: str,
                           collector_components: tuple[str, ...] = ()) -> tuple[dict, list[dict]]:
    """Read a bounded replay window while retaining only needed payloads.

    Chunk lines are still checksum- and sequence-verified across the complete
    capture. Operation payloads are resolved only for starts in the measured
    window. Collector mode also resolves its selected component's prefix, and
    is limited to a 15-minute capture-relative end until periodic RAM snapshots
    exist.
    """
    import math

    if (mode not in {"recorded_operations", "collector_with_background"}
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in
                       (window_start_seconds, window_end_seconds))
            or not 0 <= window_start_seconds < window_end_seconds <= 7200
            or window_end_seconds - window_start_seconds > 600
            or (mode == "collector_with_background" and
                (window_end_seconds > _MAX_COLLECTOR_PREFIX_SECONDS
                 or not collector_components
                 or any(type(component) is not str or not component for component in collector_components)
                 or len(set(collector_components)) != len(collector_components)))):
        raise ValueError("recorded_window_out_of_bounds")
    manifest = status(trace_id)
    if (manifest.get("schema_version") != 2 or manifest.get("state") != "complete"
            or manifest.get("known_dropped") or manifest.get("input_capture_censored")):
        raise ValueError("recorded_capture_incomplete_or_old_schema")
    started_mono_ns = manifest.get("started_mono_ns")
    finished_mono_ns = manifest.get("finished_mono_ns")
    written = manifest.get("written")
    last_seq = manifest.get("last_seq")
    if (type(started_mono_ns) is not int or type(finished_mono_ns) is not int
            or finished_mono_ns < started_mono_ns or type(written) is not int
            or type(last_seq) is not int or written != last_seq
            or written > _CAPACITY):
        raise ValueError("recorded_capture_manifest_invalid")

    start_ns = started_mono_ns + int(window_start_seconds * 1_000_000_000)
    end_ns = started_mono_ns + int(window_end_seconds * 1_000_000_000)
    if end_ns > finished_mono_ns:
        raise ValueError("recorded_window_exceeds_capture_duration")
    component_set = set(collector_components)
    payload_cache: dict[str, object] = {}
    payload_bytes_loaded = 0
    rows: list[dict] = []
    expected_seq = 1
    for part in manifest.get("chunks", ()):
        content = chunk_bytes(trace_id, part["name"])
        chunk_rows = []
        for line in content.splitlines():
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("recorded_chunk_json_invalid") from error
            if type(row) is not dict or row.get("seq") != expected_seq:
                raise ValueError("recorded_capture_sequence_invalid")
            expected_seq += 1
            event_type = row.get("event_type")
            event_mono = row.get("mono_ns")
            operation_mono = row.get("entered_mono_ns", event_mono)
            needs_payload = (
                event_type == "operation_start" and type(operation_mono) is int
                and start_ns <= operation_mono < end_ns
            ) or (
                mode == "collector_with_background" and event_type == "collector_input"
                and row.get("producer_component") in component_set
                and type(event_mono) is int and event_mono < end_ns
            )
            digest = row.pop("payload_ref", None)
            if needs_payload and digest is not None:
                if digest not in payload_cache:
                    payload = payload_bytes(trace_id, digest)
                    if payload_bytes_loaded + len(payload) > _MAX_WINDOW_PAYLOAD_BYTES:
                        raise ValueError("recorded_window_payload_limit_exceeded")
                    try:
                        payload_cache[digest] = json.loads(payload)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ValueError("recorded_payload_json_invalid") from error
                    payload_bytes_loaded += len(payload)
                row["payload"] = payload_cache[digest]
            chunk_rows.append(row)
            rows.append(row)
        if (not chunk_rows or part.get("first_seq") != chunk_rows[0]["seq"]
                or part.get("last_seq") != chunk_rows[-1]["seq"]
                or part.get("count") != len(chunk_rows)):
            raise ValueError("recorded_chunk_manifest_mismatch")
    if expected_seq - 1 != written or expected_seq - 1 != last_seq:
        raise ValueError("recorded_capture_sequence_invalid")
    return {**manifest, "window_read": {
        "start_seconds": window_start_seconds,
        "end_seconds": window_end_seconds,
        "mode": mode,
        "events_verified": written,
        "payload_blobs_loaded": len(payload_cache),
        "payload_bytes_loaded": payload_bytes_loaded,
        "collector_prefix_seconds": window_end_seconds if mode == "collector_with_background" else 0,
    }}, rows


def _manifest(directory: Path, session: dict) -> None:
    target = directory / "manifest.json"
    temporary = directory / "manifest.partial"
    with _LOCK:
        snapshot = _public(session)
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)


def _drain(session: dict, run_lock) -> None:
    directory = None
    pending: deque[dict] = deque()
    stored_blobs: dict[str, dict] = {}
    blob_bytes = 0
    chunk_bytes_written = 0
    try:
        directory = _directory() / session["trace_id"]
        directory.mkdir(mode=0o700, exist_ok=False)
        _manifest(directory, session)
        next_flush = time.monotonic() + 5
        while True:
            _WAKE.wait(0.5)
            high_water = _WAKE.is_set()
            _WAKE.clear()
            if not _STOP.is_set():
                master = diagnostic_tool_status()
                child = trace_status()
                if (not master["enabled"] or master["session_id"] != session["master_session"]
                        or not child["enabled"] or time.time() >= session["expires_at"]):
                    with _LOCK:
                        session["state"] = "stopping"
                        session["reason"] = "master_off_or_expired" if not master["enabled"] else "expired"
                    _STOP.set()
            if not _STOP.is_set() and not high_water and not pending and time.monotonic() < next_flush:
                continue
            next_flush = time.monotonic() + 5
            with _LOCK:
                if not pending:
                    while _QUEUE and len(pending) < 4096:
                        pending.append(_QUEUE.popleft())
                    session["pending_events"] = len(pending)
            if pending:
                flush_started = time.perf_counter()
                # Encoding stays in this worker. Keep the uncommitted suffix here
                # so one periodic flush stays bounded without reordering the queue.
                lines: list[bytes] = []
                chunk_blobs: dict[str, dict] = {}
                content_bytes = 0
                for row in pending:
                    encoded = {key: value for key, value in row.items()
                               if key not in {"payload", "_memory_charge", "_scalar_charge"}}
                    digest = None
                    if "payload" in row:
                        payload = json.dumps(row["payload"], ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")
                        if len(payload) > _MAX_BLOB_BYTES:
                            raise OSError("trace_event_too_large")
                        digest = hashlib.sha256(payload).hexdigest()
                        if digest not in stored_blobs:
                            if blob_bytes + chunk_bytes_written + len(payload) > session["storage_limit_bytes"]:
                                raise OSError("trace_file_limit")
                            temporary_blob = directory / f"payload-{digest}.partial"
                            with temporary_blob.open("wb") as file:
                                file.write(payload)
                                file.flush()
                                sync_started = time.perf_counter()
                                os.fsync(file.fileno())
                                sync_ms = (time.perf_counter() - sync_started) * 1000
                            with _LOCK:
                                session["payload_fsync_ms_total"] += sync_ms
                                session["payload_fsync_ms_max"] = max(session["payload_fsync_ms_max"], sync_ms)
                            os.replace(temporary_blob, directory / f"payload-{digest}.json")
                            stored_blobs[digest] = {"bytes": len(payload)}
                            blob_bytes += len(payload)
                        encoded["payload_ref"] = digest
                        del payload
                    line = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"),
                                      default=str).encode("utf-8") + b"\n"
                    if content_bytes + len(line) > _MAX_CHUNK_BYTES:
                        if not lines:
                            raise OSError("trace_event_too_large")
                        break
                    lines.append(line)
                    if digest is not None:
                        chunk_blobs[digest] = stored_blobs[digest]
                    content_bytes += len(line)
                content = b"".join(lines)
                if chunk_bytes_written + blob_bytes + len(content) > session["storage_limit_bytes"]:
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
                count = len(lines)
                checksum = hashlib.sha256(content).hexdigest()
                chunk_bytes_written += len(content)
                with _LOCK:
                    session["chunk_flush_ms_total"] += flush_ms
                    session["chunk_flush_ms_max"] = max(session["chunk_flush_ms_max"], flush_ms)
                    session["chunk_fsync_ms_total"] += fsync_ms
                    session["chunk_fsync_ms_max"] = max(session["chunk_fsync_ms_max"], fsync_ms)
                    session["chunks"].append({"name": name, "first_seq": pending[0]["seq"],
                                              "last_seq": pending[count - 1]["seq"], "count": count,
                                              "bytes": len(content), "sha256": checksum})
                    for _ in range(count):
                        committed = pending.popleft()
                        session["charged_bytes"] -= committed["_memory_charge"]
                        session["scalar_charged_bytes"] -= committed["_scalar_charge"]
                    session["blobs"].update(chunk_blobs)
                    session["pending_events"] = len(pending)
                    session["written"] += count
                    session["bytes_written"] = chunk_bytes_written + sum(part["bytes"] for part in session["blobs"].values())
                _manifest(directory, session)
                # Drain a backlog immediately instead of waiting another five seconds per chunk.
                if pending or _QUEUE:
                    _WAKE.set()
            if _STOP.is_set():
                with _LOCK:
                    if not pending and not _QUEUE and not session["copy_reserved_bytes"]:
                        break
        with _LOCK:
            session["state"] = "complete" if not session["known_dropped"] and not session["input_capture_censored"] else "incomplete"
            session["finished_at"] = time.time()
            session["finished_mono_ns"] = time.monotonic_ns()
        _manifest(directory, session)
    except Exception as error:
        with _LOCK:
            session["state"] = "failed"
            session["reason"] = (str(error) if str(error) in {
                "trace_event_too_large", "trace_file_limit",
            } else type(error).__name__)
            session["finished_at"] = time.time()
            session["finished_mono_ns"] = time.monotonic_ns()
        try:
            if directory is not None:
                _manifest(directory, session)
        except OSError:
            pass
    finally:
        # A stopped copy cannot be admitted, but still owns memory until its finally runs.
        while True:
            with _LOCK:
                copying = session["copy_reserved_bytes"]
            if not copying:
                break
            _WAKE.wait(0.05)
            _WAKE.clear()
        try:
            path = control_path()
            if path is not None:
                _set_trace(path, False, 60, expected_session=session["master_session"])
        except (OSError, ValueError):
            pass
        run_lock.__exit__(None, None, None)
