"""중앙 뉴스 원장의 식별자와 내용 hash 계약."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


ARTICLE_BODY_EXTRACTOR_VERSION = "article-text-v7"
NEWS_ANALYSIS_SCHEMA_VERSION = "news-analysis-v1"


def stable_document_hash(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def news_job_key(
    stage: str, target_id: str, input_revision: str, input_hash: str, processing_version: str,
) -> str:
    raw = "\0".join((stage, target_id, input_revision, input_hash, processing_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
