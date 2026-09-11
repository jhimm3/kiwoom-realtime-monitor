from __future__ import annotations

import logging
import time
from typing import Any

from .remote_client import CentralServerUnavailable, RemoteKiwoomRestClient


logger = logging.getLogger(__name__)


class FailoverKiwoomRestClient:
    """중앙 통신 장애일 때만 제한 시간 동안 로컬 키움 조회를 사용한다."""

    def __init__(self, primary: RemoteKiwoomRestClient, fallback: Any,
                 *, retry_primary_seconds: float = 15.0) -> None:
        self._primary, self._fallback = primary, fallback
        self._retry_primary_seconds = max(1.0, float(retry_primary_seconds))
        self._primary_retry_at = 0.0
        self._using_fallback = False

    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload, _, _ = self.request_with_continuation(api_id, path, body)
        return payload

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        if time.monotonic() >= self._primary_retry_at:
            try:
                result = self._primary.request_with_continuation(
                    api_id, path, body, cont_yn=cont_yn, next_key=next_key,
                )
                self._primary_retry_at = 0.0
                if self._using_fallback:
                    logger.info("시놀로지 조회 연결 복구 · 중앙 서버 사용 재개")
                    self._using_fallback = False
                return result
            except CentralServerUnavailable:
                self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
                if not self._using_fallback:
                    logger.warning("시놀로지 조회 연결 실패 · 이 PC의 키움 API로 자동 전환")
                    self._using_fallback = True
        return self._fallback.request_with_continuation(
            api_id, path, body, cont_yn=cont_yn, next_key=next_key,
        )

    def server_now(self):
        return self._fallback.server_now()

    def get_access_token(self) -> str:
        return self._fallback.get_access_token()
