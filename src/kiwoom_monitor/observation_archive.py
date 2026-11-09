"""Prepare, deliver, restore, or inspect a selected observation batch."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, PROGRAM_FLOW_ARCHIVE_KIND, ObservationArchiveBatch, ObservationArchiveLimit, archive_json,
)
from kiwoom_monitor.domain.research_contract import RESEARCH_OBSERVATION_KINDS
from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst
from kiwoom_monitor.infrastructure.central_content_client import CentralContentHttpError
from kiwoom_monitor.infrastructure.observation_archive_delivery import archive_connection, deliver_observation_archive
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes
from kiwoom_monitor.infrastructure.research_data_source import export_daily_dataset, load_frozen_research_export


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
    _, client = archive_connection(source_connection, source_stream)
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


def restore_observation_archive(
    target_connection: Path, source_stream: str, batch_id: str, output: Path,
) -> dict[str, Any]:
    """Recover the exact exported files and open them through the existing research loader."""
    _, client = archive_connection(target_connection, source_stream)
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
    if value.kind != PROGRAM_FLOW_ARCHIVE_KIND:
        load_frozen_research_export(output)
    return receipt


def inspect_program_flow(batch_path: Path, *, symbol: str, market: str, recorded_through: datetime) -> dict[str, Any]:
    """Read archived 0w context without inferring continuous or point-in-time coverage."""
    if (len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum() or symbol != symbol.upper()
            or market not in {"KRX", "NXT", "SOR"} or recorded_through.utcoffset() is None):
        raise ValueError("a six-character symbol, market and timezone-aware cutoff are required")
    with batch_path.open("rb") as stream:
        batch = ObservationArchiveBatch.parse(stream.read(MAX_OBSERVATION_ARCHIVE_BYTES + 1))
    if batch.kind != PROGRAM_FLOW_ARCHIVE_KIND:
        raise ValueError("a frozen program flow batch is required")
    start = aware_iso_kst(batch.captured_start)
    cutoff = recorded_through.astimezone(KST)
    if start.date() != cutoff.date():
        raise ValueError("the cutoff must belong to the batch's KST trading date")
    day = start.strftime("%Y%m%d")
    prefix = f"{day}:REALTIME:"
    samples = []
    for line in batch.document()["observations"].removesuffix("\n").split("\n"):
        snapshot = json.loads(line)
        key = snapshot["snapshot_key"]
        if snapshot["subject"] != symbol or not key.startswith(prefix):
            continue
        payload = snapshot["payload"]
        saved_at = aware_iso_kst(snapshot["saved_at"])
        rows = payload.get("rows")
        if saved_at > cutoff or payload.get("market") != market or not isinstance(rows, list):
            continue
        for row in rows:
            if (not isinstance(row, dict) or row.get("source") != "kiwoom_realtime_0w"
                    or row.get("market") != market or row.get("available") is not True):
                continue
            clock = row.get("trade_time")
            if not isinstance(clock, str) or len(clock) != 6 or key != prefix + clock:
                continue
            try:
                effective_at = datetime.strptime(day + clock, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            except ValueError:
                continue
            if effective_at <= saved_at:
                samples.append({"snapshot_key": key, "effective_at": effective_at.isoformat(),
                                "saved_at": snapshot["saved_at"], "row": row})
    return {"source": batch.identity(), "symbol": symbol, "market": market, "recorded_through": cutoff.isoformat(),
            "state": "RECORDED" if samples else "UNKNOWN", "coverage": "UNKNOWN",
            "point_in_time_availability": "UNVERIFIED", "samples": samples}


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
    inspect_flow = commands.add_parser("inspect-program-flow", help="Inspect archived 0w research context locally.")
    inspect_flow.add_argument("--batch", required=True, type=Path)
    inspect_flow.add_argument("--symbol", required=True)
    inspect_flow.add_argument("--market", required=True, choices=("KRX", "NXT", "SOR"))
    inspect_flow.add_argument("--recorded-through", required=True, type=datetime.fromisoformat)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = {"status": "prepared", "source": prepare_observation_archive(
                args.source_connection, args.source_stream, args.start, args.end, args.batch, kind=args.kind,
                subject=args.subject,
            )}
        elif args.command == "send":
            result = deliver_observation_archive(args.batch, args.target_connection)
        elif args.command == "restore":
            result = {"status": "restored", "receipt": restore_observation_archive(
                args.target_connection, args.source_stream, args.batch_id, args.output,
            )}
        else:
            result = {"status": "inspected", **inspect_program_flow(
                args.batch, symbol=args.symbol, market=args.market, recorded_through=args.recorded_through,
            )}
    except ObservationArchiveLimit:
        result = {"status": "failed", "reason": "archive_batch_size_limit",
                  "maximum_bytes": MAX_OBSERVATION_ARCHIVE_BYTES, "action": "choose a smaller explicit source range"}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        result = {"status": "failed", "reason": type(error).__name__}
        if isinstance(error, CentralContentHttpError):
            result["http_status"] = error.status_code
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"prepared", "delivered", "restored", "inspected"} else 4


if __name__ == "__main__":
    raise SystemExit(main())
