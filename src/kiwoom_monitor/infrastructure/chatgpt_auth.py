"""Windows SIWC credentials and loopback OAuth; no API-key vault or app DB.

One exclusive, cross-process lock covers token rotation and publication. Each
operation reloads the encrypted record. Tokens never appear in public status.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import _protect, _unprotect
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"
_TERMINAL_REFRESH = frozenset({"invalid_grant", "invalid_refresh_token", "token_expired",
    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"})
_ERROR_CODES = _TERMINAL_REFRESH | frozenset({"invalid_client", "access_denied",
    "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable",
    "subscription_sharing_user_not_eligible", "subscription_sharing_invalid_user",
    "subscription_sharing_user_unavailable", "subscription_sharing_unsupported_capability",
    "subscription_sharing_route_not_supported", "chatpass_v2_scope_not_authorized",
    "chatpass_v2_invalid_authorization_context"})
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_ROTATION_UNSAVED: set[str] = set()


class _ProtectedRecord(dict):
    def __repr__(self) -> str:
        return "<protected ChatGPT credential record>"


class ChatGPTPlanError(RuntimeError):
    """Only allowlisted diagnostics cross worker/UI/log boundaries."""
    def __init__(self, code: str, status_code: int = 0, *, body_shape: str = "", request_id: str = "", inference_attempted: bool = False, response_failure: str = ""):
        self.code = code if code in _ERROR_CODES or code in {
            "storage_unavailable", "storage_busy", "rotation_unsaved", "not_connected",
            "permission_required", "identity_invalid", "account_mismatch", "client_mismatch",
            "callback_invalid", "cancelled", "timeout", "network_error", "response_invalid",
            "stream_interrupted", "response_incomplete", "model_unavailable", "upstream_error",
            "browser_unavailable", "discovery_invalid", "provider_mismatch",
        } else "upstream_error"
        self.status_code = int(status_code)
        self.body_shape = body_shape if body_shape in {"error", "detail", "other"} else ""
        self.request_id = request_id if re.fullmatch(r"req_[A-Za-z0-9_-]{1,96}", request_id) else ""
        self.inference_attempted = bool(inference_attempted)
        self.identity_failure = ""
        self.response_diagnostics: dict = {}
        self.response_failure = response_failure if response_failure in {
            "stream_content_type", "event_json", "event_type_mismatch", "completed_status",
            "output_schema", "output_item", "message_status", "message_role",
            "message_content", "text_content", "empty_output", "usage_fields", "stream_size", "stream_encoding", "output_reconstruction",
        } else ""
        messages = {
            "permission_required": "로그인했지만 ChatGPT 구독 사용 권한이 없습니다. 연결에서 권한을 승인하세요.",
            "not_connected": "ChatGPT 연결이 필요합니다.",
            "subscription_sharing_usage_limit_exceeded": "ChatGPT 사용 한도에 도달했습니다. 사용량 관리에서 확인하세요.",
            "cancelled": "ChatGPT 연결을 취소했습니다.",
            "timeout": "ChatGPT 연결 시간이 초과됐습니다.",
            "rotation_unsaved": "갱신된 인증을 보호 저장하지 못했습니다. 다시 연결하세요.",
        }
        message = messages.get(self.code, "ChatGPT 요청을 완료하지 못했습니다. 연결·권한 상태를 확인하세요.")
        super().__init__(f"{message} [{self.code}; HTTP {self.status_code}]")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential through a redirect.


def open_request(request: Request, timeout: float = 30):
    return build_opener(_NoRedirect(), HTTPSHandler(context=system_ssl_context())).open(request, timeout=timeout)


def safe_request_id(headers, request: Request) -> str:
    value = str(headers.get("x-request-id") or headers.get("openai-request-id") or "") if headers else ""
    if not re.fullmatch(r"req_[A-Za-z0-9_-]{1,96}", value):
        return ""
    known = [str(request.get_header("Authorization", "")).removeprefix("Bearer ")]
    if request.get_header("Content-type", "") == "application/x-www-form-urlencoded" and request.data:
        fields = parse_qs(request.data.decode("ascii"))
        known.extend(fields.get(key, [""])[0] for key in ("code", "refresh_token", "token"))
    return "" if any(secret and secret in value for secret in known) else value


def _json_request(request: Request, opener=open_request) -> dict:
    try:
        with opener(request) as response:
            value = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except HTTPError as error:
        try:
            value = json.loads(error.read(65536))
            structured = value.get("error") if isinstance(value, dict) else None
            code = structured.get("code") if isinstance(structured, dict) else structured
            shape = "error" if structured is not None else "detail" if isinstance(value, dict) and "detail" in value else "other"
        except Exception:
            code, shape = "upstream_error", "other"
        finally:
            error.close()
        raise ChatGPTPlanError(str(code), error.code, body_shape=shape, request_id=safe_request_id(error.headers, request)) from None
    except ChatGPTPlanError:
        raise
    except (ValueError, UnicodeError):
        raise ChatGPTPlanError("response_invalid") from None
    except Exception:
        raise ChatGPTPlanError("network_error") from None


def _form_request(url: str, values: dict[str, str]) -> Request:
    return Request(url, data=urlencode(values).encode("ascii"), method="POST",
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})


def storage_path_for(config_path: Path) -> Path:
    # Separate app/config installations without exposing a local path in settings.
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise ChatGPTPlanError("storage_unavailable")
    profile = hashlib.sha256(str(config_path.resolve()).casefold().encode()).hexdigest()[:24]
    return Path(root) / "KiwoomMonitor" / "ChatGPTPlan" / profile / "credentials.dpapi"


class ChatGPTCredentialStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._key = str(self.path.resolve()).casefold()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self._key, threading.RLock())

    @contextmanager
    def locked(self):
        """A second process fails busy; it cannot reuse an old rotating token."""
        import msvcrt
        with self._lock:
            handle = None
            acquired = False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.with_suffix(".lock").open("a+b")
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    raise ChatGPTPlanError("storage_busy") from None
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                yield
            except ChatGPTPlanError:
                raise
            except Exception:
                raise ChatGPTPlanError("storage_unavailable") from None
            finally:
                if handle:
                    if acquired:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    handle.close()

    def load(self) -> dict:
        try:
            if not self.path.exists():
                return {"host_id": "", "active": "", "accounts": {}}
            value = json.loads(_unprotect(self.path.read_bytes()))
            if not isinstance(value, dict) or not isinstance(value.get("accounts"), dict):
                raise ValueError()
            for key, account in value["accounts"].items():
                value["accounts"][key] = _ProtectedRecord(account)
            return _ProtectedRecord(value)
        except Exception:
            raise ChatGPTPlanError("storage_unavailable") from None

    def save(self, value: dict) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            protected = _protect(json.dumps(value, separators=(",", ":")).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as target:
                target.write(protected)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except Exception:
            raise ChatGPTPlanError("storage_unavailable") from None
        finally:
            if temporary.exists():
                temporary.unlink()


def validate_identity(token: str, client_id: str, nonce: str | None, jwks: dict) -> dict:
    """Maintained google-auth verifier; accept only RS256 signing keys from JWKS."""
    stage = "verification_dependencies"
    try:
        from google.auth import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        stage = "token_header"
        header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        stage = "signature_algorithm"
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError()
        stage = "signing_key_lookup"
        keys = [key for key in jwks["keys"] if key.get("kid") == header["kid"]]
        if len(keys) != 1:
            raise ValueError()
        key = keys[0]
        stage = "signing_key_type"
        if key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256":
            raise ValueError()
        decode = lambda text: int.from_bytes(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)), "big")
        stage = "public_key"
        public = RSAPublicNumbers(decode(key["e"]), decode(key["n"])).public_key()
        pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        stage = "signature_or_registered_claims"
        # google-auth validates the signature and timestamps but its audience
        # comparison accepts only a scalar JWT aud. OIDC also permits arrays.
        claims = jwt.decode(token, certs={header["kid"]: pem}, clock_skew_in_seconds=5)
        stage = "audience"
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if (not isinstance(audiences, list) or not audiences
                or any(not isinstance(item, str) or not item for item in audiences)
                or client_id not in audiences):
            raise ValueError()
        stage = "authorized_party"
        if ((len(audiences) > 1 and claims.get("azp") != client_id)
                or ("azp" in claims and claims["azp"] != client_id)):
            raise ValueError()
        stage = "issuer"
        if claims.get("iss") != ISSUER:
            raise ValueError()
        stage = "subject"
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError()
        stage = "nonce"
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError()
        return claims
    except Exception:
        # Decode only to classify a rejected token. Never activate from these
        # unverified claims; expose fixed categories, no claim/token values.
        if stage == "signature_or_registered_claims":
            try:
                rejected = jwt.decode(token, verify=False)
                if float(rejected.get("exp", 0)) < time.time() - 5:
                    stage = "expired"
                elif float(rejected.get("iat", 0)) > time.time() + 5:
                    stage = "issued_in_future"
            except Exception:
                pass
        error = ChatGPTPlanError("identity_invalid")
        error.identity_failure = stage
        raise error from None


def _token_record(tokens: dict, *, client_id: str, host_id: str, claims: dict, old: dict | None = None) -> dict:
    try:
        expires = float(tokens["expires_in"])
        if (not math.isfinite(expires) or expires <= 0 or tokens.get("token_type", "").casefold() != "bearer"
                or not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]):
            raise ValueError()
        granted = tokens.get("scope")
        if not isinstance(granted, str):
            if old is None:
                raise ValueError()
            granted = " ".join(old["scopes"])
        if "offline_access" in granted.split() and (not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]):
            raise ValueError()
        now = time.time()
        return _ProtectedRecord({"client_id": client_id, "host_id": host_id, "issuer": ISSUER,
            "subject": claims["sub"], "email": str(claims.get("email", "")),
            "access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", ""),
            "id_token": tokens.get("id_token") or (old or {}).get("id_token", ""),
            "token_type": "Bearer", "scopes": granted.split(), "expires_in": expires,
            "expires_at": now + expires, "saved_at": now,
            "earliest_refresh_at": tokens.get("earliest_refresh_at"),
            "revision": int((old or {}).get("revision", 0)) + 1,
            "welcome_seen": bool((old or {}).get("welcome_seen", False))})
    except Exception:
        raise ChatGPTPlanError("response_invalid") from None


class ChatGPTAuth:
    def __init__(self, store: ChatGPTCredentialStore, *, opener=open_request, browser=webbrowser.open):
        self.store, self.opener, self.browser = store, opener, browser

    def _discovery(self) -> dict:
        value = _json_request(Request(ISSUER + "/.well-known/openid-configuration"), self.opener)
        if (value.get("issuer") != ISSUER
                or value.get("authorization_endpoint") != ISSUER + "/api/accounts/authorize"
                or value.get("token_endpoint") != ISSUER + "/api/accounts/oauth/token"
                or value.get("jwks_uri") != ISSUER + "/.well-known/jwks.json"):
            raise ChatGPTPlanError("discovery_invalid")
        return value

    def _claims(self, tokens: dict, client_id: str, nonce: str | None) -> dict:
        jwks = _json_request(Request(ISSUER + "/.well-known/jwks.json"), self.opener)
        return validate_identity(str(tokens.get("id_token", "")), client_id, nonce, jwks)

    def status(self) -> dict:
        # Read-only status never creates host credentials or refreshes a token.
        with self.store._lock:
            value = self.store.load()
        accounts = []
        for client_id, account in value["accounts"].items():
            label = f"{account.get('email') or 'ChatGPT 계정'} · {hashlib.sha256(client_id.encode()).hexdigest()[:8]}"
            accounts.append({"id": client_id, "label": label,
                "connected": bool(account.get("access_token")) and not account.get("refresh_pending", False),
                "permitted": DIRECT_SCOPE in account.get("scopes", [])})
        return {"active": value.get("active", ""), "accounts": accounts,
                "generation": int(value.get("generation", 0)),
                "pause_code": str(value.get("analysis_pause", ""))}

    def pause_analysis(self, code: str = "") -> None:
        with self.store.locked():
            value = self.store.load()
            value["analysis_pause"] = ChatGPTPlanError(code).code if code else ""
            self.store.save(value)

    def select(self, client_id: str) -> dict:
        with self.store.locked():
            value = self.store.load()
            if client_id not in value["accounts"]:
                raise ChatGPTPlanError("not_connected")
            value["active"] = client_id
            self.store.save(value)
        return self.status()

    def connect(self, cancel: threading.Event, *, account_id: str = "", timeout: float = 180) -> dict:
        if cancel.is_set():
            raise ChatGPTPlanError("cancelled")
        # Fail before a real browser login if verification dependencies are absent.
        try:
            from google.auth import jwt  # noqa: F401
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers  # noqa: F401
        except ImportError:
            raise ChatGPTPlanError("identity_invalid") from None
        with self.store.locked():
            value = self.store.load()
            if not value.get("host_id"):
                value["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                self.store.save(value)
            old = value["accounts"].get(account_id) if account_id else None
            if account_id and old is None:
                raise ChatGPTPlanError("not_connected")
            discovery = self._discovery()
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            result: dict = {}
            expected = account_id or value.get("pending_client_id") or "dynamic_agent_client"
            is_registration = expected == "dynamic_agent_client"

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    if parsed.path != "/auth/callback" or len(self.path) > 8192:
                        self.send_response(404)
                        self.end_headers()
                        return
                    try:
                        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
                        if any(len(items) != 1 for items in params.values()):
                            raise ChatGPTPlanError("callback_invalid")
                        fields = {key: items[0] for key, items in params.items()}
                        if not secrets.compare_digest(fields.get("state", ""), state):
                            raise ChatGPTPlanError("callback_invalid")
                        if fields.get("error"):
                            raise ChatGPTPlanError(fields["error"])
                        issued = fields.get("client_id", "") if is_registration else fields.get("client_id", expected)
                        if not issued or issued == "dynamic_agent_client" or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", issued):
                            raise ChatGPTPlanError("client_mismatch")
                        if not is_registration and issued != expected:
                            raise ChatGPTPlanError("client_mismatch")
                        if not fields.get("code"):
                            raise ChatGPTPlanError("callback_invalid")
                        result.update(client_id=issued, code=fields["code"])
                    except ChatGPTPlanError as error:
                        result["error"] = error
                    except Exception:
                        result["error"] = ChatGPTPlanError("callback_invalid")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    script = "history.replaceState(null, '', '/auth/done');"
                    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                    self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'sha256-{script_hash}'")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    try:
                        self.wfile.write(("<!doctype html><meta charset='utf-8'><script>" + script +
                            "</script><p>ChatGPT 확인을 마쳤습니다. 앱으로 돌아가세요.</p>").encode())
                    except OSError:
                        pass

            # Bind before opening browser; only this exact URI is redeemed.
            class LoopbackServer(HTTPServer):
                def get_request(self):
                    connection, address = super().get_request()
                    connection.settimeout(1)
                    return connection, address

                def handle_error(self, request, client_address):
                    pass  # Avoid traceback/URL/code diagnostics from callback handling.

            with LoopbackServer(("127.0.0.1", 0), Callback) as listener:
                listener.timeout = 0.2
                redirect = f"http://127.0.0.1:{listener.server_port}/auth/callback"
                params = {"client_id": expected, "ext_agent_host_id": value["host_id"],
                    "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                    "resource": RESOURCE, "state": state, "nonce": nonce,
                    "code_challenge_method": "S256", "code_challenge": challenge}
                if is_registration:
                    params["agent_name_hint"] = "Kiwoom Realtime Monitor"
                elif old and old.get("email"):
                    params["login_hint"] = old["email"]
                # Deliberately omit optional id_token_hint to keep browser URLs token-free.
                if old and DIRECT_SCOPE not in old.get("scopes", []):
                    params["prompt"] = "consent"  # Explicit user action enabling plan use.
                if cancel.is_set():
                    raise ChatGPTPlanError("cancelled")
                try:
                    opened = self.browser(discovery["authorization_endpoint"] + "?" + urlencode(params))
                except Exception:
                    raise ChatGPTPlanError("browser_unavailable") from None
                if not opened:
                    raise ChatGPTPlanError("browser_unavailable")
                deadline = time.monotonic() + timeout
                while not result:
                    if cancel.is_set():
                        raise ChatGPTPlanError("cancelled")
                    if time.monotonic() >= deadline:
                        raise ChatGPTPlanError("timeout")
                    listener.handle_request()
            if cancel.is_set():
                raise ChatGPTPlanError("cancelled")
            if "error" in result:
                raise result["error"]
            issued = result["client_id"]
            # Registration is distinct from a validated active account. Keep the
            # issued ID after invalid_grant without trusting unverified identity.
            if not old:
                value["pending_client_id"] = issued
                self.store.save(value)
            tokens = _json_request(_form_request(discovery["token_endpoint"], {
                "grant_type": "authorization_code", "client_id": issued, "code": result.pop("code"),
                "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE}), self.opener)
            claims = self._claims(tokens, issued, nonce)
            existing = value["accounts"].get(issued)
            if (old or existing) and claims["sub"] != (old or existing)["subject"]:
                raise ChatGPTPlanError("account_mismatch")
            if cancel.is_set():
                raise ChatGPTPlanError("cancelled")
            value["accounts"][issued] = _token_record(tokens, client_id=issued, host_id=value["host_id"], claims=claims, old=old or existing)
            value["active"] = issued
            value.pop("pending_client_id", None)
            self.store.save(value)
            _ROTATION_UNSAVED.discard(self.store._key)
        return self.status()

    @contextmanager
    def session(self):
        """Hold ownership through inference so switching/sign-out drains the request."""
        with self.store.locked():
            if self.store._key in _ROTATION_UNSAVED:
                raise ChatGPTPlanError("rotation_unsaved")
            value = self.store.load()
            account = value["accounts"].get(value.get("active"))
            if not account or not account.get("access_token"):
                raise ChatGPTPlanError("not_connected")
            if account.get("refresh_pending"):
                raise ChatGPTPlanError("rotation_unsaved")
            if DIRECT_SCOPE not in account.get("scopes", []):
                raise ChatGPTPlanError("permission_required")
            if float(account["expires_at"]) <= time.time() + 30:
                if not account.get("refresh_token"):
                    raise ChatGPTPlanError("not_connected")
                # Durable intent fences a crash/ambiguous response during rotation.
                account["refresh_pending"] = True
                self.store.save(value)
                try:
                    tokens = _json_request(_form_request(ISSUER + "/api/accounts/oauth/token", {
                        "grant_type": "refresh_token", "client_id": account["client_id"],
                        "refresh_token": account["refresh_token"], "resource": RESOURCE}), self.opener)
                except ChatGPTPlanError as error:
                    if error.code in _TERMINAL_REFRESH:
                        for key in ("access_token", "refresh_token", "id_token"):
                            account[key] = ""
                        account["refresh_pending"] = False
                        self.store.save(value)
                    elif error.status_code:
                        account["refresh_pending"] = False
                        self.store.save(value)
                    raise
                # Rotation happened upstream: any validation/publication failure fences reuse.
                _ROTATION_UNSAVED.add(self.store._key)
                claims = self._claims(tokens, account["client_id"], None) if tokens.get("id_token") else {
                    "sub": account["subject"], "email": account["email"]}
                if claims["sub"] != account["subject"]:
                    raise ChatGPTPlanError("account_mismatch")
                account = _token_record(tokens, client_id=account["client_id"], host_id=value["host_id"], claims=claims, old=account)
                value["accounts"][value["active"]] = account
                try:
                    self.store.save(value)
                except ChatGPTPlanError:
                    raise ChatGPTPlanError("rotation_unsaved") from None
                _ROTATION_UNSAVED.discard(self.store._key)
            if DIRECT_SCOPE not in account["scopes"]:
                raise ChatGPTPlanError("permission_required")
            yield account

    def welcome_seen(self) -> bool:
        with self.store.locked():
            value = self.store.load()
            account = value["accounts"].get(value.get("active"), {})
            seen = bool(account.get("welcome_seen"))
            if account and DIRECT_SCOPE in account.get("scopes", []):
                account["welcome_seen"] = True
                self.store.save(value)
            return seen

    def disconnect(self) -> dict:
        confirmed = False
        with self.store.locked():
            value = self.store.load()
            account = value["accounts"].get(value.get("active"))
            if account and account.get("refresh_token"):
                try:
                    endpoint = self._discovery().get("revocation_endpoint", "")
                    parsed = urlsplit(endpoint)
                    if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.query or parsed.fragment:
                        raise ChatGPTPlanError("discovery_invalid")
                    request = _form_request(endpoint, {"token": account["refresh_token"],
                        "token_type_hint": "refresh_token", "client_id": account["client_id"]})
                    with self.opener(request) as response:
                        confirmed = response.status == 200
                except Exception:
                    confirmed = False
            if account:
                for key in ("access_token", "refresh_token", "id_token"):
                    account[key] = ""
                self.store.save(value)
                _ROTATION_UNSAVED.discard(self.store._key)
        return {**self.status(), "revocation_confirmed": confirmed}
