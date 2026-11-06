from __future__ import annotations

import json
import math
from contextlib import contextmanager
from uuid import uuid4
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler, HTTPSHandler

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context
from kiwoom_monitor.domain.time_basis import aware_iso_kst, kst_now
from kiwoom_monitor.domain.operation_context import (
    OperationContextSnapshot, operation_context_scope, snapshot_operation_context,
    validated_request_id,
)


def validate_producer_connection(server_url: str, owner: str, access_token: str) -> str:
    """Reuse the explicit C2 target validation for a plain producer connection."""
    from kiwoom_monitor.domain.research_contract import encode_reported_result_owner
    parts = urlsplit(server_url)
    if (parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
        raise ValueError("an explicit result server URL without credentials/query/fragment is required")
    encode_reported_result_owner(owner)
    if not access_token:
        raise ValueError("a dedicated result credential is required")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _http_request_id(value: object, *known_secrets: str) -> str:
    """Choose observation metadata at the wire boundary; never authorize work."""
    request_id = validated_request_id(value)
    try:
        request_id.encode("latin-1")
    except UnicodeEncodeError:
        request_id = ""
    if any(secret and secret in request_id for secret in known_secrets):
        request_id = ""
    return request_id or uuid4().hex


@contextmanager
def _http_operation_context(access_token: str):
    """Temporarily bind the exact outbound ID to the existing product context."""
    current = snapshot_operation_context()
    request_id = _http_request_id(current.request_id, access_token)
    with operation_context_scope(OperationContextSnapshot(
        current.source or "pc.central_http", request_id,
    )):
        yield request_id


class CentralContentHttpError(RuntimeError):
    """중앙 콘텐츠 API가 HTTP 오류 상태를 반환했다."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.detail = detail
        suffix = f" · {detail}" if detail else ""
        super().__init__(f"중앙 자료 서버 오류: HTTP {self.status_code}{suffix}")


class CentralThemeConflict(CentralContentHttpError):
    """Theme CAS/divergence requires reconciliation; never a legacy fallback."""


class CentralContentUnavailableError(RuntimeError):
    """중앙 콘텐츠 API에 연결하지 못했다."""


def is_missing_collection_error(error: BaseException) -> bool:
    """구형 fake client도 유지하면서 실제 HTTP 404만 기능 부재로 판정한다."""
    if isinstance(error, CentralContentHttpError):
        return error.status_code == 404
    return type(error) is RuntimeError and "HTTP 404" in str(error)


class _RejectResultRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _result_urlopen(request, *, timeout, context):
    return build_opener(HTTPSHandler(context=context), _RejectResultRedirect()).open(request, timeout=timeout)


class CentralContentClient:
    """뉴스·AI·테마 문서를 개인 중앙 서버와 교환한다."""

    def __init__(
        self, server_url: str, access_token: str, *, opener: Callable[..., Any] = urlopen,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._token = access_token
        self._opener = opener
        self._result_opener = _result_urlopen if opener is urlopen else opener
        self._timeout = timeout_seconds
        self._server_clock: tuple[datetime, float] | None = None

    def server_now(self) -> datetime:
        clock = self._server_clock
        return clock[0] + timedelta(seconds=monotonic() - clock[1]) if clock else kst_now()

    def _capture_server_clock(self, response) -> None:
        headers = getattr(response, "headers", None)
        raw = headers.get("X-Kiwoom-Server-Time") if headers is not None else None
        if not isinstance(raw, str):
            return
        try:
            self._server_clock = (aware_iso_kst(raw), monotonic())
        except (ValueError, OverflowError):
            return

    def load_health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def accept_research_result(self, document: dict[str, Any], *, expected_owner: str) -> dict[str, Any]:
        """Submit reported bytes with a pinned result-only principal, never admin."""
        from kiwoom_monitor.domain.research_contract import ReportedResearchResult
        value = ReportedResearchResult.from_document(document)
        return self._request("POST", "/api/v1/research/reported-results", value.document,
                             compact_json=True, expected_result_owner=expected_owner)

    def load_research_result(self, result_id: str, *, expected_owner: str) -> dict[str, Any]:
        """Read only the pinned authenticated producer's result namespace."""
        from urllib.parse import quote
        return self._request("GET", "/api/v1/research/reported-results/" + quote(result_id, safe=""),
                             expected_result_owner=expected_owner)

    def claim_news_body_job(self, *, expected_owner: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/news/body-jobs/claim", {}, expected_result_owner=expected_owner)

    def load_news_body_input(self, key: str, attempts: int, *, expected_owner: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/news/body-jobs/{quote(key, safe='')}/input?attempts={attempts}", expected_result_owner=expected_owner)

    def accept_news_body_result(self, key: str, envelope: dict, *, expected_owner: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/news/body-jobs/{quote(key, safe='')}/result", envelope,
                             compact_json=True, expected_result_owner=expected_owner)

    def load_news_body_receipt(self, key: str, attempts: int, *, expected_owner: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/news/body-jobs/{quote(key, safe='')}/receipt?attempts={attempts}", expected_result_owner=expected_owner)

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

    def load_current_theme_snapshot(self) -> tuple[dict[str, list[dict[str, Any]]], float | None]:
        """Read current collections from one server snapshot, without adopting history."""
        result = self._request("GET", "/api/v1/themes/current-snapshot")
        collections, completed_at = result.get("collections"), result.get("completed_at")
        if (not isinstance(collections, dict)
                or set(collections) != {"theme_profile", "theme_stock", "theme_metadata"}
                or any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
                       for rows in collections.values())
                or "completed_at" not in result
                or completed_at is not None and (
                    type(completed_at) not in (int, float) or not math.isfinite(completed_at))):
            raise RuntimeError("중앙 테마 스냅샷 응답 형식이 올바르지 않습니다.")
        return collections, completed_at

    @property
    def theme_sync_scope(self) -> str:
        parts = urlsplit(self._server_url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.rsplit("@", 1)[-1].lower(),
                           parts.path.rstrip("/"), "", ""))

    def load_accepted_theme_snapshot(self, *, snapshot_id: str | None = None) -> dict[str, Any] | None:
        query = "" if snapshot_id is None else "?" + urlencode({"snapshot_id": snapshot_id})
        try:
            result = self._request("GET", "/api/v1/themes/snapshot" + query)
        except CentralContentHttpError as error:
            if error.status_code == 409:
                raise CentralThemeConflict(error.status_code, error.detail) from error
            raise
        snapshot = result.get("snapshot")
        if (type(result.get("known")) is not bool
                or result["known"] != isinstance(snapshot, dict)
                or not result["known"] and snapshot is not None):
            raise RuntimeError("중앙 테마 수락본 응답 형식이 올바르지 않습니다.")
        return snapshot

    def publish_theme_snapshot(
        self, document: dict[str, Any], *, publication_id: str,
        expected_snapshot_id: str | None, effective_at: str | None, origin_device: str,
    ) -> dict[str, Any]:
        try:
            return self._request("PUT", "/api/v1/themes/snapshot", {
                "document": document, "publication_id": publication_id,
                "expected_snapshot_id": expected_snapshot_id,
                "effective_at": effective_at, "origin_device": origin_device,
            })
        except CentralContentHttpError as error:
            if error.status_code == 409:
                raise CentralThemeConflict(error.status_code, error.detail) from error
            raise

    def load_news_history(
        self, kind: str, *, target: str = "", identity: str = "",
        as_of: float | None = None, limit: int = 100, stock_code: str = "",
        before_revision_id: str = "",
    ) -> dict[str, Any]:
        query: dict[str, object] = {
            "target": target, "identity": identity, "limit": max(1, min(limit, 1000)),
        }
        if as_of is not None:
            query["as_of"] = max(0.0, float(as_of))
        if stock_code:
            query["stock_code"] = stock_code
        if before_revision_id:
            query["before_revision_id"] = before_revision_id
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

    def load_ram_shadow(self, *, include_recorded: bool = False) -> dict[str, Any]:
        query = urlencode({"include_recorded": "true" if include_recorded else "false"})
        return self._request("GET", f"/api/v1/research/ram-shadow?{query}", timeout_seconds=10.0)

    def check_ram_shadow_sources(self, *, expected_revision: int, lookback_seconds: int = 20) -> dict[str, Any]:
        if (type(expected_revision) is not int or expected_revision < 0
                or type(lookback_seconds) is not int or not 1 <= lookback_seconds <= 60):
            raise ValueError("source check requires an explicit revision and 1-60 second window")
        query = urlencode({"expected_revision": expected_revision, "lookback_seconds": lookback_seconds})
        return self._request("GET", f"/api/v1/research/ram-shadow/source-check?{query}", timeout_seconds=10.0)

    def check_ram_shadow_minute_sources(self, *, expected_revision: int, lookback_minutes: int) -> dict[str, Any]:
        if (type(expected_revision) is not int or expected_revision < 0
                or type(lookback_minutes) is not int or not 1 <= lookback_minutes <= 60):
            raise ValueError('source check requires a revision and 1-60 minute read budget')
        query = urlencode({'expected_revision':expected_revision, 'lookback_minutes':lookback_minutes})
        return self._request('GET', f'/api/v2/research/ram-shadow/source-check?{query}', timeout_seconds=10.0)

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

    def load_mock_automation_specs(self, account_ref: str, *, include_admission_refs: bool = False) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        return self._request(
            "GET", f"/api/v1/research/mock-automation-specs/{encoded_ref}"
            + ('?include_admission_refs=true' if include_admission_refs else ''),
        )

    def publish_mock_automation_account_selection(
        self, *, account_ref: str, credential_profile_id: str,
        expected_binding_revision: int, selection: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("POST", "/api/v1/research/mock-automation-account-selections", {
            "account_ref": account_ref, "credential_profile_id": credential_profile_id,
            "expected_binding_revision": expected_binding_revision, "selection": selection,
        })

    def load_mock_automation_account_selections(
        self, account_ref: str, *, credential_profile_id: str,
    ) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        query = urlencode({"credential_profile_id": credential_profile_id})
        return self._request(
            "GET", f"/api/v1/research/mock-automation-account-selections/{encoded_ref}?{query}",
        )

    def load_mock_automation_status(
        self, account_ref: str, *, credential_profile_id: str,
    ) -> dict[str, Any]:
        encoded_ref = quote(account_ref, safe="")
        query = urlencode({"credential_profile_id": credential_profile_id})
        return self._request(
            "GET", f"/api/v1/mock-automation/accounts/{encoded_ref}?{query}",
        )

    def load_selected_order_management(self, *, operation_id: str) -> dict[str, Any]:
        return self._request('GET', '/api/v1/mock-automation/selected-order-evidence/'
            + quote(operation_id, safe='') + '/management')

    def prepare_selected_order_management(self, *, operation_id: str, credential_profile_id: str,
            credential_revision: int, account_ref: str, binding_revision: int, packet_sha256: str,
            expected_settings_revision: int) -> dict[str, Any]:
        return self._request('POST', '/api/v1/mock-automation/selected-order-evidence/'
            + quote(operation_id, safe='') + '/prepare-management', dict(credential_profile_id=credential_profile_id,
                credential_revision=credential_revision, account_ref=account_ref,
                binding_revision=binding_revision, packet_sha256=packet_sha256,
                expected_settings_revision=expected_settings_revision))

    def accept_selected_order_management(self, *, operation_id: str, credential_profile_id: str,
            credential_revision: int, account_ref: str, binding_revision: int, packet_sha256: str,
            expected_control_revision: int, expected_settings_revision: int) -> dict[str, Any]:
        return self._request('POST', '/api/v1/mock-automation/selected-order-evidence/'
            + quote(operation_id, safe='') + '/accept-management', dict(credential_profile_id=credential_profile_id,
                credential_revision=credential_revision, account_ref=account_ref, binding_revision=binding_revision,
                packet_sha256=packet_sha256, expected_control_revision=expected_control_revision,
                expected_settings_revision=expected_settings_revision))

    def prepare_native_unfilled_population(self, research_database, selections, *, decision_time,
            horizon_seconds, population_ref, symbol, development_role, account_ref, credential_profile_id,
            spec_id, execution_run_id, expected_control_revision, root_intent_id, managed_intent_id):
        """Explicit PC worker read once; retain this packet for any HTTP retry."""
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.application.research_evaluation import (
            read_unfilled_action_outcomes,prepare_unfilled_population_publication)
        outcomes,receipt=read_unfilled_action_outcomes(
            ResearchRepository(research_database,read_only=True).load_position_action_evidence,selections,
            decision_time=decision_time,horizon_seconds=horizon_seconds,population_ref=population_ref,
            symbol=symbol,development_role=development_role)
        return prepare_unfilled_population_publication(outcomes,receipt,account_ref=account_ref,
            credential_profile_id=credential_profile_id,spec_id=spec_id,execution_run_id=execution_run_id,
            expected_control_revision=expected_control_revision,root_intent_id=root_intent_id,managed_intent_id=managed_intent_id)

    def publish_native_unfilled_population(self, document):
        """Send a prepared bounded packet; no SQLite read or timestamp renewal."""
        from kiwoom_monitor.application.research_evaluation import unfilled_population_publication_from_dict
        unfilled_population_publication_from_dict(document)
        return self._request('POST','/api/v1/mock-automation/unfilled-population',document)

    def prepare_dynamic_allocation_models(self, selection_document, rows, *, expected_selection_revision,position_studies=None):
        """Freeze prepared readonly receipts; no new source read or account change."""
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import prepare_dynamic_allocation_model_publication
        kwargs={'position_studies':position_studies} if position_studies is not None else {}
        return prepare_dynamic_allocation_model_publication(selection_document,rows,
            expected_selection_revision=expected_selection_revision,**kwargs)

    def publish_dynamic_allocation_models(self, document):
        """Existing authenticated transport sends the fixed packet without renewal."""
        return self._request('POST','/api/v1/research/ram-shadow/allocation-models',document)

    def start_mock_automation(
        self,
        *,
        account_ref: str,
        credential_profile_id: str,
        spec_id: str,
        expected_settings_revision: int,
        credential_revision: int,
        account_selection_id: str | None = None,
    ) -> dict[str, Any]:
        document = {
            "account_ref": account_ref,
            "credential_profile_id": credential_profile_id,
            "spec_id": spec_id,
            "expected_settings_revision": expected_settings_revision,
            "credential_revision": credential_revision,
        }
        if account_selection_id is not None:
            document["account_selection_id"] = account_selection_id
        return self._request("POST", "/api/v1/mock-automation/start", document)

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

    def replace_selection_mock_automation(self, *, account_ref: str, credential_profile_id: str, spec_id: str,
            account_selection_id: str, expected_control_revision: int, expected_settings_revision: int,
            credential_revision: int, reason: str) -> dict[str, Any]:
        return self._request('POST', '/api/v1/mock-automation/replace-selection', dict(
            account_ref=account_ref, credential_profile_id=credential_profile_id, spec_id=spec_id,
            account_selection_id=account_selection_id, expected_control_revision=expected_control_revision,
            expected_settings_revision=expected_settings_revision, credential_revision=credential_revision, reason=reason))

    def capabilities(self) -> dict[str, bool]:
        document = self._request("GET", "/api/v1/capabilities")
        values = document.get("capabilities", {})
        if not isinstance(values, dict):
            raise RuntimeError("중앙 서버 capability 응답 형식이 올바르지 않습니다.")
        return {str(key): bool(value) for key, value in values.items()}

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 *, timeout_seconds: float | None = None, compact_json: bool = False,
                 expected_result_owner: str | None = None) -> dict[str, Any]:
        with _http_operation_context(self._token) as request_id:
            if body is None:
                encoded = None
            elif compact_json:
                encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False).encode("utf-8")
            else:
                encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json;charset=UTF-8",
                       "X-Kiwoom-Clock": "1", "X-Request-ID": request_id}
            if expected_result_owner is not None:
                from kiwoom_monitor.domain.research_contract import encode_reported_result_owner
                headers["X-Kiwoom-Expected-Result-Owner"] = encode_reported_result_owner(expected_result_owner)
            request = Request(f"{self._server_url}{path}", method=method, data=encoded, headers=headers)
            opener = self._opener if expected_result_owner is None else self._result_opener
            try:
                with opener(request, timeout=self._timeout if timeout_seconds is None else timeout_seconds,
                                  context=system_ssl_context()) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    self._capture_server_clock(response)
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
