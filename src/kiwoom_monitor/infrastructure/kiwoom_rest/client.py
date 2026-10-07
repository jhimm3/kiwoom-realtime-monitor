"""토큰을 메모리에만 보관하는 키움 REST API 클라이언트."""

from __future__ import annotations

import json
import copy
import logging
import time
from threading import RLock
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import KiwoomSettings

JsonObject = dict[str, Any]
UrlOpen = Callable[..., Any]
logger = logging.getLogger(__name__)


class KiwoomApiError(RuntimeError):
    """키움 API 통신 또는 API 응답 오류."""


class KiwoomSubmissionUnknownError(KiwoomApiError):
    """단발성 요청 응답이 유실되어 외부 접수 여부를 알 수 없음."""


@dataclass(frozen=True)
class PreparedKiwoomCredentials:
    settings: KiwoomSettings = field(repr=False)
    token: str = field(repr=False)
    expires_at: datetime = field(repr=False)
    account_payload: JsonObject = field(repr=False)
    expected_generation: int
    account_query_completed: bool = False


class KiwoomCredentialBusyError(KiwoomApiError):
    """The connection is fenced for an explicit credential change."""


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
        self._last_response_headers: dict[str, str] = {}
        self._credential_paused = False
        self._credential_generation = 0

    def prepare_credentials(self, settings: KiwoomSettings) -> PreparedKiwoomCredentials:
        """Candidate OAuth/account query uses this client's lock and rate history.

        A shallow transport snapshot isolates its token/settings without publishing
        them. Holding the original lock prevents separate request limits or queues.
        """
        with self._request_lock:
            return self.verify_prepared_credentials(self.prepare_credential_token(settings))

    def prepare_credential_token(self, settings: KiwoomSettings) -> PreparedKiwoomCredentials:
        """First management phase; active token and credential generation are unchanged."""
        with self._request_lock:
            self._ensure_credential_accepting()
            if (not isinstance(settings, KiwoomSettings) or settings.environment != self.environment
                    or not isinstance(settings.app_key, str) or not settings.app_key.strip()
                    or not isinstance(settings.secret_key, str) or not settings.secret_key.strip()):
                raise KiwoomApiError("INVALID_CANDIDATE_CREDENTIALS")
            candidate = copy.copy(self)
            candidate._settings = settings
            candidate._token = None
            candidate._expires_at = None
            candidate._last_response_headers = {}
            try:
                candidate._get_token()
                if not candidate._token or candidate._expires_at is None:
                    raise KiwoomApiError("CANDIDATE_VALIDATION_FAILED")
                return PreparedKiwoomCredentials(settings, candidate._token, candidate._expires_at,
                                                  {}, self._credential_generation)
            except Exception:
                raise KiwoomApiError("CANDIDATE_VALIDATION_FAILED") from None
            finally:
                for name in ("_last_request_at", "_request_interval_seconds", "_rate_limit_success_count"):
                    setattr(self, name, getattr(candidate, name))

    def verify_prepared_credentials(self, prepared: PreparedKiwoomCredentials) -> PreparedKiwoomCredentials:
        """Second phase can be queued after ranking instead of holding two HTTP slots."""
        with self._request_lock:
            self._ensure_credential_accepting()
            if prepared.expected_generation != self._credential_generation or prepared.settings.environment != self.environment:
                raise KiwoomCredentialBusyError("CREDENTIAL_GENERATION_CONFLICT")
            if prepared.expires_at <= self._clock():
                raise KiwoomApiError("CANDIDATE_TOKEN_EXPIRED")
            candidate = copy.copy(self)
            candidate._settings = prepared.settings
            candidate._token = prepared.token
            candidate._expires_at = prepared.expires_at
            candidate._last_response_headers = {}
            try:
                payload = candidate._request_unlocked("ka00001", "/api/dostk/acnt", {})
                return PreparedKiwoomCredentials(prepared.settings, candidate._token, candidate._expires_at,
                                                  payload, self._credential_generation, True)
            except Exception:
                raise KiwoomApiError("CANDIDATE_VALIDATION_FAILED") from None
            finally:
                for name in ("_last_request_at", "_request_interval_seconds", "_rate_limit_success_count"):
                    setattr(self, name, getattr(candidate, name))

    def begin_credential_change(self) -> None:
        # Acquiring this lock waits for actual synchronous HTTP, including token refresh.
        with self._request_lock:
            self._credential_paused = True

    def prepare_drained_credentials(self, settings: KiwoomSettings) -> PreparedKiwoomCredentials:
        """Validate explicit recovery without admitting old-token reads or refresh.

        The private transport snapshot shares this lock and rate history. Only its
        candidate credentials are accepting; the active client stays fenced.
        """
        with self._request_lock:
            if not self._credential_paused:
                raise KiwoomCredentialBusyError("CREDENTIAL_DRAIN_REQUIRED")
            candidate = copy.copy(self)
            candidate._credential_paused = False
            try:
                return candidate.prepare_credentials(settings)
            finally:
                for name in ("_last_request_at", "_request_interval_seconds", "_rate_limit_success_count"):
                    setattr(self, name, getattr(candidate, name))

    def fork_verified_candidate(self, prepared: PreparedKiwoomCredentials) -> KiwoomRestClient:
        """Give a newly identified account its own lock, retaining probe rate history.

        No token is published here. The caller still drains and commits the
        candidate through the normal activation barrier before exposing it.
        """
        with self._request_lock:
            if (not prepared.account_query_completed or prepared.settings.environment != self.environment
                    or prepared.expected_generation != self._credential_generation
                    or prepared.expires_at <= self._clock()):
                raise KiwoomApiError("CANDIDATE_ACCOUNT_QUERY_REQUIRED")
            candidate = copy.copy(self)
            candidate._settings = prepared.settings
            candidate._request_lock = RLock()
            candidate._token = None
            candidate._expires_at = None
            candidate._credential_paused = False
            candidate._last_response_headers = {}
            return candidate

    def activate_prepared_credentials(self, prepared: PreparedKiwoomCredentials) -> int:
        with self._request_lock:
            if not self._credential_paused or prepared.expected_generation != self._credential_generation:
                raise KiwoomCredentialBusyError("CREDENTIAL_GENERATION_CONFLICT")
            if not prepared.account_query_completed:
                raise KiwoomApiError("CANDIDATE_ACCOUNT_QUERY_REQUIRED")
            if prepared.expires_at <= self._clock():
                raise KiwoomApiError("CANDIDATE_TOKEN_EXPIRED")
            if prepared.settings.environment != self.environment:
                raise KiwoomApiError("CREDENTIAL_ENVIRONMENT_CONFLICT")
            self._settings = prepared.settings
            self._token = prepared.token
            self._expires_at = prepared.expires_at
            self._last_response_headers = {}
            self._credential_generation += 1
            return self._credential_generation

    def disable_credentials(self) -> None:
        """Discard active credentials only after the existing connection is drained."""
        with self._request_lock:
            if not self._credential_paused:
                raise KiwoomCredentialBusyError("CREDENTIAL_GENERATION_CONFLICT")
            self._settings = KiwoomSettings("", "", self.environment)
            self._token = self._expires_at = None
            self._last_response_headers = {}
            self._credential_generation += 1

    def end_credential_change(self) -> None:
        with self._request_lock:
            self._credential_paused = False

    def _ensure_credential_accepting(self) -> None:
        if self._credential_paused:
            raise KiwoomCredentialBusyError("CREDENTIAL_CHANGE_IN_PROGRESS")

    @property
    def environment(self) -> str:
        return self._settings.environment

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
            self._ensure_credential_accepting()
            return self._request_unlocked(api_id, path, body)

    def request_once(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        """인증된 요청을 정확히 한 번 전송한다. 주문 전용 경계에서만 사용한다."""
        if not api_id or not path.startswith("/"):
            raise ValueError("api_id and absolute path are required")
        with self._request_lock:
            self._ensure_credential_accepting()
            response = self._authenticated_post_once(api_id, path, body)
        if response.get("return_code") not in (None, 0, "0"):
            message = str(response.get("return_msg", "알 수 없는 오류")).replace("\n", " ")
            raise KiwoomApiError(f"키움 API 오류 ({api_id}): {response.get('return_code')} {message}")
        return response

    def request_with_continuation(
        self, api_id: str, path: str, body: JsonObject, *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[JsonObject, bool, str]:
        """연속조회 응답과 다음 페이지 정보를 함께 반환한다."""
        with self._request_lock:
            self._ensure_credential_accepting()
            response = self._request_unlocked(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
            headers = self._last_response_headers
        has_next = headers.get("cont-yn", "N").upper() == "Y"
        return response, has_next, headers.get("next-key", "")

    def _request_unlocked(
        self, api_id: str, path: str, body: JsonObject, *, cont_yn: str = "N", next_key: str = ""
    ) -> JsonObject:
        """API-ID를 포함해 인증된 POST 요청을 보낸다."""
        if not api_id:
            raise ValueError("api_id is required")
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        response = self._authenticated_post(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
        return_code = response.get("return_code")
        # 서버가 만료·무효 토큰을 돌려주는 경우에는 메모리 토큰을 버리고
        # 한 번만 새 토큰으로 재요청한다. 순위 갱신이 토큰 오류로 멈추지 않는다.
        if self._is_invalid_token(return_code, response.get("return_msg")):
            self._token = None
            self._expires_at = None
            response = self._authenticated_post(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
            return_code = response.get("return_code")
        if return_code not in (None, 0, "0"):
            message = str(response.get("return_msg", "알 수 없는 오류")).replace("\n", " ")
            raise KiwoomApiError(f"키움 API 오류 ({api_id}): {return_code} {message}")
        return response

    def _authenticated_post(
        self, api_id: str, path: str, body: JsonObject, *, cont_yn: str = "N", next_key: str = ""
    ) -> JsonObject:
        return self._post(
            path,
            body,
            {
                "authorization": f"Bearer {self._get_token()}",
                "api-id": api_id,
                "cont-yn": cont_yn,
                "next-key": next_key,
            },
        )

    def _authenticated_post_once(self, api_id: str, path: str, body: JsonObject) -> JsonObject:
        return self._post_once(
            path,
            body,
            {"authorization": f"Bearer {self._get_token()}", "api-id": api_id},
        )

    def _post_once(self, path: str, body: JsonObject, extra_headers: dict[str, str]) -> JsonObject:
        self._wait_for_request_slot()
        request = Request(
            f"{self._settings.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json;charset=UTF-8", **extra_headers},
            method="POST",
        )
        try:
            with self._opener(request, timeout=15) as response:
                headers = getattr(response, "headers", {})
                self._last_response_headers = {
                    str(key).lower(): str(value)
                    for key, value in (headers.items() if hasattr(headers, "items") else ())
                }
                self._update_server_time(headers.get("Date") if hasattr(headers, "get") else None)
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise KiwoomApiError(f"키움 API 서버 오류: HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise KiwoomSubmissionUnknownError(
                "키움 주문 응답을 받지 못했습니다. 재전송하지 말고 주문·체결 조회로 접수 여부를 확인하세요."
            ) from error
        if not isinstance(payload, dict):
            raise KiwoomSubmissionUnknownError("키움 주문 응답 형식이 올바르지 않습니다.")
        self._recover_request_rate()
        return payload

    @staticmethod
    def _is_invalid_token(return_code: object, message: object) -> bool:
        text = str(message).casefold()
        return str(return_code).strip() in {"3", "8005"} or "token" in text or "인증에 실패" in text

    def get_access_token(self) -> str:
        """WebSocket 로그인에 사용할 실행 중 메모리 토큰을 제공한다."""
        with self._request_lock:
            self._ensure_credential_accepting()
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
            return_code = response.get("return_code", "unknown")
            return_msg = str(response.get("return_msg") or "응답에 토큰이 없습니다.").strip()
            raise KiwoomApiError(
                f"키움 API 토큰 발급에 실패했습니다. "
                f"[CODE={return_code}, MESSAGE={return_msg}]"
            )
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
                    self._last_response_headers = {
                        str(key).lower(): str(value)
                        for key, value in (headers.items() if hasattr(headers, "items") else ())
                    }
                    self._update_server_time(headers.get("Date") if hasattr(headers, "get") else None)
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code == 429:
                    logger.warning(
                        "키움 REST 요청 제한(HTTP 429): api_id=%s · 시도=%d/4%s",
                        extra_headers.get("api-id", "oauth2/token"), attempt + 1,
                        " · 감속 후 재시도" if attempt < 3 else " · 재시도 한도 도달",
                    )
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
