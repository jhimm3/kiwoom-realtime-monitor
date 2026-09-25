from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


class CentralContentHttpError(RuntimeError):
    """중앙 콘텐츠 API가 HTTP 오류 상태를 반환했다."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.detail = detail
        suffix = f" · {detail}" if detail else ""
        super().__init__(f"중앙 자료 서버 오류: HTTP {self.status_code}{suffix}")


class CentralContentUnavailableError(RuntimeError):
    """중앙 콘텐츠 API에 연결하지 못했다."""


def is_missing_collection_error(error: BaseException) -> bool:
    """구형 fake client도 유지하면서 실제 HTTP 404만 기능 부재로 판정한다."""
    if isinstance(error, CentralContentHttpError):
        return error.status_code == 404
    return type(error) is RuntimeError and "HTTP 404" in str(error)


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

    def load_health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

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

    def load_theme_history(
        self, *, as_of: float | None = None, limit: int = 100,
    ) -> dict[str, Any]:
        query_values: dict[str, object] = {"limit": max(1, min(int(limit), 1000))}
        if as_of is not None:
            query_values["as_of"] = max(0.0, float(as_of))
        return self._request("GET", f"/api/v1/themes/history?{urlencode(query_values)}")

    def load_news_history(
        self, kind: str, *, target: str = "", identity: str = "",
        as_of: float | None = None, limit: int = 100, stock_code: str = "",
    ) -> dict[str, Any]:
        query: dict[str, object] = {
            "target": target, "identity": identity, "limit": max(1, min(limit, 1000)),
        }
        if as_of is not None:
            query["as_of"] = max(0.0, float(as_of))
        if stock_code:
            query["stock_code"] = stock_code
        return self._request("GET", f"/api/v1/news/history/{kind}?{urlencode(query)}")

    def claim_historical_news_job(self, stage: str,
                                  excluded_codes: tuple[str, ...] = (),
                                  scope: str = "all") -> dict[str, Any]:
        if stage not in {"BODY", "RULE"}:
            raise ValueError("BODY 또는 RULE 작업만 요청할 수 있습니다.")
        if scope not in {"all", "pc", "pc_market", "pc_search"}:
            raise ValueError("지원하지 않는 과거 뉴스 작업 범위입니다.")
        query = {"stage": stage, "scope": scope}
        if excluded_codes:
            query["excluded_codes"] = ",".join(excluded_codes)
        return self._request("POST", f"/api/v1/news/historical-jobs/claim?{urlencode(query)}")

    def complete_historical_news_job(self, result: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/news/historical-jobs/complete", result)

    def import_historical_market_articles(self, batch: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/news/historical-market-articles", batch)

    def load_research_observations_page(
        self,
        start: datetime,
        end: datetime,
        kinds: tuple[str, ...],
        *,
        subject: str = "",
        watermark: str = "",
        cursor: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any]:
        query = urlencode({
            "start": start.isoformat(), "end": end.isoformat(), "kinds": ",".join(kinds),
            "subject": subject, "watermark": watermark, "cursor": max(0, int(cursor)),
            "limit": max(1, min(int(limit), 1000)),
        })
        return self._request("GET", f"/api/v1/research/observations?{query}")

    def load_candidate_events(
        self, *, after_sequence: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        query = urlencode({
            "after_sequence": max(0, int(after_sequence)),
            "limit": max(1, min(int(limit), 1000)),
        })
        return self._request("GET", f"/api/v1/research/candidates?{query}")

    def publish_mock_automation_candidate(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        expected_binding_revision: int,
        package: dict[str, Any],
        eligibility_policy: dict[str, Any],
        eligibility_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/research/mock-automation-candidates", {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "expected_binding_revision": expected_binding_revision,
            "package": package,
            "eligibility_policy": eligibility_policy,
            "eligibility_receipt": eligibility_receipt,
        })

    def load_mock_automation_candidates(
        self, account_ref: str, *, credential_profile_id: str,
    ) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        query = urlencode({"credential_profile_id": credential_profile_id})
        return self._request(
            "GET", f"/api/v1/research/mock-automation-candidates/{encoded_ref}?{query}",
        )

    def publish_mock_automation_spec(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        expected_binding_revision: int,
        shadow_event_id: str,
        forward_profile: dict[str, Any],
        stage_revisions: list[dict[str, Any]],
        operating_spec: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/research/mock-automation-specs", {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "expected_binding_revision": expected_binding_revision,
            "shadow_event_id": shadow_event_id,
            "forward_profile": forward_profile,
            "stage_revisions": stage_revisions,
            "operating_spec": operating_spec,
        })

    def load_mock_automation_specs(self, account_ref: str) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        return self._request(
            "GET", f"/api/v1/research/mock-automation-specs/{encoded_ref}",
        )

    def load_mock_automation_status(
        self, account_ref: str, *, credential_profile_id: str,
    ) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        query = urlencode({"credential_profile_id": credential_profile_id})
        return self._request(
            "GET", f"/api/v1/mock-automation/accounts/{encoded_ref}?{query}",
        )

    def start_mock_automation(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        spec_id: str,
        expected_settings_revision: int,
        credential_revision: int,
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/mock-automation/start", {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "spec_id": spec_id,
            "expected_settings_revision": expected_settings_revision,
            "credential_revision": credential_revision,
        })

    def stop_mock_automation(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        spec_id: str,
        expected_control_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/mock-automation/stop", {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "spec_id": spec_id,
            "expected_control_revision": expected_control_revision,
            "reason": reason,
        })

    def resume_mock_automation(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        spec_id: str,
        expected_control_revision: int,
        expected_settings_revision: int,
        credential_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/mock-automation/resume", {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "spec_id": spec_id,
            "expected_control_revision": expected_control_revision,
            "expected_settings_revision": expected_settings_revision,
            "credential_revision": credential_revision,
            "reason": reason,
        })

    def capabilities(self) -> dict[str, bool]:
        document = self._request("GET", "/api/v1/capabilities")
        values = document.get("capabilities", {})
        if not isinstance(values, dict):
            raise RuntimeError("중앙 서버 capability 응답 형식이 올바르지 않습니다.")
        return {str(key): bool(value) for key, value in values.items()}

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
            raise CentralContentHttpError(error.code, detail) from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CentralContentUnavailableError("중앙 자료 서버에 연결할 수 없습니다.") from error
        if not isinstance(result, dict):
            raise RuntimeError("중앙 자료 서버 응답 형식이 올바르지 않습니다.")
        return result
