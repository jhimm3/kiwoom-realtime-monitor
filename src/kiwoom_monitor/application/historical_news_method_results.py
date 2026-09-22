"""Bind method predictions to one immutable blind historical-news request set."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.historical_news_blind_validation import (
    CONTRACT_VERSION as BLIND_VALIDATION_CONTRACT_VERSION,
    HistoricalNewsBlindValidation,
    validate_historical_news_blind_validation,
)


CONTRACT_VERSION = "historical_news_method_results/v1"
METHOD_KINDS = frozenset({"prompt_baseline", "rag", "fine_tuned"})


@dataclass(frozen=True)
class HistoricalNewsMethodResults:
    manifest: dict[str, Any]
    predictions: tuple[dict[str, Any], ...]


def build_historical_news_method_results(
    request_set: HistoricalNewsBlindValidation,
    *,
    method: Mapping[str, Any],
    predictions: Iterable[Mapping[str, Any]],
) -> HistoricalNewsMethodResults:
    validate_historical_news_blind_validation(request_set)
    method_document = _method_document(method)
    supplied: dict[str, Mapping[str, Any]] = {}
    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            raise ValueError("historical news method prediction must be an object")
        sample_id = str(prediction.get("sample_id", "")).strip()
        if not sample_id or sample_id in supplied:
            raise ValueError("historical news method prediction identity is missing or duplicated")
        supplied[sample_id] = prediction
    expected_ids = [str(row["sample_id"]) for row in request_set.requests]
    if set(supplied) != set(expected_ids):
        raise ValueError("historical news method predictions must cover the blind request exactly")
    rows = tuple(
        _prediction_row(ordinal, sample_id, supplied[sample_id])
        for ordinal, sample_id in enumerate(expected_ids, start=1)
    )
    encoded = _encode_rows(rows)
    predictions_hash = hashlib.sha256(encoded).hexdigest()
    request_file = request_set.manifest["files"]["REQUESTS"]
    binding = {
        "contract_version": CONTRACT_VERSION,
        "request_set_id": request_set.manifest["request_set_id"],
        "request_file_hash": request_file["sha256"],
        "method": method_document,
        "predictions_file_hash": predictions_hash,
    }
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_method_results",
        "result_set_id": "historical-news-method-results-" + _hash_document(binding),
        "source": {
            "contract_version": BLIND_VALIDATION_CONTRACT_VERSION,
            "request_set_id": request_set.manifest["request_set_id"],
            "request_file_hash": request_file["sha256"],
            "development_dataset_id": request_set.manifest["source"]["dataset_id"],
        },
        "method": method_document,
        "boundaries": {
            "blind_requests_only": True,
            "human_target_used": False,
            "oos_used": False,
            "credentials_included": False,
            "automatic_model_promotion": False,
        },
        "counts": {
            "predictions": len(rows),
            "abstained": sum(row["abstained"] is True for row in rows),
        },
        "files": {
            "PREDICTIONS": {
                "name": "predictions.jsonl",
                "count": len(rows),
                "sha256": predictions_hash,
            },
        },
    }
    dataset = HistoricalNewsMethodResults(manifest, rows)
    _validate_dataset(dataset)
    return dataset


def write_historical_news_method_results(
    dataset: HistoricalNewsMethodResults, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news method results are immutable")
    _validate_dataset(dataset)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "predictions.jsonl").write_bytes(_encode_rows(dataset.predictions))
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_news_method_results(path: Path) -> HistoricalNewsMethodResults:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news method result manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise ValueError("historical news method result manifest is invalid")
    file_document = manifest.get("files", {}).get("PREDICTIONS", {})
    name = str(file_document.get("name", ""))
    if not name or Path(name).name != name:
        raise ValueError("historical news method result file name is invalid")
    try:
        encoded = (root / name).read_bytes()
    except OSError as exc:
        raise ValueError("historical news method predictions cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != file_document.get("sha256"):
        raise ValueError("historical news method prediction hash does not match")
    predictions: list[dict[str, Any]] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("historical news method prediction is invalid") from exc
        if not isinstance(row, dict) or row.get("ordinal") != ordinal:
            raise ValueError("historical news method prediction identity is invalid")
        predictions.append(row)
    dataset = HistoricalNewsMethodResults(manifest, tuple(predictions))
    _validate_dataset(dataset)
    return dataset


def validate_historical_news_method_results(dataset: HistoricalNewsMethodResults) -> None:
    _validate_dataset(dataset)


def _prediction_row(
    ordinal: int, sample_id: str, value: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = {
        "sample_id", "abstained", "event_cluster_id", "theme_profile_name", "theme_names",
    }
    if set(value) - allowed:
        raise ValueError("historical news method prediction contains unsupported fields")
    abstained = value.get("abstained")
    if type(abstained) is not bool:
        raise ValueError("historical news method prediction requires boolean abstained")
    event_cluster_id = str(value.get("event_cluster_id", "")).strip()
    profile = str(value.get("theme_profile_name", "")).strip()
    raw_themes = value.get("theme_names")
    if not isinstance(raw_themes, (list, tuple)):
        raise ValueError("historical news method prediction theme_names must be a list")
    themes = [str(theme).strip() for theme in raw_themes]
    if any(not theme for theme in themes) or len(themes) != len(set(themes)):
        raise ValueError("historical news method prediction themes are blank or duplicated")
    if abstained and (event_cluster_id or profile or themes):
        raise ValueError("abstained historical news prediction must not contain labels")
    if not abstained and not event_cluster_id:
        raise ValueError("historical news method prediction requires an event cluster")
    return {
        "ordinal": ordinal,
        "sample_id": sample_id,
        "abstained": abstained,
        "prediction": {
            "event_cluster_id": event_cluster_id,
            "theme_profile_name": profile,
            "theme_names": themes,
        },
    }


def _method_document(value: Mapping[str, Any]) -> dict[str, str]:
    allowed = {"kind", "provider", "model", "implementation_version", "artifact_id"}
    if set(value) != allowed:
        raise ValueError("historical news method identity fields are invalid")
    document = {key: str(value.get(key, "")).strip() for key in allowed}
    if document["kind"] not in METHOD_KINDS or any(not item for item in document.values()):
        raise ValueError("historical news method identity is incomplete")
    return {key: document[key] for key in sorted(document)}


def _validate_dataset(dataset: HistoricalNewsMethodResults) -> None:
    manifest = dataset.manifest
    if (
        manifest.get("schema_version") != 1
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("dataset_kind") != "historical_news_method_results"
    ):
        raise ValueError("unsupported historical news method result contract")
    source = manifest.get("source")
    if (
        not isinstance(source, Mapping)
        or source.get("contract_version") != BLIND_VALIDATION_CONTRACT_VERSION
        or not all(str(source.get(key, "")) for key in (
            "request_set_id", "request_file_hash", "development_dataset_id",
        ))
    ):
        raise ValueError("historical news method result source is invalid")
    method = _method_document(manifest.get("method", {}))
    boundaries = manifest.get("boundaries")
    if (
        not isinstance(boundaries, Mapping)
        or boundaries.get("blind_requests_only") is not True
        or boundaries.get("human_target_used") is not False
        or boundaries.get("oos_used") is not False
        or boundaries.get("credentials_included") is not False
        or boundaries.get("automatic_model_promotion") is not False
    ):
        raise ValueError("historical news method result boundary is invalid")
    if not dataset.predictions:
        raise ValueError("historical news method predictions are empty")
    sample_ids: list[str] = []
    abstained = 0
    for ordinal, row in enumerate(dataset.predictions, start=1):
        prediction = row.get("prediction")
        if (
            set(row) != {"ordinal", "sample_id", "abstained", "prediction"}
            or row.get("ordinal") != ordinal
            or not str(row.get("sample_id", ""))
            or type(row.get("abstained")) is not bool
            or not isinstance(prediction, Mapping)
            or set(prediction) != {"event_cluster_id", "theme_profile_name", "theme_names"}
        ):
            raise ValueError("historical news method prediction fields are invalid")
        normalized = _prediction_row(ordinal, str(row["sample_id"]), {
            "sample_id": row["sample_id"],
            "abstained": row["abstained"],
            **prediction,
        })
        if normalized != row:
            raise ValueError("historical news method prediction is not normalized")
        sample_ids.append(str(row["sample_id"]))
        abstained += row["abstained"] is True
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("historical news method prediction sample identity is duplicated")
    encoded = _encode_rows(dataset.predictions)
    predictions_hash = hashlib.sha256(encoded).hexdigest()
    file_document = manifest.get("files", {}).get("PREDICTIONS", {})
    counts = manifest.get("counts")
    if (
        not isinstance(file_document, Mapping)
        or file_document.get("name") != "predictions.jsonl"
        or file_document.get("count") != len(dataset.predictions)
        or file_document.get("sha256") != predictions_hash
        or not isinstance(counts, Mapping)
        or counts.get("predictions") != len(dataset.predictions)
        or counts.get("abstained") != abstained
    ):
        raise ValueError("historical news method result file binding is invalid")
    binding = {
        "contract_version": CONTRACT_VERSION,
        "request_set_id": source.get("request_set_id"),
        "request_file_hash": source.get("request_file_hash"),
        "method": method,
        "predictions_file_hash": predictions_hash,
    }
    expected = "historical-news-method-results-" + _hash_document(binding)
    if manifest.get("result_set_id") != expected:
        raise ValueError("historical news method result identity does not match content")


def _encode_rows(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
