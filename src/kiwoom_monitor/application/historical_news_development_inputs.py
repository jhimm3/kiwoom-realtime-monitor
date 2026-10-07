"""Project reviewed news into immutable TRAIN/VALIDATION inputs with OOS omitted."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.historical_news_event_split import (
    CONTRACT_VERSION as EVENT_SPLIT_CONTRACT_VERSION,
    validate_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    CONTRACT_VERSION as REVIEW_DECISIONS_CONTRACT_VERSION,
    HistoricalNewsReviewDecisions,
)


CONTRACT_VERSION = "historical_news_development_inputs/v1"


@dataclass(frozen=True)
class HistoricalNewsDevelopmentInputs:
    manifest: dict[str, Any]
    train: tuple[dict[str, Any], ...]
    validation: tuple[dict[str, Any], ...]


def build_historical_news_development_inputs(
    decisions: HistoricalNewsReviewDecisions,
    event_split: Mapping[str, Any],
) -> HistoricalNewsDevelopmentInputs:
    """Copy only human-reviewed TRAIN/VALIDATION records from an exact event split."""
    _validate_sources(decisions, event_split)
    by_id = {str(row["decision_id"]): row for row in decisions.decisions}
    assignments = event_split["event_assignments"]
    assigned_ids = [
        str(decision_id)
        for assignment in assignments
        for decision_id in assignment["decision_ids"]
    ]
    relevant_ids: list[str] = []
    for row in decisions.decisions:
        review = row.get("human_review")
        if isinstance(review, Mapping) and review.get("decision") == "relevant":
            relevant_ids.append(str(row["decision_id"]))
    if len(assigned_ids) != len(set(assigned_ids)) or set(assigned_ids) != set(relevant_ids):
        raise ValueError("news development split does not cover relevant decisions exactly once")

    records: dict[str, list[dict[str, Any]]] = {"TRAIN": [], "VALIDATION": []}
    for assignment in assignments:
        role = str(assignment["role"])
        if role == "OOS":
            continue
        if role not in records:
            raise ValueError("news development input contains an unsupported partition")
        event_id = str(assignment["canonical_event_id"])
        for decision_id in assignment["decision_ids"]:
            decision = by_id.get(str(decision_id))
            if decision is None:
                raise ValueError("news development input references an unknown decision")
            review = decision.get("human_review")
            if not isinstance(review, Mapping) or review.get("decision") != "relevant":
                raise ValueError("news development input may include relevant decisions only")
            if str(review.get("canonical_event_id", "")) != event_id:
                raise ValueError("news development event assignment does not match decision")
            records[role].append(_record(role, event_id, decision))

    if not records["TRAIN"] or not records["VALIDATION"]:
        raise ValueError("news development input requires TRAIN and VALIDATION records")
    train = tuple({"ordinal": index, **row} for index, row in enumerate(records["TRAIN"], start=1))
    validation = tuple(
        {"ordinal": index, **row}
        for index, row in enumerate(records["VALIDATION"], start=1)
    )
    train_encoded = _encode_rows(train)
    validation_encoded = _encode_rows(validation)
    train_hash = hashlib.sha256(train_encoded).hexdigest()
    validation_hash = hashlib.sha256(validation_encoded).hexdigest()
    split_boundaries = event_split["boundaries"]
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source_decisions_dataset_id": decisions.manifest["dataset_id"],
        "source_decisions_file_hash": decisions.manifest["decisions_file_hash"],
        "source_event_split_plan_id": event_split["plan_id"],
        "train_file_hash": train_hash,
        "validation_file_hash": validation_hash,
    }
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_development_inputs",
        "dataset_id": "historical-news-development-inputs-" + _hash_document(binding),
        "source": {
            "decisions": {
                "contract_version": REVIEW_DECISIONS_CONTRACT_VERSION,
                "dataset_id": decisions.manifest["dataset_id"],
                "decisions_file_hash": decisions.manifest["decisions_file_hash"],
            },
            "event_split": {
                "contract_version": EVENT_SPLIT_CONTRACT_VERSION,
                "plan_id": event_split["plan_id"],
            },
        },
        "boundaries": {
            "included_records_human_reviewed": True,
            "canonical_event_atomic": True,
            "train_validation_only": True,
            "oos_included": False,
            "oos_payload_included": False,
            "partial_review_source": split_boundaries.get("partial_review_source") is True,
            "llm_used": False,
            "model_weight_training_ready": False,
            "strict_backtest_input": False,
        },
        "counts": {
            "train_records": len(train),
            "validation_records": len(validation),
            "train_events": len({row["canonical_event_id"] for row in train}),
            "validation_events": len({row["canonical_event_id"] for row in validation}),
            "oos_events_omitted": int(event_split["partitions"][2]["event_count"]),
            "oos_articles_omitted": int(event_split["partitions"][2]["article_count"]),
        },
        "files": {
            "TRAIN": {"name": "train.jsonl", "count": len(train), "sha256": train_hash},
            "VALIDATION": {
                "name": "validation.jsonl", "count": len(validation),
                "sha256": validation_hash,
            },
        },
    }
    return HistoricalNewsDevelopmentInputs(manifest, train, validation)


def write_historical_news_development_inputs(
    dataset: HistoricalNewsDevelopmentInputs, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news development input is immutable")
    _validate_dataset(dataset)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "train.jsonl").write_bytes(_encode_rows(dataset.train))
        (staging / "validation.jsonl").write_bytes(_encode_rows(dataset.validation))
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_news_development_inputs(path: Path) -> HistoricalNewsDevelopmentInputs:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news development manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise ValueError("historical news development manifest is invalid")
    rows: dict[str, tuple[dict[str, Any], ...]] = {}
    for role in ("TRAIN", "VALIDATION"):
        file_document = manifest.get("files", {}).get(role, {})
        name = str(file_document.get("name", ""))
        if not name or Path(name).name != name:
            raise ValueError("historical news development file name is invalid")
        try:
            encoded = (root / name).read_bytes()
        except OSError as exc:
            raise ValueError("historical news development records cannot be read") from exc
        if hashlib.sha256(encoded).hexdigest() != file_document.get("sha256"):
            raise ValueError("historical news development file hash does not match")
        values: list[dict[str, Any]] = []
        for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("historical news development record is invalid") from exc
            if not isinstance(row, dict) or row.get("ordinal") != ordinal or row.get("role") != role:
                raise ValueError("historical news development record identity is invalid")
            values.append(row)
        rows[role] = tuple(values)
    dataset = HistoricalNewsDevelopmentInputs(manifest, rows["TRAIN"], rows["VALIDATION"])
    _validate_dataset(dataset)
    return dataset


def validate_historical_news_development_inputs(
    dataset: HistoricalNewsDevelopmentInputs,
) -> None:
    """Validate an in-memory development dataset before deriving another contract."""
    _validate_dataset(dataset)


def _record(role: str, event_id: str, decision: Mapping[str, Any]) -> dict[str, Any]:
    evidence = decision.get("article_evidence")
    review = decision.get("human_review")
    if not isinstance(evidence, Mapping) or not isinstance(review, Mapping):
        raise ValueError("news development input evidence is incomplete")
    title = str(evidence.get("title", "")).strip()
    published_at = str(evidence.get("published_at", "")).strip()
    if not title or not published_at:
        raise ValueError("news development input requires title and published_at")
    sample_binding = {
        "role": role,
        "canonical_event_id": event_id,
        "decision_id": decision["decision_id"],
        "review_item_id": decision["review_item_id"],
    }
    return {
        "sample_id": "historical-news-development-" + _hash_document(sample_binding),
        "role": role,
        "canonical_event_id": event_id,
        "decision_id": decision["decision_id"],
        "review_item_id": decision["review_item_id"],
        "model_input": {
            "article_identity": dict(decision.get("article_identity", {})),
            "title": title,
            "search_summary": str(evidence.get("search_summary", "")),
            "published_at": published_at,
            "published_precision": str(evidence.get("published_precision", "")),
            "published_at_source": str(evidence.get("published_at_source", "")),
            "case_links": list(decision.get("case_links", [])),
        },
        "human_target": {
            "semantic_relevance": "relevant",
            "canonical_event_id": event_id,
            "theme_profile_name": str(review.get("theme_profile_name", "")),
            "theme_names": list(review.get("theme_names", [])),
        },
        "eligibility": {
            "human_reviewed": True,
            "llm_used_for_target": False,
            "model_weight_training_ready": False,
        },
    }


def _validate_sources(
    decisions: HistoricalNewsReviewDecisions, event_split: Mapping[str, Any],
) -> None:
    if decisions.manifest.get("contract_version") != REVIEW_DECISIONS_CONTRACT_VERSION:
        raise ValueError("news development input requires review decisions v1")
    if event_split.get("contract_version") != EVENT_SPLIT_CONTRACT_VERSION:
        raise ValueError("news development input requires event split v1")
    validate_historical_news_event_split(event_split)
    split_source = event_split.get("source")
    if not isinstance(split_source, Mapping):
        raise ValueError("news development event split source is missing")
    if (
        split_source.get("dataset_id") != decisions.manifest.get("dataset_id")
        or split_source.get("decisions_file_hash") != decisions.manifest.get("decisions_file_hash")
    ):
        raise ValueError("news development event split source does not match decisions")
    boundaries = event_split.get("boundaries")
    if (
        not isinstance(boundaries, Mapping)
        or boundaries.get("canonical_event_atomic") is not True
        or boundaries.get("oos_status") != "SEALED"
        or boundaries.get("oos_review_payload_included") is not False
    ):
        raise ValueError("news development event split safety boundary is invalid")
    if not isinstance(event_split.get("event_assignments"), list):
        raise ValueError("news development event assignments are missing")


def _validate_dataset(dataset: HistoricalNewsDevelopmentInputs) -> None:
    manifest = dataset.manifest
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("dataset_kind") != "historical_news_development_inputs"
    ):
        raise ValueError("unsupported historical news development contract")
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("historical news development source is invalid")
    source_decisions = source.get("decisions")
    source_split = source.get("event_split")
    if not isinstance(source_decisions, Mapping) or not isinstance(source_split, Mapping):
        raise ValueError("historical news development source binding is invalid")
    if (
        source_decisions.get("contract_version") != REVIEW_DECISIONS_CONTRACT_VERSION
        or source_split.get("contract_version") != EVENT_SPLIT_CONTRACT_VERSION
    ):
        raise ValueError("historical news development source contract is invalid")
    boundaries = manifest.get("boundaries")
    if (
        not isinstance(boundaries, Mapping)
        or boundaries.get("included_records_human_reviewed") is not True
        or boundaries.get("canonical_event_atomic") is not True
        or boundaries.get("train_validation_only") is not True
        or boundaries.get("oos_included") is not False
        or boundaries.get("oos_payload_included") is not False
        or boundaries.get("llm_used") is not False
        or boundaries.get("model_weight_training_ready") is not False
        or boundaries.get("strict_backtest_input") is not False
    ):
        raise ValueError("historical news development safety boundary is invalid")
    for role, rows in (("TRAIN", dataset.train), ("VALIDATION", dataset.validation)):
        file_document = manifest.get("files", {}).get(role, {})
        encoded = _encode_rows(rows)
        if (
            int(file_document.get("count", -1)) != len(rows)
            or hashlib.sha256(encoded).hexdigest() != file_document.get("sha256")
            or any(row.get("role") != role for row in rows)
            or [row.get("ordinal") for row in rows] != list(range(1, len(rows) + 1))
            or any(
                not isinstance(row.get("eligibility"), Mapping)
                or row["eligibility"].get("human_reviewed") is not True
                or row["eligibility"].get("llm_used_for_target") is not False
                or row["eligibility"].get("model_weight_training_ready") is not False
                for row in rows
            )
        ):
            raise ValueError("historical news development file binding is invalid")
    train_events = {str(row.get("canonical_event_id", "")) for row in dataset.train}
    validation_events = {str(row.get("canonical_event_id", "")) for row in dataset.validation}
    if not train_events or not validation_events or train_events & validation_events:
        raise ValueError("historical news development events overlap or are empty")
    counts = manifest.get("counts")
    if (
        not isinstance(counts, Mapping)
        or counts.get("train_records") != len(dataset.train)
        or counts.get("validation_records") != len(dataset.validation)
        or counts.get("train_events") != len(train_events)
        or counts.get("validation_events") != len(validation_events)
    ):
        raise ValueError("historical news development counts do not match records")
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source_decisions_dataset_id": source_decisions.get("dataset_id"),
        "source_decisions_file_hash": source_decisions.get("decisions_file_hash"),
        "source_event_split_plan_id": source_split.get("plan_id"),
        "train_file_hash": manifest.get("files", {}).get("TRAIN", {}).get("sha256"),
        "validation_file_hash": manifest.get("files", {}).get("VALIDATION", {}).get("sha256"),
    }
    expected = "historical-news-development-inputs-" + _hash_document(binding)
    if manifest.get("dataset_id") != expected:
        raise ValueError("historical news development dataset identity does not match content")


def _encode_rows(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
