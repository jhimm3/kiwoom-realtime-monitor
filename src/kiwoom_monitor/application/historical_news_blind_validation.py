"""Create target-free validation requests for comparable historical news methods."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.historical_news_development_inputs import (
    CONTRACT_VERSION as DEVELOPMENT_INPUTS_CONTRACT_VERSION,
    HistoricalNewsDevelopmentInputs,
    validate_historical_news_development_inputs,
)


CONTRACT_VERSION = "historical_news_blind_validation/v1"
_FORBIDDEN_INPUT_KEYS = frozenset({
    "human_target",
    "canonical_event_id",
    "semantic_relevance",
    "theme_profile_name",
    "theme_names",
})
_ALLOWED_MODEL_INPUT_KEYS = frozenset({
    "article_identity",
    "title",
    "search_summary",
    "published_at",
    "published_precision",
    "published_at_source",
    "case_links",
})


@dataclass(frozen=True)
class HistoricalNewsBlindValidation:
    manifest: dict[str, Any]
    requests: tuple[dict[str, Any], ...]


def build_historical_news_blind_validation(
    source: HistoricalNewsDevelopmentInputs,
) -> HistoricalNewsBlindValidation:
    """Project only method inputs from the exact VALIDATION partition."""
    validate_historical_news_development_inputs(source)
    requests: list[dict[str, Any]] = []
    for ordinal, row in enumerate(source.validation, start=1):
        model_input = row.get("model_input")
        if (
            not isinstance(model_input, Mapping)
            or set(model_input) != _ALLOWED_MODEL_INPUT_KEYS
            or _contains_forbidden_key(model_input)
        ):
            raise ValueError("blind validation model input contains target data")
        requests.append({
            "ordinal": ordinal,
            "sample_id": str(row["sample_id"]),
            "model_input": dict(model_input),
        })
    if not requests:
        raise ValueError("blind validation requires validation requests")
    request_rows = tuple(requests)
    request_hash = hashlib.sha256(_encode_rows(request_rows)).hexdigest()
    source_validation = source.manifest["files"]["VALIDATION"]
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source_dataset_id": source.manifest["dataset_id"],
        "source_validation_file_hash": source_validation["sha256"],
        "request_file_hash": request_hash,
    }
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_blind_validation",
        "request_set_id": "historical-news-blind-validation-" + _hash_document(binding),
        "source": {
            "contract_version": DEVELOPMENT_INPUTS_CONTRACT_VERSION,
            "dataset_id": source.manifest["dataset_id"],
            "validation_file_hash": source_validation["sha256"],
        },
        "boundaries": {
            "validation_only": True,
            "human_target_included": False,
            "canonical_event_id_included": False,
            "oos_included": False,
            "method_neutral": True,
            "training_allowed": False,
        },
        "counts": {"requests": len(request_rows)},
        "files": {
            "REQUESTS": {
                "name": "requests.jsonl",
                "count": len(request_rows),
                "sha256": request_hash,
            },
        },
    }
    dataset = HistoricalNewsBlindValidation(manifest, request_rows)
    _validate_dataset(dataset)
    return dataset


def write_historical_news_blind_validation(
    dataset: HistoricalNewsBlindValidation, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news blind validation is immutable")
    _validate_dataset(dataset)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "requests.jsonl").write_bytes(_encode_rows(dataset.requests))
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_news_blind_validation(path: Path) -> HistoricalNewsBlindValidation:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news blind validation manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise ValueError("historical news blind validation manifest is invalid")
    file_document = manifest.get("files", {}).get("REQUESTS", {})
    name = str(file_document.get("name", ""))
    if not name or Path(name).name != name:
        raise ValueError("historical news blind validation file name is invalid")
    try:
        encoded = (root / name).read_bytes()
    except OSError as exc:
        raise ValueError("historical news blind validation requests cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != file_document.get("sha256"):
        raise ValueError("historical news blind validation file hash does not match")
    requests: list[dict[str, Any]] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("historical news blind validation request is invalid") from exc
        if not isinstance(row, dict) or row.get("ordinal") != ordinal:
            raise ValueError("historical news blind validation request identity is invalid")
        requests.append(row)
    dataset = HistoricalNewsBlindValidation(manifest, tuple(requests))
    _validate_dataset(dataset)
    return dataset


def validate_historical_news_blind_validation(
    dataset: HistoricalNewsBlindValidation,
) -> None:
    """Validate an in-memory blind request set before a method consumes it."""
    _validate_dataset(dataset)


def _validate_dataset(dataset: HistoricalNewsBlindValidation) -> None:
    manifest = dataset.manifest
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("dataset_kind") != "historical_news_blind_validation"
    ):
        raise ValueError("unsupported historical news blind validation contract")
    source = manifest.get("source")
    if (
        not isinstance(source, Mapping)
        or source.get("contract_version") != DEVELOPMENT_INPUTS_CONTRACT_VERSION
        or not str(source.get("dataset_id", ""))
        or not str(source.get("validation_file_hash", ""))
    ):
        raise ValueError("historical news blind validation source is invalid")
    boundaries = manifest.get("boundaries")
    if (
        not isinstance(boundaries, Mapping)
        or boundaries.get("validation_only") is not True
        or boundaries.get("human_target_included") is not False
        or boundaries.get("canonical_event_id_included") is not False
        or boundaries.get("oos_included") is not False
        or boundaries.get("method_neutral") is not True
        or boundaries.get("training_allowed") is not False
    ):
        raise ValueError("historical news blind validation boundary is invalid")
    if not dataset.requests:
        raise ValueError("historical news blind validation requests are empty")
    sample_ids: list[str] = []
    for ordinal, row in enumerate(dataset.requests, start=1):
        model_input = row.get("model_input")
        sample_id = str(row.get("sample_id", ""))
        if (
            set(row) != {"ordinal", "sample_id", "model_input"}
            or row.get("ordinal") != ordinal
            or not sample_id
            or not isinstance(model_input, Mapping)
            or set(model_input) != _ALLOWED_MODEL_INPUT_KEYS
            or _contains_forbidden_key(model_input)
        ):
            raise ValueError("historical news blind validation request leaks target data")
        sample_ids.append(sample_id)
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("historical news blind validation sample identity is duplicated")
    encoded = _encode_rows(dataset.requests)
    request_hash = hashlib.sha256(encoded).hexdigest()
    file_document = manifest.get("files", {}).get("REQUESTS", {})
    counts = manifest.get("counts")
    if (
        not isinstance(file_document, Mapping)
        or file_document.get("name") != "requests.jsonl"
        or file_document.get("count") != len(dataset.requests)
        or file_document.get("sha256") != request_hash
        or not isinstance(counts, Mapping)
        or counts.get("requests") != len(dataset.requests)
    ):
        raise ValueError("historical news blind validation file binding is invalid")
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source_dataset_id": source.get("dataset_id"),
        "source_validation_file_hash": source.get("validation_file_hash"),
        "request_file_hash": request_hash,
    }
    expected = "historical-news-blind-validation-" + _hash_document(binding)
    if manifest.get("request_set_id") != expected:
        raise ValueError("historical news blind validation identity does not match content")


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _FORBIDDEN_INPUT_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _encode_rows(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
