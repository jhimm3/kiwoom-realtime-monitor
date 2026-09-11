from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


class CentralContentClient:
    """뉴스·AI·테마 문서를 개인 중앙 서버와 교환한다."""

    def __init__(
        self, server_url: str, access_token: str, *, opener: Callable[..., Any] = urlopen,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._token = access_token
        self._opener = opener
        self._timeout = timeout_seconds

    def load(
        self, collection: str, owner: str = "", limit: int = 1000, *, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        query = urlencode({
            "owner": owner, "limit": limit, "offset": max(0, int(offset)),
            "updated_after": max(0.0, float(updated_after)),
        })
        document = self._request("GET", f"/api/v1/content/{collection}?{query}")
        values = document.get("documents", [])
        if not isinstance(values, list):
            raise RuntimeError("중앙 자료 응답 형식이 올바르지 않습니다.")
        return [value for value in values if isinstance(value, dict)]

    def load_all(
        self, collection: str, owner: str = "", *, page_size: int = 1000,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(int(page_size), 10_000))
        result: list[dict[str, Any]] = []
        while True:
            page = self.load(
                collection, owner, page_size, offset=len(result), updated_after=updated_after,
            )
            result.extend(page)
            if len(page) < page_size:
                return result

    def upsert(self, collection: str, documents: list[dict[str, Any]]) -> int:
        if not documents:
            return 0
        result = self._request("POST", f"/api/v1/content/{collection}", {"documents": documents})
        return int(result.get("saved", 0))

    def replace(self, collection: str, documents: list[dict[str, Any]]) -> int:
        result = self._request("PUT", f"/api/v1/content/{collection}", {"documents": documents})
        return int(result.get("saved", 0))

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(
            f"{self._server_url}{path}", method=method,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json;charset=UTF-8"},
        )
        try:
            with self._opener(request, timeout=self._timeout, context=system_ssl_context()) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = ""
            try:
                document = json.loads(error.read().decode("utf-8", errors="replace"))
                if isinstance(document, dict):
                    detail = str(document.get("detail", "")).strip()
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                pass
            suffix = f" · {detail}" if detail else ""
            raise RuntimeError(f"중앙 자료 서버 오류: HTTP {error.code}{suffix}") from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise RuntimeError("중앙 자료 서버에 연결할 수 없습니다.") from error
        if not isinstance(result, dict):
            raise RuntimeError("중앙 자료 서버 응답 형식이 올바르지 않습니다.")
        return result
