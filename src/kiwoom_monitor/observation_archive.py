"""Prepare, deliver, or restore one explicitly selected observation batch."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, ObservationArchiveBatch, ObservationArchiveLimit, archive_json, archive_source_stream,
)
from kiwoom_monitor.domain.research_contract import RESEARCH_OBSERVATION_KINDS
from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient, CentralContentHttpError, validate_producer_connection,
)
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes
from kiwoom_monitor.infrastructure.research_data_source import export_daily_dataset, load_frozen_research_export


def _connection(path: Path, source_stream: str) -> tuple[str, CentralContentClient]:
    archive_source_stream(source_stream)
    settings = DataSourceConfig(path).load()
    if settings.mode not in {"local_server", "personal_server"}:
        raise ValueError("an explicit archive server connection is required")
    url = validate_producer_connection(settings.server_url, source_stream, settings.access_token)
    return url, CentralContentClient.for_observation_archive(url, source_stream, settings.access_token)


def prepare_observation_archive(
    source_connection: Path, source_stream: str, start: datetime, end: datetime, batch_path: Path, *, kind: str,
    subject: str = "",
) -> dict[str, Any]:
    """Keep the original watermark/manifest/observation bytes from the public source cut."""
    if (start.utcoffset() is None or end.utcoffset() is None
            or not timedelta(0) < end - start <= timedelta(days=1)):
        raise ValueError("one timezone-aware observation export range is required")
    if kind not in RESEARCH_OBSERVATION_KINDS:
        raise ValueError("one supported observation export kind is required")
    if batch_path.exists():
        raise FileExistsError("archive batch already exists; send the original or choose a new batch file")
    _, client = _connection(source_connection, source_stream)
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="observation-export-", dir=batch_path.parent) as work:
        output = Path(work) / "artifact"
        try:
            export_daily_dataset(client, start, end, (kind,), subject, output,
                                 theme_history={"snapshots": []}, max_encoded_bytes=MAX_OBSERVATION_ARCHIVE_BYTES)
        except ValueError as error:
            if str(error) != "research_export_encoded_byte_limit_exceeded":
                raise
            raise ObservationArchiveLimit("source range exceeds the bounded research input budget") from error
        value = ObservationArchiveBatch.from_artifact(source_stream, (output / "manifest.json").read_bytes(),
                                                      (output / "observations.jsonl").read_bytes())
        write_immutable_bytes(batch_path, value.raw)
    return value.identity()


def _read_batch(batch_path: Path) -> ObservationArchiveBatch:
    with batch_path.open("rb") as file:
        return ObservationArchiveBatch.parse(file.read(MAX_OBSERVATION_ARCHIVE_BYTES + 1))


def deliver_observation_archive(batch_path: Path, target_connection: Path) -> dict[str, Any]:
    """One attempt: retain the exact batch and pending target until a verified first receipt."""
    stage, prepared = "batch", False
    try:
        value = _read_batch(batch_path)
        prepared = True
        stage = "target"
        url, client = _connection(target_connection, value.source_stream)
        descriptor = {"target": url, "source": value.identity()}
        descriptor_bytes = archive_json(descriptor)
        target_path = batch_path.with_name(batch_path.name + ".target.json")
        ack_path = batch_path.with_name(batch_path.name + ".ack.json")
        write_immutable_bytes(target_path, descriptor_bytes)
        if ack_path.is_file():
            stage = "ack"
            ack = json.loads(ack_path.read_bytes())
            if (not isinstance(ack, dict) or set(ack) != {"target", "source", "receipt"}
                    or archive_json({key: ack[key] for key in descriptor}) != descriptor_bytes):
                raise ValueError("archive ACK does not match the pinned target/source")
            return {"status": "delivered", "receipt": value.verify_receipt(ack["receipt"])}
        stage = "post"
        try:
            receipt = value.verify_receipt(client.accept_observation_archive(
                value.document(), source_stream=value.source_stream, sha256=value.content_sha256,
            ))
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as post_error:
            if isinstance(post_error, CentralContentHttpError) and post_error.status_code < 500:
                raise
            # Only an uncertain commit or missing/malformed ACK can be confirmed by GET.
            stage = "confirm"
            receipt = value.verify_receipt(client.load_observation_archive(value.batch_id, source_stream=value.source_stream))
        stage = "ack"
        if batch_path.read_bytes() != value.raw or target_path.read_bytes() != descriptor_bytes:
            raise ValueError("archive source or target changed while waiting for durable acceptance")
        write_immutable_bytes(ack_path, archive_json({**descriptor, "receipt": receipt}))
        return {"status": "delivered", "receipt": receipt}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        outcome = {"status": "pending" if prepared else "not_prepared", "stage": stage, "reason": type(error).__name__}
        if isinstance(error, CentralContentHttpError):
            outcome["http_status"] = error.status_code
        return outcome


def restore_observation_archive(
    target_connection: Path, source_stream: str, batch_id: str, output: Path,
) -> dict[str, Any]:
    """Recover the exact exported files and open them through the existing research loader."""
    _, client = _connection(target_connection, source_stream)
    stored = client.load_observation_archive(batch_id, source_stream=source_stream, include_body=True)
    if set(stored) != {"receipt", "batch"}:
        raise ValueError("invalid observation archive download")
    value = ObservationArchiveBatch.parse(archive_json(stored["batch"]))
    if (value.source_stream, value.batch_id) != (source_stream, batch_id):
        raise ValueError("downloaded archive does not match the selected source stream and batch")
    receipt = value.verify_receipt(stored["receipt"])
    body = value.document()
    write_immutable_bytes(output / "observations.jsonl", body["observations"].encode("utf-8"))
    write_immutable_bytes(output / "theme_snapshots.jsonl", b"")
    write_immutable_bytes(output / "manifest.json", body["manifest"].encode("utf-8"))
    load_frozen_research_export(output)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Archive one fixed observation export. No execution authority.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-connection", required=True, type=Path)
    prepare.add_argument("--source-stream", required=True)
    prepare.add_argument("--start", required=True, type=datetime.fromisoformat)
    prepare.add_argument("--end", required=True, type=datetime.fromisoformat)
    prepare.add_argument("--kind", required=True, choices=sorted(RESEARCH_OBSERVATION_KINDS))
    prepare.add_argument("--subject", default="")
    prepare.add_argument("--batch", required=True, type=Path)
    send = commands.add_parser("send")
    send.add_argument("--batch", required=True, type=Path)
    send.add_argument("--target-connection", required=True, type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--target-connection", required=True, type=Path)
    restore.add_argument("--source-stream", required=True)
    restore.add_argument("--batch-id", required=True)
    restore.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = {"status": "prepared", "source": prepare_observation_archive(
                args.source_connection, args.source_stream, args.start, args.end, args.batch, kind=args.kind,
                subject=args.subject,
            )}
        elif args.command == "send":
            result = deliver_observation_archive(args.batch, args.target_connection)
        else:
            result = {"status": "restored", "receipt": restore_observation_archive(
                args.target_connection, args.source_stream, args.batch_id, args.output,
            )}
    except ObservationArchiveLimit:
        result = {"status": "failed", "reason": "archive_batch_size_limit",
                  "maximum_bytes": MAX_OBSERVATION_ARCHIVE_BYTES, "action": "choose a smaller explicit source range"}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        result = {"status": "failed", "reason": type(error).__name__}
        if isinstance(error, CentralContentHttpError):
            result["http_status"] = error.status_code
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"prepared", "delivered", "restored"} else 4


if __name__ == "__main__":
    raise SystemExit(main())
