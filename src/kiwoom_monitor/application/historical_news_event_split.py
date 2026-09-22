"""Create immutable chronological partitions without splitting canonical news events."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from kiwoom_monitor.application.historical_news_review_decisions import (
    CONTRACT_VERSION as REVIEW_DECISIONS_CONTRACT_VERSION,
    HistoricalNewsReviewDecisions,
)


CONTRACT_VERSION = "historical_news_event_split/v1"
ASSIGNMENT_POLICY = "chronological_canonical_events/v1"


def build_historical_news_event_split(
    source: HistoricalNewsReviewDecisions,
    *,
    train_events: int,
    validation_events: int,
) -> dict[str, Any]:
    """Assign whole relevant events in time order and keep the remainder sealed OOS."""
    _validate_source(source)
    if type(train_events) is not int or type(validation_events) is not int:
        raise ValueError("news event split counts must be integers")
    if train_events < 1 or validation_events < 1:
        raise ValueError("news event split requires TRAIN and VALIDATION events")

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    excluded = {"not_relevant": 0, "uncertain": 0}
    for decision in source.decisions:
        review = decision.get("human_review")
        if not isinstance(review, Mapping):
            raise ValueError("news event split decision review is missing")
        value = str(review.get("decision", ""))
        if value in excluded:
            excluded[value] += 1
            continue
        if value != "relevant":
            raise ValueError("news event split contains an unsupported human decision")
        event_id = str(review.get("canonical_event_id", "")).strip()
        if not event_id:
            raise ValueError("relevant news event split decision requires canonical_event_id")
        grouped.setdefault(event_id, []).append(decision)

    events = [_event_document(event_id, decisions) for event_id, decisions in grouped.items()]
    events.sort(key=lambda row: (
        _aware_datetime(row["first_published_at"]), row["canonical_event_id"],
    ))
    if len(events) < train_events + validation_events + 1:
        raise ValueError("news event split requires at least one sealed OOS event")

    ranges = (
        ("TRAIN", events[:train_events]),
        ("VALIDATION", events[train_events:train_events + validation_events]),
        ("OOS", events[train_events + validation_events:]),
    )
    assignments: list[dict[str, Any]] = []
    partitions: list[dict[str, Any]] = []
    for role, rows in ranges:
        event_ids: list[str] = []
        article_count = 0
        for row in rows:
            assignments.append({"role": role, **row})
            event_ids.append(str(row["canonical_event_id"]))
            article_count += int(row["article_count"])
        partitions.append({
            "role": role,
            "event_ids": event_ids,
            "event_count": len(rows),
            "article_count": article_count,
            "first_published_at": rows[0]["first_published_at"],
            "last_published_at": max(
                (_aware_datetime(row["last_published_at"]) for row in rows),
            ).isoformat(),
        })

    source_boundaries = source.manifest.get("boundaries", {})
    binding = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_event_split",
        "source": {
            "contract_version": REVIEW_DECISIONS_CONTRACT_VERSION,
            "dataset_id": source.manifest["dataset_id"],
            "decisions_file_hash": source.manifest["decisions_file_hash"],
            "decision_count": len(source.decisions),
        },
        "assignment_policy": ASSIGNMENT_POLICY,
        "parameters": {
            "train_events": train_events,
            "validation_events": validation_events,
        },
        "boundaries": {
            "canonical_event_atomic": True,
            "chronological_assignment": True,
            "source_queue_review_complete": bool(
                isinstance(source_boundaries, Mapping)
                and source_boundaries.get("source_queue_review_complete") is True
            ),
            "partial_review_source": not bool(
                isinstance(source_boundaries, Mapping)
                and source_boundaries.get("source_queue_review_complete") is True
            ),
            "oos_status": "SEALED",
            "oos_review_payload_included": False,
            "model_weight_training_ready": False,
        },
        "counts": {
            "reviewed_decisions": len(source.decisions),
            "relevant_articles": sum(int(row["article_count"]) for row in events),
            "canonical_events": len(events),
            "not_relevant_excluded": excluded["not_relevant"],
            "uncertain_excluded": excluded["uncertain"],
        },
        "partitions": partitions,
        "event_assignments": assignments,
    }
    return {
        **binding,
        "plan_id": "historical-news-event-split-" + _hash_document(binding),
    }


def write_historical_news_event_split(plan: Mapping[str, Any], output: Path) -> None:
    """Write one immutable event split plan after content validation."""
    _validate_plan(plan)
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news event split is immutable and cannot be overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(plan), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}-", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(encoded)
    try:
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_historical_news_event_split(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news event split cannot be read") from exc
    if not isinstance(value, dict):
        raise ValueError("historical news event split must be an object")
    _validate_plan(value)
    return value


def _event_document(
    event_id: str, decisions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(decisions, key=lambda row: (int(row.get("source_queue_ordinal", 0)), str(row.get("decision_id", ""))))
    published = [_published_at(row) for row in ordered]
    decision_ids = [str(row.get("decision_id", "")).strip() for row in ordered]
    review_item_ids = [str(row.get("review_item_id", "")).strip() for row in ordered]
    if not all(decision_ids) or len(decision_ids) != len(set(decision_ids)):
        raise ValueError("news event split decision identity is invalid")
    if not all(review_item_ids) or len(review_item_ids) != len(set(review_item_ids)):
        raise ValueError("news event split review item identity is invalid")
    stocks = sorted({
        str(link.get("stock", {}).get("code", "")).strip()
        for row in ordered
        for link in row.get("case_links", [])
        if isinstance(link, Mapping) and isinstance(link.get("stock"), Mapping)
        and str(link["stock"].get("code", "")).strip()
    })
    return {
        "canonical_event_id": event_id,
        "first_published_at": min(published).isoformat(),
        "last_published_at": max(published).isoformat(),
        "article_count": len(ordered),
        "decision_ids": decision_ids,
        "review_item_ids": review_item_ids,
        "stock_codes": stocks,
    }


def _published_at(decision: Mapping[str, Any]) -> datetime:
    evidence = decision.get("article_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("news event split requires article publication evidence")
    try:
        value = datetime.fromisoformat(str(evidence.get("published_at", "")))
    except ValueError as exc:
        raise ValueError("news event split published_at is invalid") from exc
    if value.tzinfo is None:
        raise ValueError("news event split published_at must be timezone-aware")
    return value


def _aware_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("historical news event split timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("historical news event split timestamp must be timezone-aware")
    return parsed


def _validate_source(source: HistoricalNewsReviewDecisions) -> None:
    manifest = source.manifest
    if manifest.get("contract_version") != REVIEW_DECISIONS_CONTRACT_VERSION:
        raise ValueError("news event split requires historical_news_review_decisions/v1")
    if not manifest.get("dataset_id") or re.fullmatch(
        r"[0-9a-f]{64}", str(manifest.get("decisions_file_hash", "")),
    ) is None:
        raise ValueError("news event split source identity is incomplete")
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, Mapping) or boundaries.get("included_decisions_human_reviewed") is not True:
        raise ValueError("news event split requires human-reviewed decisions")
    if int(manifest.get("decision_count", -1)) != len(source.decisions):
        raise ValueError("news event split source decision count does not match")
    for decision in source.decisions:
        eligibility = decision.get("eligibility")
        if (
            not isinstance(eligibility, Mapping)
            or eligibility.get("human_review_complete") is not True
            or eligibility.get("model_weight_training_ready") is not False
        ):
            raise ValueError("news event split source decision eligibility is invalid")


def _validate_plan(value: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "contract_version", "dataset_kind", "source",
        "assignment_policy", "parameters", "boundaries", "counts",
        "partitions", "event_assignments", "plan_id",
    }
    if (
        set(value) != required
        or value.get("schema_version") != 1
        or value.get("contract_version") != CONTRACT_VERSION
        or value.get("dataset_kind") != "historical_news_event_split"
    ):
        raise ValueError("historical news event split fields are invalid")
    if value.get("assignment_policy") != ASSIGNMENT_POLICY:
        raise ValueError("historical news event split assignment policy is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping) or source.get("contract_version") != REVIEW_DECISIONS_CONTRACT_VERSION:
        raise ValueError("historical news event split source is invalid")
    if not source.get("dataset_id") or re.fullmatch(
        r"[0-9a-f]{64}", str(source.get("decisions_file_hash", "")),
    ) is None:
        raise ValueError("historical news event split source identity is invalid")
    boundaries = value.get("boundaries")
    if not isinstance(boundaries, Mapping) or boundaries.get("canonical_event_atomic") is not True:
        raise ValueError("historical news event split atomic boundary is missing")
    if boundaries.get("oos_status") != "SEALED" or boundaries.get("oos_review_payload_included") is not False:
        raise ValueError("historical news event split OOS must remain sealed")
    if boundaries.get("model_weight_training_ready") is not False:
        raise ValueError("historical news event split training boundary is missing")
    assignments = value.get("event_assignments")
    partitions = value.get("partitions")
    if not isinstance(assignments, list) or not isinstance(partitions, list) or len(partitions) != 3:
        raise ValueError("historical news event split assignments are invalid")
    partition_fields = {
        "role", "event_ids", "event_count", "article_count",
        "first_published_at", "last_published_at",
    }
    assignment_fields = {
        "role", "canonical_event_id", "first_published_at", "last_published_at",
        "article_count", "decision_ids", "review_item_ids", "stock_codes",
    }
    if any(
        not isinstance(row, Mapping) or set(row) != partition_fields
        or not isinstance(row.get("event_ids"), list) or not row["event_ids"]
        or type(row.get("event_count")) is not int or row["event_count"] < 1
        or type(row.get("article_count")) is not int or row["article_count"] < 1
        for row in partitions
    ):
        raise ValueError("historical news event split partition is invalid")
    if any(
        not isinstance(row, Mapping) or set(row) != assignment_fields
        or type(row.get("article_count")) is not int or row["article_count"] < 1
        or not isinstance(row.get("decision_ids"), list) or not row["decision_ids"]
        or len(row["decision_ids"]) != row["article_count"]
        or not isinstance(row.get("review_item_ids"), list)
        or len(row["review_item_ids"]) != row["article_count"]
        or not isinstance(row.get("stock_codes"), list)
        for row in assignments
    ):
        raise ValueError("historical news event split event assignment is invalid")
    if [row.get("role") for row in partitions if isinstance(row, Mapping)] != ["TRAIN", "VALIDATION", "OOS"]:
        raise ValueError("historical news event split roles are invalid")
    event_ids = [str(row.get("canonical_event_id", "")) for row in assignments if isinstance(row, Mapping)]
    if len(event_ids) != len(assignments) or not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("historical news event split events must be unique")
    chronological = [
        (_aware_datetime(row["first_published_at"]), str(row["canonical_event_id"]))
        for row in assignments
    ]
    if chronological != sorted(chronological):
        raise ValueError("historical news event split events must be chronological")
    partition_ids = [str(event_id) for row in partitions for event_id in row.get("event_ids", [])]
    if partition_ids != event_ids:
        raise ValueError("historical news event split partitions do not match assignments")
    role_by_event = {
        str(event_id): str(row["role"])
        for row in partitions for event_id in row["event_ids"]
    }
    if any(str(row["role"]) != role_by_event[str(row["canonical_event_id"])] for row in assignments):
        raise ValueError("historical news event split assignment role does not match partition")
    if any(
        row["event_count"] != len(row["event_ids"])
        or row["article_count"] != sum(
            int(assignment["article_count"])
            for assignment in assignments
            if assignment["canonical_event_id"] in row["event_ids"]
        )
        for row in partitions
    ):
        raise ValueError("historical news event split partition counts do not match assignments")
    binding = {key: value[key] for key in required if key != "plan_id"}
    expected = "historical-news-event-split-" + _hash_document(binding)
    if value.get("plan_id") != expected:
        raise ValueError("historical news event split identity does not match content")


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
