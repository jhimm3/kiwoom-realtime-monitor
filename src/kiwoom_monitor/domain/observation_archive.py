"""A storage-only, fixed observation export batch."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from .research_contract import OBSERVATION_REVISION_SCHEMA_VERSION, RESEARCH_OBSERVATION_KINDS
from .time_basis import KST


# Use the existing 32 MiB research preparation budget for the entire HTTP body,
# including the JSON envelope and escaping of the two original UTF-8 files.
MAX_OBSERVATION_ARCHIVE_BYTES = 32 * 1024 * 1024
OBSERVATION_ARCHIVE_PATH = "/api/v1/archive/observation-batches"
PROGRAM_FLOW_ARCHIVE_KIND = "program_flow"
SQLITE_ARCHIVE_DURABILITY = "sqlite_wal_synchronous_full"
POSTGRES_ARCHIVE_DURABILITY = "postgresql_fsync_synchronous_commit_on"
_RECEIPT_FIELDS = {"status", "accepted_at", "receipt_sequence", "durability", "storage_only"}


class ObservationArchiveConflict(ValueError):
    """An immutable batch identity already belongs to different bytes."""


class ObservationArchiveLimit(ValueError):
    """The selected source range cannot fit in one bounded archive upload."""


def archive_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def archive_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(value: str | bytes) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("archive JSON object required")
    return decoded


def _aware(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("archive timestamp required")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("archive timestamp must be timezone-aware")
    return moment


def archive_batch_id(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError("archive batch ID must be a fixed export watermark")
    return value


def archive_source_stream(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 96
            or any(not (character.isalnum() or character in "_-") for character in value)):
        raise ValueError("archive source stream must match a producer grant owner")
    return value


def _program_snapshot_ids(observations: str, start: datetime, end: datetime) -> list[str]:
    if (start.utcoffset() != KST.utcoffset(None) or start.time() != time()
            or end != start + timedelta(days=1)):
        raise ValueError("program flow archive requires one KST trading date")
    prefix = start.strftime("%Y%m%d") + ":"
    previous = ("", "")
    identities = []
    if not observations or not observations.endswith("\n"):
        raise ValueError("program flow archive requires a complete snapshot batch")
    for line in observations[:-1].split("\n"):
        row = _object(line)
        if (set(row) != {"subject", "snapshot_key", "saved_at", "payload"}
                or not isinstance(row["subject"], str) or not row["subject"]
                or not isinstance(row["snapshot_key"], str) or not row["snapshot_key"].startswith(prefix)
                or not isinstance(row["payload"], dict) or archive_json(row) != line.encode("utf-8")):
            raise ValueError("invalid program flow archive snapshot")
        _aware(row["saved_at"])
        key = (row["subject"], row["snapshot_key"])
        if key <= previous:
            raise ValueError("program flow archive snapshots must be unique and ordered")
        previous = key
        identities.append(archive_sha256(line.encode("utf-8")))
    return identities


@dataclass(frozen=True)
class ObservationArchiveBatch:
    raw: bytes
    source_stream: str
    batch_id: str
    kind: str
    content_sha256: str
    captured_start: str
    captured_end: str
    revision_count: int
    revision_ids_hash: str
    first_source_accepted_sequence: int | None
    last_source_accepted_sequence: int | None

    @classmethod
    def from_artifact(cls, source_stream: str, manifest: bytes, observations: bytes) -> ObservationArchiveBatch:
        return cls.parse(archive_json({
            "version": 1, "source_stream": source_stream, "manifest": manifest.decode("utf-8"),
            "observations": observations.decode("utf-8"),
        }))

    @classmethod
    def parse(cls, raw: bytes, *, expected_sha256: str | None = None) -> ObservationArchiveBatch:
        if len(raw) > MAX_OBSERVATION_ARCHIVE_BYTES:
            raise ObservationArchiveLimit("observation archive batch exceeds the bounded research input budget")
        digest = archive_sha256(raw)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("observation archive upload SHA256 does not match")
        body = _object(raw)
        if (set(body) != {"version", "source_stream", "manifest", "observations"}
                or type(body["version"]) is not int or body["version"] != 1
                or not all(isinstance(body[key], str) for key in ("source_stream", "manifest", "observations"))
                or archive_json(body) != raw):
            raise ValueError("invalid observation archive batch envelope")
        source_stream = archive_source_stream(body["source_stream"])
        manifest = _object(body["manifest"])
        batch_id = archive_batch_id(manifest.get("fixed_watermark"))
        kinds = manifest.get("kinds")
        kind = kinds[0] if isinstance(kinds, list) and len(kinds) == 1 and isinstance(kinds[0], str) else ""
        captured = manifest.get("captured_range")
        observations = body["observations"]
        if (manifest.get("dataset_id") != batch_id or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1 or kind not in RESEARCH_OBSERVATION_KINDS | {PROGRAM_FLOW_ARCHIVE_KIND}
                or not isinstance(captured, dict) or set(captured) != {"start", "end"}
                or not isinstance(manifest.get("subject"), str) or manifest.get("observations_file") != "observations.jsonl"
                or manifest.get("bundle_version") is not None or manifest.get("research_session_profile") is not None
                or manifest.get("observations_file_hash") != archive_sha256(observations.encode("utf-8"))
                or manifest.get("theme_snapshots_file") != "theme_snapshots.jsonl"
                or manifest.get("theme_snapshots_file_hash") != archive_sha256(b"")
                or type(manifest.get("theme_snapshot_count")) is not int or manifest["theme_snapshot_count"] != 0):
            raise ValueError("invalid observation archive source manifest")
        start, end = _aware(captured["start"]), _aware(captured["end"])
        if not timedelta(0) < end - start <= timedelta(days=1):
            raise ValueError("archive requires one fixed observation export range")
        _aware(manifest.get("exported_at"))
        revision_ids, sequences = [], set()
        if kind == PROGRAM_FLOW_ARCHIVE_KIND:
            if manifest["subject"]:
                raise ValueError("program flow archive requires the completed trading date scope")
            revision_ids = _program_snapshot_ids(observations, start, end)
            lines = ()
        else:
            lines = observations.splitlines()
        for ordinal, line in enumerate(lines, start=1):
            row = _object(line)
            sequence = row.get("accepted_sequence")
            if (row.get("kind") != kind or type(row.get("ordinal")) is not int or row["ordinal"] != ordinal
                    or type(sequence) is not int or sequence < 1 or sequence in sequences
                    or type(row.get("schema_version")) is not int
                    or row["schema_version"] != OBSERVATION_REVISION_SCHEMA_VERSION
                    or not isinstance(row.get("subject"), str)
                    or (manifest["subject"] and row["subject"] != manifest["subject"])
                    or not isinstance(row.get("payload"), dict)
                    or not start <= _aware(row.get("available_at")) < end):
                raise ValueError("observation revision is outside the fixed archive contract")
            for key in ("source_id", "observation_key", "source_session_id", "source_sequence", "payload_hash"):
                if not isinstance(row.get(key), str):
                    raise ValueError("observation revision has missing source identity")
            if len(row["payload_hash"]) != 64 or any(char not in "0123456789abcdef" for char in row["payload_hash"]):
                raise ValueError("observation revision has an invalid original payload hash")
            for key in ("effective_at", "received_at"):
                if key not in row:
                    raise ValueError("observation revision has missing source timestamp")
                if row[key] is not None:
                    _aware(row[key])
            revision_ids.append(archive_batch_id(row.get("revision_id")))
            if "revision_of" not in row:
                raise ValueError("observation revision has missing correction reference")
            if row["revision_of"] is not None:
                archive_batch_id(row["revision_of"])
            sequences.add(sequence)
        count = len(revision_ids)
        ids_hash = archive_sha256("\n".join(revision_ids).encode("utf-8"))
        if (len(set(revision_ids)) != count or type(manifest.get("revision_count")) is not int
                or manifest["revision_count"] != count or manifest.get("revision_ids_hash") != ids_hash):
            raise ValueError("archive does not match its fixed revision membership")
        return cls(raw, source_stream, batch_id, kind, digest, captured["start"], captured["end"], count, ids_hash,
                   min(sequences, default=None), max(sequences, default=None))

    def document(self) -> dict[str, Any]:
        return _object(self.raw)

    def identity(self) -> dict[str, Any]:
        return {
            "version": 1, "source_stream": self.source_stream, "batch_id": self.batch_id, "kind": self.kind,
            "content_sha256": self.content_sha256,
            "captured_range": {"start": self.captured_start, "end": self.captured_end},
            "revision_count": self.revision_count, "revision_ids_hash": self.revision_ids_hash,
            "first_source_accepted_sequence": self.first_source_accepted_sequence,
            "last_source_accepted_sequence": self.last_source_accepted_sequence,
        }

    def verify_receipt(self, receipt: Any) -> dict[str, Any]:
        identity = self.identity()
        if (not isinstance(receipt, dict) or set(receipt) != identity.keys() | _RECEIPT_FIELDS
                or archive_json({key: receipt[key] for key in identity}) != archive_json(identity)
                or receipt["status"] != "stored" or receipt["storage_only"] is not True
                or not isinstance(receipt["durability"], str)
                or receipt["durability"] not in {SQLITE_ARCHIVE_DURABILITY, POSTGRES_ARCHIVE_DURABILITY}
                or type(receipt["receipt_sequence"]) is not int or receipt["receipt_sequence"] < 1
                or _aware(receipt["accepted_at"]).utcoffset() != timedelta(hours=9)):
            raise ValueError("archive first receipt does not match the pinned batch")
        return receipt
