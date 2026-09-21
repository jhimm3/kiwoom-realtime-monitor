"""Manual NAS credentials; secrets never enter PC configuration or mirrors."""
from __future__ import annotations

import json
import re
import uuid
from threading import RLock
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener

from .central_server_config import DataSourceSettings
from .system_ssl import system_ssl_context


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CentralCredentialsClient:
    """Own HTTPS transport and safe request IDs across timeout/dialog reopening."""

    GLOBAL_FIELDS = {"naver": ("client_id", "client_secret"), "dart": ("api_key",),
                     "openai": ("api_key",), "gemini": ("api_key",), "claude": ("api_key",)}
    ACCOUNT_ENVIRONMENTS = {"kiwoom_mock": "mock", "kiwoom_real": "real"}

    def __init__(self, source: DataSourceSettings, *, provider="kiwoom_mock"):
        if provider not in self.ACCOUNT_ENVIRONMENTS and provider not in self.GLOBAL_FIELDS:
            raise ValueError("지원하지 않는 NAS 공급자입니다.")
        self.provider = provider
        self.source = source
        self._lock = RLock()
        self._prepare_request = None  # profile/revision/disable/request_id only
        self._create_request = None  # label/request_id only
        self.operation_id = None
        self.operation_profile = None
        self.operation_state = None
        self._operation_revision = None
        self._target_ref = None

    @property
    def pending_profile(self):
        if self._prepare_request is not None: return self._prepare_request[0]
        if self.operation_state in {"VALIDATING", "READY", "DRAINING", "COMMITTING", "BUSY"}:
            return self.operation_profile
        return None

    def _validate_source(self):
        try:
            parsed = urlsplit(self.source.server_url)
            valid = (self.source.mode == "personal_server" and parsed.scheme == "https"
                     and parsed.hostname and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment and self.source.access_token)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError("NAS 모드의 HTTPS 주소와 접속 토큰이 필요합니다.")

    @staticmethod
    def _profile(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
            raise ValueError("연결 프로필이 올바르지 않습니다.")
        return value

    @staticmethod
    def _revision(value):
        if type(value) is not int or not 0 <= value < 2**63:
            raise ValueError("설정 버전을 다시 확인하세요.")
        return value

    def _request(self, method, path, body=None):
        self._validate_source()  # Before serializing or sending credentials.
        with self._lock:
            request = Request(self.source.server_url.rstrip("/") + path, method=method,
                headers={"Authorization": "Bearer " + self.source.access_token, "Content-Type": "application/json"},
                data=json.dumps(body).encode("utf-8") if body is not None else None)
            try:
                opener = build_opener(_NoRedirect(), HTTPSHandler(context=system_ssl_context()))
                with opener.open(request, timeout=10) as response:
                    if response.geturl() != request.full_url:
                        raise RuntimeError("redirect")
                    content = response.read(1_048_577)
                    if len(content) > 1_048_576: raise ValueError("oversized")
                    result = json.loads(content)
                if not isinstance(result, dict): raise ValueError("invalid response")
                return result
            except HTTPError as error:
                if 300 <= error.code < 400:
                    message = "NAS 주소가 이동되었습니다. HTTPS 주소를 직접 확인하세요."
                elif error.code == 409:
                    message = "NAS 설정 또는 요청이 충돌했습니다. 상태를 다시 확인하세요."
                elif error.code == 401:
                    message = "NAS 접속 토큰을 확인하세요."
                else:
                    message = f"NAS 요청을 처리하지 못했습니다. HTTP {error.code}"
                raise RuntimeError(message) from None
            except Exception:
                raise RuntimeError("NAS 응답을 확인하지 못했습니다. 새로 적용하지 말고 요청 상태를 확인하세요.") from None

    def load(self):
        caps = self._request("GET", "/api/v1/capabilities")
        if caps.get("capabilities", {}).get("runtime_credentials_v1") is not True:
            raise RuntimeError("이 NAS 버전은 실행 중 API 설정을 지원하지 않습니다.")
        result = self._request("GET", "/api/v1/settings/credentials")
        if not isinstance(result.get("profiles"), list) or not isinstance(result.get("providers"), list):
            raise RuntimeError("NAS 계좌 목록 형식이 올바르지 않습니다.")
        fields = {"provider", "profile_id", "label", "lifecycle_state", "configured", "supported",
                  "revision", "runtime", "disabled", "account_ref", "validation", "runtime_validation"}
        return {"profiles": [{k: v for k, v in p.items() if k in fields}
                             for p in result["profiles"] if isinstance(p, dict)],
                "providers": [{"provider": p.get("provider"), "supported": p.get("supported") is True}
                              for p in result["providers"] if isinstance(p, dict)]}

    def create_mock_profile(self, label):
        if self.provider != "kiwoom_mock":
            raise ValueError("모의계좌 공급자를 확인하세요.")
        return self.create_account_profile(label)

    def create_account_profile(self, label):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자에서만 새 계좌를 추가할 수 있습니다.")
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("새 계좌 이름을 입력하세요. 최대 120자입니다.")
        with self._lock:
            if self._create_request is None: self._create_request = (label, str(uuid.uuid4()))
            if self._create_request[0] != label:
                raise RuntimeError("이전 계좌 추가 응답을 확인하려면 같은 이름으로 다시 요청하세요.")
            result = self._request("POST", f"/api/v1/settings/credentials/{self.provider}/profiles",
                {"request_id": self._create_request[1], "label": label})
            self._profile(result.get("profile_id"))
            self._create_request = None
            return result

    def delete_account_profile(self, profile, revision):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자를 확인하세요.")
        self._profile(profile); self._revision(revision)
        result = self._request(
            "DELETE", f"/api/v1/settings/credentials/{self.provider}/profiles/{profile}",
            {"expected_revision": revision},
        )
        if result != {"provider": self.provider, "profile_id": profile,
                      "lifecycle_state": "archived"}:
            raise RuntimeError("NAS 계좌 삭제 결과를 확인하지 못했습니다.")
        return result

    def rename_account_profile(self, profile, revision, label):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자를 확인하세요.")
        self._profile(profile); self._revision(revision)
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > 120:
            raise ValueError("계좌 이름을 입력하세요. 최대 120자입니다.")
        result = self._request(
            "PUT", f"/api/v1/settings/credentials/{self.provider}/profiles/{profile}",
            {"expected_revision": revision, "label": label},
        )
        if result != {"provider": self.provider, "profile_id": profile, "label": label}:
            raise RuntimeError("NAS 계좌 이름 변경 결과를 확인하지 못했습니다.")
        return result

    def prepare_mock(self, profile, revision, app_key="", secret_key="", *, disabled=False):
        if self.provider != "kiwoom_mock":
            raise ValueError("모의계좌 공급자를 확인하세요.")
        return self.prepare_account(profile, revision, app_key, secret_key, disabled=disabled)

    def prepare_account(self, profile, revision, app_key="", secret_key="", *, disabled=False):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자를 확인하세요.")
        self._profile(profile); self._revision(revision)
        if type(disabled) is not bool or any(not isinstance(value, str) or len(value) > 4096
                for value in (app_key, secret_key)) or (not disabled and (not app_key.strip() or not secret_key.strip())):
            raise ValueError("App Key와 Secret Key를 모두 입력하세요.")
        return self._prepare(profile, revision,
            {} if disabled else {"app_key": app_key.strip(), "secret_key": secret_key.strip()}, disabled)

    def prepare_global(self, revision, replacement=None, *, disabled=False):
        fields = self.GLOBAL_FIELDS.get(self.provider)
        self._revision(revision)
        if fields is None or type(disabled) is not bool:
            raise ValueError("뉴스·AI 공급자를 확인하세요.")
        replacement = {} if replacement is None else replacement
        if not isinstance(replacement, dict) or (disabled and replacement) or (not disabled and (
                set(replacement) != set(fields) or any(not isinstance(replacement[key], str)
                    or not replacement[key].strip() or len(replacement[key]) > 4096 for key in fields))):
            raise ValueError("공급자 인증정보를 모두 입력하세요.")
        return self._prepare(f"nas-{self.provider}-default", revision,
            {key: value.strip() for key, value in replacement.items()}, disabled)

    def _prepare(self, profile, revision, replacement, disabled):
        with self._lock:
            identity = (profile, revision, disabled)
            if self.operation_id is not None and self.pending_profile is not None:
                raise RuntimeError("진행 중인 요청의 상태를 먼저 확인하세요.")
            if self._prepare_request is not None and self._prepare_request[:3] != identity:
                raise RuntimeError("이전 준비 요청을 먼저 확인하세요.")
            if self._prepare_request is None: self._prepare_request = (*identity, str(uuid.uuid4()))
            self.operation_id = None
            self._operation_revision, self._target_ref = revision, None
            self.operation_profile, self.operation_state = profile, "VALIDATING"
            body = {"request_id": self._prepare_request[3], "expected_revision": revision}
            if disabled: body["disable"] = True
            else: body["replacement"] = replacement
            result = self._request("POST", f"/api/v1/settings/credentials/{self.provider}/profiles/{profile}/prepare", body)
            self._accept_operation(result)
            operation_id = str(uuid.UUID(result["operation_id"]))
            self.operation_id, self.operation_profile = operation_id, profile
            self.operation_state = result.get("state")
            self._prepare_request = None
            return result

    def _accept_operation(self, result):
        try:
            operation_id = str(uuid.UUID(result["operation_id"]))
            revision = result.get("expected_revision")
            if revision is None and result.get("committed") is True:
                revision = self._revision(result["revision"]) - 1
            valid = (result.get("provider") == self.provider
                and result.get("profile_id") == self.operation_profile
                and type(revision) is int and revision == self._operation_revision
                and (self.operation_id is None or operation_id == self.operation_id)
                and result.get("state") in {"VALIDATING", "READY", "DRAINING", "COMMITTING", "BUSY",
                    "ACTIVE", "FAILED", "CONFLICT", "EXPIRED", "CANCELLED", "RECOVERY_REQUIRED"})
            target = result.get("target_account_ref")
            if target is not None: target = str(uuid.UUID(target))
            if self.provider in self.ACCOUNT_ENVIRONMENTS:
                if result.get("state") == "READY" and not target: valid = False
            elif target is not None or self.operation_profile != f"nas-{self.provider}-default":
                valid = False
            elif result.get("state") == "READY" and result.get("validation") not in (
                    {"VERIFIED", "UNVERIFIED"} if self.provider in {"openai", "gemini", "claude"} else {"VERIFIED"}):
                valid = False
            if self._target_ref is not None and target is not None and self._target_ref != target: valid = False
            if not valid: raise ValueError("context")
        except (KeyError, ValueError, TypeError, AttributeError):
            raise RuntimeError("NAS 요청의 계좌 또는 설정 버전이 일치하지 않습니다.") from None
        if target is not None: self._target_ref = target
        self.operation_state = result["state"]
        return result

    def status(self):
        if not self.operation_id: raise ValueError("확인할 요청이 없습니다.")
        result = self._request("GET", "/api/v1/settings/credential-operations/" + self.operation_id)
        return self._accept_operation(result)

    def apply(self, revision, account_ref):
        self._revision(revision)
        if not self.operation_id: raise ValueError("API 키 확인을 먼저 진행하세요.")
        if self.provider in self.ACCOUNT_ENVIRONMENTS:
            target = str(uuid.UUID(account_ref))
        else:
            if account_ref is not None:
                raise ValueError("뉴스·AI 공급자에는 계좌 대상을 지정할 수 없습니다.")
            target = None
        if self.operation_state != "READY" or revision != self._operation_revision or target != self._target_ref:
            raise ValueError("확인한 계좌와 설정 버전으로만 적용할 수 있습니다.")
        self.operation_state = "DRAINING"
        return self._accept_operation(self._request("POST", "/api/v1/settings/credential-operations/" + self.operation_id + "/apply",
            {"expected_revision": revision, "target_account_ref": target}))

    def cancel(self):
        if not self.operation_id: raise ValueError("확인할 요청이 없습니다.")
        return self._accept_operation(self._request("DELETE", "/api/v1/settings/credential-operations/" + self.operation_id))

    def account_settings(self, account_ref):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자를 확인하세요.")
        target = str(uuid.UUID(account_ref))
        return self._accept_settings(self._request("GET", "/api/v1/settings/accounts/" + target + "?" + urlencode({"environment": self.ACCOUNT_ENVIRONMENTS[self.provider]})), target)

    def _accept_settings(self, result, target):
        document = result.get("settings")
        if not isinstance(document, dict) or document.get("scope") != {
                "broker": "kiwoom", "environment": self.ACCOUNT_ENVIRONMENTS[self.provider], "account_ref": target}:
            raise RuntimeError("NAS 계좌 설정의 대상이 일치하지 않습니다.")
        self._revision(document.get("revision"))
        if type(document.get("monitor_enabled")) is not bool or type(document.get("mock_order_enabled")) is not bool:
            raise RuntimeError("NAS 계좌 설정 형식이 올바르지 않습니다.")
        if self.provider == "kiwoom_real" and document["mock_order_enabled"]:
            raise RuntimeError("실전 계좌의 모의주문 설정이 올바르지 않습니다.")
        return result

    def update_account(self, account_ref, profile, revision, *, monitor_enabled, mock_order_enabled):
        if self.provider not in self.ACCOUNT_ENVIRONMENTS:
            raise ValueError("계좌 공급자를 확인하세요.")
        self._profile(profile); self._revision(revision)
        if type(monitor_enabled) is not bool or type(mock_order_enabled) is not bool:
            raise ValueError("계좌 설정을 다시 확인하세요.")
        if self.provider == "kiwoom_real" and mock_order_enabled:
            raise ValueError("실전 계좌에는 모의주문을 허용할 수 없습니다.")
        if mock_order_enabled and not monitor_enabled:
            raise ValueError("계좌 조회를 켠 뒤 모의주문을 허용하세요.")
        target = str(uuid.UUID(account_ref))
        return self._accept_settings(self._request("PUT", "/api/v1/settings/accounts/" + target + "?" + urlencode({"environment": self.ACCOUNT_ENVIRONMENTS[self.provider]}),
            {"expected_revision": revision, "active_profile_id": profile,
             "monitor_enabled": monitor_enabled, "mock_order_enabled": mock_order_enabled}), target)
