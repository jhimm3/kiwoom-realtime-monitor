"""Pin archive deliveries and retain the first native storage receipt."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, ObservationArchiveBatch, archive_json, archive_source_stream,
)

from .central_content_client import CentralContentClient, CentralContentHttpError, validate_producer_connection
from .central_server_config import DataSourceConfig
from .persistence.backup_file import write_immutable_bytes


def archive_connection(path: Path, source_stream: str) -> tuple[str, CentralContentClient]:
    archive_source_stream(source_stream)
    settings = DataSourceConfig(path).load()
    if settings.mode not in {"local_server", "personal_server"}:
        raise ValueError("an explicit archive server connection is required")
    url = validate_producer_connection(settings.server_url, source_stream, settings.access_token)
    return url, CentralContentClient.for_observation_archive(url, source_stream, settings.access_token)


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
        url, client = archive_connection(target_connection, value.source_stream)
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
