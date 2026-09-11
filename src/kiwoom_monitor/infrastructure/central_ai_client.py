from __future__ import annotations

import json
from typing import Any, Callable

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.news_ai import AICompanyImpact, AINewsAnalysis, AIRequestUsage


class CentralAIClient(CentralContentClient):
    def __init__(self, server_url: str, access_token: str, *, opener: Callable[..., Any] | None = None) -> None:
        kwargs = {"opener": opener} if opener is not None else {}
        super().__init__(server_url, access_token, **kwargs)

    def analyze(self, stock_code: str, stock_name: str, provider: str, model: str,
                events: list[dict[str, Any]], article_count: int) -> tuple[
                    tuple[AINewsAnalysis, ...], tuple[str, ...], str, str, AIRequestUsage, bool,
                ]:
        result = self._request("POST", "/api/v1/news/analyze", {
            "stock_code": stock_code, "stock_name": stock_name, "provider": provider,
            "model": model, "events": events, "article_count": article_count,
        })
        raw_results = result.get("results", [])
        if not isinstance(raw_results, list):
            raise RuntimeError("중앙 AI 응답 형식이 올바르지 않습니다.")
        usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
        return (
            tuple(_analysis(value) for value in raw_results if isinstance(value, dict)),
            tuple(map(str, result.get("body_hashes", ()))),
            str(result.get("provider", provider)), str(result.get("model", model)),
            AIRequestUsage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                           int(usage.get("total_tokens", 0))),
            bool(result.get("cache_hit", False)),
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
    return AINewsAnalysis(
        str(value.get("summary", "")), str(value.get("outlook", "판단 자료 부족")),
        int(value.get("confidence", 0)), str(value.get("reason", "")),
        evidence("positive_evidence"), evidence("negative_evidence"),
        str(value.get("category", "기타 증권뉴스")), impacts,
    )
