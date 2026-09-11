from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context

from .client import KiwoomApiError


class CentralServerUnavailable(KiwoomApiError):
    """중앙 서버 자체에 연결할 수 없어 선택적 로컬 전환이 가능한 오류."""


class RemoteKiwoomRestClient:
    """개인 중앙 서버의 조회 큐를 사용하는 동기식 호환 클라이언트."""

    def __init__(
        self, server_url: str, access_token: str, *, opener: Callable[..., Any] = urlopen,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._access_token = access_token
        self._opener = opener
        self._timeout = timeout_seconds

    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload, _, _ = self.request_with_continuation(api_id, path, body)
        return payload

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]:
        request = Request(
            f"{self._server_url}/api/v1/kiwoom/query",
            data=json.dumps({
                "api_id": api_id, "path": path, "body": body,
                "cont_yn": cont_yn, "next_key": next_key,
            }, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json;charset=UTF-8",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout, context=system_ssl_context()) as response:
                document = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("detail", "")
            except (ValueError, AttributeError):
                detail = ""
            raise KiwoomApiError(f"개인 중앙 서버 오류: HTTP {error.code} {detail}".strip()) from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CentralServerUnavailable("개인 중앙 서버에 연결할 수 없습니다.") from error
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise KiwoomApiError("개인 중앙 서버 응답 형식이 올바르지 않습니다.")
        return document["payload"], bool(document.get("has_next")), str(document.get("next_key", ""))

    def server_now(self) -> datetime:
        # 실시간 수집을 중앙화하기 전까지 서비스 프로토콜과의 호환을 위한 값이다.
        return datetime.utcnow() + timedelta(hours=9)

    def get_access_token(self) -> str:
        raise KiwoomApiError("키움 접근 토큰은 중앙 서버 외부로 제공하지 않습니다.")
