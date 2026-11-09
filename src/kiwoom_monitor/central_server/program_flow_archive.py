"""Freeze completed PC market-data cuts and retry their storage-only delivery."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, PROGRAM_FLOW_ARCHIVE_KIND, ObservationArchiveBatch,
    ObservationArchiveLimit, archive_json, archive_sha256, archive_source_stream,
)
from kiwoom_monitor.domain.time_basis import KST, as_kst
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.infrastructure.observation_archive_delivery import deliver_observation_archive
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes

from .async_lifetime import await_owned_task, owned_create_task, owned_to_thread
from .database_research_export import _observation_export_manifest


logger = logging.getLogger(__name__)
# The remaining space is for the fixed manifest and outer batch envelope.
_MAX_ENCODED_SNAPSHOTS_BYTES = MAX_OBSERVATION_ARCHIVE_BYTES - 8192


class ProgramFlowArchive:
    def __init__(self, store, directory: Path, target_connection: Path, source_stream: str) -> None:
        self._store = store
        self._directory = directory
        self._target_connection = target_connection
        self._source_stream = archive_source_stream(source_stream)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._wake.set()
            self._task = owned_create_task(self._run(), name="pc-program-flow-archive")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def freeze_day(self, day: str, completed_at: datetime, *, excluded_codes: frozenset[str] = frozenset()) -> None:
        await await_owned_task(owned_create_task(owned_to_thread(self._freeze, day, completed_at, excluded_codes),
                                                name="pc-program-flow-archive-freeze"))
        self._wake.set()

    async def freeze_observations(self, day: str, completed_at: datetime, *, excluded_codes: frozenset[str] = frozenset()) -> None:
        await await_owned_task(owned_create_task(owned_to_thread(self._freeze_observations, day, completed_at, excluded_codes),
                                                name="pc-market-archive-freeze"))
        self._wake.set()

    def _freeze_observations(self, day: str, completed_at: datetime, excluded_codes: frozenset[str]) -> None:
        start = datetime.combine(date.fromisoformat(day), time(), KST)
        completed_at = as_kst(completed_at)
        destination = self._directory / "observations" / day
        if destination.is_dir():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination.parent, prefix=".prepare-") as work:
            staged = Path(work) / "cut"
            staged.mkdir()
            with self._store.read_after_close_observations(start, start + timedelta(days=1), completed_at) as rows:
                eligible = (row for row in rows if row["kind"] not in {"minute_bar", "daily_bar"}
                            or normalize_stock_code(row["subject"].partition(":")[0]) not in excluded_codes)
                for (kind, recorded_day), group in groupby(eligible, _observation_scope):
                    recorded_start = datetime.combine(recorded_day, time(), KST)
                    self._write_observation_batches(staged, kind, recorded_start, completed_at, group)
            staged.rename(destination)

    def _write_observation_batches(self, staged, kind, start, completed_at, rows) -> None:
        lines, selected, encoded_size = [], [], 0
        for row in rows:
            line = archive_json({**row, "ordinal": len(lines) + 1})
            size = _encoded_line_size(line)
            if size > _MAX_ENCODED_SNAPSHOTS_BYTES:
                raise ObservationArchiveLimit("one market observation exceeds the archive batch limit")
            if encoded_size + size > _MAX_ENCODED_SNAPSHOTS_BYTES:
                self._write_observation_batch(staged, kind, start, completed_at, lines, selected)
                lines, selected, encoded_size = [], [], 0
                line = archive_json({**row, "ordinal": 1})
                size = _encoded_line_size(line)
            lines.append(line)
            selected.append((row["revision_id"], row["source_id"], row["available_at"]))
            encoded_size += size
        if lines:
            self._write_observation_batch(staged, kind, start, completed_at, lines, selected)

    def _write_observation_batch(self, staged, kind, start, completed_at, lines, selected) -> None:
        manifest = _observation_export_manifest(start, start + timedelta(days=1), (kind,), "", selected,
                                                exported_at=completed_at)
        self._write_archive_batch(staged, manifest, lines)

    def _freeze(self, day: str, completed_at: datetime, excluded_codes: frozenset[str]) -> None:
        start = datetime.combine(date.fromisoformat(day), time(), KST)
        destination = self._directory / day
        if destination.is_dir():
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._directory, prefix=".prepare-") as work:
            staged = Path(work) / "cut"
            staged.mkdir()
            lines, encoded_size = [], 0
            with self._store.read_program_flow_archive(day) as snapshots:
                for snapshot in snapshots:
                    if normalize_stock_code(snapshot["subject"]) in excluded_codes:
                        continue
                    line = archive_json(snapshot)
                    size = _encoded_line_size(line)
                    if size > _MAX_ENCODED_SNAPSHOTS_BYTES:
                        raise ObservationArchiveLimit("one program flow snapshot exceeds the archive batch limit")
                    if encoded_size + size > _MAX_ENCODED_SNAPSHOTS_BYTES:
                        self._write_batch(staged, start, completed_at, lines)
                        lines, encoded_size = [], 0
                    lines.append(line)
                    encoded_size += size
                if lines:
                    self._write_batch(staged, start, completed_at, lines)
            # Publish the whole cut at once. A crash during preparation exposes no partial day.
            staged.rename(destination)

    def _write_batch(self, staged: Path, start: datetime, completed_at: datetime, lines: list[bytes]) -> None:
        batch_id = str(uuid4())
        self._write_archive_batch(staged, {
            "dataset_id": batch_id, "fixed_watermark": batch_id, "schema_version": 1,
            "kinds": [PROGRAM_FLOW_ARCHIVE_KIND], "subject": "",
            "captured_range": {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
            "exported_at": as_kst(completed_at).isoformat(),
            "revision_count": len(lines),
            "revision_ids_hash": archive_sha256("\n".join(map(archive_sha256, lines)).encode("utf-8")),
        }, lines)

    def _write_archive_batch(self, staged: Path, manifest: dict, lines: list[bytes]) -> None:
        observations = b"\n".join(lines) + b"\n"
        manifest_bytes = archive_json({
            **manifest,
            "observations_file": "observations.jsonl", "observations_file_hash": archive_sha256(observations),
            "theme_snapshots_file": "theme_snapshots.jsonl", "theme_snapshots_file_hash": archive_sha256(b""),
            "theme_snapshot_count": 0,
        })
        value = ObservationArchiveBatch.from_artifact(self._source_stream, manifest_bytes, observations)
        write_immutable_bytes(staged / (value.batch_id + ".batch"), value.raw)

    def _pending_batches(self) -> list[Path]:
        return sorted(batch for pattern in ("[0-9]*/*.batch", "observations/[0-9]*/*.batch")
                      for batch in self._directory.glob(pattern)
                      if not batch.with_name(batch.name + ".ack.json").is_file())

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            retry = False
            try:
                pending = await await_owned_task(owned_create_task(owned_to_thread(self._pending_batches),
                                                                   name="pc-program-flow-archive-list"))
                for batch in pending:
                    outcome = await await_owned_task(owned_create_task(owned_to_thread(
                        deliver_observation_archive, batch, self._target_connection,
                    ), name="pc-program-flow-archive-send"))
                    if outcome["status"] != "delivered":
                        logger.warning("장후 자료 보관 전송 대기: %s %s", batch.name, outcome)
                        retry = True
            except OSError as error:
                logger.warning("장후 자료 보관 대기 목록 조회 실패: %s", type(error).__name__)
                retry = True
            if retry:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=60)
                except asyncio.TimeoutError:
                    self._wake.set()


def _encoded_line_size(line: bytes) -> int:
    return len(archive_json(line.decode("utf-8") + "\n")) - 2


def _observation_scope(row: dict) -> tuple[str, date]:
    return row["kind"], as_kst(datetime.fromisoformat(row["available_at"])).date()
