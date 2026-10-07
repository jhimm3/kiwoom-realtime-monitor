"""Score one blind historical-news method result without exposing OOS."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from kiwoom_monitor.application.historical_news_blind_validation import (
    HistoricalNewsBlindValidation,
    validate_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    HistoricalNewsDevelopmentInputs,
    validate_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_method_results import (
    METHOD_KINDS,
    HistoricalNewsMethodResults,
    validate_historical_news_method_results,
)


CONTRACT_VERSION = "historical_news_method_evaluation/v1"
_SOURCE_KEYS = frozenset({
    "development_dataset_id", "validation_file_hash", "request_set_id",
    "request_file_hash", "result_set_id", "predictions_file_hash",
})
_METHOD_KEYS = frozenset({
    "kind", "provider", "model", "implementation_version", "artifact_id",
})
_METRIC_KEYS = frozenset({
    "sample_count", "answered_count", "abstained_count", "coverage_ppm",
    "event_pair_true_positive", "event_pair_false_positive",
    "event_pair_false_negative", "event_pair_precision_ppm",
    "event_pair_recall_ppm", "event_pair_f1_ppm", "theme_true_positive",
    "theme_false_positive", "theme_false_negative", "theme_precision_ppm",
    "theme_recall_ppm", "theme_f1_ppm", "theme_exact_set_count",
    "theme_exact_set_ppm", "theme_profile_labeled_count",
    "theme_profile_exact_count", "theme_profile_accuracy_ppm",
})
_BOUNDARIES = {
    "validation_only": True,
    "oos_used": False,
    "blind_inference_required": True,
    "validation_targets_used_by_evaluator_only": True,
    "event_cluster_labels_are_opaque": True,
    "semantic_relevance_metric_supported": False,
    "automatic_model_promotion": False,
}
_INTERPRETATION = {
    "event_metric": "pairwise_same_event_clustering",
    "theme_metric": "normalized_exact_label_set",
    "semantic_relevance_metric": "not_supported_relevant_only_source",
    "null_metric_reason": "no_positive_denominator",
}


def build_historical_news_method_evaluation(
    development: HistoricalNewsDevelopmentInputs,
    request_set: HistoricalNewsBlindValidation,
    results: HistoricalNewsMethodResults,
) -> dict[str, Any]:
    validate_historical_news_development_inputs(development)
    validate_historical_news_blind_validation(request_set)
    validate_historical_news_method_results(results)
    _validate_bindings(development, request_set, results)

    targets = {str(row["sample_id"]): row["human_target"] for row in development.validation}
    prediction_rows = {str(row["sample_id"]): row for row in results.predictions}
    request_ids = [str(row["sample_id"]) for row in request_set.requests]
    if set(targets) != set(request_ids) or set(prediction_rows) != set(request_ids):
        raise ValueError("historical news evaluation sample coverage does not match")

    actual_events: dict[str, str] = {}
    predicted_events: dict[str, str] = {}
    theme_true_positive = 0
    theme_predicted = 0
    theme_actual = 0
    theme_exact = 0
    profile_labeled = 0
    profile_exact = 0
    abstained = 0
    for sample_id in request_ids:
        target = targets[sample_id]
        if not isinstance(target, Mapping):
            raise ValueError("historical news evaluation target is invalid")
        actual_event = str(target.get("canonical_event_id", "")).strip()
        raw_actual_themes = target.get("theme_names")
        if not actual_event or not isinstance(raw_actual_themes, (list, tuple)):
            raise ValueError("historical news evaluation target labels are invalid")
        prediction_row = prediction_rows[sample_id]
        prediction = prediction_row["prediction"]
        is_abstained = prediction_row["abstained"] is True
        abstained += is_abstained
        actual_events[sample_id] = actual_event
        predicted_events[sample_id] = (
            f"__abstained__:{sample_id}"
            if is_abstained else str(prediction["event_cluster_id"])
        )
        actual_themes = {_normalize_label(value) for value in raw_actual_themes}
        predicted_themes = (
            set() if is_abstained
            else {_normalize_label(value) for value in prediction.get("theme_names", ())}
        )
        actual_themes.discard("")
        predicted_themes.discard("")
        theme_true_positive += len(actual_themes & predicted_themes)
        theme_actual += len(actual_themes)
        theme_predicted += len(predicted_themes)
        theme_exact += actual_themes == predicted_themes
        actual_profile = _normalize_label(target.get("theme_profile_name", ""))
        if actual_profile:
            profile_labeled += 1
            predicted_profile = (
                "" if is_abstained
                else _normalize_label(prediction.get("theme_profile_name", ""))
            )
            profile_exact += predicted_profile == actual_profile

    event_actual_positive = 0
    event_predicted_positive = 0
    event_true_positive = 0
    for left, right in combinations(request_ids, 2):
        actual_same = actual_events[left] == actual_events[right]
        predicted_same = predicted_events[left] == predicted_events[right]
        event_actual_positive += actual_same
        event_predicted_positive += predicted_same
        event_true_positive += actual_same and predicted_same

    event_false_positive = event_predicted_positive - event_true_positive
    event_false_negative = event_actual_positive - event_true_positive
    theme_false_positive = theme_predicted - theme_true_positive
    theme_false_negative = theme_actual - theme_true_positive
    sample_count = len(request_ids)
    metrics = {
        "sample_count": sample_count,
        "answered_count": sample_count - abstained,
        "abstained_count": abstained,
        "coverage_ppm": _ratio_ppm(sample_count - abstained, sample_count),
        "event_pair_true_positive": event_true_positive,
        "event_pair_false_positive": event_false_positive,
        "event_pair_false_negative": event_false_negative,
        "event_pair_precision_ppm": _ratio_ppm(event_true_positive, event_predicted_positive),
        "event_pair_recall_ppm": _ratio_ppm(event_true_positive, event_actual_positive),
        "event_pair_f1_ppm": _f1_ppm(
            event_true_positive, event_false_positive, event_false_negative,
        ),
        "theme_true_positive": theme_true_positive,
        "theme_false_positive": theme_false_positive,
        "theme_false_negative": theme_false_negative,
        "theme_precision_ppm": _ratio_ppm(theme_true_positive, theme_predicted),
        "theme_recall_ppm": _ratio_ppm(theme_true_positive, theme_actual),
        "theme_f1_ppm": _f1_ppm(
            theme_true_positive, theme_false_positive, theme_false_negative,
        ),
        "theme_exact_set_count": theme_exact,
        "theme_exact_set_ppm": _ratio_ppm(theme_exact, sample_count),
        "theme_profile_labeled_count": profile_labeled,
        "theme_profile_exact_count": profile_exact,
        "theme_profile_accuracy_ppm": _ratio_ppm(profile_exact, profile_labeled),
    }
    source = {
        "development_dataset_id": development.manifest["dataset_id"],
        "validation_file_hash": development.manifest["files"]["VALIDATION"]["sha256"],
        "request_set_id": request_set.manifest["request_set_id"],
        "request_file_hash": request_set.manifest["files"]["REQUESTS"]["sha256"],
        "result_set_id": results.manifest["result_set_id"],
        "predictions_file_hash": results.manifest["files"]["PREDICTIONS"]["sha256"],
    }
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source": source,
        "method": results.manifest["method"],
        "boundaries": _BOUNDARIES,
        "metrics": metrics,
        "interpretation": _INTERPRETATION,
    }
    report = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_method_evaluation",
        "evaluation_id": "historical-news-method-evaluation-" + _hash_document(binding),
        "source": source,
        "method": dict(results.manifest["method"]),
        "boundaries": dict(_BOUNDARIES),
        "metrics": metrics,
        "interpretation": dict(_INTERPRETATION),
    }
    _validate_report(report)
    return report


def write_historical_news_method_evaluation(report: Mapping[str, Any], output: Path) -> None:
    _validate_report(report)
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news method evaluation is immutable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
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


def load_historical_news_method_evaluation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news method evaluation cannot be read") from exc
    if not isinstance(value, dict):
        raise ValueError("historical news method evaluation must be an object")
    _validate_report(value)
    return value


def _validate_bindings(
    development: HistoricalNewsDevelopmentInputs,
    request_set: HistoricalNewsBlindValidation,
    results: HistoricalNewsMethodResults,
) -> None:
    development_validation_hash = development.manifest["files"]["VALIDATION"]["sha256"]
    request_source = request_set.manifest["source"]
    if (
        request_source.get("dataset_id") != development.manifest.get("dataset_id")
        or request_source.get("validation_file_hash") != development_validation_hash
    ):
        raise ValueError("historical news evaluation blind request source does not match")
    result_source = results.manifest["source"]
    if (
        result_source.get("request_set_id") != request_set.manifest.get("request_set_id")
        or result_source.get("request_file_hash")
        != request_set.manifest["files"]["REQUESTS"]["sha256"]
        or result_source.get("development_dataset_id") != development.manifest.get("dataset_id")
    ):
        raise ValueError("historical news evaluation result source does not match")


def _validate_report(report: Mapping[str, Any]) -> None:
    if (
        report.get("schema_version") != 1
        or report.get("contract_version") != CONTRACT_VERSION
        or report.get("dataset_kind") != "historical_news_method_evaluation"
    ):
        raise ValueError("unsupported historical news method evaluation contract")
    source = report.get("source")
    method = report.get("method")
    metrics = report.get("metrics")
    if not isinstance(source, Mapping) or not isinstance(method, Mapping) or not isinstance(metrics, Mapping):
        raise ValueError("historical news method evaluation fields are invalid")
    if set(source) != _SOURCE_KEYS or not all(str(source.get(key, "")) for key in _SOURCE_KEYS):
        raise ValueError("historical news method evaluation source is incomplete")
    if (
        set(method) != _METHOD_KEYS
        or str(method.get("kind", "")) not in METHOD_KINDS
        or not all(str(method.get(key, "")).strip() for key in _METHOD_KEYS)
    ):
        raise ValueError("historical news method evaluation method is invalid")
    if set(metrics) != _METRIC_KEYS:
        raise ValueError("historical news method evaluation metric fields are invalid")
    if any(
        value is not None and (type(value) is not int or value < 0)
        for value in metrics.values()
    ):
        raise ValueError("historical news method evaluation metrics are invalid")
    for key, value in metrics.items():
        if key.endswith("_ppm") and value is not None and value > 1_000_000:
            raise ValueError("historical news method evaluation ratio is invalid")
    boundaries = report.get("boundaries")
    interpretation = report.get("interpretation")
    if not isinstance(boundaries, Mapping) or dict(boundaries) != _BOUNDARIES:
        raise ValueError("historical news method evaluation boundary is invalid")
    if not isinstance(interpretation, Mapping) or dict(interpretation) != _INTERPRETATION:
        raise ValueError("historical news method evaluation interpretation is invalid")
    binding = {
        "contract_version": CONTRACT_VERSION,
        "source": dict(source),
        "method": dict(method),
        "boundaries": dict(boundaries),
        "metrics": dict(metrics),
        "interpretation": dict(interpretation),
    }
    expected = "historical-news-method-evaluation-" + _hash_document(binding)
    if report.get("evaluation_id") != expected:
        raise ValueError("historical news method evaluation identity does not match content")


def _normalize_label(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _ratio_ppm(numerator: int, denominator: int) -> int | None:
    if denominator == 0:
        return None
    return numerator * 1_000_000 // denominator


def _f1_ppm(true_positive: int, false_positive: int, false_negative: int) -> int | None:
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator == 0:
        return None
    return 2 * true_positive * 1_000_000 // denominator


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
