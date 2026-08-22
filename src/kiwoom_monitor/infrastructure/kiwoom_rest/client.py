"""토큰을 메모리에만 보관하는 키움 REST API 클라이언트."""

from __future__ import annotations

import json
import time
from threading import RLock
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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
        request_interval_seconds: float | None = None,
        time_adjustment_provider: Callable[[], object] | None = None,
    ) -> None:
        self._settings = settings
        self._opener = opener
        self._clock = clock or (lambda: datetime.now(UTC))
        self._time_adjustment_provider = time_adjustment_provider
        # 국내주식 실전 조회 TR은 초당 5회까지 가능하다. 순위 요청에
        # 자리를 남기기 위해 화면 쪽에서 우선순위를 제어하고, 클라이언트는
        # 모든 REST 요청을 합쳐 이 간격을 넘지 않게 한다. 모의투자는 1초 1회다.
        self._base_request_interval_seconds = (
            request_interval_seconds
            if request_interval_seconds is not None
            else (0.2 if settings.environment == "real" else 1.0)
        )
        self._request_interval_seconds = self._base_request_interval_seconds
        self._rate_limit_success_count = 0
        self._last_request_at: float | None = None
        self._request_lock = RLock()
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._server_time: datetime | None = None
        self._server_time_at: float | None = None

    def server_now(self) -> datetime:
        if self._server_time is None or self._server_time_at is None:
            return (datetime.now(UTC) + timedelta(hours=9)).replace(tzinfo=None)
        return (
            self._server_time
            + timedelta(seconds=time.monotonic() - self._server_time_at)
            - timedelta(seconds=self._time_adjustment_seconds())
        ).replace(tzinfo=None)

    def _time_adjustment_seconds(self) -> float:
        try:
            value = float(self._time_adjustment_provider()) if self._time_adjustment_provider is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
        return max(-2.0, min(2.0, value))

    def request(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        with self._request_lock:
            return self._request_unlocked(api_id, path, body)

    def _request_unlocked(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        """API-ID를 포함해 인증된 POST 요청을 보낸다."""
        if not api_id:
            raise ValueError("api_id is required")
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        response = self._authenticated_post(api_id, path, body)
        return_code = response.get("return_code")
        # 서버가 만료·무효 토큰을 돌려주는 경우에는 메모리 토큰을 버리고
        # 한 번만 새 토큰으로 재요청한다. 순위 갱신이 토큰 오류로 멈추지 않는다.
        if self._is_invalid_token(return_code, response.get("return_msg")):
            self._token = None
            self._expires_at = None
            response = self._authenticated_post(api_id, path, body)
            return_code = response.get("return_code")
        if return_code not in (None, 0, "0"):
            message = str(response.get("return_msg", "알 수 없는 오류")).replace("\n", " ")
            raise KiwoomApiError(f"키움 API 오류 ({api_id}): {return_code} {message}")
        return response

    def _authenticated_post(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        return self._post(
            path,
            body,
            {
                "authorization": f"Bearer {self._get_token()}",
                "api-id": api_id,
            },
        )

    @staticmethod
    def _is_invalid_token(return_code: object, message: object) -> bool:
        text = str(message).casefold()
        return str(return_code).strip() in {"3", "8005"} or "token" in text or "인증에 실패" in text

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
                    headers = getattr(response, "headers", {})
                    self._update_server_time(headers.get("Date") if hasattr(headers, "get") else None)
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code == 429 and attempt < 3:
                    self._slow_down_after_rate_limit()
                    time.sleep(5 * (attempt + 1))
                    continue
                raise KiwoomApiError(f"키움 API 서버 오류: HTTP {error.code}") from error
            except (URLError, TimeoutError, ConnectionError, OSError) as error:
                # WinError 10054처럼 서버가 keep-alive 연결을 먼저 끊는
                # 경우는 일시적인 통신 오류다. 새 연결로 짧게 재시도한다.
                if attempt < 3:
                    time.sleep(attempt + 1)
                    continue
                raise KiwoomApiError("키움 API 서버 연결이 반복해서 끊겼습니다. 잠시 후 다시 시도하세요.") from error
        if not isinstance(payload, dict):
            raise KiwoomApiError("키움 API 응답 형식이 올바르지 않습니다.")
        self._recover_request_rate()
        return payload

    def _update_server_time(self, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        try:
            self._server_time = parsedate_to_datetime(value).astimezone(UTC) + timedelta(hours=9)
            self._server_time_at = time.monotonic()
        except (TypeError, ValueError, IndexError):
            return

    def _wait_for_request_slot(self) -> None:
        """짧은 간격을 두어 서버의 순간 호출 제한을 피한다."""
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._request_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _slow_down_after_rate_limit(self) -> None:
        """429가 오면 보완 요청 속도를 단계적으로 낮춰 연결을 회복한다."""
        self._request_interval_seconds = min(1.0, max(self._request_interval_seconds * 2, 0.4))
        self._rate_limit_success_count = 0

    def _recover_request_rate(self) -> None:
        """정상 응답이 이어지면 실전 최대 속도로 서서히 복귀한다."""
        if self._request_interval_seconds <= self._base_request_interval_seconds:
            return
        self._rate_limit_success_count += 1
        if self._rate_limit_success_count >= 20:
            self._request_interval_seconds = max(self._base_request_interval_seconds, self._request_interval_seconds / 2)
            self._rate_limit_success_count = 0

    def _parse_expiry(self, value: object) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC) - timedelta(minutes=1)
            except ValueError:
                pass
        return self._clock() + timedelta(hours=23, minutes=59)
