from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope

from .client import KiwoomApiError
from .account_query import (
    AccountQueryBatch,
    AccountQueryCapabilityError,
    AccountQueryContext,
    AccountScopeMismatchError,
    IncompleteAccountQueryError,
    InterruptedAccountQueryError,
)


class CentralServerUnavailable(KiwoomApiError):
    """중앙 서버 자체에 연결할 수 없어 선택적 로컬 전환이 가능한 오류."""


_CENTRAL_GATEWAY_UNAVAILABLE_STATUS = frozenset({502, 503, 504})


def _http_error_detail(error: HTTPError) -> object:
    try:
        return json.loads(error.read().decode("utf-8")).get("detail", "")
    except (ValueError, AttributeError):
        return ""


def _raise_central_http_error(error: HTTPError, detail: object) -> None:
    if error.code in _CENTRAL_GATEWAY_UNAVAILABLE_STATUS:
        raise CentralServerUnavailable(
            f"개인 중앙 서버에 연결할 수 없습니다. (HTTP {error.code})"
        ) from error
    raise KiwoomApiError(f"개인 중앙 서버 오류: HTTP {error.code} {detail}".strip()) from error


def planned_reconnect_remaining(status: Any) -> float | None:
    """Only explicit, bounded server reconnect metadata may defer failover."""
    if not isinstance(status, dict) or status.get("planned_reconnect") is not True:
        return None
    generation, remaining = status.get("generation"), status.get("remaining_seconds")
    if (type(generation) is not int or not 0 <= generation < 2**63
            or type(remaining) not in (int, float) or not 0 < remaining <= 30
            or not math.isfinite(remaining)):
        return None
    return float(remaining)


class CentralPlannedReconnect(KiwoomApiError):
    """A live NAS is temporarily reconnecting; this is not a transport failure."""

    def __init__(self, status: dict[str, Any]):
        super().__init__("나스 실시간 연결 변경 중입니다. 기존 순위를 유지하며 재시도합니다.")
        self.generation = status["generation"]
        self.remaining_seconds = planned_reconnect_remaining(status)


class RemoteKiwoomRestClient:
    """개인 중앙 서버의 조회 큐를 사용하는 동기식 호환 클라이언트."""

    def __init__(
        self, server_url: str, access_token: str, *, opener: Callable[..., Any] = urlopen,
        timeout_seconds: float = 30.0,
        account_context: AccountQueryContext | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._access_token = access_token
        self._opener = opener
        self._timeout = timeout_seconds
        if account_context is not None and (account_context.transport != "nas" or account_context.binding_revision < 1):
            raise ValueError("verified NAS context is required")
        self._selected_context = account_context
        self._command_lock = RLock()
        self._pending_submit = None

    def load_account_contexts(self) -> tuple[AccountQueryContext, ...]:
        request = Request(f"{self._server_url}/api/v3/kiwoom/accounts",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET")
        document = self._read_central_document(request, "계좌 목록", timeout_seconds=10)
        values = document.get("accounts")
        if not isinstance(values, list): raise AccountQueryCapabilityError("NAS 계좌 목록을 확인할 수 없습니다.")
        contexts = tuple(self._account_context({"context": value}, verified=True) for value in values)
        if any(c.binding_revision < 1 for c in contexts) or len({c.scope for c in contexts}) != len(contexts):
            raise AccountScopeMismatchError("NAS 계좌 목록에 잘못되거나 중복된 계좌가 있습니다.")
        return contexts

    def for_account_scope(self, scope: AccountScope):
        matches = [c for c in self.load_account_contexts() if c.scope == scope]
        if len(matches) != 1:
            raise AccountQueryCapabilityError("선택한 계좌는 NAS 조회 연결이 준비되지 않았습니다. NAS 계좌 설정을 확인하세요.")
        return RemoteKiwoomRestClient(self._server_url, self._access_token, opener=self._opener,
            timeout_seconds=self._timeout, account_context=matches[0])

    def _selected_fields(self):
        context = self._selected_context
        if context is None: raise ValueError("계좌 선택을 먼저 확인하세요.")
        return {"account_scope": context.scope.to_dict(), "credential_profile_id": context.credential_profile_id,
            "expected_binding_revision": context.binding_revision}

    def _mock_order(self, method, intent_id="", command=None):
        fields = self._selected_fields()
        context = self._selected_context
        if context.scope.environment != AccountEnvironment.MOCK:
            raise ValueError("모의계좌에서만 모의주문을 사용할 수 있습니다.")
        if intent_id and (not isinstance(intent_id, str) or not all(c.isalnum() or c in "_-" for c in intent_id)):
            raise ValueError("주문 식별자가 올바르지 않습니다.")
        path = f"/api/v2/mock/accounts/{context.scope.account_ref}/orders"
        if intent_id: path += "/" + intent_id
        body = None
        if method == "GET":
            path += "?" + urlencode({"broker": context.scope.broker, "environment": "mock", "credential_profile_id": context.credential_profile_id,
                "expected_binding_revision": context.binding_revision})
        else:
            if intent_id: path += "/cancel"
            body = {**fields, **(command or {})}
        request = Request(self._server_url + path, method=method,
            headers={"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        document = self._read_central_document(request, "모의주문")
        if self._account_context(document, verified=True) != context:
            raise AccountScopeMismatchError("모의주문 응답의 계좌 연결이 변경되었습니다.")
        return document

    def submit_mock_order(self, *, request_id, symbol, side, quantity, limit_price, expires_seconds=120):
        if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request_id)
                or not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) or side not in {"BUY", "SELL"}
                or type(quantity) is not int or not 1 <= quantity <= 1_000_000
                or type(limit_price) is not int or limit_price < 1
                or type(expires_seconds) is not int or not 10 <= expires_seconds <= 600):
            raise ValueError("모의주문 입력과 기존 request_id를 확인하세요.")
        command = {"request_id": request_id, "symbol": symbol, "side": side,
            "quantity": quantity, "limit_price": limit_price, "expires_seconds": expires_seconds}
        with self._command_lock:
            if self._pending_submit is not None and self._pending_submit != command:
                raise ValueError("이전 주문 응답을 같은 계좌/request_id/내용으로 먼저 확인하세요.")
            self._pending_submit = dict(command)
            result = self._mock_order("POST", command=command)
            if not isinstance(result.get("intent_id"), str) or not result["intent_id"]:
                raise KiwoomApiError("모의주문 응답의 식별자를 확인할 수 없습니다.")
            self._pending_submit = None
            return result

    def get_mock_order(self, intent_id):
        if not intent_id: raise ValueError("주문 식별자가 필요합니다.")
        return self._mock_order("GET", intent_id)

    def cancel_mock_order(self, intent_id, *, quantity=0):
        if not intent_id: raise ValueError("주문 식별자가 필요합니다.")
        if type(quantity) is not int or not 0 <= quantity <= 1_000_000: raise ValueError("취소 수량을 확인하세요.")
        return self._mock_order("POST", intent_id, {"quantity": quantity})

    def load_mock_execution_events(self, *, after_sequence=0, limit=500):
        """Read the selected mock account ledger without issuing a Kiwoom TR."""
        context = self._selected_context
        if context is None:
            raise ValueError("계좌 선택을 먼저 확인하세요.")
        if context.scope.environment != AccountEnvironment.MOCK:
            raise ValueError("모의계좌 실행 원장은 모의계좌에서만 읽을 수 있습니다.")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("실행 원장 커서가 올바르지 않습니다.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("실행 원장 조회 수는 1~1000이어야 합니다.")
        query = urlencode({
            "broker": context.scope.broker,
            "environment": "mock",
            "credential_profile_id": context.credential_profile_id,
            "expected_binding_revision": context.binding_revision,
            "after_sequence": after_sequence,
            "limit": limit,
        })
        request = Request(
            f"{self._server_url}/api/v2/mock/accounts/{context.scope.account_ref}/execution-events?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            method="GET",
        )
        document = self._read_central_document(request, "모의 실행 원장")
        if self._account_context(document, verified=True) != context:
            raise AccountScopeMismatchError("모의 실행 원장 응답의 계좌 연결이 변경되었습니다.")
        events = document.get("events")
        next_cursor = document.get("next_cursor")
        has_more = document.get("has_more")
        if (not isinstance(events, list) or any(not isinstance(row, dict) for row in events)
                or type(next_cursor) is not int or next_cursor < after_sequence
                or type(has_more) is not bool):
            raise KiwoomApiError("모의 실행 원장 응답 형식이 올바르지 않습니다.")
        sequences = [row.get("accepted_sequence") for row in events]
        if (any(type(value) is not int or value <= after_sequence for value in sequences)
                or sequences != sorted(set(sequences))
                or (sequences and next_cursor != sequences[-1])
                or (not sequences and next_cursor != after_sequence)):
            raise KiwoomApiError("모의 실행 원장 커서가 연속되지 않습니다.")
        return document

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
            detail = _http_error_detail(error)
            if (error.code == 503 and isinstance(detail, dict) and detail.get("code") == "REALTIME_RECONNECTING"
                    and planned_reconnect_remaining(detail.get("connection_status")) is not None):
                raise CentralPlannedReconnect(detail["connection_status"]) from None
            _raise_central_http_error(error, detail)
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CentralServerUnavailable("개인 중앙 서버에 연결할 수 없습니다.") from error
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise KiwoomApiError("개인 중앙 서버 응답 형식이 올바르지 않습니다.")
        return document["payload"], bool(document.get("has_next")), str(document.get("next_key", ""))

    def load_stored_minute_bars(
        self, code: str, trading_date: str, market: str = "",
    ) -> tuple[dict[str, Any], ...]:
        """키움 TR을 만들지 않고 중앙 DB에 이미 저장된 분봉을 읽는다."""
        bars, _complete = self.load_stored_minute_bars_with_coverage(code, trading_date, market)
        return bars

    def load_stored_minute_bars_with_coverage(
        self, code: str, trading_date: str, market: str = "",
    ) -> tuple[tuple[dict[str, Any], ...], bool]:
        """중앙 분봉과 장후 전체 조회 완료 근거를 함께 읽는다."""
        query = urlencode({
            "code": code,
            "trading_date": trading_date,
            "market": market.upper(),
        })
        request = Request(
            f"{self._server_url}/api/v1/market/minute-bars?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self._timeout, context=system_ssl_context()) as response:
                document = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            _raise_central_http_error(error, _http_error_detail(error))
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CentralServerUnavailable("개인 중앙 서버에 연결할 수 없습니다.") from error
        bars = document.get("bars") if isinstance(document, dict) else None
        if not isinstance(bars, list) or any(not isinstance(value, dict) for value in bars):
            raise KiwoomApiError("개인 중앙 서버 분봉 응답 형식이 올바르지 않습니다.")
        coverage = document.get("coverage") if isinstance(document, dict) else None
        complete = bool(isinstance(coverage, dict) and coverage.get("complete") is True)
        return tuple(bars), complete

    def load_stored_recent_minute_bars(
        self, code: str, end_date: str, market: str = "", trading_days: int = 2,
    ) -> tuple[dict[str, Any], ...]:
        """최근 저장 거래일 분봉을 한 번의 중앙 DB 조회로 읽는다."""
        query = urlencode({
            "code": code, "end_date": end_date, "market": market.upper(),
            "trading_days": max(1, min(int(trading_days), 5)),
        })
        request = Request(
            f"{self._server_url}/api/v1/market/recent-minute-bars?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, "최근 분봉")
        bars = document.get("bars")
        if not isinstance(bars, list) or any(not isinstance(value, dict) for value in bars):
            raise KiwoomApiError("개인 중앙 서버 최근 분봉 응답 형식이 올바르지 않습니다.")
        return tuple(bars)

    def load_stored_market_caps(
        self, codes: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        """Read last persisted 0B market caps without issuing a Kiwoom TR."""
        normalized = tuple(dict.fromkeys(str(code).strip() for code in codes if code))
        if not normalized:
            return {}
        request = Request(
            f"{self._server_url}/api/v1/market/latest-market-caps?"
            + urlencode({"codes": normalized}, doseq=True),
            headers={"Authorization": f"Bearer {self._access_token}"},
            method="GET",
        )
        document = self._read_central_document(request, "최근 0B 시가총액")
        rows = document.get("market_caps")
        if not isinstance(rows, list) or any(not isinstance(value, dict) for value in rows):
            raise KiwoomApiError("개인 중앙 서버 시가총액 응답 형식이 올바르지 않습니다.")
        result: dict[str, dict[str, Any]] = {}
        for value in rows:
            code = str(value.get("code", "")).strip()
            try:
                market_cap = float(value.get("market_cap_eok"))
            except (TypeError, ValueError):
                continue
            if code and market_cap > 0:
                result[code] = {
                    "market_cap_eok": market_cap,
                    "observed_at": str(value.get("observed_at", "")),
                }
        return result

    def load_stored_ranking(self, query_type: str = "5") -> dict[str, Any] | None:
        kind = "top20_membership" if query_type == "5" else "ranking"
        query = urlencode({
            "subject": (
                datetime.now(timezone(timedelta(hours=9))).date().isoformat()
                if query_type == "5" else query_type
            ),
            "limit": 1,
            "prefer_live": "true" if query_type == "5" else "false",
        })
        request = Request(
            f"{self._server_url}/api/v1/market/snapshots/{kind}?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, "순위")
        snapshots = document.get("snapshots")
        if not isinstance(snapshots, list):
            raise KiwoomApiError("개인 중앙 서버 순위 응답 형식이 올바르지 않습니다.")
        if not snapshots:
            # 중앙 연결은 정상이지만 아직 snapshot이 없는 상태다. 빈 정상
            # 응답으로 구분해 앱이 ka00198을 대신 발생시키지 않게 한다.
            return {"item_inq_rank": []}
        payload = snapshots[0].get("payload") if isinstance(snapshots[0], dict) else None
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise KiwoomApiError("개인 중앙 서버 순위 snapshot 형식이 올바르지 않습니다.")
        return {"item_inq_rank": items}

    def load_stored_daily_bars(
        self, code: str, market: str = "", limit: int = 250,
    ) -> tuple[dict[str, Any], ...] | None:
        query = urlencode({"code": code, "market": market.upper(), "limit": limit})
        request = Request(
            f"{self._server_url}/api/v1/market/daily-bars?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, "일봉")
        bars = document.get("bars")
        if not isinstance(bars, list) or any(not isinstance(value, dict) for value in bars):
            raise KiwoomApiError("개인 중앙 서버 일봉 응답 형식이 올바르지 않습니다.")
        return tuple(bars)

    def load_stored_investor_flow(
        self, code: str, trading_date: str,
    ) -> dict[str, Any] | None:
        """중앙에 저장된 당일 외국인·기관 응답을 읽고 Kiwoom TR을 만들지 않는다."""
        snapshots = self._load_market_snapshots("investor_flow", code, 10, "외국인·기관")
        day = trading_date.replace("-", "")
        for snapshot in snapshots:
            if not str(snapshot.get("snapshot_key", "")).startswith(day):
                continue
            payload = snapshot.get("payload")
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                return {"stk_orgn_trde_trnsn": rows}
        return {"stk_orgn_trde_trnsn": []}

    def load_stored_program_flow(
        self, code: str, trading_date: str,
    ) -> dict[str, Any] | None:
        """중앙의 0W/장후 보완 자료를 읽고 Kiwoom TR을 만들지 않는다."""
        snapshots = self._load_market_snapshots("program_flow", code, 500, "프로그램매매")
        day = trading_date.replace("-", "")
        rows: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if not str(snapshot.get("snapshot_key", "")).startswith(day):
                continue
            payload = snapshot.get("payload")
            values = payload.get("rows") if isinstance(payload, dict) else None
            if isinstance(values, list):
                rows.extend(value for value in values if isinstance(value, dict))
        return {"stk_tm_prm_trde_trnsn": rows}

    def load_stored_new_highs(
        self, periods: tuple[int, ...],
    ) -> dict[int, set[str]] | None:
        result: dict[int, set[str]] = {}
        for period in periods:
            snapshots = self._load_market_snapshots(
                "new_high", str(period), 1, f"{period}일 신고가",
            )
            items = (
                snapshots[0].get("payload", {}).get("items", [])
                if snapshots else []
            )
            if not isinstance(items, list):
                raise KiwoomApiError("개인 중앙 서버 신고가 응답 형식이 올바르지 않습니다.")
            result[period] = {
                str(value.get("stk_cd", "")).strip()
                for value in items if isinstance(value, dict) and value.get("stk_cd")
            }
        return result

    def load_stored_historical_high(self, code: str) -> dict[str, Any] | None:
        query = urlencode({"owner": code, "limit": 1})
        request = Request(
            f"{self._server_url}/api/v1/content/historical_highs?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, "역사적 신고가")
        values = document.get("documents")
        if not isinstance(values, list):
            raise KiwoomApiError("개인 중앙 서버 역사적 신고가 응답 형식이 올바르지 않습니다.")
        if not values:
            return {"price": None, "first_year": None, "last_year": None, "evidence": []}
        value = values[0].get("document") if isinstance(values[0], dict) else None
        target = value.get("target") if isinstance(value, dict) else None
        if not isinstance(target, dict):
            raise KiwoomApiError("개인 중앙 서버 역사적 신고가 문서 형식이 올바르지 않습니다.")
        return target

    def load_stored_fundamentals(self, code: str) -> dict[str, Any] | None:
        return self._load_stored_stock_document("stock_fundamentals", code, "기본정보")

    def load_stored_nxt_eligibility(self, code: str) -> dict[str, Any] | None:
        return self._load_stored_stock_document("stock_nxt_eligibility", code, "NXT 가능 여부")

    def _load_stored_stock_document(self, collection: str, code: str, label: str) -> dict[str, Any]:
        query = urlencode({"owner": code, "limit": 1})
        request = Request(
            f"{self._server_url}/api/v1/content/{collection}?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, label)
        values = document.get("documents")
        if not isinstance(values, list):
            raise KiwoomApiError(f"개인 중앙 서버 {label} 응답 형식이 올바르지 않습니다.")
        if not values:
            return {}
        value = values[0].get("document") if isinstance(values[0], dict) else None
        payload = value.get("payload") if isinstance(value, dict) else None
        return payload if isinstance(payload, dict) else {}

    def load_stored_market_index(self, market: str, trading_date: str) -> dict[str, Any] | None:
        day = trading_date.replace("-", "")
        snapshots = self._load_market_snapshots(
            "market_index_chart", f"{day}:{market.casefold()}", 1, "시장지수 차트",
        )
        if not snapshots:
            return {"minutes": [], "daily": []}
        payload = snapshots[0].get("payload")
        return payload if isinstance(payload, dict) else {"minutes": [], "daily": []}

    def load_stored_top20_index(self, trading_date: str) -> tuple[dict[str, Any], ...]:
        day = trading_date[:10]
        snapshots = self._load_market_snapshots("top20_index", day, 1_440, "TOP20 지수")
        values = []
        for snapshot in reversed(snapshots):
            payload = snapshot.get("payload")
            if isinstance(payload, dict):
                values.append(payload)
        return tuple(values)

    def load_stored_top20_statistics(self, start_date: str, end_date: str) -> dict[str, Any]:
        query = urlencode({"start_date": start_date[:10], "end_date": end_date[:10]})
        request = Request(
            f"{self._server_url}/api/v1/market/top20-statistics?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, "TOP20 통계")
        if not isinstance(document.get("hourly"), list) or not isinstance(document.get("comparisons"), list):
            raise KiwoomApiError("개인 중앙 서버 TOP20 통계 응답 형식이 올바르지 않습니다.")
        return document

    def _load_market_snapshots(
        self, kind: str, subject: str, limit: int, label: str,
    ) -> list[dict[str, Any]]:
        query = urlencode({"subject": subject, "limit": limit})
        request = Request(
            f"{self._server_url}/api/v1/market/snapshots/{kind}?{query}",
            headers={"Authorization": f"Bearer {self._access_token}"}, method="GET",
        )
        document = self._read_central_document(request, label)
        snapshots = document.get("snapshots")
        if not isinstance(snapshots, list) or any(not isinstance(value, dict) for value in snapshots):
            raise KiwoomApiError(f"개인 중앙 서버 {label} 응답 형식이 올바르지 않습니다.")
        return snapshots

    def _read_central_document(self, request: Request, label: str, *, timeout_seconds=None) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self._timeout if timeout_seconds is None else timeout_seconds, context=system_ssl_context()) as response:
                document = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            _raise_central_http_error(error, _http_error_detail(error))
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CentralServerUnavailable("개인 중앙 서버에 연결할 수 없습니다.") from error
        if not isinstance(document, dict):
            raise KiwoomApiError(f"개인 중앙 서버 {label} 응답 형식이 올바르지 않습니다.")
        return document

    def query_account_pages(
        self, api_id: str, path: str, body: dict[str, Any], *, max_pages: int = 20,
    ) -> AccountQueryBatch:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        pages: list[dict[str, Any]] = []
        context: AccountQueryContext | None = None
        batch_id, next_key = "", ""
        for page_index in range(max_pages):
            request = Request(
                f"{self._server_url}/api/{'v3' if self._selected_context else 'v2'}/kiwoom/account-query",
                data=json.dumps({
                    "api_id": api_id, "path": path, "body": body,
                    "batch_id": batch_id, "page_index": page_index,
                    "next_key": next_key,
                    **(self._selected_fields() if self._selected_context else {}),
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
                detail = _http_error_detail(error)
                if error.code in {404, 405, 501}:
                    raise AccountQueryCapabilityError(
                        "중앙 서버가 검증 계좌 조회 v2를 지원하지 않습니다."
                    ) from error
                if error.code in _CENTRAL_GATEWAY_UNAVAILABLE_STATUS:
                    raise InterruptedAccountQueryError(
                        "개인 중앙 서버 계좌 조회가 중단되었습니다.", context,
                    ) from error
                raise KiwoomApiError(f"개인 중앙 서버 오류: HTTP {error.code} {detail}".strip()) from error
            except (URLError, TimeoutError, ConnectionError, OSError) as error:
                raise InterruptedAccountQueryError(
                    "개인 중앙 서버 계좌 조회가 중단되었습니다.", context,
                ) from error
            page_context = self._account_context(document, verified=self._selected_context is not None)
            if self._selected_context is not None and page_context != self._selected_context:
                raise AccountScopeMismatchError("선택한 계좌와 NAS 응답의 연결 버전이 다릅니다.")
            if self._selected_context is not None and (type(document.get("complete")) is not bool
                    or type(document.get("page_index")) is not int):
                raise IncompleteAccountQueryError("NAS 계좌 페이지 완료 상태가 올바르지 않습니다.")
            if context is None:
                context = page_context
            elif page_context != context:
                raise KiwoomApiError("계좌 조회 도중 중앙 서버 context가 변경되었습니다.")
            payload = document.get("payload") if isinstance(document, dict) else None
            response_batch_id = str(document.get("batch_id", "")) if isinstance(document, dict) else ""
            if not isinstance(payload, dict) or not response_batch_id:
                raise KiwoomApiError("개인 중앙 서버 계좌 응답 형식이 올바르지 않습니다.")
            if batch_id and response_batch_id != batch_id:
                raise KiwoomApiError("계좌 조회 batch ID가 변경되었습니다.")
            if int(document.get("page_index", -1)) != page_index:
                raise KiwoomApiError("계좌 조회 페이지 순서가 올바르지 않습니다.")
            pages.append(payload)
            batch_id = response_batch_id
            if bool(document.get("complete")):
                assert context is not None
                return AccountQueryBatch(tuple(pages), context, len(pages), True)
            next_key = str(document.get("next_key", ""))
            if not next_key:
                raise KiwoomApiError("미완료 계좌 조회에 다음 cursor가 없습니다.")
        raise IncompleteAccountQueryError(
            f"account query exceeded the {max_pages}-page safety limit"
        )

    @staticmethod
    def _account_context(document: object, *, verified=False) -> AccountQueryContext:
        if not isinstance(document, dict) or not isinstance(document.get("context"), dict):
            raise KiwoomApiError("개인 중앙 서버 계좌 context가 없습니다.")
        value = document["context"]
        try:
            if verified and (type(value["binding_revision"]) is not int or value["binding_revision"] < 1
                    or not isinstance(value["credential_profile_id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value["credential_profile_id"])
                    or value["broker"] != "kiwoom"):
                raise ValueError("verified context required")
            return AccountQueryContext(
                scope=AccountScope(
                    broker=str(value["broker"]),
                    environment=AccountEnvironment(str(value["environment"])),
                    account_ref=str(value["account_ref"]),
                ),
                credential_profile_id=str(value["credential_profile_id"]),
                binding_revision=int(value["binding_revision"]),
                transport="nas",
                display_label=str(value.get("display_label", "")).strip(),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise KiwoomApiError("개인 중앙 서버 계좌 context가 올바르지 않습니다.") from error

    def server_now(self) -> datetime:
        # 실시간 수집을 중앙화하기 전까지 서비스 프로토콜과의 호환을 위한 값이다.
        return datetime.utcnow() + timedelta(hours=9)

    def get_access_token(self) -> str:
        raise KiwoomApiError("키움 접근 토큰은 중앙 서버 외부로 제공하지 않습니다.")
