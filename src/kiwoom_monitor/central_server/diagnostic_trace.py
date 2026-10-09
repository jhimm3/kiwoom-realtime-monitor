"""Bounded, opt-in DB call trace.  No file operation runs in a DB caller."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4
from .diagnostic_trace_ram import PackedRow, pack_records
from .diagnostic_delivery_record import (
    Context, DeliveryIdentity, DeliveryStage, SOURCE_NAMES, CONTROL_SOURCE_NAMES,
    SUBSCRIBER_NAMES, IDENTITY_NAMES, UNTRACKED_NAMES, MISSING, additional_charge, retain, release,
)

from .diagnostic_workloads import (
    _set_trace, control_path, diagnostic_run_lock, diagnostic_tool_status, instance_id, trace_status,
)

_LOCK = threading.Lock()
_QUEUE: deque[dict | DeliveryStage] = deque()
_PACKED = deque()
_RAW_LIMIT = 64 * 1024 * 1024
_RAW_CAPACITY = 32_768
_PACK_WORKER_RESERVE = 128 * 1024 * 1024
_CAPACITY = 5_000_000
_MAX_TOP20_WINDOW_ROWS = 50_000
_SESSION: dict | None = None
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_ABORT = threading.Event()
_WAKE = threading.Event()
_COPY_GATE = threading.BoundedSemaphore(2)
# A DB worker's large snapshot copy must not occupy the collector's only lane.
# All three reservations still share the same session/raw-memory admission limits.
_COLLECTOR_COPY_GATE = threading.BoundedSemaphore(1)
_COPY_RESERVATION = 8 * 1024 * 1024
_MEMORY_LIMIT = 256 * 1024 * 1024
_DEFERRED_MEMORY_LIMIT = 8 * 1024 * 1024 * 1024
_DEFERRED_WRITE_BYTES_PER_SECOND = 1024 * 1024
_DEFERRED_WRITE_BLOCK_BYTES = 64 * 1024
_SCALAR_RESERVE = 8 * 1024 * 1024
_MAX_BLOB_BYTES = 16 * 1024 * 1024
_MAX_PAYLOAD_BATCH_BYTES = 16 * 1024 * 1024
_FLUSH_HIGH_WATER_BYTES = 16 * 1024 * 1024
_WORKER_RESERVE = 16 * 1024 * 1024  # Encoding, hashing, and chunk buffers stay off the caller.
_MAX_CHUNK_BYTES = 1_048_576
_MAX_DOWNLOAD_CHUNK_BYTES = 2_000_000  # Keep older, readable chunks compatible.
_MAX_WINDOW_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_COLLECTOR_PREFIX_SECONDS = 900
_MAX_BYTES = 1_073_741_824
_MAX_SESSIONS = 8
_MAX_STORED_BYTES = 8 * _MAX_BYTES


def _directory() -> Path:
    parent = control_path()
    if parent is None:
        raise ValueError("diagnostic_control_unavailable")
    return parent.parent / "diagnostic-traces"


def _deferred_memory_check() -> dict:
    """Check real Linux headroom, including a container's independent limit."""
    try:
        available = next(int(line.split()[1]) * 1024 for line in
                         Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
    except (OSError, StopIteration, ValueError):
        raise ValueError('trace_memory_headroom_unavailable') from None
    container_headroom = None
    container_limit_observed = False
    for limit_path, usage_path in (
        ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes'),
    ):
        try:
            limit = Path(limit_path).read_text().strip()
            if limit != 'max':
                container_headroom = int(limit) - int(Path(usage_path).read_text().strip())
            container_limit_observed = True
            break
        except (OSError, ValueError):
            continue
    if not container_limit_observed:
        raise ValueError('trace_container_memory_headroom_unavailable')
    required = _DEFERRED_MEMORY_LIMIT + 1024 * 1024 * 1024
    if available < required or container_headroom is not None and container_headroom < required:
        raise ValueError('trace_memory_headroom_insufficient')
    return {'host_available_bytes': available, 'container_headroom_bytes': container_headroom,
            'required_headroom_bytes': required, 'scope': 'start_preflight_not_rss_guarantee'}


def start(*, seconds: int, store_inputs: bool = False, collector_inputs: bool = False,
          top20_inputs: bool = False,
          persist_at: float | None = None) -> dict:
    global _SESSION, _THREAD
    if (not 60 <= seconds <= 7200 or type(store_inputs) is not bool
            or type(collector_inputs) is not bool or type(top20_inputs) is not bool):
        raise ValueError("trace_duration_out_of_bounds")
    now = time.time()
    if persist_at is not None and (type(persist_at) not in (int, float)
            or not math.isfinite(persist_at) or not now + seconds <= persist_at <= now + 86400):
        raise ValueError('trace_persistence_time_out_of_bounds')
    memory_preflight = _deferred_memory_check() if persist_at is not None else None
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
        if _SESSION is not None:
            _discard_queue(_SESSION)
        else:
            _QUEUE.clear()
            _PACKED.clear()
        _STOP.clear()
        _ABORT.clear()
        _WAKE.clear()
        from .diagnostic_replay_contract import reset_actor_sequences
        reset_actor_sequences()
        source_root = Path(__file__).resolve().parents[3]
        _SESSION = {"trace_id": identifier, "producer_id": uuid4().hex,
                    "schema_version": 3 if top20_inputs else 2 if store_inputs or collector_inputs else 1,
                    "coverage": "observed_paths_only",
                    "payload_capture": {"store_inputs": store_inputs, "collector_inputs": collector_inputs,
                                        **({"top20_inputs": True} if top20_inputs else {})},
                    "source_release": source_root.name if source_root.parent.name == "releases" else "image_or_local",
                    "instance_id": instance_id(),
                    "master_session": tool["session_id"], "state": "running",
                    "started_at": time.time(), "started_mono_ns": time.monotonic_ns(),
                    "expires_at": time.time() + seconds,
                    "last_seq": 0, "accepted": 0, "written": 0,
                    "known_dropped": 0, "drop_reasons": {}, "bytes_written": 0, "chunks": [],
                    "chunk_flush_ms_total": 0.0, "chunk_flush_ms_max": 0.0,
                    "chunk_fsync_ms_total": 0.0, "chunk_fsync_ms_max": 0.0,
                    "queue_high_water": 0, "pending_events": 0, "reason": None,
                    "charged_bytes": 0, "scalar_charged_bytes": 0, "copy_reserved_bytes": 0,
                    "memory_high_water": 0, "blobs": {}, "payload_accepted": 0,
                    "copy_inflight": {"store": 0, "collector": 0},
                    "copy_slot_limits": {"store": 2, "collector": 1},
                    "memory_limit_bytes": _DEFERRED_MEMORY_LIMIT if persist_at is not None else _MEMORY_LIMIT,
                    "event_capacity": _CAPACITY,
                    "persistence_mode": 'deferred_ram' if persist_at is not None else 'streaming',
                    "delivery_retention": 'framed_ram_v1' if persist_at is not None else 'legacy_dictionary',
                    "persist_at": persist_at, "memory_preflight": memory_preflight,
                    "write_bytes_per_second": _DEFERRED_WRITE_BYTES_PER_SECOND if persist_at is not None else None,
                    "persistence_throttle_seconds": 0.0,
                    "payload_storage": "chunk_bundle_v1",
                    "payload_batch_limit_bytes": _MAX_PAYLOAD_BATCH_BYTES,
                    "payload_fsync_count": 0,
                    "worker_reserved_bytes": _PACK_WORKER_RESERVE if persist_at is not None else _WORKER_RESERVE,
                    "raw_charged_bytes": 0, "packing_events": 0,
                    "packed_events": 0, "packed_bytes": 0, "packed_segments": 0,
                    "raw_high_water_bytes": 0, "pack_ms_total": 0.0, "pack_ms_max": 0.0,
                    "event_counts": {}, "admitted_raw_bytes_by_event": {},
                    "input_rejected": 0, "input_rejected_reasons": {}, "input_coverage": {}}
        _SESSION["input_capture_censored"] = False
        _SESSION["storage_limit_bytes"] = min(
            _DEFERRED_MEMORY_LIMIT if persist_at is not None else _MAX_BYTES,
            _MAX_STORED_BYTES - stored_bytes)
        if persist_at is not None and shutil.disk_usage(directory).free < _SESSION['storage_limit_bytes'] + 64 * 1024 * 1024:
            _SESSION['state'] = 'failed'
            run_lock.__exit__(None, None, None)
            raise ValueError('trace_storage_quota_exceeded')
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
            "copy_inflight": dict(session.get("copy_inflight", {})),
            "copy_slot_limits": dict(session.get("copy_slot_limits", {})),
            "chunks": [dict(part) for part in session.get("chunks", [])],
            "input_coverage": {key: dict(value) for key, value in session.get("input_coverage", {}).items()},
            "input_rejected_reasons": dict(session.get("input_rejected_reasons", {})),
            "drop_reasons": dict(session.get('drop_reasons', {})),
            "blobs": {key: dict(value) for key, value in session.get("blobs", {}).items()},
            "event_counts": dict(session.get('event_counts', {})),
            "admitted_raw_bytes_by_event": dict(session.get('admitted_raw_bytes_by_event', {})),
            "queued": _retained_events(session)}


def _retained_events(session):
    if session.get('persist_at') is not None:
        return len(_QUEUE) + session.get('packing_events', 0) + session.get('packed_events', 0)
    return len(_QUEUE) + session.get('pending_events', 0)


def _worker_reserve(session):
    return session.get('worker_reserved_bytes', _WORKER_RESERVE)


def _close_admission(session, reason):
    if session.get('persist_at') is not None and session['state'] == 'running':
        session.update(state='stopping', reason=reason, input_capture_censored=True)
        _STOP.set()
        _WAKE.set()


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
    return _chunk_bytes_from_manifest(trace_id, name, manifest)


def _chunk_bytes_from_manifest(trace_id: str, name: str, manifest: dict) -> bytes:
    if (type(name) is not str or not _valid_id(trace_id) or len(name) != 12
            or not name[:6].isascii() or not name[:6].isdigit() or name[6:] != ".jsonl"):
        raise KeyError("invalid_trace_chunk")
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
        if session.get('persist_at') is not None and reason == 'server_shutdown' and session['state'] != 'persisting':
            _ABORT.set()
        _WAKE.set()
        thread = _THREAD
        deferred_wait = session.get('persist_at') is not None and not _ABORT.is_set()
    # Ending capture never forces a deferred recording onto disk before its deadline.
    if deferred_wait:
        return status()
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
            if (session.get("state") not in {"running", "stopping", "awaiting_persistence", "persisting"}
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


def _enqueue(session: dict, event: dict | DeliveryStage, charge: int, *, payload: bool) -> bool:
    session["last_seq"] += 1
    scalar = 0 if payload else charge
    payload_bytes = session["charged_bytes"] - session["scalar_charged_bytes"]
    reason = None
    if _retained_events(session) >= _CAPACITY:
        reason = 'event_capacity'
    elif session["charged_bytes"] + session["copy_reserved_bytes"] + charge > session["memory_limit_bytes"] - _worker_reserve(session):
        reason = 'memory_budget'
    elif payload and payload_bytes + session["copy_reserved_bytes"] + charge > session["memory_limit_bytes"] - _SCALAR_RESERVE:
        reason = 'payload_memory_budget'
    elif session.get('persist_at') is not None and (
            len(_QUEUE) + session['packing_events'] >= _RAW_CAPACITY
            or session['raw_charged_bytes'] + session['copy_reserved_bytes'] + charge > _RAW_LIMIT):
        reason = 'raw_staging_budget'
    if reason is not None:
        session["known_dropped"] += 1
        reasons = session.setdefault('drop_reasons', {})
        reasons[reason] = reasons.get(reason, 0) + 1
        _close_admission(session, reason)
        return False
    if isinstance(event, DeliveryStage):
        retain(event)
        event.seq, event.producer_id = session['last_seq'], session['producer_id']
        event.admission_charge = charge
    else:
        event["seq"] = session["last_seq"]
        event["producer_id"] = session["producer_id"]
        event["_memory_charge"] = charge
        event["_scalar_charge"] = scalar
    _QUEUE.append(event)
    session["charged_bytes"] += charge
    session["scalar_charged_bytes"] += scalar
    session["accepted"] += 1
    counts = session['event_counts']
    kind = event['event_type']
    counts[kind] = counts.get(kind, 0) + 1
    charges = session['admitted_raw_bytes_by_event']
    charges[kind] = charges.get(kind, 0) + charge
    if session.get('persist_at') is not None:
        session['raw_charged_bytes'] += charge
        session['raw_high_water_bytes'] = max(session['raw_high_water_bytes'], session['raw_charged_bytes'])
        if len(_QUEUE) >= 256 or session['raw_charged_bytes'] >= 1024 * 1024:
            _WAKE.set()
    session["queue_high_water"] = max(session["queue_high_water"], _retained_events(session))
    session["memory_high_water"] = max(session["memory_high_water"],
                                        session["charged_bytes"] + session["copy_reserved_bytes"])
    if session.get('persist_at') is None and (session["charged_bytes"] >= _FLUSH_HIGH_WATER_BYTES or len(_QUEUE) >= 4096):
        _WAKE.set()
    return True


def delivery_identity(trace_id, fields, subscriber, source_owner):
    """Return a compact receipt only for this deferred epoch; no wire encoding."""
    with _LOCK:
        session = _SESSION
        if (session is None or session['trace_id'] != trace_id or session['state'] != 'running'
                or session.get('persist_at') is None):
            return None
        subscriber_context = subscriber.capture_context
        if subscriber_context is None or subscriber_context.epoch != trace_id:
            subscriber_context = Context(trace_id, SUBSCRIBER_NAMES,
                                         tuple(fields[name] for name in SUBSCRIBER_NAMES))
            subscriber.capture_context = subscriber_context
        source_context = None
        if source_owner is not None:
            source_context = source_owner.compact_context
            if source_context is None or source_context.epoch != trace_id:
                names = CONTROL_SOURCE_NAMES if 'source_kind' in fields else SOURCE_NAMES
                source_context = Context(trace_id, names, tuple(fields[name] for name in names))
                object.__setattr__(source_owner, 'compact_context', source_context)
        names = IDENTITY_NAMES if source_context is not None else UNTRACKED_NAMES
        return DeliveryIdentity(trace_id, tuple(fields[name] for name in names),
                                subscriber_context, source_context)


def emit_delivery(trace_id, identity, stage, *, outcome=MISSING):
    now_wall, now_mono = time.time_ns(), time.monotonic_ns()
    record = DeliveryStage(identity, stage, outcome, now_wall, now_mono)
    with _LOCK:
        session = _SESSION
        if (session is None or session['trace_id'] != trace_id or session['state'] != 'running'
                or identity.node.epoch != trace_id):
            return
        _enqueue(session, record, additional_charge(record), payload=False)


def _release_record(session, record):
    if isinstance(record, PackedRow):
        segment = record.segment
        segment.commit(record)
        session['packed_events'] -= 1
        if segment.committed == segment.count:
            if not _PACKED or _PACKED[0] is not segment:
                raise RuntimeError('trace_ram_segment_out_of_order')
            _PACKED.popleft()
            session['packed_segments'] -= 1
            session['packed_bytes'] -= segment.charge
            session['charged_bytes'] -= segment.charge
            session['scalar_charged_bytes'] -= segment.scalar_charge
        return
    if isinstance(record, DeliveryStage):
        charge = release(record)
        session['charged_bytes'] -= charge
        session['scalar_charged_bytes'] -= charge
    else:
        session['charged_bytes'] -= record['_memory_charge']
        session['scalar_charged_bytes'] -= record['_scalar_charge']
        charge = record['_memory_charge']
    if session.get('persist_at') is not None:
        session['raw_charged_bytes'] -= charge


def _discard_queue(session):
    while _QUEUE:
        _release_record(session, _QUEUE.popleft())
    while _PACKED:
        segment = _PACKED.popleft()
        session['charged_bytes'] -= segment.charge
        session['scalar_charged_bytes'] -= segment.scalar_charge
    session.update(packed_bytes=0, packed_events=0, packed_segments=0)


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
    lane = "collector" if event_type == "collector_input" else "store"
    gate = _COLLECTOR_COPY_GATE if lane == "collector" else _COPY_GATE
    if not gate.acquire(blocking=False):
        with _LOCK:
            current = _SESSION if _SESSION and _SESSION["trace_id"] == trace_id else {}
            detail = {"copy_lane": lane, "lane_capacity": 1 if lane == "collector" else 2,
                      "store_reserved_copies": current.get("copy_inflight", {}).get("store", 0),
                      "collector_reserved_copies": current.get("copy_inflight", {}).get("collector", 0),
                      "copy_reserved_bytes": current.get("copy_reserved_bytes", 0)}
        reject_input(trace_id, {**fields, "reason": "capture_copy_busy", "rejection_detail": detail})
        return False
    reservation = False
    try:
        from .diagnostic_replay_contract import freeze_payload, InputRejected, payload_copy_limit
        try:
            reservation_bytes = payload_copy_limit(event_type, fields, value)
        except InputRejected as error:
            reject_input(trace_id, {**fields, "reason": str(error)})
            return False
        with _LOCK:
            session = _SESSION
            if session is None or session["trace_id"] != trace_id or session["state"] != "running":
                if session is not None and session["trace_id"] == trace_id and session["state"] == "stopping":
                    session["input_capture_censored"] = True
                return False
            if (session["charged_bytes"] + session["copy_reserved_bytes"] + reservation_bytes
                    > session["memory_limit_bytes"] - _worker_reserve(session) - _SCALAR_RESERVE
                    or session.get('persist_at') is not None and
                    session['raw_charged_bytes'] + session['copy_reserved_bytes'] + reservation_bytes > _RAW_LIMIT):
                budget_available = False
            else:
                budget_available = True
                session["copy_reserved_bytes"] += reservation_bytes
                session["copy_inflight"][lane] += 1
                session["memory_high_water"] = max(session["memory_high_water"],
                                                    session["charged_bytes"] + session["copy_reserved_bytes"])
                reservation = True
        if not budget_available:
            reject_input(trace_id, {**fields, "reason": "capture_memory_full"})
            with _LOCK:
                _close_admission(session, 'capture_memory_full')
            return False
        copy_started = time.perf_counter()
        try:
            frozen = (freeze_payload(value) if reservation_bytes == _COPY_RESERVATION
                      else freeze_payload(value, maximum_bytes=reservation_bytes))
        except Exception as error:
            reason = str(error) if isinstance(error, InputRejected) else "capture_copy_error"
            rejection = {**fields, "reason": reason}
            details = getattr(error, "details", None)
            if isinstance(details, dict) and details:
                rejection["rejection_detail"] = details
            reject_input(trace_id, rejection)
            return False
        with _LOCK:
            # Charge remains reserved if stop/worker is draining; its finally waits for copies.
            session["copy_reserved_bytes"] -= reservation_bytes
            session["copy_inflight"][lane] -= 1
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
                session["copy_reserved_bytes"] -= reservation_bytes
                session["copy_inflight"][lane] -= 1
            if session.get('persist_at') is None or session['state'] != 'running':
                _WAKE.set()
        gate.release()


def payload_bytes(trace_id: str, digest: str) -> bytes:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise KeyError("invalid_payload_hash")
    manifest = status(trace_id)
    return _payload_bytes_from_manifest(trace_id, digest, manifest)


def _payload_bytes_from_manifest(trace_id: str, digest: str, manifest: dict) -> bytes:
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise KeyError("invalid_payload_hash")
    part = manifest.get("blobs", {}).get(digest)
    if not part or type(part.get("bytes")) is not int or not 0 < part["bytes"] <= _MAX_BLOB_BYTES:
        raise KeyError("payload_not_committed")
    name = part.get("name")
    if name is None:
        # Existing schema-2 captures stored one JSON file per hash.
        name, offset, file_limit = f"payload-{digest}.json", 0, _MAX_BLOB_BYTES
    else:
        if (type(name) is not str or len(name) != 15 or not name[:6].isascii()
                or not name[:6].isdigit() or name[6:] != ".payloads"):
            raise KeyError("invalid_payload_bundle")
        offset, file_limit = part.get("offset"), _MAX_PAYLOAD_BATCH_BYTES
        if type(offset) is not int or offset < 0 or offset + part["bytes"] > file_limit:
            raise KeyError("invalid_payload_offset")
    path = _directory() / trace_id / name
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > file_limit:
        raise KeyError("payload_missing_or_oversized")
    with path.open("rb") as file:
        file.seek(offset)
        content = file.read(part["bytes"])
        if part.get("name") is None and file.read(1):
            raise ValueError("payload_checksum_mismatch")
    if len(content) != part["bytes"] or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("payload_checksum_mismatch")
    return content


def validate_top20_capture_manifest(manifest: dict, events: list[dict], *, trace_id: str) -> None:
    """Strict whole-capture preflight; generic partial replay keeps its own policy.

    This verifies metadata against hydrated events, not file checksums. The
    disk entry point below additionally uses the checksummed trace reader.
    """
    _validate_top20_capture_manifest(manifest, events, trace_id=trace_id, hydrated=True)


def _validate_top20_capture_manifest(manifest, events, *, trace_id, hydrated):
    if (type(manifest) is not dict or manifest.get("trace_id") != trace_id
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 3 or manifest.get("state") != "complete"
            or manifest.get("coverage") != "observed_paths_only"
            or manifest.get("input_capture_censored") is not False):
        raise ValueError("top20_capture_manifest_incomplete")
    options = manifest.get("payload_capture")
    if type(options) is not dict or any(options.get(key) is not True for key in
                                       ("store_inputs", "collector_inputs", "top20_inputs")):
        raise ValueError("top20_capture_options_incomplete")
    counters = ("accepted", "written", "last_seq", "known_dropped", "input_rejected",
                "payload_accepted", "queued", "pending_events", "copy_reserved_bytes", "bytes_written")
    if any(type(manifest.get(key)) is not int or manifest[key] < 0 for key in counters):
        raise ValueError("top20_capture_manifest_counter_invalid")
    if (any(manifest[key] for key in ("known_dropped", "input_rejected", "queued",
                                     "pending_events", "copy_reserved_bytes"))
            or not manifest["accepted"] == manifest["written"] == manifest["last_seq"]):
        raise ValueError("top20_capture_incomplete_or_not_drained")
    for key in ("drop_reasons", "input_rejected_reasons"):
        reasons = manifest.get(key)
        if type(reasons) is not dict or any(type(count) is not int or count != 0 for count in reasons.values()):
            raise ValueError("top20_capture_rejection_or_drop")
    started, finished = manifest.get("started_mono_ns"), manifest.get("finished_mono_ns")
    if type(started) is not int or type(finished) is not int or not 0 <= started < finished:
        raise ValueError("top20_capture_clock_invalid")
    coverage = manifest.get("input_coverage")
    if type(coverage) is not dict:
        raise ValueError("top20_capture_input_coverage_invalid")
    observed = {}
    event_count = 0
    for sequence, row in enumerate(events, 1):
        event_count = sequence
        if (type(row) is not dict or type(row.get("seq")) is not int or row["seq"] != sequence
                or row.get("event_type") == "input_rejected"):
            raise ValueError("top20_capture_sequence_or_rejection_invalid")
        if hydrated and "payload_ref" in row:
            raise ValueError("top20_capture_payload_not_hydrated")
        if "payload" in row or "payload_ref" in row:
            group = row.get("workload_id", "unsupported")
            if type(group) is not str:
                raise ValueError("top20_capture_input_coverage_invalid")
            observed[group] = observed.get(group, 0) + 1
    if event_count != manifest["written"]:
        raise ValueError("top20_capture_incomplete_or_not_drained")
    accepted = 0
    for group, bucket in coverage.items():
        if (type(group) is not str or type(bucket) is not dict
                or type(bucket.get("accepted")) is not int or bucket["accepted"] < 0
                or type(bucket.get("rejected")) is not int or bucket["rejected"] != 0
                or bucket["accepted"] != observed.get(group, 0)):
            raise ValueError("top20_capture_input_coverage_invalid")
        accepted += bucket["accepted"]
    if set(observed) - set(coverage) or accepted != manifest["payload_accepted"] or accepted != sum(observed.values()):
        raise ValueError("top20_capture_input_coverage_invalid")
    chunks = manifest.get("chunks")
    if type(chunks) is not list:
        raise ValueError("top20_capture_chunks_invalid")
    sequence, size = 1, 0
    for index, part in enumerate(chunks, 1):
        if (type(part) is not dict or part.get("name") != f"{index:06d}.jsonl"
                or any(type(part.get(key)) is not int for key in ("count", "first_seq", "last_seq", "bytes"))
                or part["count"] <= 0 or part["bytes"] <= 0 or part["first_seq"] != sequence
                or part["last_seq"] != sequence + part["count"] - 1
                or type(part.get("sha256")) is not str
                or re.fullmatch(r"[0-9a-f]{64}", part["sha256"]) is None):
            raise ValueError("top20_capture_chunks_invalid")
        sequence += part["count"]
        size += part["bytes"]
    # bytes_written includes payload bundles as well as scalar chunks.
    if sequence - 1 != event_count or size > manifest["bytes_written"]:
        raise ValueError("top20_capture_chunks_invalid")


def recorded_top20_queue_frontier(trace_id: str, *, component: str, **selection) -> dict:
    """Bounded, file-verified entry point; this still does not start a runner."""
    from .diagnostic_replay_contract import compile_top20_queue_frontier
    manifest, events = recorded_events(trace_id)
    result = compile_top20_queue_frontier(events, manifest=manifest, trace_id=trace_id,
                                         component=component, **selection)
    return {**result, "source_integrity": "checksummed_capture"}


def _top20_source_rows(trace_id, manifest):
    """One checksummed chunk at a time, preserving original source sequence."""
    if type(manifest.get("chunks")) is not list:
        raise ValueError("top20_capture_chunks_invalid")
    for part in manifest["chunks"]:
        if type(part) is not dict or type(part.get("name")) is not str:
            raise ValueError("top20_capture_chunks_invalid")
        count, first, last = 0, None, None
        for line in _chunk_bytes_from_manifest(trace_id, part["name"], manifest).splitlines():
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("recorded_chunk_json_invalid") from error
            if type(row) is not dict or type(row.get("seq")) is not int:
                raise ValueError("recorded_capture_sequence_invalid")
            first = row["seq"] if first is None else first
            last = row["seq"]
            count += 1
            yield row, len(line)
        if (not count or any(type(part.get(key)) is not int for key in ("count", "first_seq", "last_seq"))
                or part["count"] != count or part["first_seq"] != first or part["last_seq"] != last):
            raise ValueError("recorded_chunk_manifest_mismatch")


def recorded_top20_window_frontier(trace_id: str, *, component: str,
                                   window_start_seconds: float, window_end_seconds: float,
                                   include_workloads: tuple[str, ...] = (),
                                   exclude_workloads: tuple[str, ...] = ()) -> dict:
    """Verify a long capture, retaining only bounded queue-frontier causes.

    This is a preflight projection, never an executor input or a warm RAM seed.
    Delivery coverage is the capture prefix through the window end. Store-call
    selection remains the requested window. No source sequence is rewritten.
    """
    from .diagnostic_replay_contract import _compile_top20_queue_frontier_verified
    if (type(component) is not str or not component
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (window_start_seconds, window_end_seconds))
            or not 0 <= window_start_seconds < window_end_seconds <= 7200
            or window_end_seconds - window_start_seconds > 600):
        raise ValueError("recorded_window_out_of_bounds")
    manifest = status(trace_id)
    started = manifest.get("started_mono_ns")
    if type(started) is not int:
        raise ValueError("top20_capture_clock_invalid")
    start_ns = started + int(window_start_seconds * 1e9)
    end_ns = started + int(window_end_seconds * 1e9)
    if type(manifest.get("finished_mono_ns")) is not int or end_ns > manifest["finished_mono_ns"]:
        raise ValueError("top20_capture_window_out_of_bounds")
    # Only IDs survive the first pass. Prefix/selected row and byte budgets
    # bound both passes, independently of total capture length.
    limit = _MAX_TOP20_WINDOW_ROWS
    delivery_ids, parent_ids, message_ids, source_components, operation_ids = set(), set(), set(), set(), set()

    def inspected_rows():
        for row, _ in _top20_source_rows(trace_id, manifest):
            at = row.get("mono_ns")
            kind = row.get("event_type")
            if kind == "top20_delivery" and row.get("producer_component") == component:
                if type(at) is not int:
                    raise ValueError("recorded_time_missing")
                if at < end_ns:
                    identifier = row.get("delivery_id")
                    if type(identifier) is str:
                        delivery_ids.add(identifier)
                    if row.get("stage") == "enqueue":
                        refs = row.get("parent_input_ids", ())
                        if type(refs) not in (tuple, list) or any(type(ref) is not str for ref in refs):
                            raise ValueError("top20_delivery_parent_invalid")
                        parent_ids.update(refs)
                        if type(row.get("message_id")) is str:
                            message_ids.add(row["message_id"])
                        if type(row.get("source_component")) is str:
                            source_components.add(row["source_component"])
            elif kind == "operation_start":
                at = row.get("entered_mono_ns", at)
                if type(at) is not int:
                    raise ValueError("recorded_time_missing")
                if start_ns <= at < end_ns:
                    identifier = row.get("operation_id")
                    if type(identifier) is not str:
                        raise ValueError("recorded_operation_pair_invalid")
                    operation_ids.add(identifier)
            if sum(map(len, (delivery_ids, parent_ids, message_ids, source_components, operation_ids))) > limit:
                raise ValueError("top20_window_metadata_limit_exceeded")
            yield row

    _validate_top20_capture_manifest(manifest, inspected_rows(), trace_id=trace_id, hydrated=False)
    blobs = manifest.get("blobs", {})
    if type(blobs) is not dict:
        raise ValueError("top20_capture_payload_manifest_invalid")
    # Check every committed payload once, including payloads outside selection.
    for digest in blobs:
        _payload_bytes_from_manifest(trace_id, digest, manifest)
    rows, payload_cache = [], {}
    scalar_bytes, loaded_bytes = 0, 0
    for row, size in _top20_source_rows(trace_id, manifest):
        kind, at = row.get("event_type"), row.get("mono_ns")
        keep = ((kind == "top20_delivery" and row.get("producer_component") == component
                 and (row.get("delivery_id") in delivery_ids or type(at) is int and at < end_ns))
                or (kind in {"operation_start", "operation_end"} and row.get("operation_id") in operation_ids)
                or (kind == "collector_input" and (row.get("input_id") in parent_ids
                    or row.get("input_kind") == "initial_state" and row.get("producer_component") in source_components
                    and type(at) is int and at < end_ns))
                or (kind == "collector_message_receipt" and row.get("message_id") in message_ids)
                or (kind == "top20_realtime_input" and type(at) is int and at < end_ns)
                or (kind == "market_input" and type(at) is int and start_ns <= at < end_ns))
        if "payload_ref" in row:
            digest = row["payload_ref"]
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None or digest not in blobs:
                raise ValueError("top20_capture_payload_reference_missing")
        if not keep:
            continue
        scalar_bytes += size
        if len(rows) >= limit or scalar_bytes > _MAX_WINDOW_PAYLOAD_BYTES:
            raise ValueError("top20_window_metadata_limit_exceeded")
        digest = row.pop("payload_ref", None)
        if digest is not None:
            if digest not in payload_cache:
                payload = _payload_bytes_from_manifest(trace_id, digest, manifest)
                loaded_bytes += len(payload)
                if loaded_bytes > _MAX_WINDOW_PAYLOAD_BYTES:
                    raise ValueError("recorded_window_payload_limit_exceeded")
                try:
                    payload_cache[digest] = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("recorded_payload_json_invalid") from error
            row["payload"] = payload_cache[digest]
        row["source_seq"] = row["seq"]
        rows.append(row)
    if status(trace_id) != manifest:
        raise ValueError("top20_capture_manifest_changed")
    result = _compile_top20_queue_frontier_verified(rows, trace_id=trace_id, component=component,
        source_sequences_verified=True, started_mono_ns=started,
        window_start_seconds=window_start_seconds, window_end_seconds=window_end_seconds,
        include_workloads=include_workloads, exclude_workloads=exclude_workloads)
    return {**result, "source_integrity": "checksummed_capture",
        "input_manifest_hash": hashlib.sha256(json.dumps(manifest, sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
        "fixture_selection": {"window_start_seconds": window_start_seconds,
            "window_end_seconds": window_end_seconds,
            "include_workloads": tuple(include_workloads), "exclude_workloads": tuple(exclude_workloads)},
        "window_read": {
        "events_verified": manifest["written"], "events_retained": len(rows),
        "payload_bytes_loaded": loaded_bytes, "scalar_bytes_retained": scalar_bytes,
        "payload_blobs_verified": len(blobs), "delivery_prefix_end_seconds": window_end_seconds,
        "source_sequences": tuple(row["source_seq"] for row in rows),
        "scope": "queue_frontier_preflight_only", "warm_state_equivalent": False}}


def recorded_events(trace_id: str) -> tuple[dict, list[dict]]:
    manifest = status(trace_id)
    if (manifest.get("schema_version") not in {2, 3} or manifest.get("state") != "complete"
            or manifest.get("known_dropped") or manifest.get("input_capture_censored")):
        raise ValueError("recorded_capture_incomplete_or_old_schema")
    # Internal bounded reader; the future public window reader must stream a long capture.
    if manifest.get("written", 0) > 10_000:
        raise ValueError("recorded_window_reader_required")
    rows = []
    loaded_bytes = 0
    for part in manifest["chunks"]:
        chunk_start = len(rows)
        for line in chunk_bytes(trace_id, part["name"]).splitlines():
            row = json.loads(line)
            if (type(row) is not dict or type(row.get("seq")) is not int
                    or row["seq"] != len(rows) + 1):
                raise ValueError("recorded_capture_sequence_invalid")
            digest = row.pop("payload_ref", None)
            if digest is not None:
                content = payload_bytes(trace_id, digest)
                loaded_bytes += len(content)
                if loaded_bytes > 32 * 1024 * 1024:
                    raise ValueError("recorded_window_reader_required")
                row["payload"] = json.loads(content)
            rows.append(row)
        if (len(rows) == chunk_start or type(part.get("count")) is not int
                or type(part.get("first_seq")) is not int or type(part.get("last_seq")) is not int
                or part["count"] != len(rows) - chunk_start
                or part["first_seq"] != rows[chunk_start]["seq"]
                or part["last_seq"] != rows[-1]["seq"]):
            raise ValueError("recorded_chunk_manifest_mismatch")
    if len(rows) != manifest["written"] or [row["seq"] for row in rows] != list(range(1, manifest["last_seq"] + 1)):
        raise ValueError("recorded_capture_sequence_invalid")
    return manifest, rows


def recorded_window_events(trace_id: str, *, window_start_seconds: float,
                           window_end_seconds: float, mode: str,
                           collector_components: tuple[str, ...] = (),
                           capture_policy: str = 'complete',
                           include_workloads: tuple[str, ...] = (),
                           exclude_workloads: tuple[str, ...] = ()) -> tuple[dict, list[dict]]:
    """Read a bounded replay window while retaining only needed payloads.

    Chunk lines are still checksum- and sequence-verified across the complete
    capture. Operation payloads are resolved only for starts in the measured
    window. Collector mode also resolves its selected component's prefix, and
    is limited to a 15-minute capture-relative end until periodic RAM snapshots
    exist.
    """
    if capture_policy in {'scoped-operations', 'partial-operations'}:
        return _recorded_store_window(trace_id, window_start_seconds=window_start_seconds,
            window_end_seconds=window_end_seconds, mode=mode, collector_components=collector_components,
            include_workloads=include_workloads, exclude_workloads=exclude_workloads,
            capture_policy=capture_policy)
    if capture_policy != 'complete':
        raise ValueError('recorded_capture_policy_invalid')

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
    if (manifest.get("schema_version") not in {2, 3} or manifest.get("state") != "complete"
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
    market_input_cycles_touched = set()
    market_input_events_by_id: dict[str, list[dict]] = {}
    market_input_window_events = 0
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
            if event_type == "market_input" and type(row.get("input_id")) is str:
                market_input_events_by_id.setdefault(row["input_id"], []).append(row)
                if type(event_mono) is int and start_ns <= event_mono < end_ns:
                    market_input_window_events += 1
                    market_input_cycles_touched.add(row["input_id"])
            operation_mono = row.get("entered_mono_ns", event_mono)
            needs_payload = (
                event_type == "operation_start" and type(operation_mono) is int
                and start_ns <= operation_mono < end_ns
            ) or (
                event_type == "market_input" and type(event_mono) is int
                and start_ns <= event_mono < end_ns
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
    split_market_input_cycles = []
    for input_id in sorted(market_input_cycles_touched):
        cycle = market_input_events_by_id[input_id]
        in_window = [row for row in cycle if type(row.get("mono_ns")) is int
                     and start_ns <= row["mono_ns"] < end_ns]
        kinds = [row.get("input_kind") for row in in_window]
        if (not in_window or kinds[0] != "cycle_start" or kinds[-1] != "cycle_end"
                or len(in_window) != len(cycle)):
            split_market_input_cycles.append(input_id)
    return {**manifest, "window_read": {
        "start_seconds": window_start_seconds,
        "end_seconds": window_end_seconds,
        "mode": mode,
        "events_verified": written,
        "payload_blobs_loaded": len(payload_cache),
        "payload_bytes_loaded": payload_bytes_loaded,
        "collector_prefix_seconds": window_end_seconds if mode == "collector_with_background" else 0,
        "market_input_events_in_window": market_input_window_events,
        "market_input_cycles_touched": len(market_input_cycles_touched),
        "market_input_cycles_with_split_window": split_market_input_cycles,
    }}, rows


def _scoped_capture_manifest(manifest, trace_id):
    """Durable source admission only; selected inputs still need their own gate."""
    if (type(manifest) is not dict or manifest.get('trace_id') != trace_id
            or type(manifest.get('schema_version')) is not int or manifest['schema_version'] not in {2, 3}
            or manifest.get('state') not in {'complete', 'incomplete'}
            or manifest.get('coverage') != 'observed_paths_only'
            or manifest.get('input_capture_censored') is not False
            or type(manifest.get('payload_capture')) is not dict
            or manifest['payload_capture'].get('store_inputs') is not True
            or manifest.get('unknown_tail_loss', False) is not False):
        raise ValueError('recorded_capture_incomplete_or_old_schema')
    counters = ('accepted', 'written', 'last_seq', 'known_dropped', 'input_rejected',
                'payload_accepted', 'queued', 'pending_events', 'copy_reserved_bytes', 'bytes_written')
    if (any(type(manifest.get(key)) is not int or manifest[key] < 0 for key in counters)
            or not manifest['accepted'] == manifest['written'] == manifest['last_seq']
            or manifest['written'] > _CAPACITY
            or any(manifest[key] for key in ('known_dropped', 'queued', 'pending_events', 'copy_reserved_bytes'))):
        raise ValueError('recorded_capture_not_durably_drained')
    for key in ('charged_bytes', 'packed_events', 'packing_events', 'raw_charged_bytes',
                'scalar_charged_bytes', 'pending_bytes', 'packed_bytes', 'packing_bytes'):
        if key in manifest and (type(manifest[key]) is not int or manifest[key] != 0):
            raise ValueError('recorded_capture_not_durably_drained')
    if (manifest['state'] == 'incomplete' and (manifest['input_rejected'] <= 0
            or manifest.get('reason') not in {'expired', 'manual', 'master_off_or_expired'})):
        raise ValueError('recorded_capture_terminal_reason_invalid')
    started, finished = manifest.get('started_mono_ns'), manifest.get('finished_mono_ns')
    if type(started) is not int or type(finished) is not int or not 0 <= started < finished:
        raise ValueError('recorded_capture_clock_invalid')
    for key in ('drop_reasons', 'input_rejected_reasons'):
        bucket = manifest.get(key)
        if (type(bucket) is not dict or any(type(name) is not str or not name
                or type(count) is not int or count < 0 for name, count in bucket.items())):
            raise ValueError('recorded_capture_rejection_accounting_invalid')
    if sum(manifest['drop_reasons'].values()) or sum(manifest['input_rejected_reasons'].values()) != manifest['input_rejected']:
        raise ValueError('recorded_capture_rejection_accounting_invalid')
    chunks = manifest.get('chunks')
    if type(chunks) is not list or type(manifest.get('blobs')) is not dict:
        raise ValueError('recorded_capture_manifest_invalid')
    sequence, size = 1, 0
    for index, part in enumerate(chunks, 1):
        if (type(part) is not dict or part.get('name') != f'{index:06d}.jsonl'
                or any(type(part.get(key)) is not int for key in ('count', 'first_seq', 'last_seq', 'bytes'))
                or part['count'] <= 0 or part['bytes'] <= 0 or part['first_seq'] != sequence
                or part['last_seq'] != sequence + part['count'] - 1
                or type(part.get('sha256')) is not str
                or re.fullmatch(r'[0-9a-f]{64}', part['sha256']) is None):
            raise ValueError('recorded_chunk_manifest_mismatch')
        sequence += part['count']
        size += part['bytes']
    if sequence - 1 != manifest['written'] or size > manifest['bytes_written']:
        raise ValueError('recorded_chunk_manifest_mismatch')


def _recorded_store_window(trace_id, *, window_start_seconds, window_end_seconds, mode,
                           collector_components, include_workloads, exclude_workloads,
                           capture_policy='scoped-operations'):
    """Checksummed sparse native-operation frontier; no collector reconstruction.

    Full source validation streams twice with a fixed manifest. Only selected
    arguments are hydrated; source sequence and native DB-call associations stay
    intact, including ends recorded after the selected entry window.
    """
    from .diagnostic_replay_contract import _seal_store_window, compile_recorded_plan

    if (mode != 'recorded_operations' or collector_components or not include_workloads
            or any(type(name) is not str or not name for name in (*include_workloads, *exclude_workloads))
            or len(set(include_workloads)) != len(include_workloads)
            or len(set(exclude_workloads)) != len(exclude_workloads)
            or set(include_workloads) & set(exclude_workloads)):
        raise ValueError('recorded_scoped_operations_selection_required')
    if (any(type(value) not in (int, float) or not math.isfinite(value)
            for value in (window_start_seconds, window_end_seconds))
            or not 0 <= window_start_seconds < window_end_seconds <= 7200
            or window_end_seconds - window_start_seconds > 600):
        raise ValueError('recorded_window_out_of_bounds')
    # Pin the raw manifest as well as its parsed value. status() can expose an
    # in-process trace; this entry requires the durable disk manifest to match.
    if type(trace_id) is not str or not _valid_id(trace_id):
        raise ValueError('invalid_trace_id')
    path = _directory() / trace_id / 'manifest.json'
    if (path.parent.is_symlink() or path.is_symlink() or not path.is_file()
            or path.stat().st_size > 64 * 1024 * 1024):
        raise ValueError('recorded_capture_manifest_invalid')
    raw_manifest = path.read_bytes()
    manifest = status(trace_id)
    if json.loads(raw_manifest) != manifest:
        raise ValueError('recorded_capture_manifest_changed')
    _scoped_capture_manifest(manifest, trace_id)
    manifest_hash = hashlib.sha256(raw_manifest).hexdigest()
    started = manifest['started_mono_ns']
    start_ns = started + int(window_start_seconds * 1e9)
    end_ns = started + int(window_end_seconds * 1e9)
    if end_ns > manifest['finished_mono_ns']:
        raise ValueError('recorded_window_exceeds_capture_duration')
    selected = set(include_workloads)
    rows_by_seq, selected_ids = {}, set()
    seen_starts = set()
    rejection_reasons, coverage, observed_workloads, event_counts = {}, {}, set(), {}
    window_counts, omitted_inputs, omitted_ids = {}, [], set()
    scalar_bytes = 0

    def retain(row, size):
        nonlocal scalar_bytes
        if row['seq'] in rows_by_seq:
            return
        scalar_bytes += size
        if len(rows_by_seq) >= _MAX_TOP20_WINDOW_ROWS or scalar_bytes > _MAX_WINDOW_PAYLOAD_BYTES:
            raise ValueError('recorded_window_metadata_limit_exceeded')
        rows_by_seq[row['seq']] = row

    def verified_rows():
        count = 0
        for row, size in _top20_source_rows(trace_id, manifest):
            count += 1
            if row['seq'] != count:
                raise ValueError('recorded_capture_sequence_invalid')
            digest = row.get('payload_ref')
            if 'payload_ref' in row and (type(digest) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', digest) is None or digest not in manifest['blobs']):
                raise ValueError('recorded_payload_reference_missing')
            yield row, size
        if count != manifest['written']:
            raise ValueError('recorded_capture_sequence_invalid')

    for row, size in verified_rows():
        kind = row.get('event_type')
        if type(kind) is not str or not 0 < len(kind) <= 96:
            raise ValueError('recorded_event_type_invalid')
        event_counts[kind] = event_counts.get(kind, 0) + 1
        if len(event_counts) > 128:
            raise ValueError('recorded_window_metadata_limit_exceeded')
        workload = row.get('workload_id', 'unsupported')
        if kind == 'input_rejected' or 'payload_ref' in row:
            if type(workload) is not str or not 0 < len(workload) <= 160:
                raise ValueError('recorded_workload_missing')
            bucket = coverage.setdefault(workload, {'accepted': 0, 'rejected': 0})
            if len(coverage) > 4096:
                raise ValueError('recorded_window_metadata_limit_exceeded')
            bucket['rejected' if kind == 'input_rejected' else 'accepted'] += 1
        if kind == 'input_rejected':
            # A rejection with unknown time/owner cannot be safely attributed
            # outside this experiment. Never quietly treat it as excluded.
            at = row.get('entered_mono_ns', row.get('mono_ns'))
            reason = row.get('reason')
            if (type(at) is not int or not started <= at <= manifest['finished_mono_ns']
                    or type(reason) is not str or not 0 < len(reason) <= 160
                    or type(row.get('workload_id')) is not str or not row['workload_id']):
                raise ValueError('recorded_rejection_attribution_invalid')
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            if len(rejection_reasons) > 4096:
                raise ValueError('recorded_window_metadata_limit_exceeded')
        if kind not in {'operation_start', 'input_rejected', 'collector_input', 'market_input'}:
            continue
        at = row.get('entered_mono_ns', row.get('mono_ns'))
        if type(at) is not int:
            raise ValueError('recorded_time_missing')
        if not start_ns <= at < end_ns:
            continue
        if type(row.get('workload_id')) is not str or not row['workload_id']:
            raise ValueError('recorded_workload_missing')
        if kind in {'operation_start', 'input_rejected', 'collector_input'}:
            observed_workloads.add(row['workload_id'])
        bucket = window_counts.setdefault(row['workload_id'], {'operations': 0, 'rejected': 0})
        if kind == 'input_rejected':
            bucket['rejected'] += 1
            if workload in selected and capture_policy == 'partial-operations':
                omitted_inputs.append({key: row[key] for key in (
                    'seq', 'mono_ns', 'entered_mono_ns', 'operation_id', 'workload_id',
                    'method', 'reason', 'rejection_detail', 'producer_component') if key in row})
                if type(row.get('operation_id')) is str:
                    omitted_ids.add(row['operation_id'])
                # Keep original rejection evidence inside the sealed projection,
                # without passing it off as an executable captured input.
                row['original_event_type'] = kind
                row['event_type'] = 'omitted_input'
        if kind == 'operation_start':
            bucket['operations'] += 1
            identifier = row.get('operation_id')
            if type(identifier) is not str or not identifier or identifier in seen_starts:
                raise ValueError('recorded_operation_pair_invalid')
            seen_starts.add(identifier)
            if workload in selected:
                selected_ids.add(identifier)
        # Keep unselected start metadata so compile reports exclusions, but
        # strip their payload references. They can never become replay inputs.
        if workload not in selected or kind != 'operation_start':
            row.pop('payload_ref', None)
        retain(row, size)
    if (rejection_reasons != manifest['input_rejected_reasons']
            or coverage != manifest.get('input_coverage')
            or sum(bucket['accepted'] for bucket in coverage.values()) != manifest['payload_accepted']
            or 'event_counts' in manifest and event_counts != manifest['event_counts']):
        raise ValueError('recorded_capture_rejection_accounting_invalid')
    if (set(include_workloads) | set(exclude_workloads)) - observed_workloads:
        raise ValueError('recorded_workload_not_observed')
    if (capture_policy != 'partial-operations'
            and any(window_counts.get(group, {}).get('rejected', 0) for group in selected)):
        raise ValueError('recorded_selected_input_unsupported')
    selected_ids -= omitted_ids
    for row in rows_by_seq.values():
        if row.get('event_type') == 'operation_start' and row.get('operation_id') in omitted_ids:
            row['original_event_type'] = row['event_type']
            row['event_type'] = 'omitted_operation_start'
            row.pop('payload_ref', None)
    if not selected_ids:
        raise ValueError('recorded_selection_empty')
    ends, calls = set(), {}
    for row, size in verified_rows():
        kind = row.get('event_type')
        if kind == 'operation_end' and row.get('operation_id') in selected_ids:
            identifier = row['operation_id']
            if identifier in ends:
                raise ValueError('recorded_operation_pair_invalid')
            ends.add(identifier)
            retain(row, size)
        elif kind in {'call_start', 'call_end'} and row.get('input_operation_id') in selected_ids:
            call_id = row.get('call_id')
            if type(call_id) is not str or not call_id:
                raise ValueError('recorded_db_call_pair_invalid')
            pair = calls.setdefault(call_id, {})
            if kind in pair:
                raise ValueError('recorded_db_call_pair_invalid')
            pair[kind] = row['input_operation_id']
            retain(row, size)
    if ends != selected_ids:
        raise ValueError('recorded_operation_censored_or_codec_invalid')
    if any(set(pair) != {'call_start', 'call_end'} or len(set(pair.values())) != 1 for pair in calls.values()):
        raise ValueError('recorded_db_call_pair_invalid')
    rows, cache, loaded_bytes = [], {}, 0
    for row in sorted(rows_by_seq.values(), key=lambda item: item['seq']):
        digest = row.pop('payload_ref', None)
        if digest is not None:
            if digest not in cache:
                payload = _payload_bytes_from_manifest(trace_id, digest, manifest)
                loaded_bytes += len(payload)
                if loaded_bytes > _MAX_WINDOW_PAYLOAD_BYTES:
                    raise ValueError('recorded_window_payload_limit_exceeded')
                try:
                    cache[digest] = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError('recorded_payload_json_invalid') from error
            row['payload'] = cache[digest]
        row['source_seq'] = row['seq']
        rows.append(row)
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest_hash or status(trace_id) != manifest:
        raise ValueError('recorded_capture_manifest_changed')
    selection = dict(started_mono_ns=started, window_start_seconds=window_start_seconds,
        window_end_seconds=window_end_seconds, include_workloads=tuple(include_workloads),
        exclude_workloads=tuple(exclude_workloads), mode=mode, collector_components=())
    events = _seal_store_window(rows, manifest_hash=manifest_hash, **selection)
    plan = compile_recorded_plan(events, **selection)
    report = dict(capture_policy=capture_policy, source_integrity='checksummed_capture',
        source_manifest_sha256=manifest_hash, original_capture_state=manifest['state'],
        start_seconds=window_start_seconds, end_seconds=window_end_seconds, mode=mode,
        events_verified=manifest['written'], events_retained=len(rows), scalar_bytes_retained=scalar_bytes,
        payload_blobs_loaded=len(cache), payload_bytes_loaded=loaded_bytes,
        selected_payload_hashes=sorted(cache), selected_workloads=list(plan.selected_workloads),
        excluded_workloads=sorted(observed_workloads - selected), window_workloads=window_counts,
        actor_known=plan.actor_known, source_state_equivalent=False,
        omitted_input_count=len(omitted_inputs), omitted_inputs=omitted_inputs,
        missing_load_reconstructed=False, selected_input_complete=not omitted_inputs,
        fidelity='surviving_native_operations_only' if omitted_inputs else 'selected_native_operations_only')
    return {**manifest, 'window_read': report}, events


def _manifest(directory: Path, session: dict) -> None:
    target = directory / "manifest.json"
    temporary = directory / "manifest.partial"
    with _LOCK:
        snapshot = _public(session)
    with temporary.open("wb") as file:
        buffer = bytearray()
        for fragment in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(snapshot):
            buffer.extend(fragment.encode('utf-8'))
            if len(buffer) >= _DEFERRED_WRITE_BLOCK_BYTES:
                _write(file, buffer, session)
                buffer.clear()
        if buffer:
            _write(file, buffer, session)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)


def _write(file, content, session: dict) -> None:
    """Pace actual writes, without accumulating credit during slow fsync calls."""
    rate = session.get('write_bytes_per_second') if session.get('state') != 'running' else None
    if not rate:
        file.write(content)
        return
    for offset in range(0, len(content), _DEFERRED_WRITE_BLOCK_BYTES):
        block = content[offset:offset + _DEFERRED_WRITE_BLOCK_BYTES]
        delay = len(block) / rate
        time.sleep(delay)
        file.write(block)
        with _LOCK:
            session['persistence_throttle_seconds'] += delay


def _release_credit(records):
    """Preview shared-context release without mutating ownership before admission."""
    refs = {}

    def preview(node):
        remaining = refs.get(id(node), node._refs) - 1
        if remaining < 0:
            raise RuntimeError('delivery_retention_underflow')
        refs[id(node)] = remaining
        return node.charge + sum(preview(child) for child in node.children) if remaining == 0 else 0

    charge = scalar = 0
    for record in records:
        if isinstance(record, DeliveryStage):
            freed = record.own_charge + preview(record.identity.node)
            charge += freed
            scalar += freed
        else:
            charge += record['_memory_charge']
            scalar += record['_scalar_charge']
    return charge, scalar


def _pack_deferred(session):
    """The existing worker alone transfers raw ownership into immutable byte blocks."""
    records = []
    raw_bytes = 0
    with _LOCK:
        while _QUEUE and len(records) < 128:
            charge = _QUEUE[0]['_memory_charge']
            if records and raw_bytes + charge > 4 * 1024 * 1024:
                break
            records.append(_QUEUE.popleft())
            raw_bytes += charge
        session['packing_events'] = len(records)
    if not records:
        return
    started = time.perf_counter()
    try:
        processed = 0

        def bounded_records():
            nonlocal processed
            for record in records:
                if processed and time.perf_counter() - started >= .010:
                    break
                processed += 1
                yield record

        segments = pack_records(bounded_records(), max_metadata_bytes=_MAX_CHUNK_BYTES,
                                max_payload_bytes=_MAX_BLOB_BYTES)
        new_charge = sum(segment.charge for segment in segments)
        new_scalar = sum(segment.scalar_charge for segment in segments)
        with _LOCK:
            credit, _ = _release_credit(records[:processed])
            if (session['charged_bytes'] - credit + session['copy_reserved_bytes'] + new_charge
                    > session['memory_limit_bytes'] - _worker_reserve(session)):
                raise RuntimeError('trace_ram_transfer_budget')
            old_blocks = len(_PACKED)
            try:
                _PACKED.extend(segments)
            except BaseException:
                while len(_PACKED) > old_blocks:
                    _PACKED.pop()
                raise
            for record in records[:processed]:
                _release_record(session, record)
            _QUEUE.extendleft(reversed(records[processed:]))
            session['charged_bytes'] += new_charge
            session['scalar_charged_bytes'] += new_scalar
            session['packed_bytes'] += new_charge
            session['packed_segments'] += len(segments)
            session['packed_events'] += processed
            session['packing_events'] = 0
            session['memory_high_water'] = max(session['memory_high_water'],
                                               session['charged_bytes'] + session['copy_reserved_bytes'])
            elapsed = (time.perf_counter() - started) * 1000
            session['pack_ms_total'] += elapsed
            session['pack_ms_max'] = max(session['pack_ms_max'], elapsed)
    except BaseException:
        with _LOCK:
            _QUEUE.extendleft(reversed(records))
            session['packing_events'] = 0
        raise
    finally:
        records.clear()


def _hold_deferred(session: dict) -> bool:
    """Seal the 65-minute input window; master expiry cannot force early I/O."""
    if session['state'] == 'stopping':
        with _LOCK:
            session.update(state='awaiting_persistence', captured_at=time.time(),
                           captured_mono_ns=time.monotonic_ns())
        path = control_path()
        if path is not None:
            try:
                _set_trace(path, False, 60, expected_session=session['master_session'])
            except ValueError:
                pass  # Master OFF/expiry already revoked this capture child.
    if _ABORT.is_set():
        with _LOCK:
            _discard_queue(session)
            session.update(state='interrupted', reason='server_shutdown_before_persistence',
                           input_capture_censored=True, charged_bytes=0, scalar_charged_bytes=0)
        return False
    if session['state'] == 'running':
        return False
    with _LOCK:
        if _QUEUE or session['packing_events'] or session['copy_reserved_bytes']:
            if _QUEUE:
                _WAKE.set()
            return False
    if time.time() < session['persist_at']:
        _WAKE.wait(max(0, min(30, session['persist_at'] - time.time())))
        return False
    with _LOCK:
        # A copy admitted before capture stopped still owns its reservation.
        if session['copy_reserved_bytes']:
            return False
        session.update(state='persisting', persistence_started_at=time.time())
    return True


def _drain(session: dict, run_lock) -> None:
    directory = None
    pending: deque[dict] = deque()
    stored_blobs: dict[str, dict] = {}
    blob_bytes = 0
    chunk_bytes_written = 0
    next_manifest = time.monotonic() + 60
    try:
        directory = _directory() / session["trace_id"]
        directory.mkdir(mode=0o700, exist_ok=False)
        _manifest(directory, session)
        next_flush = time.monotonic() + 5
        next_control = 0.0
        while True:
            packing = session.get('persist_at') is not None and session['state'] != 'persisting'
            _WAKE.wait(0.01 if packing else 0.5)
            high_water = _WAKE.is_set()
            _WAKE.clear()
            if not _STOP.is_set() and session['state'] == 'running' and time.monotonic() >= next_control:
                next_control = time.monotonic() + 0.5
                master = diagnostic_tool_status()
                child = trace_status()
                if (not master["enabled"] or master["session_id"] != session["master_session"]
                        or not child["enabled"] or time.time() >= session["expires_at"]):
                    with _LOCK:
                        session["state"] = "stopping"
                        session["reason"] = "master_off_or_expired" if not master["enabled"] else "expired"
                    _STOP.set()
            if session.get('persist_at') is not None and session['state'] != 'persisting':
                if not _ABORT.is_set():
                    _pack_deferred(session)
                    with _LOCK:
                        if _QUEUE:
                            _WAKE.set()
                    time.sleep(0)  # Yield between bounded CPU batches without changing global GC.
                if not _hold_deferred(session):
                    if session['state'] == 'interrupted':
                        _manifest(directory, session)
                        return
                    continue
            if not _STOP.is_set() and not high_water and not pending and time.monotonic() < next_flush:
                continue
            next_flush = time.monotonic() + 5
            if not pending:
                if session.get('persist_at') is not None:
                    # Only this worker owns block cursors. Decode outside the admission lock.
                    metadata_bytes = 0
                    for segment in _PACKED:
                        while len(pending) < 4096 and metadata_bytes < 8 * 1024 * 1024:
                            row = segment.take()
                            if row is None:
                                break
                            pending.append(row)
                            metadata_bytes += row.payload_offset - row.offset
                        if len(pending) >= 4096 or metadata_bytes >= 8 * 1024 * 1024:
                            break
                else:
                    with _LOCK:
                        while _QUEUE and len(pending) < 4096:
                            pending.append(_QUEUE.popleft())
                with _LOCK:
                    session["pending_events"] = len(pending)
            if pending:
                flush_started = time.perf_counter()
                # Encoding stays in this worker. Keep the uncommitted suffix here
                # so one periodic flush stays bounded without reordering the queue.
                lines: list[bytes] = []
                chunk_blobs: dict[str, dict] = {}
                content_bytes = 0
                chunk_id = len(session["chunks"]) + 1
                bundle_name = f"{chunk_id:06d}.payloads"
                bundle_temporary = directory / f"{bundle_name}.partial"
                bundle_file = None
                batch_bytes = 0
                try:
                    for row in pending:
                        encoded = {key: value for key, value in row.items()
                                   if key not in {"payload", "_memory_charge", "_scalar_charge"}}
                        digest, payload = None, None
                        if isinstance(row, PackedRow):
                            payload = row.payload_bytes()
                        elif "payload" in row:
                            payload = json.dumps(row["payload"], ensure_ascii=False,
                                                 separators=(",", ":")).encode("utf-8")
                        if payload is not None:
                            if len(payload) > _MAX_BLOB_BYTES:
                                raise OSError("trace_event_too_large")
                            digest = hashlib.sha256(payload).hexdigest()
                            encoded["payload_ref"] = digest
                        line = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"),
                                          default=str).encode("utf-8") + b"\n"
                        new_blob = digest is not None and digest not in stored_blobs and digest not in chunk_blobs
                        added_bytes = len(payload) if new_blob else 0
                        if (content_bytes + len(line) > _MAX_CHUNK_BYTES
                                or batch_bytes + added_bytes > _MAX_PAYLOAD_BATCH_BYTES):
                            if not lines:
                                raise OSError("trace_event_too_large")
                            break
                        if (blob_bytes + chunk_bytes_written + batch_bytes + added_bytes
                                + content_bytes + len(line) > session["storage_limit_bytes"]):
                            raise OSError("trace_file_limit")
                        if new_blob:
                            if bundle_file is None:
                                bundle_file = bundle_temporary.open("wb")
                            _write(bundle_file, payload, session)
                            chunk_blobs[digest] = {"bytes": len(payload), "name": bundle_name,
                                                   "offset": batch_bytes}
                            batch_bytes += len(payload)
                        elif digest is not None and digest in stored_blobs:
                            chunk_blobs[digest] = stored_blobs[digest]
                        lines.append(line)
                        content_bytes += len(line)
                        del payload
                    if bundle_file is not None:
                        bundle_file.flush()
                        sync_started = time.perf_counter()
                        os.fsync(bundle_file.fileno())
                        sync_ms = (time.perf_counter() - sync_started) * 1000
                        with _LOCK:
                            session["payload_fsync_count"] += 1
                            session["payload_fsync_ms_total"] += sync_ms
                            session["payload_fsync_ms_max"] = max(session["payload_fsync_ms_max"], sync_ms)
                finally:
                    if bundle_file is not None:
                        bundle_file.close()
                if batch_bytes:
                    os.replace(bundle_temporary, directory / bundle_name)
                content = b"".join(lines)
                name = f"{chunk_id:06d}.jsonl"
                temporary = directory / f"{name}.partial"
                with temporary.open("wb") as file:
                    _write(file, content, session)
                    file.flush()
                    fsync_started = time.perf_counter()
                    os.fsync(file.fileno())
                    fsync_ms = (time.perf_counter() - fsync_started) * 1000
                os.replace(temporary, directory / name)
                flush_ms = (time.perf_counter() - flush_started) * 1000
                count = len(lines)
                checksum = hashlib.sha256(content).hexdigest()
                chunk_bytes_written += len(content)
                blob_bytes += batch_bytes
                stored_blobs.update(chunk_blobs)
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
                        _release_record(session, committed)
                    session["blobs"].update(chunk_blobs)
                    session["pending_events"] = len(pending)
                    session["written"] += count
                    session["bytes_written"] = chunk_bytes_written + blob_bytes
                # During deferred persistence, avoid rewriting an ever-growing
                # blob index for each small chunk. Only durable chunks are named.
                if session.get('persist_at') is None or time.monotonic() >= next_manifest:
                    _manifest(directory, session)
                    next_manifest = time.monotonic() + 60
                if session.get('persist_at') is not None:
                    # Back off further after slow durable writes; never catch up
                    # by flooding the device following an I/O stall.
                    cooldown = min(5.0, max(0.25, flush_ms / 1000))
                    time.sleep(cooldown)
                    with _LOCK:
                        session['persistence_throttle_seconds'] += cooldown
                # Drain a backlog immediately instead of waiting another five seconds per chunk.
                if pending or _QUEUE or _PACKED:
                    _WAKE.set()
            if _STOP.is_set():
                with _LOCK:
                    if not pending and not _QUEUE and not _PACKED and not session["copy_reserved_bytes"]:
                        break
        with _LOCK:
            finished = {
                "state": "complete" if not session["known_dropped"] and not session["input_capture_censored"]
                    and not session['input_rejected'] else "incomplete",
                "finished_at": time.time(),
                "finished_mono_ns": session.get('captured_mono_ns', time.monotonic_ns()),
                "persisted_at": time.time(),
            }
            final_manifest = {**session, **finished}
        # Replay must not observe a terminal success before its manifest is durable.
        # Keep stop/status in "stopping" while this worker performs the final sync.
        _manifest(directory, final_manifest)
        with _LOCK:
            session.update(finished)
    except Exception as error:
        with _LOCK:
            # The failed suffix still owns its shared contexts until explicit
            # discard/new capture; do not leave invisible retained references.
            if session.get('persist_at') is not None:
                for segment in _PACKED:
                    segment.rewind()
                pending.clear()
            else:
                while pending:
                    _QUEUE.appendleft(pending.pop())
            session['pending_events'] = 0
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
