from __future__ import annotations

import logging
import time
from typing import Any

from .remote_client import CentralServerUnavailable, RemoteKiwoomRestClient
from .mock_execution import ORDER_API_IDS
from .account_query import (
    AccountQueryBatch,
    AccountQueryCapabilityError,
    AccountScopeMismatchError,
    InterruptedAccountQueryError,
    as_account_batch_client,
)


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
        if api_id in ORDER_API_IDS:
            raise ValueError("order APIs cannot use the query failover client")
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

    def load_stored_minute_bars(
        self, code: str, trading_date: str, market: str = "",
    ) -> tuple[dict[str, Any], ...] | None:
        """중앙 DB가 가용할 때만 저장 분봉을 읽고, 단절 시 TR fallback을 허용한다."""
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            result = self._primary.load_stored_minute_bars(code, trading_date, market)
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
            return None

    def load_stored_minute_bars_with_coverage(
        self, code: str, trading_date: str, market: str = "",
    ):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            result = self._primary.load_stored_minute_bars_with_coverage(
                code, trading_date, market,
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
            return None

    def load_stored_recent_minute_bars(
        self, code: str, end_date: str, market: str = "", trading_days: int = 2,
    ):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_recent_minute_bars(
                code, end_date, market, trading_days,
            )
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            if not self._using_fallback:
                logger.warning("시놀로지 조회 연결 실패 · 이 PC의 키움 API로 자동 전환")
                self._using_fallback = True
            return None

    def load_stored_ranking(self, query_type: str = "5") -> dict[str, Any] | None:
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_ranking(query_type)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_daily_bars(
        self, code: str, market: str = "", limit: int = 250,
    ) -> tuple[dict[str, Any], ...] | None:
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_daily_bars(code, market, limit)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_investor_flow(self, code: str, trading_date: str):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_investor_flow(code, trading_date)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_program_flow(self, code: str, trading_date: str):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_program_flow(code, trading_date)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_new_highs(self, periods: tuple[int, ...]):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_new_highs(periods)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_historical_high(self, code: str):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return self._primary.load_stored_historical_high(code)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_stored_fundamentals(self, code: str):
        return self._stored_primary("load_stored_fundamentals", code)

    def load_stored_nxt_eligibility(self, code: str):
        return self._stored_primary("load_stored_nxt_eligibility", code)

    def load_stored_market_index(self, market: str, trading_date: str):
        return self._stored_primary("load_stored_market_index", market, trading_date)

    def _stored_primary(self, method: str, *args):
        if time.monotonic() < self._primary_retry_at:
            return None
        try:
            return getattr(self._primary, method)(*args)
        except CentralServerUnavailable:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            return None

    def load_account_contexts(self):
        return self._primary.load_account_contexts()

    def for_account_scope(self, scope):
        # PC fallback has only one local profile: never query it for another NAS account.
        return self._primary.for_account_scope(scope)

    def query_account_pages(
        self, api_id: str, path: str, body: dict[str, Any], *, max_pages: int = 20,
    ) -> AccountQueryBatch:
        """Fail over only at the whole-batch boundary and restart at page one."""
        if time.monotonic() < self._primary_retry_at:
            return self._verified_fallback_account_batch(api_id, path, body, max_pages)
        try:
            result = self._primary.query_account_pages(api_id, path, body, max_pages=max_pages)
            self._primary_retry_at = 0.0
            return result
        except InterruptedAccountQueryError as error:
            self._primary_retry_at = time.monotonic() + self._retry_primary_seconds
            result = self._verified_fallback_account_batch(api_id, path, body, max_pages)
            if error.context is not None and result.context.scope != error.context.scope:
                raise AccountScopeMismatchError(
                    "NAS와 직접 API의 계좌가 달라 미완료 batch를 폐기했습니다."
                ) from error
            logger.warning("NAS 계좌 조회 중단 · 직접 API에서 첫 페이지부터 다시 조회")
            return result

    def _verified_fallback_account_batch(
        self, api_id: str, path: str, body: dict[str, Any], max_pages: int,
    ) -> AccountQueryBatch:
        result = as_account_batch_client(self._fallback).query_account_pages(
            api_id, path, body, max_pages=max_pages,
        )
        if result.context.transport != "direct":
            raise AccountQueryCapabilityError(
                "직접 API 계좌 binding이 검증되지 않아 계좌 조회를 전환할 수 없습니다."
            )
        return result

    def server_now(self):
        return self._fallback.server_now()

    def get_access_token(self) -> str:
        return self._fallback.get_access_token()
