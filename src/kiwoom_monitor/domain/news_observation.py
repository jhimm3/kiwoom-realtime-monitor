"""중앙 뉴스 원장의 식별자와 내용 hash 계약."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


ARTICLE_BODY_EXTRACTOR_VERSION = "article-text-v7"
NEWS_ANALYSIS_SCHEMA_VERSION = "news-analysis-v2"
SUPPLY_CONTRACT_RULE_VERSION = "supply-contract-rule-v2"


def stable_document_hash(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def news_projection_article_hash(document: Mapping[str, Any], collection_scope: str) -> str:
    """Preserve legacy full hashes; only the explicit new local producer adds metadata."""
    if collection_scope == "local_projection_sync" and document.get("input_source_contract") == "v1":
        return stable_document_hash({name: value for name, value in document.items()
                                     if name not in {"input_source_contract", "input_source_json"}})
    return stable_document_hash(document)


def news_job_key(
    stage: str, target_id: str, input_revision: str, input_hash: str, processing_version: str,
) -> str:
    raw = "\0".join((stage, target_id, input_revision, input_hash, processing_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AIJobResultError(RuntimeError):
    """Accepted request owns its original error and native settlement result."""
    def __init__(self, error: Exception, receipt: dict[str, str] | None, *,
                 settlement_error: Exception | None = None) -> None:
        super().__init__(str(error))
        self.original_error = error
        self.receipt = receipt
        self.settlement_error = settlement_error


class NewsBodyDeliveryError(RuntimeError):
    """A transport/authority outcome is not a BODY calculation failure."""


def news_article_content_hash(document: dict[str, Any]) -> str:
    # 언론사명·도메인은 URL에서 다시 계산할 수 있는 검색 편의 필드다. 이 값의
    # 추가나 매핑 보정만으로 원문 기사 리비전을 새로 만들지 않는다.
    content = {
        key: value for key, value in document.items()
        if key not in {"publisher_domain", "publisher_name"}
    }
    return stable_document_hash(content)


class NewsBodyConflict(ValueError):
    """The server-owned BODY authority or its immutable input does not match."""
