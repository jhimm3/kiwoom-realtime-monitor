"""Fixed-membership observation exports; store callers retain transaction ownership."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from kiwoom_monitor.domain.research_contract import RESEARCH_OBSERVATION_KINDS

from .database_codec import (
    _observation_revision_columns,
    bounded_limit,
    json_mapping,
    observation_revision_result_rows,
)


class SQLiteResearchExportStoreMixin:
    def create_observation_export(
            self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
        ) -> dict[str, Any]:
            normalized_start, normalized_end, normalized_kinds = _observation_export_inputs(
                start, end, kinds,
            )
            placeholders = ",".join("?" for _ in normalized_kinds)
            sql = (
                "SELECT revision_id,source_id,available_at FROM central_observation_revisions "
                f"WHERE kind IN ({placeholders}) AND available_at>=? AND available_at<?"
            )
            parameters: list[object] = [*normalized_kinds, normalized_start.isoformat(), normalized_end.isoformat()]
            if subject:
                sql += " AND subject=?"
                parameters.append(subject)
            sql += " ORDER BY available_at,accepted_sequence,revision_id"
            with self._lock, self._connection() as connection:
                selected = connection.execute(sql, parameters).fetchall()
                manifest = _observation_export_manifest(
                    normalized_start, normalized_end, normalized_kinds, subject, selected,
                )
                connection.execute(
                    "INSERT INTO central_research_exports VALUES(?,?,?,?,?,?,?,?,?)",
                    _sqlite_export_values(manifest),
                )
                connection.executemany(
                    "INSERT INTO central_research_export_members VALUES(?,?,?)",
                    [(manifest["dataset_id"], ordinal, str(row[0]))
                     for ordinal, row in enumerate(selected, start=1)],
                )
            return manifest

    def load_observation_export_page(
            self, watermark: str, cursor: int = 0, limit: int = 1000,
        ) -> dict[str, Any]:
            page_limit = bounded_limit(limit, 1000)
            with self._lock, self._connection() as connection:
                manifest_row = connection.execute(
                    "SELECT manifest_json FROM central_research_exports WHERE dataset_id=?", (watermark,),
                ).fetchone()
                if manifest_row is None:
                    raise ValueError("unknown research export watermark")
                rows = connection.execute(
                    _observation_export_page_sql("?"),
                    (watermark, max(0, int(cursor)), page_limit + 1),
                ).fetchall()
            return _observation_export_page(manifest_row[0], rows, page_limit)


class PostgresResearchExportStoreMixin:
    def create_observation_export(
            self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
        ) -> dict[str, Any]:
            normalized_start, normalized_end, normalized_kinds = _observation_export_inputs(
                start, end, kinds,
            )
            from .postgres_access import DBWriterContext, open_observed_connection

            writer = DBWriterContext(
                writer_family="research.observation_export",
                writer_kind="research_observation_export_create",
                operation="create_observation_export",
            )
            placeholders = ",".join("%s" for _ in normalized_kinds)
            sql = (
                "SELECT revision_id,source_id,available_at FROM central_observation_revisions "
                f"WHERE kind IN ({placeholders}) AND available_at>=%s AND available_at<%s"
            )
            parameters: list[object] = [*normalized_kinds, normalized_start, normalized_end]
            if subject:
                sql += " AND subject=%s"
                parameters.append(subject)
            sql += " ORDER BY available_at,accepted_sequence,revision_id"
            with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                selected = cursor.fetchall()
                manifest = _observation_export_manifest(
                    normalized_start, normalized_end, normalized_kinds, subject, selected,
                )
                cursor.execute(
                    "INSERT INTO central_research_exports VALUES(" + ",".join(("%s",) * 9) + ")",
                    _postgres_export_values(manifest),
                )
                cursor.executemany(
                    "INSERT INTO central_research_export_members VALUES(%s,%s,%s)",
                    [(manifest["dataset_id"], ordinal, str(row[0]))
                     for ordinal, row in enumerate(selected, start=1)],
                )
            return manifest

    def load_observation_export_page(
            self, watermark: str, cursor: int = 0, limit: int = 1000,
        ) -> dict[str, Any]:
            from .postgres_access import DBWriterContext, open_observed_connection

            page_limit = bounded_limit(limit, 1000)
            reader = DBWriterContext(
                writer_family="read.research_export", writer_kind="export_page",
                operation="load_observation_export_page", access_mode="read",
            )
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as db_cursor:
                db_cursor.execute(
                    "SELECT manifest_json FROM central_research_exports WHERE dataset_id=%s", (watermark,),
                )
                manifest_row = db_cursor.fetchone()
                if manifest_row is None:
                    raise ValueError("unknown research export watermark")
                db_cursor.execute(
                    _observation_export_page_sql("%s", postgres=True),
                    (watermark, max(0, int(cursor)), page_limit + 1),
                )
                rows = db_cursor.fetchall()
            return _observation_export_page(manifest_row[0], rows, page_limit)



def _observation_export_inputs(
    start: datetime, end: datetime, kinds: tuple[str, ...],
) -> tuple[datetime, datetime, tuple[str, ...]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("research export timestamps must be timezone-aware")
    normalized_start = start.astimezone(timezone.utc)
    normalized_end = end.astimezone(timezone.utc)
    if normalized_start >= normalized_end:
        raise ValueError("research export start must be before end")
    if (normalized_end - normalized_start).total_seconds() > 86_400:
        raise ValueError("research export range is limited to one session (24 hours)")
    normalized_kinds = tuple(dict.fromkeys(str(kind).strip() for kind in kinds if str(kind).strip()))
    if not normalized_kinds or any(kind not in RESEARCH_OBSERVATION_KINDS for kind in normalized_kinds):
        raise ValueError("research export kinds must be ranking, top20_membership, or minute_bar")
    return normalized_start, normalized_end, normalized_kinds


def _observation_export_manifest(
    start: datetime,
    end: datetime,
    kinds: tuple[str, ...],
    subject: str,
    selected: list[tuple[object, ...]],
) -> dict[str, Any]:
    dataset_id = str(uuid.uuid4())
    revision_ids = [str(row[0]) for row in selected]
    revision_ids_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    recorded_from = _iso_value(selected[0][2]) if selected else None
    return {
        "dataset_id": dataset_id,
        "schema_version": 1,
        "captured_range": {"start": start.isoformat(), "end": end.isoformat()},
        "recorded_from": recorded_from,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_ids": sorted({str(row[1]) for row in selected}),
        "fixed_watermark": dataset_id,
        "revision_count": len(revision_ids),
        "revision_ids_hash": revision_ids_hash,
        "kinds": list(kinds),
        "subject": subject,
        "universe_rule": (
            "top20-membership-with-minute-bars-v1"
            if "minute_bar" in kinds else "top20_membership-v1"
        ),
        "timezone": "UTC",
        "order_policy_version": "available_at-ingest_sequence-revision_id-v1",
        "quality_summary": {
            "flags": (["coverage_not_evaluated"] if selected else
                      ["coverage_not_evaluated", "no_recorded_observations"]),
            "recording_gap": "unknown",
        },
        "replay_profile": "observed_replay",
    }


def _sqlite_export_values(manifest: dict[str, Any]) -> tuple[object, ...]:
    captured = manifest["captured_range"]
    return (
        manifest["dataset_id"], manifest["exported_at"], captured["start"], captured["end"],
        json.dumps(manifest["kinds"], separators=(",", ":")), manifest["subject"],
        manifest["revision_count"], manifest["revision_ids_hash"],
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
    )


def _postgres_export_values(manifest: dict[str, Any]) -> tuple[object, ...]:
    captured = manifest["captured_range"]
    return (
        manifest["dataset_id"], datetime.fromisoformat(manifest["exported_at"]),
        datetime.fromisoformat(captured["start"]), datetime.fromisoformat(captured["end"]),
        json.dumps(manifest["kinds"], separators=(",", ":")), manifest["subject"],
        manifest["revision_count"], manifest["revision_ids_hash"],
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
    )


def _observation_export_page_sql(placeholder: str, *, postgres: bool = False) -> str:
    return (
        f"SELECT {_observation_revision_columns(prefix='r.', postgres=postgres)},m.ordinal "
        "FROM central_research_export_members m JOIN central_observation_revisions r "
        "ON r.revision_id=m.revision_id "
        f"WHERE m.dataset_id={placeholder} AND m.ordinal>{placeholder} "
        f"ORDER BY m.ordinal LIMIT {placeholder}"
    )


def _observation_export_page(
    raw_manifest: object, rows: list[tuple[object, ...]], page_limit: int,
) -> dict[str, Any]:
    manifest = json_mapping(raw_manifest)
    page_rows = rows[:page_limit]
    observations = observation_revision_result_rows([row[:24] for row in page_rows])
    for observation, row in zip(observations, page_rows, strict=True):
        observation["ordinal"] = int(row[24])
    has_more = len(rows) > page_limit
    return {
        "watermark": manifest["fixed_watermark"],
        "manifest": manifest,
        "observations": observations,
        "next_cursor": observations[-1]["ordinal"] if has_more and observations else None,
    }


def _iso_value(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
