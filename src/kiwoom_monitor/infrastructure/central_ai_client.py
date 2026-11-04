from __future__ import annotations

import json
from typing import Any, Callable

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.domain.news_ai_contract import decode_news_ai_provenance, NewsAIResultEnvelope
from kiwoom_monitor.domain.news_observation import stable_document_hash
from kiwoom_monitor.infrastructure.news_ai import (
    AICompanyImpact, AINewsAnalysis, AIRequestUsage, AIThemeCandidate,
)


class CentralAIClient(CentralContentClient):
    def __init__(self, server_url: str, access_token: str, *, opener: Callable[..., Any] | None = None) -> None:
        kwargs = {"opener": opener} if opener is not None else {}
        super().__init__(server_url, access_token, **kwargs)

    def analyze(self, stock_code: str, stock_name: str, provider: str, model: str,
                events: list[dict[str, Any]], article_count: int) -> NewsAIResultEnvelope:
        result = self._request("POST", "/api/v1/news/analyze", {
            "stock_code": stock_code, "stock_name": stock_name, "provider": provider,
            "model": model, "events": events, "article_count": article_count,
        })
        raw_results = result.get("results", [])
        if not isinstance(raw_results, list):
            raise RuntimeError("중앙 AI 응답 형식이 올바르지 않습니다.")
        usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
        values = [value for value in raw_results if isinstance(value, dict)]
        reported = result.get("analysis_revision_ids", ())
        unassigned = [str(value) for value in reported if value] if isinstance(reported, (list, tuple)) else []
        metadata = result.get("analysis_provenance")
        provenance = []
        for index, value in enumerate(values):
            reference = decode_news_ai_provenance(
                metadata[index] if isinstance(metadata, list) and len(metadata) == len(raw_results)
                and len(values) == len(raw_results) else None
            )
            if (reference.get("status") != "REPORTED" and (reference.get("status") != "ACCEPTED"
                    or reference.get("output_hash") != stable_document_hash(value)
                    or reference.get("target_id") != stock_code
                    or reference.get("input_hash") != value.get("body_hash")
                    or reference.get("provider") != value.get("provider")
                    or reference.get("model") != value.get("model")
                    or "available_at" not in reference
                    or not all(reference.get(name) for name in
                               ("analysis_revision_id", "article_revision_id", "body_revision_id")))):
                reference["status"] = "UNKNOWN"
            provenance.append(reference)
        assigned = {str(value["analysis_revision_id"]) for value in provenance
                    if value.get("status") == "ACCEPTED"}
        unassigned = [value for value in unassigned if value not in assigned]
        if unassigned:
            for reference in provenance:
                if reference.get("status") != "ACCEPTED":
                    reference["unassigned_analysis_revision_ids"] = list(unassigned)
        return NewsAIResultEnvelope(
            tuple(_analysis(value) for value in values),
            tuple(map(str, result.get("body_hashes", ()))),
            str(result.get("provider", provider)), str(result.get("model", model)),
            AIRequestUsage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                           int(usage.get("total_tokens", 0))),
            article_count, bool(result.get("cache_hit", False)), tuple(provenance), stock_name=stock_name,
        )


def _analysis(value: dict[str, Any]) -> AINewsAnalysis:
    def evidence(name: str) -> tuple[str, ...]:
        raw = value.get(name, ())
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except ValueError: raw = ()
        return tuple(map(str, raw)) if isinstance(raw, (list, tuple)) else ()

    impacts = tuple(AICompanyImpact(
        str(item.get("company", "")), str(item.get("outlook", "판단 자료 부족")),
        int(item.get("confidence", 0)), str(item.get("reason", "")),
    ) for item in value.get("company_impacts", ()) if isinstance(item, dict))
    candidates = tuple(AIThemeCandidate(
        str(item.get("name", "")), int(item.get("confidence", 0)), str(item.get("evidence", "")),
    ) for item in value.get("theme_candidates", ()) if isinstance(item, dict) and item.get("name"))
    return AINewsAnalysis(
        str(value.get("summary", "")), str(value.get("outlook", "판단 자료 부족")),
        int(value.get("confidence", 0)), str(value.get("reason", "")),
        evidence("positive_evidence"), evidence("negative_evidence"),
        str(value.get("category", "기타 증권뉴스")), impacts, candidates,
    )
