"""Export human review sheets and import validated immutable decisions."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.historical_news_review_queue import (
    CONTRACT_VERSION as REVIEW_QUEUE_CONTRACT_VERSION,
    HistoricalNewsReviewQueue,
)


CONTRACT_VERSION = "historical_news_review_decisions/v1"
DECISIONS = frozenset({"relevant", "not_relevant", "uncertain"})
CSV_FIELDS = (
    "source_queue_dataset_id",
    "source_queue_items_file_hash",
    "queue_ordinal",
    "review_item_id",
    "published_at",
    "stock_codes",
    "stock_names",
    "title",
    "search_summary",
    "article_url",
    "rule_relevant_hint",
    "maximum_rule_relevance_score",
    "human_decision",
    "canonical_event_id",
    "theme_profile_name",
    "theme_names",
    "notes",
    "reviewer",
    "reviewed_at",
)


@dataclass(frozen=True)
class HistoricalNewsReviewDecisions:
    manifest: dict[str, Any]
    decisions: tuple[dict[str, Any], ...]


def export_historical_news_review_sheet(
    source: HistoricalNewsReviewQueue,
    output: Path,
    *,
    rule_relevant_only: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Create a non-authoritative, editable UTF-8 CSV without overwriting prior work."""
    _validate_source(source)
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news review sheet already exists and will not be overwritten")
    if limit is not None and limit <= 0:
        raise ValueError("review sheet limit must be positive")
    selected = [
        item for item in source.items
        if not rule_relevant_only or bool(item.get("priority", {}).get("has_rule_relevant_hint"))
    ]
    if limit is not None:
        selected = selected[:limit]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in selected:
            writer.writerow(_sheet_row(source, item))
    return {
        "source_queue_dataset_id": source.manifest["dataset_id"],
        "source_queue_items_file_hash": source.manifest["items_file_hash"],
        "row_count": len(selected),
        "rule_relevant_only": rule_relevant_only,
        "authoritative": False,
        "human_review_complete": False,
        "model_weight_training_ready": False,
    }


def load_historical_news_review_sheet(
    source: HistoricalNewsReviewQueue, sheet: Path,
) -> tuple[dict[str, str], ...]:
    """Load a working sheet only after binding every row to the immutable queue."""
    _validate_source(source)
    items_by_id = {str(item["review_item_id"]): item for item in source.items}
    rows = _read_sheet(sheet)
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        _validate_source_binding(source, row, row_number)
        review_item_id = row["review_item_id"].strip()
        if not review_item_id or review_item_id not in items_by_id:
            raise ValueError(f"review row {row_number} has an unknown review_item_id")
        if review_item_id in seen:
            raise ValueError(f"review row {row_number} duplicates review_item_id")
        seen.add(review_item_id)
        if row["queue_ordinal"].strip() != str(items_by_id[review_item_id]["ordinal"]):
            raise ValueError(f"review row {row_number} queue ordinal does not match source")
        _review_values(row, row_number)
    return tuple(dict(row) for row in rows)


def update_historical_news_review_sheet_row(
    source: HistoricalNewsReviewQueue,
    sheet: Path,
    review_item_id: str,
    values: Mapping[str, str],
) -> dict[str, str]:
    """Atomically update one editable review row while preserving source-bound fields."""
    editable = {
        "human_decision", "canonical_event_id", "theme_profile_name",
        "theme_names", "notes", "reviewer", "reviewed_at",
    }
    unknown = set(values) - editable
    if unknown:
        raise ValueError(f"unsupported review sheet fields: {', '.join(sorted(unknown))}")
    rows = [dict(row) for row in load_historical_news_review_sheet(source, sheet)]
    matched = [index for index, row in enumerate(rows) if row["review_item_id"] == review_item_id]
    if len(matched) != 1:
        raise ValueError("review item is missing or duplicated in working sheet")
    index = matched[0]
    rows[index].update({key: str(value) for key, value in values.items()})
    _review_values(rows[index], index + 2)
    destination = Path(sheet)
    staging = destination.with_name(f".{destination.name}.tmp")
    try:
        with staging.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        staging.replace(destination)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    return dict(rows[index])

def build_historical_news_review_decisions(
    source: HistoricalNewsReviewQueue,
    sheet: Path,
    *,
    created_at: datetime | None = None,
) -> HistoricalNewsReviewDecisions:
    """Validate reviewed CSV rows and bind decisions to the exact immutable queue."""
    _validate_source(source)
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    items_by_id = {str(item["review_item_id"]): item for item in source.items}
    rows = load_historical_news_review_sheet(source, sheet)
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row_number, row in enumerate(rows, start=2):
        review_item_id = row["review_item_id"].strip()
        item = items_by_id[review_item_id]
        decision, reviewer, reviewed_at, event_id, profile_name, theme_names = _review_values(
            row, row_number,
        )
        if not decision:
            continue
        assert reviewed_at is not None
        counts[decision] += 1
        decisions.append({
            "decision_id": "historical-news-decision-" + _hash_document({
                "source_queue_dataset_id": source.manifest["dataset_id"],
                "review_item_id": review_item_id,
                "human_decision": decision,
                "canonical_event_id": event_id,
                "theme_profile_name": profile_name,
                "theme_names": theme_names,
                "reviewer": reviewer,
                "reviewed_at": reviewed_at.astimezone(UTC).isoformat(),
            }),
            "source_queue_dataset_id": source.manifest["dataset_id"],
            "source_queue_ordinal": item["ordinal"],
            "review_item_id": review_item_id,
            "article_identity": {
                key: item["article"].get(key, "")
                for key in ("provider", "office_id", "article_id")
            },
            "case_links": [
                {
                    "case_id": link.get("case_id", ""),
                    "selection_date": link.get("selection_date", ""),
                    "stock": link.get("stock", {}),
                }
                for link in item.get("case_links", [])
            ],
            "human_review": {
                "decision": decision,
                "canonical_event_id": event_id,
                "theme_profile_name": profile_name,
                "theme_names": theme_names,
                "notes": row["notes"].strip(),
                "reviewer": reviewer,
                "reviewed_at": reviewed_at.astimezone(UTC).isoformat(),
            },
            "eligibility": {
                "human_review_complete": True,
                "llm_used_for_decision": False,
                "event_split_ready": decision != "relevant" or bool(event_id),
                "model_weight_training_ready": False,
            },
        })
    if not decisions:
        raise ValueError("review sheet has no completed human decisions")
    decisions.sort(key=lambda item: (int(item["source_queue_ordinal"]), str(item["review_item_id"])))
    decisions = [{"ordinal": ordinal, **item} for ordinal, item in enumerate(decisions, start=1)]
    encoded = _encode_decisions(decisions)
    decisions_hash = hashlib.sha256(encoded).hexdigest()
    source_count = int(source.manifest.get("item_count", len(source.items)))
    reviewed_ids = {str(item["review_item_id"]) for item in decisions}
    source_complete = len(reviewed_ids) == source_count
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_review_decisions",
        "dataset_id": "historical-news-review-decisions-" + _hash_document({
            "contract_version": CONTRACT_VERSION,
            "source_queue_dataset_id": source.manifest["dataset_id"],
            "source_queue_items_file_hash": source.manifest["items_file_hash"],
            "decisions_file_hash": decisions_hash,
        }),
        "created_at": created.astimezone(UTC).isoformat(),
        "source": {
            "contract_version": REVIEW_QUEUE_CONTRACT_VERSION,
            "dataset_id": source.manifest["dataset_id"],
            "items_file_hash": source.manifest["items_file_hash"],
            "item_count": source_count,
        },
        "boundaries": {
            "included_decisions_human_reviewed": True,
            "llm_used_for_decisions": False,
            "source_queue_review_complete": source_complete,
            "partial_review_result": not source_complete,
            "event_ids_required_for_relevant": True,
            "theme_names_require_profile_name": True,
            "model_weight_training_ready": False,
            "strict_backtest_input": False,
        },
        "counts": {
            "decisions": len(decisions),
            "relevant": counts["relevant"],
            "not_relevant": counts["not_relevant"],
            "uncertain": counts["uncertain"],
            "source_items_unreviewed": source_count - len(reviewed_ids),
        },
        "decisions_file": "decisions.jsonl",
        "decision_count": len(decisions),
        "decisions_file_hash": decisions_hash,
    }
    return HistoricalNewsReviewDecisions(manifest, tuple(decisions))


def write_historical_news_review_decisions(
    dataset: HistoricalNewsReviewDecisions, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news review decisions export is immutable and cannot be overwritten")
    encoded = _encode_decisions(dataset.decisions)
    if hashlib.sha256(encoded).hexdigest() != dataset.manifest.get("decisions_file_hash"):
        raise ValueError("historical news review decisions changed after manifest creation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "decisions.jsonl").write_bytes(encoded)
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_news_review_decisions(path: Path) -> HistoricalNewsReviewDecisions:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news review decisions manifest cannot be read") from exc
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported historical news review decisions contract")
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, dict):
        raise ValueError("historical news review decisions boundaries are missing")
    if boundaries.get("included_decisions_human_reviewed") is not True:
        raise ValueError("human review boundary is missing")
    if boundaries.get("llm_used_for_decisions") is not False:
        raise ValueError("LLM decision boundary is missing")
    if boundaries.get("model_weight_training_ready") is not False:
        raise ValueError("model training boundary is missing")
    file_name = str(manifest.get("decisions_file", ""))
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("historical news decisions_file must be local")
    try:
        encoded = (root / file_name).read_bytes()
    except OSError as exc:
        raise ValueError("historical news review decisions cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("decisions_file_hash"):
        raise ValueError("historical news review decisions file hash does not match manifest")
    decisions: list[dict[str, Any]] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"historical news review decision {ordinal} is invalid") from exc
        if not isinstance(item, dict) or item.get("ordinal") != ordinal or not item.get("decision_id"):
            raise ValueError("historical news review decision ordinal/identity is invalid")
        if item.get("source_queue_dataset_id") != manifest.get("source", {}).get("dataset_id"):
            raise ValueError("historical news review decision source identity does not match manifest")
        eligibility = item.get("eligibility")
        if not isinstance(eligibility, dict) or eligibility.get("human_review_complete") is not True:
            raise ValueError("historical news review decision completion boundary is missing")
        if eligibility.get("model_weight_training_ready") is not False:
            raise ValueError("historical news review decision training boundary is missing")
        decisions.append(item)
    if len(decisions) != int(manifest.get("decision_count", -1)):
        raise ValueError("historical news review decision count does not match manifest")
    return HistoricalNewsReviewDecisions(dict(manifest), tuple(decisions))


def _validate_source(source: HistoricalNewsReviewQueue) -> None:
    if source.manifest.get("contract_version") != REVIEW_QUEUE_CONTRACT_VERSION:
        raise ValueError("review workflow requires historical_news_review_queue/v1")
    if not source.manifest.get("dataset_id") or not source.manifest.get("items_file_hash"):
        raise ValueError("review queue source identity is incomplete")


def _sheet_row(source: HistoricalNewsReviewQueue, item: Mapping[str, Any]) -> dict[str, Any]:
    links = item.get("case_links", [])
    stocks = [link.get("stock", {}) for link in links if isinstance(link, Mapping)]
    article = item.get("article", {})
    priority = item.get("priority", {})
    return {
        "source_queue_dataset_id": source.manifest["dataset_id"],
        "source_queue_items_file_hash": source.manifest["items_file_hash"],
        "queue_ordinal": item.get("ordinal", ""),
        "review_item_id": item.get("review_item_id", ""),
        "published_at": article.get("published_at", ""),
        "stock_codes": "|".join(sorted({str(stock.get("code", "")) for stock in stocks if stock.get("code")})),
        "stock_names": "|".join(sorted({str(stock.get("name", "")) for stock in stocks if stock.get("name")})),
        "title": article.get("title", ""),
        "search_summary": article.get("search_summary", ""),
        "article_url": article.get("article_url", ""),
        "rule_relevant_hint": str(bool(priority.get("has_rule_relevant_hint"))).lower(),
        "maximum_rule_relevance_score": priority.get("maximum_rule_relevance_score", 0),
        "human_decision": "",
        "canonical_event_id": "",
        "theme_profile_name": "",
        "theme_names": "",
        "notes": "",
        "reviewer": "",
        "reviewed_at": "",
    }


def _read_sheet(path: Path) -> list[dict[str, str]]:
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or any(field not in reader.fieldnames for field in CSV_FIELDS):
                raise ValueError("review sheet is missing required columns")
            return [
                {field: str(row.get(field, "") or "") for field in CSV_FIELDS}
                for row in reader
            ]
    except UnicodeDecodeError as exc:
        raise ValueError("review sheet must be UTF-8 CSV") from exc
    except OSError as exc:
        raise ValueError("review sheet cannot be read") from exc


def _validate_source_binding(
    source: HistoricalNewsReviewQueue, row: Mapping[str, str], row_number: int,
) -> None:
    if row["source_queue_dataset_id"].strip() != source.manifest["dataset_id"]:
        raise ValueError(f"review row {row_number} source queue dataset does not match")
    if row["source_queue_items_file_hash"].strip() != source.manifest["items_file_hash"]:
        raise ValueError(f"review row {row_number} source queue hash does not match")


def _review_values(
    row: Mapping[str, str], row_number: int,
) -> tuple[str, str, datetime | None, str, str, list[str]]:
    decision = row["human_decision"].strip().casefold()
    reviewer = row["reviewer"].strip()
    reviewed_at = _aware_datetime(row["reviewed_at"].strip())
    event_id = row["canonical_event_id"].strip()
    profile_name = row["theme_profile_name"].strip()
    theme_names = _split_names(row["theme_names"])
    if not decision:
        return "", reviewer, reviewed_at, event_id, profile_name, theme_names
    if decision not in DECISIONS:
        raise ValueError(f"review row {row_number} has an unsupported human_decision")
    if not reviewer or reviewed_at is None:
        raise ValueError(f"review row {row_number} requires reviewer and timezone-aware reviewed_at")
    if decision == "relevant":
        if not event_id:
            raise ValueError(f"review row {row_number} relevant decision requires canonical_event_id")
        if theme_names and not profile_name:
            raise ValueError(f"review row {row_number} theme names require theme_profile_name")
    elif event_id or profile_name or theme_names:
        raise ValueError(
            f"review row {row_number} non-relevant/uncertain decision cannot assign event or theme"
        )
    return decision, reviewer, reviewed_at, event_id, profile_name, theme_names

def _split_names(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.split("|") if part.strip()))


def _aware_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_decisions(decisions: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for item in decisions
    )
