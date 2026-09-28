from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote, urlencode

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings, StockNewsItem


class CentralNewsClient(CentralContentClient):
    def __init__(self, server_url: str, access_token: str, *, opener: Callable[..., Any] | None = None,
                 timeout_seconds: float = 30.0) -> None:
        kwargs = {"opener": opener} if opener is not None else {}
        super().__init__(server_url, access_token, timeout_seconds=timeout_seconds, **kwargs)

    def market_feed(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if source not in {"common", "flash", "world"}:
            raise ValueError("지원하지 않는 시장 뉴스 수집원입니다.")
        result = self._request("GET", f"/api/v1/news/market-feed?source={source}&limit={max(1, min(limit, 1000))}")
        items = result.get("items")
        if not isinstance(items, list):
            raise RuntimeError("NAS 시장 뉴스 응답 형식이 올바르지 않습니다.")
        return [item for item in items if isinstance(item, dict)]

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

    def stored_page(self, code: str, name: str, *, offset: int = 0,
                    ai: NewsAISettings | None = None) -> tuple[tuple[StockNewsItem, ...], int | None]:
        payload: dict[str, Any] = {"stock_code": code, "stock_name": name, "offset": offset}
        if ai is not None:
            payload.update({
                "ai_auto_analyze": ai.auto_analyze, "ai_auto_recent_limit": ai.auto_recent_limit,
                "ai_provider": ai.provider, "ai_model": ai.model,
            })
        result = self._request("POST", "/api/v1/news/stored-page", payload)
        values = result.get("items", [])
        if not isinstance(values, list):
            raise RuntimeError("중앙 뉴스 페이지 응답 형식이 올바르지 않습니다.")
        next_offset = result.get("next_offset")
        if next_offset is not None and (not isinstance(next_offset, int) or next_offset <= offset):
            raise RuntimeError("중앙 뉴스 다음 페이지 위치가 올바르지 않습니다.")
        return tuple(_deserialize(value) for value in values if isinstance(value, dict)), next_offset

    def historical_archive_page(self, stock_code: str, *, limit: int = 100,
                                cursor: str | None = None) -> dict[str, Any]:
        """Read a separate, immutable historical dataset; never merge offset pages."""
        if not 1 <= limit <= 200:
            raise ValueError("과거 뉴스 페이지 크기는 1~200이어야 합니다.")
        query: dict[str, str | int] = {"stock_code": stock_code, "limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        result = self._request(
            "GET", f"/api/v1/news/historical-archive/search?{urlencode(query)}",
        )
        if (not isinstance(result.get("dataset_id"), str) or not result["dataset_id"]
                or not isinstance(result.get("items"), list)
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("article_revision_id"), str)
                       or not isinstance(item.get("display"), dict)
                       for item in result["items"])
                or result.get("next_cursor") is not None
                and not isinstance(result["next_cursor"], str)):
            raise RuntimeError("과거 뉴스 archive 페이지 응답 형식이 올바르지 않습니다.")
        return result

    def historical_archive_article(self, dataset_id: str, article_revision_id: str, *,
                                   body_revision_id: str | None = None) -> dict[str, Any]:
        if not dataset_id or not article_revision_id:
            raise ValueError("과거 뉴스 dataset과 기사 ID가 필요합니다.")
        query: dict[str, str] = {"dataset_id": dataset_id}
        if body_revision_id is not None:
            query["body_revision_id"] = body_revision_id
        path = ("/api/v1/news/historical-archive/articles/"
                f"{quote(article_revision_id, safe='')}?{urlencode(query)}")
        result = self._request("GET", path)
        if result.get("dataset_id") != dataset_id or result.get("article_revision_id") != article_revision_id:
            raise RuntimeError("과거 뉴스 archive 상세 응답의 dataset 또는 기사 ID가 다릅니다.")
        if not isinstance(result.get("document"), dict):
            raise RuntimeError("과거 뉴스 archive 기사 문서 형식이 올바르지 않습니다.")
        if body_revision_id is not None:
            body = result.get("body")
            if not isinstance(body, dict) or body.get("body_revision_id") != body_revision_id:
                raise RuntimeError("과거 뉴스 archive 본문 ID가 다릅니다.")
        return result


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
