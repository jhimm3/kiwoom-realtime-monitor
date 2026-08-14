"""토큰을 메모리에만 보관하는 키움 REST API 클라이언트."""

from __future__ import annotations

import json
import time
from threading import RLock
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import KiwoomSettings

JsonObject = dict[str, Any]
UrlOpen = Callable[..., Any]


class KiwoomApiError(RuntimeError):
    """키움 API 통신 또는 API 응답 오류."""


class KiwoomRestClient:
    """인증 토큰을 자동 발급·재사용하는 동기식 REST 클라이언트.

    토큰은 실행 중인 메모리에만 보관한다. 로그·SQLite·화면에는 기록하지 않는다.
    """

    def __init__(
        self,
        settings: KiwoomSettings,
        *,
        opener: UrlOpen = urlopen,
        clock: Callable[[], datetime] | None = None,
        request_interval_seconds: float = 2.0,
    ) -> None:
        self._settings = settings
        self._opener = opener
        self._clock = clock or (lambda: datetime.now(UTC))
        self._request_interval_seconds = request_interval_seconds
        self._last_request_at: float | None = None
        self._request_lock = RLock()
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def request(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        with self._request_lock:
            return self._request_unlocked(api_id, path, body)

    def _request_unlocked(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        """API-ID를 포함해 인증된 POST 요청을 보낸다."""
        if not api_id:
            raise ValueError("api_id is required")
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        response = self._post(
            path,
            body,
            {
                "authorization": f"Bearer {self._get_token()}",
                "api-id": api_id,
            },
        )
        return_code = response.get("return_code")
        if return_code not in (None, 0, "0"):
            message = str(response.get("return_msg", "알 수 없는 오류")).replace("\n", " ")
            raise KiwoomApiError(f"키움 API 오류 ({api_id}): {return_code} {message}")
        return response

    def get_access_token(self) -> str:
        """WebSocket 로그인에 사용할 실행 중 메모리 토큰을 제공한다."""
        with self._request_lock:
            return self._get_token()

    def _get_token(self) -> str:
        if self._token and self._expires_at and self._clock() < self._expires_at:
            return self._token

        response = self._post(
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appkey": self._settings.app_key,
                "secretkey": self._settings.secret_key,
            },
            {},
        )
        token = response.get("token")
        if response.get("return_code") not in (None, 0, "0") or not isinstance(token, str) or not token:
            raise KiwoomApiError("키움 API 토큰 발급에 실패했습니다.")
        self._token = token
        self._expires_at = self._parse_expiry(response.get("expires_dt"))
        return token

    def _post(self, path: str, body: JsonObject, extra_headers: dict[str, str]) -> JsonObject:
        self._wait_for_request_slot()
        headers = {"Content-Type": "application/json;charset=UTF-8", **extra_headers}
        request = Request(
            f"{self._settings.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        for attempt in range(4):
            try:
                with self._opener(request, timeout=15) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code == 429 and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise KiwoomApiError(f"키움 API 서버 오류: HTTP {error.code}") from error
            except URLError as error:
                raise KiwoomApiError("키움 API 서버에 연결할 수 없습니다.") from error
            except TimeoutError as error:
                raise KiwoomApiError("키움 API 응답 시간이 초과되었습니다.") from error
        if not isinstance(payload, dict):
            raise KiwoomApiError("키움 API 응답 형식이 올바르지 않습니다.")
        return payload

    def _wait_for_request_slot(self) -> None:
        """짧은 간격을 두어 서버의 순간 호출 제한을 피한다."""
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._request_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _parse_expiry(self, value: object) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC) - timedelta(minutes=1)
            except ValueError:
                pass
        return self._clock() + timedelta(hours=23, minutes=59)
