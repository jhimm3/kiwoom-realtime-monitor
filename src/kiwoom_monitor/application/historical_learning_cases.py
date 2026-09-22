"""Build retrospective learning cases without disguising them as point-in-time replay."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.infrastructure.historical_reconstruction import (
    CONTRACT_VERSION as RECONSTRUCTION_CONTRACT_VERSION,
    HistoricalReconstructionDataset,
)


CONTRACT_VERSION = "historical_learning_cases/v1"
_SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class HistoricalLearningCaseDataset:
    manifest: dict[str, Any]
    cases: tuple[dict[str, Any], ...]


def build_historical_learning_cases(
    source: HistoricalReconstructionDataset,
    *,
    created_at: datetime | None = None,
) -> HistoricalLearningCaseDataset:
    """Separate post-hoc selection, reconstructed evidence, interpretation, and outcomes."""
    if source.manifest.get("contract_version") != RECONSTRUCTION_CONTRACT_VERSION:
        raise ValueError("learning cases require historical_reconstruction/v1")
    population = source.manifest.get("population")
    if not isinstance(population, Mapping) or population.get("not_contemporaneous_top20") is not True:
        raise ValueError("learning case source must retain post-hoc population provenance")
    source_dataset_id = str(source.manifest.get("dataset_id", ""))
    source_revision_hash = str(source.manifest.get("revision_ids_hash", ""))
    if not source_dataset_id or not source_revision_hash:
        raise ValueError("learning case source identity is incomplete")
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    created_text = created.astimezone(UTC).isoformat()

    news_by_case: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    bars_by_case: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source.records:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if row.get("kind") == "historical_news_evidence":
            news_by_case[(str(payload.get("source_date", "")), str(payload.get("code", "")))].append(row)
        elif row.get("kind") == "historical_market_bar" and payload.get("phase") == "outcome":
            bars_by_case[(str(payload.get("case_date", "")), str(payload.get("code", "")))].append(row)

    cases: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    exclusion_counts: Counter[str] = Counter()
    news_case_count = 0
    candidates = sorted(
        source.records_of_kind("historical_candidate"),
        key=lambda row: (
            str(row.get("payload", {}).get("date", "")),
            str(row.get("payload", {}).get("code", "")),
        ),
    )
    for ordinal, candidate in enumerate(candidates, start=1):
        payload = candidate.get("payload", {})
        selection_date = str(payload.get("date", ""))
        code = str(payload.get("code", ""))
        if not selection_date or not code:
            raise ValueError("historical candidate identity is incomplete")
        evidence, exclusions = _news_evidence(news_by_case[(selection_date, code)], selection_date)
        outcome = _outcome_label(bars_by_case[(selection_date, code)], selection_date)
        outcome_counts[str(outcome["status"])] += 1
        exclusion_counts.update(exclusions)
        news_case_count += int(bool(evidence))
        case_id = "historical-learning-case-" + _hash_document({
            "source_dataset_id": source_dataset_id,
            "selection_date": selection_date,
            "code": code,
        })
        cases.append({
            "ordinal": ordinal,
            "case_id": case_id,
            "source_dataset_id": source_dataset_id,
            "selection_date": selection_date,
            "stock": {"code": code, "name": str(payload.get("name", ""))},
            "sample_selection": {
                "kind": "post_session_posthoc_candidate",
                "available_at": str(candidate.get("available_at", "")),
                "not_contemporaneous_top20": True,
                "generalization_scope": "selected_candidate_population_only",
                "features": {
                    key: payload.get(key)
                    for key in (
                        "score", "reasons", "rank_value", "rank_gain", "rank_high",
                        "rank_volume_ratio", "gain_pct", "high_pct", "volume_ratio",
                        "trading_value",
                    )
                },
            },
            "model_input": {
                "kind": "retrospective_news_evidence",
                "strict_point_in_time_available": False,
                "content_scope": "title_and_search_summary_not_full_article_body",
                "news_evidence": evidence,
                "excluded_relation_counts": dict(sorted(exclusions.items())),
            },
            "interpretation": {
                "status": "not_generated",
                "provider": "",
                "model": "",
                "generated_at": None,
                "summary": "",
                "theme_candidates": [],
            },
            "outcome_label": outcome,
            "eligibility": {
                "retrospective_semantic_case": bool(evidence),
                "semantic_relevance_reviewed": False,
                "outcome_label_available": outcome["status"] == "observed",
                "strict_point_in_time_case": False,
                "model_weight_training_ready": False,
            },
        })

    case_bytes = _encode_cases(cases)
    cases_hash = hashlib.sha256(case_bytes).hexdigest()
    identity = {
        "contract_version": CONTRACT_VERSION,
        "source_dataset_id": source_dataset_id,
        "source_revision_ids_hash": source_revision_hash,
        "cases_file_hash": cases_hash,
    }
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_learning_cases",
        "dataset_id": "historical-learning-cases-" + _hash_document(identity),
        "created_at": created_text,
        "source": {
            "contract_version": RECONSTRUCTION_CONTRACT_VERSION,
            "dataset_id": source_dataset_id,
            "revision_ids_hash": source_revision_hash,
            "not_contemporaneous_top20": True,
        },
        "boundaries": {
            "sample_selection_is_posthoc": True,
            "news_was_collected_retrospectively": True,
            "publication_time_does_not_replace_source_available_at": True,
            "model_input_and_outcome_label_are_separate": True,
            "ai_interpretation_generated": False,
            "strict_backtest_input": False,
            "oos_results_opened": False,
        },
        "counts": {
            "cases": len(cases),
            "cases_with_news_evidence": news_case_count,
            "outcome_status": dict(sorted(outcome_counts.items())),
            "excluded_news_relations": dict(sorted(exclusion_counts.items())),
        },
        "cases_file": "cases.jsonl",
        "case_count": len(cases),
        "cases_file_hash": cases_hash,
    }
    return HistoricalLearningCaseDataset(manifest, tuple(cases))


def write_historical_learning_cases(
    dataset: HistoricalLearningCaseDataset, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical learning case export is immutable and cannot be overwritten")
    encoded = _encode_cases(dataset.cases)
    if hashlib.sha256(encoded).hexdigest() != dataset.manifest.get("cases_file_hash"):
        raise ValueError("historical learning case content changed after manifest creation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "cases.jsonl").write_bytes(encoded)
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_learning_cases(path: Path) -> HistoricalLearningCaseDataset:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical learning case manifest cannot be read") from exc
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported historical learning case contract")
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, dict) or boundaries.get("strict_backtest_input") is not False:
        raise ValueError("historical learning case boundary is missing")
    if boundaries.get("model_input_and_outcome_label_are_separate") is not True:
        raise ValueError("historical learning case input/label boundary is missing")
    file_name = str(manifest.get("cases_file", ""))
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("historical learning cases_file must be local")
    try:
        encoded = (root / file_name).read_bytes()
    except OSError as exc:
        raise ValueError("historical learning cases cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("cases_file_hash"):
        raise ValueError("historical learning case file hash does not match manifest")
    cases: list[dict[str, Any]] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"historical learning case {ordinal} is invalid") from exc
        if not isinstance(item, dict) or item.get("ordinal") != ordinal or not item.get("case_id"):
            raise ValueError("historical learning case ordinal/identity is invalid")
        if item.get("source_dataset_id") != manifest.get("source", {}).get("dataset_id"):
            raise ValueError("historical learning case source identity does not match manifest")
        cases.append(item)
    if len(cases) != int(manifest.get("case_count", -1)):
        raise ValueError("historical learning case count does not match manifest")
    return HistoricalLearningCaseDataset(dict(manifest), tuple(cases))


def _news_evidence(
    rows: Iterable[dict[str, Any]], selection_date: str,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    cutoff = datetime.combine(date.fromisoformat(selection_date) + timedelta(days=1), time.min, _SEOUL)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    query_texts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    exclusions: Counter[str] = Counter()
    for row in rows:
        payload = row.get("payload", {})
        if not payload.get("publication_time_verified") or not payload.get("training_eligible"):
            exclusions[str(payload.get("training_exclusion_reason") or "publication_time_unverified")] += 1
            continue
        published = _aware_datetime(payload.get("published_at"))
        if published is None:
            exclusions["publication_time_invalid"] += 1
            continue
        if published.astimezone(_SEOUL) >= cutoff:
            exclusions["published_after_selection_date"] += 1
            continue
        key = (
            str(payload.get("provider", "")),
            str(payload.get("office_id", "")),
            str(payload.get("article_id", "")),
        )
        if not all(key):
            exclusions["article_identity_missing"] += 1
            continue
        query = str(payload.get("query_text", "")).strip()
        if query:
            query_texts[key].add(query)
        if key in unique:
            exclusions["duplicate_article_relation"] += 1
            continue
        unique[key] = {
            "provider": key[0],
            "office_id": key[1],
            "article_id": key[2],
            "office_name": str(payload.get("office_name", "")),
            "title": str(payload.get("title", "")),
            "search_summary": str(payload.get("summary", "")),
            "published_at": published.isoformat(),
            "published_precision": str(payload.get("published_precision", "")),
            "published_at_source": str(payload.get("published_at_source", "")),
            "article_url": str(payload.get("article_url", "")),
            "original_url": str(payload.get("original_url", "")),
            "collector_available_at": str(row.get("available_at", "")),
            "source_revision_id": str(row.get("revision_id", "")),
        }
    values: list[dict[str, Any]] = []
    for key, value in unique.items():
        value["query_texts"] = sorted(query_texts[key])
        values.append(value)
    values.sort(key=lambda value: (
        str(value["published_at"]), str(value["provider"]),
        str(value["office_id"]), str(value["article_id"]),
    ))
    return values, exclusions


def _outcome_label(rows: Iterable[dict[str, Any]], selection_date: str) -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload", {})
        bar_time = _aware_datetime(payload.get("bar_time"))
        if bar_time is None or bar_time.astimezone(_SEOUL).date().isoformat() <= selection_date:
            continue
        interval = int(payload.get("interval_seconds", 0) or 0)
        if interval not in {60, 300}:
            continue
        valid.append(row)
    if not valid:
        return {
            "status": "missing",
            "reason": "no_observed_outcome_bars",
            "uses_future_market_data": True,
        }
    resolution = 60 if any(int(row["payload"].get("interval_seconds", 0)) == 60 for row in valid) else 300
    selected = sorted(
        (row for row in valid if int(row["payload"].get("interval_seconds", 0)) == resolution),
        key=lambda row: str(row["payload"].get("bar_time", "")),
    )
    first, last = selected[0]["payload"], selected[-1]["payload"]
    first_open = int(first.get("open", 0) or 0)
    last_close = int(last.get("close", 0) or 0)
    high = max(int(row["payload"].get("high", 0) or 0) for row in selected)
    low = min(int(row["payload"].get("low", 0) or 0) for row in selected)
    status = "observed" if first_open > 0 else "invalid_price"
    return {
        "status": status,
        "resolution_seconds": resolution,
        "bar_count": len(selected),
        "first_bar_time": str(first.get("bar_time", "")),
        "last_bar_time": str(last.get("bar_time", "")),
        "first_open": first_open,
        "last_close": last_close,
        "session_high": high,
        "session_low": low,
        "close_return_pct": _percent(last_close, first_open),
        "max_up_pct": _percent(high, first_open),
        "max_down_pct": _percent(low, first_open),
        "source_available_at": max(str(row.get("available_at", "")) for row in selected),
        "uses_future_market_data": True,
        "label_is_model_input": False,
    }


def _percent(value: int, basis: int) -> float | None:
    return round((value / basis - 1.0) * 100.0, 6) if basis > 0 else None


def _aware_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_cases(cases: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for case in cases
    )
