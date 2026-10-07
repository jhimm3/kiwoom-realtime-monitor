"""Strict, run-local fixture for the native TOP20 JsonRecordOutbox.

Seed identity is separate from immutable DB baseline v1/v2. Atomic replacement
matches the native outbox; this is not a power-loss/fsync durability guarantee.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import stat
from uuid import uuid4
from datetime import datetime

from .persistent_outbox import JsonRecordOutbox


_LIMIT = 8 * 1024 * 1024
_FIELDS = {"minute", "market_values", "codes", "market_counts", "cohort_segments", "capture_state", "total"}


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("top20_outbox_duplicate_json_key")
        value[key] = item
    return value


def _read_bounded(path, limit):
    if path.stat().st_size > limit:
        raise ValueError("top20_outbox_seed_size_invalid")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("top20_outbox_seed_size_invalid")
    return data


def _decode(data):
    if type(data) is not bytes or len(data) > _LIMIT:
        raise ValueError("top20_outbox_seed_size_invalid")
    try:
        values = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("top20_outbox_corrupt") from error
    if type(values) is not dict or len(values) > 4096:
        raise ValueError("top20_outbox_records_invalid")
    for key, payload in values.items():
        if type(payload) is not dict or set(payload) != _FIELDS or payload["minute"] != key:
            raise ValueError("top20_outbox_record_invalid")
        try:
            minute = datetime.fromisoformat(key)
        except (TypeError, ValueError) as error:
            raise ValueError("top20_outbox_minute_invalid") from error
        if minute.tzinfo is None or minute.second or minute.microsecond:
            raise ValueError("top20_outbox_minute_invalid")
        numbers, counts, codes, segments = (payload[k] for k in (
            "market_values", "market_counts", "codes", "cohort_segments"))
        if (type(numbers) is not list or len(numbers) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in numbers)
                or type(counts) is not list or len(counts) != 3
                or any(type(x) is not int or x < 0 for x in counts)
                or type(codes) is not list or len(codes) > 200
                or any(type(x) is not str or not x or len(x) > 64 for x in codes)
                or len(set(codes)) != len(codes)
                or type(segments) is not list or len(segments) > 120
                or any(type(x) is not list or len(x) != 2 or type(x[0]) is not str
                       or type(x[1]) is not list or len(x[1]) > 200
                       or any(type(code) is not str or not code or len(code) > 64 for code in x[1])
                       for x in segments)
                or payload["capture_state"] not in {"realtime_complete", "partial", "missing"}
                or type(payload["total"]) not in (int, float)
                or not math.isfinite(payload["total"])
                or not math.isclose(payload["total"], sum(numbers), rel_tol=1e-12, abs_tol=1e-9)):
            raise ValueError("top20_outbox_record_invalid")
    return values


class Top20ReplayOutbox:
    """Own only a newly created child directory under an explicit fixture root."""

    def __init__(self, parent, *, records=None):
        parent = Path(parent).absolute()
        if any(p.is_symlink() for p in (parent, *parent.parents)):
            raise ValueError("top20_outbox_root_symlink")
        parent = parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError("top20_outbox_root_invalid")
        seed = _encode({} if records is None else records)
        _decode(seed)
        self.seed_bytes = seed
        self.seed_id = hashlib.sha256(seed).hexdigest()
        self.root = parent / ("top20-replay-" + uuid4().hex)
        self.root.mkdir(mode=0o700)
        info = self.root.stat()
        self._root_identity = (info.st_dev, info.st_ino)
        self.path = self.root / "top20-index.json"
        self._marker = _encode({"version": 1, "owner": self.root.name, "seed_id": self.seed_id})
        (self.root / ".owner").write_bytes(self._marker)
        self.path.write_bytes(seed)
        self._runtime = None
        self._baseline_verified = True
        self._check_paths()

    def _check_paths(self):
        if (any(p.is_symlink() for p in (self.root, *self.root.parents))
                or not self.root.is_dir()):
            raise RuntimeError("top20_outbox_owned_root_changed")
        info = self.root.stat()
        if (info.st_dev, info.st_ino) != self._root_identity:
            raise RuntimeError("top20_outbox_owned_root_changed")
        names = []
        for path in self.root.iterdir():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or path.name not in {".owner", self.path.name}
                    and not re.fullmatch(re.escape(self.path.name) + r"\.tmp-\d+", path.name)):
                raise RuntimeError("top20_outbox_foreign_or_linked_file")
            names.append(path.name)
        if (".owner" not in names or self.path.name not in names
                or _read_bounded(self.root / ".owner", len(self._marker)) != self._marker):
            raise RuntimeError("top20_outbox_owner_changed")

    def identity(self):
        self.require_baseline()
        return {"version": "top20-file-outbox/v1", "seed_id": self.seed_id,
                "records": len(_decode(self.seed_bytes)), "bytes": len(self.seed_bytes),
                "file": self.path.name, "atomic_replace_only": True}

    def require_baseline(self):
        self._check_paths()
        self._validate_seed()
        data = _read_bounded(self.path, _LIMIT)
        _decode(data)  # Do not let native load() hide corruption as an empty seed.
        if (not self._baseline_verified or data != self.seed_bytes
                or any(p.name not in {".owner", self.path.name} for p in self.root.iterdir())):
            raise RuntimeError("top20_outbox_baseline_not_restored")

    def _validate_seed(self):
        _decode(self.seed_bytes)
        if hashlib.sha256(self.seed_bytes).hexdigest() != self.seed_id:
            raise RuntimeError("top20_outbox_seed_changed")

    def begin_run(self, runtime):
        from .diagnostic_replay_runtime import ReplayRuntimeScope
        if type(runtime) is not ReplayRuntimeScope or runtime.status()["phase"] != "running":
            raise ValueError("top20_outbox_runtime_required")
        self.require_baseline()
        if self._runtime is not None:
            self._runtime.require_drained()
        self._runtime = runtime
        self._baseline_verified = False
        return JsonRecordOutbox(self.path)

    def restore(self, runtime):
        if runtime is not self._runtime:
            raise RuntimeError("top20_outbox_runtime_owner_changed")
        runtime.require_drained()
        self._baseline_verified = False
        self._check_paths()
        self._validate_seed()
        records = _decode(self.seed_bytes)
        # Only native-owned temporary files in our exclusive child directory.
        # A failed previous replace is not evidence of a restored outbox.
        JsonRecordOutbox(self.path)._write(records)
        self._check_paths()
        for path in self.root.iterdir():
            if path.name not in {".owner", self.path.name}:
                path.unlink()
        self._baseline_verified = True
        self.require_baseline()
        return {"outbox_restored": True, "seed_id": self.seed_id}


def restore_top20_replay_baseline(lease, baseline_id, outbox, runtime):
    """DB/file are separate commits: report completion only when both verify."""
    runtime.require_drained()
    if outbox._runtime is not runtime:
        raise RuntimeError("top20_outbox_runtime_owner_changed")
    outbox._check_paths()
    outbox._validate_seed()
    database = lease.restore(baseline_id)
    files = outbox.restore(runtime)
    return {"cleanup_complete": True, "database": database, "outbox": files}
