"""Build an immutable human-review queue for retrospective news evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.historical_learning_cases import (
    CONTRACT_VERSION as LEARNING_CASE_CONTRACT_VERSION,
    HistoricalLearningCaseDataset,
)
from kiwoom_monitor.application.news_analysis import assess_stock_news


CONTRACT_VERSION = "historical_news_review_queue/v1"


@dataclass(frozen=True)
class HistoricalNewsReviewQueue:
    manifest: dict[str, Any]
    items: tuple[dict[str, Any], ...]


def build_historical_news_review_queue(
    source: HistoricalLearningCaseDataset,
    *,
    created_at: datetime | None = None,
) -> HistoricalNewsReviewQueue:
    """Collapse article evidence across cases and add non-authoritative rule hints."""
    if source.manifest.get("contract_version") != LEARNING_CASE_CONTRACT_VERSION:
        raise ValueError("review queue requires historical_learning_cases/v1")
    source_dataset_id = str(source.manifest.get("dataset_id", ""))
    source_file_hash = str(source.manifest.get("cases_file_hash", ""))
    if not source_dataset_id or not source_file_hash:
        raise ValueError("learning case source identity is incomplete")
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    articles: dict[tuple[str, str, str], dict[str, Any]] = {}
    links: dict[tuple[str, str, str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    raw_link_count = 0
    for case in source.cases:
        case_id = str(case.get("case_id", ""))
        selection_date = str(case.get("selection_date", ""))
        stock = case.get("stock")
        model_input = case.get("model_input")
        if not case_id or not selection_date or not isinstance(stock, Mapping):
            raise ValueError("learning case identity is incomplete")
        if not isinstance(model_input, Mapping):
            raise ValueError("learning case model_input is missing")
        code = str(stock.get("code", ""))
        name = str(stock.get("name", ""))
        if not code or not name:
            raise ValueError("learning case stock identity is incomplete")
        evidence_rows = model_input.get("news_evidence", [])
        if not isinstance(evidence_rows, list):
            raise ValueError("learning case news_evidence must be a list")
        for evidence in evidence_rows:
            if not isinstance(evidence, Mapping):
                raise ValueError("learning case news evidence is invalid")
            key = _article_key(evidence)
            raw_link_count += 1
            article = _article_document(evidence)
            existing = articles.get(key)
            if existing is not None and existing != article:
                raise ValueError("article identity has conflicting evidence")
            articles[key] = article
            assessment = assess_stock_news(
                name,
                str(evidence.get("title", "")),
                str(evidence.get("search_summary", "")),
            )
            link = {
                "case_id": case_id,
                "selection_date": selection_date,
                "stock": {"code": code, "name": name},
                "query_texts": sorted({str(value).strip() for value in evidence.get("query_texts", []) if str(value).strip()}),
                "source_relation": {
                    "collector_available_at": str(evidence.get("collector_available_at", "")),
                    "source_revision_id": str(evidence.get("source_revision_id", "")),
                },
                "rule_hint": asdict(assessment),
            }
            link_key = (case_id, code)
            prior = links[key].get(link_key)
            if prior is not None and prior != link:
                raise ValueError("article/case link has conflicting evidence")
            links[key][link_key] = link

    provisional: list[dict[str, Any]] = []
    rule_relevant_count = 0
    for key, article in articles.items():
        case_links = sorted(
            links[key].values(),
            key=lambda value: (
                str(value["selection_date"]),
                str(value["stock"]["code"]),
                str(value["case_id"]),
            ),
        )
        relevant = any(bool(link["rule_hint"]["relevant"]) for link in case_links)
        maximum_score = max((int(link["rule_hint"]["relevance_score"]) for link in case_links), default=0)
        rule_relevant_count += int(relevant)
        provisional.append({
            "review_item_id": "historical-news-review-" + _hash_document({
                "provider": key[0], "office_id": key[1], "article_id": key[2],
            }),
            "source_learning_dataset_id": source_dataset_id,
            "article": article,
            "case_links": case_links,
            "priority": {
                "has_rule_relevant_hint": relevant,
                "maximum_rule_relevance_score": maximum_score,
            },
            "review": {
                "status": "pending",
                "human_decision": None,
                "canonical_event_id": "",
                "theme_names": [],
                "notes": "",
            },
            "eligibility": {
                "rule_hint_is_ground_truth": False,
                "llm_used": False,
                "human_review_complete": False,
                "model_weight_training_ready": False,
            },
        })

    provisional.sort(key=lambda value: (
        not bool(value["priority"]["has_rule_relevant_hint"]),
        -int(value["priority"]["maximum_rule_relevance_score"]),
        str(value["article"]["published_at"]),
        str(value["review_item_id"]),
    ))
    items = [{"ordinal": ordinal, **value} for ordinal, value in enumerate(provisional, start=1)]
    encoded = _encode_items(items)
    items_hash = hashlib.sha256(encoded).hexdigest()
    manifest = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "dataset_kind": "historical_news_review_queue",
        "dataset_id": "historical-news-review-queue-" + _hash_document({
            "contract_version": CONTRACT_VERSION,
            "source_learning_dataset_id": source_dataset_id,
            "source_cases_file_hash": source_file_hash,
            "items_file_hash": items_hash,
        }),
        "created_at": created.astimezone(UTC).isoformat(),
        "source": {
            "contract_version": LEARNING_CASE_CONTRACT_VERSION,
            "dataset_id": source_dataset_id,
            "cases_file_hash": source_file_hash,
        },
        "boundaries": {
            "article_identity_deduplicated": True,
            "rule_hint_is_review_priority_only": True,
            "rule_hint_is_ground_truth": False,
            "llm_used": False,
            "human_review_complete": False,
            "model_weight_training_ready": False,
            "strict_backtest_input": False,
        },
        "counts": {
            "unique_articles": len(items),
            "case_article_links": sum(len(item["case_links"]) for item in items),
            "article_relations_collapsed_to_unique_articles": raw_link_count - len(items),
            "duplicate_case_article_links_collapsed": raw_link_count - sum(len(item["case_links"]) for item in items),
            "items_with_rule_relevant_hint": rule_relevant_count,
            "pending_review": len(items),
        },
        "items_file": "review_items.jsonl",
        "item_count": len(items),
        "items_file_hash": items_hash,
    }
    return HistoricalNewsReviewQueue(manifest, tuple(items))


def write_historical_news_review_queue(
    dataset: HistoricalNewsReviewQueue, output: Path,
) -> dict[str, Any]:
    destination = Path(output)
    if destination.exists():
        raise ValueError("historical news review queue export is immutable and cannot be overwritten")
    encoded = _encode_items(dataset.items)
    if hashlib.sha256(encoded).hexdigest() != dataset.manifest.get("items_file_hash"):
        raise ValueError("historical news review queue changed after manifest creation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "review_items.jsonl").write_bytes(encoded)
        (staging / "manifest.json").write_text(
            json.dumps(dataset.manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(dataset.manifest)


def load_historical_news_review_queue(path: Path) -> HistoricalNewsReviewQueue:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical news review queue manifest cannot be read") from exc
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported historical news review queue contract")
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, dict):
        raise ValueError("historical news review queue boundaries are missing")
    required_false = (
        "rule_hint_is_ground_truth", "llm_used", "human_review_complete",
        "model_weight_training_ready", "strict_backtest_input",
    )
    if any(boundaries.get(key) is not False for key in required_false):
        raise ValueError("historical news review queue safety boundary is missing")
    if boundaries.get("rule_hint_is_review_priority_only") is not True:
        raise ValueError("historical news review queue priority boundary is missing")
    file_name = str(manifest.get("items_file", ""))
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("historical news review items_file must be local")
    try:
        encoded = (root / file_name).read_bytes()
    except OSError as exc:
        raise ValueError("historical news review items cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("items_file_hash"):
        raise ValueError("historical news review queue file hash does not match manifest")
    items: list[dict[str, Any]] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"historical news review item {ordinal} is invalid") from exc
        if not isinstance(item, dict) or item.get("ordinal") != ordinal or not item.get("review_item_id"):
            raise ValueError("historical news review item ordinal/identity is invalid")
        if item.get("source_learning_dataset_id") != manifest.get("source", {}).get("dataset_id"):
            raise ValueError("historical news review source identity does not match manifest")
        eligibility = item.get("eligibility")
        if not isinstance(eligibility, dict) or eligibility.get("rule_hint_is_ground_truth") is not False:
            raise ValueError("historical news review item safety boundary is missing")
        items.append(item)
    if len(items) != int(manifest.get("item_count", -1)):
        raise ValueError("historical news review item count does not match manifest")
    return HistoricalNewsReviewQueue(dict(manifest), tuple(items))


def _article_key(evidence: Mapping[str, Any]) -> tuple[str, str, str]:
    key = (
        str(evidence.get("provider", "")),
        str(evidence.get("office_id", "")),
        str(evidence.get("article_id", "")),
    )
    if not all(key):
        raise ValueError("learning case article identity is incomplete")
    return key


def _article_document(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: evidence.get(key, "")
        for key in (
            "provider", "office_id", "article_id", "office_name", "title",
            "search_summary", "published_at", "published_precision",
            "published_at_source", "article_url", "original_url",
        )
    }


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_items(items: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for item in items
    )
