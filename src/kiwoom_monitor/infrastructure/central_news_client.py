from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings, StockNewsItem


class CentralNewsClient(CentralContentClient):
    def __init__(self, server_url: str, access_token: str, *, opener: Callable[..., Any] | None = None) -> None:
        kwargs = {"opener": opener} if opener is not None else {}
        super().__init__(server_url, access_token, **kwargs)

    def search(
        self, code: str, name: str, since: datetime | None = None,
        ai: NewsAISettings | None = None,
    ) -> tuple[StockNewsItem, ...]:
        payload: dict[str, Any] = {"stock_code": code, "stock_name": name}
        if since is not None:
            payload["since"] = since.astimezone(UTC).isoformat()
        if ai is not None:
            payload.update({
                "ai_auto_analyze": ai.auto_analyze, "ai_auto_recent_limit": ai.auto_recent_limit,
                "ai_provider": ai.provider, "ai_model": ai.model,
            })
        result = self._request("POST", "/api/v1/news/search", payload)
        values = result.get("items", [])
        if not isinstance(values, list):
            raise RuntimeError("중앙 뉴스 응답 형식이 올바르지 않습니다.")
        return tuple(_deserialize(value) for value in values if isinstance(value, dict))


def _deserialize(value: dict[str, Any]) -> StockNewsItem:
    published_at = None
    if value.get("published_at"):
        published_at = datetime.fromisoformat(str(value["published_at"]))
    return StockNewsItem(
        str(value.get("title", "")), str(value.get("description", "")),
        str(value.get("link", "")), str(value.get("original_link", "")), published_at,
        NewsAssessment(
            bool(value.get("relevant", 0)), str(value.get("category", "")),
            str(value.get("outlook", "")), str(value.get("reason", "")),
            int(value.get("relevance_score", 0)), int(value.get("outlook_score", 0)),
        ),
    )
