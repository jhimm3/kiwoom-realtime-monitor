from __future__ import annotations

import base64
import copy
import io
import json
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt

from kiwoom_monitor.infrastructure.chatgpt_auth import (
    ChatGPTAuth, ChatGPTCredentialStore, ChatGPTPlanError, DIRECT_SCOPE,
    ISSUER, RESOURCE, _ROTATION_UNSAVED, validate_identity,
)
from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient, GroundedResponse, _completed_response
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig, NaverNewsCredentials, NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import analyze_articles, analysis_body_hash
from kiwoom_monitor.infrastructure.central_ai_client import CentralAIClient


def event(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


def news_result(summary="요약"):
    return {"summary": summary, "category": "수주·계약", "outlook": "긍정", "confidence": 80,
            "reason": "계약 근거", "positive_evidence": ["수주"], "negative_evidence": [],
            "company_impacts": [{"company": "시험기업", "outlook": "긍정", "confidence": 80, "reason": "매출"}],
            "theme_candidates": [{"name": "로봇산업", "confidence": 70, "evidence": "복수 기업 참여"}]}


class Response(io.BytesIO):
    def __init__(self, value, *, stream=False, status=200):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.status = status
        self.headers = {"Content-Type": "text/event-stream" if stream else "application/json"}


class StreamDeadlineTests(unittest.TestCase):
    def test_unlimited_stream_accepts_completion_without_a_wall_clock_deadline(self):
        payload = {"status": "completed", "output": []}
        with patch("kiwoom_monitor.infrastructure.chatgpt_plan.time.monotonic", side_effect=AssertionError("unexpected deadline")):
            result = _completed_response(Response(event("response.completed", response=payload), stream=True),
                                         None, stream_timeout=None)
        self.assertEqual(result["status"], "completed")

    def test_default_stream_still_expires_at_its_existing_deadline(self):
        with patch("kiwoom_monitor.infrastructure.chatgpt_plan.time.monotonic", side_effect=[0, 181]):
            with self.assertRaises(ChatGPTPlanError) as caught:
                _completed_response(Response(b"", stream=True), None)
        self.assertEqual(caught.exception.code, "timeout")

    def test_unlimited_stream_still_honors_cancellation_and_broken_streams(self):
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(ChatGPTPlanError) as caught:
            _completed_response(Response(b"", stream=True), cancelled, stream_timeout=None)
        self.assertEqual(caught.exception.code, "cancelled")
        with self.assertRaises(ChatGPTPlanError) as caught:
            _completed_response(Response(b"", stream=True), None, stream_timeout=None)
        self.assertEqual(caught.exception.code, "stream_interrupted")

    def test_research_timeout_override_keeps_web_search_and_default_is_600(self):
        client = ChatGPTPlanClient(object())
        with patch.object(client, "_respond", return_value=GroundedResponse("{}", {})) as respond:
            client.research("model-b", "prompt")
            self.assertEqual(respond.call_args.kwargs["stream_timeout"], 600)
            client.research("model-b", "prompt", stream_timeout=None)
            self.assertIsNone(respond.call_args.kwargs["stream_timeout"])
            self.assertTrue(respond.call_args.kwargs["web_search"])


class ChatGPTPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cls.signer = crypt.RSASigner.from_string(pem, key_id="test-key")
        public = cls.key.public_key().public_numbers()
        encode = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")
        cls.jwks = {"keys": [{"kid": "test-key", "kty": "RSA", "alg": "RS256", "use": "sig", "n": encode(public.n), "e": encode(public.e)}]}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ChatGPTCredentialStore(Path(self.temp.name) / "oauth" / "credentials.dpapi")
        self.auth = ChatGPTAuth(self.store, opener=self.open, browser=self.browser)
        self.client = ChatGPTPlanClient(self.auth)
        self.nonce = ""
        self.issued = "oaiapp_" + secrets.token_hex(8)
        self.secret = secrets.token_urlsafe(32)
        self.calls = []
        self.threads = []
        self.addCleanup(self.join_threads)
        self.callback_override = {}
        self.scope = "openid offline_access " + DIRECT_SCOPE
        self.claim_changes = {}
        self.refresh_count = 0
        self.final = {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(news_result(), ensure_ascii=False)}]}],
            "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60}}
        self.stream = None

    def join_threads(self):
        for thread in self.threads:
            thread.join(3)

    def identity(self, **changes):
        claims = {"iss": ISSUER, "aud": self.issued, "sub": "test-subject", "email": "fake@example.invalid",
                  "nonce": self.nonce, "iat": int(time.time()) - 1, "exp": int(time.time()) + 300}
        claims.update(changes)
        return jwt.encode(self.signer, claims).decode()

    def tokens(self):
        return {"access_token": self.secret, "refresh_token": secrets.token_urlsafe(32), "id_token": self.identity(**self.claim_changes),
                "token_type": "Bearer", "expires_in": 3600, "scope": self.scope, "earliest_refresh_at": int(time.time()) + 3500}

    def open(self, request):
        self.calls.append(request)
        path = urlsplit(request.full_url).path
        if path == "/.well-known/openid-configuration":
            return Response({"issuer": ISSUER, "authorization_endpoint": ISSUER + "/api/accounts/authorize",
                "token_endpoint": ISSUER + "/api/accounts/oauth/token", "jwks_uri": ISSUER + "/.well-known/jwks.json",
                "revocation_endpoint": ISSUER + "/api/accounts/oauth/revoke"})
        if path == "/.well-known/jwks.json":
            return Response(self.jwks)
        if path == "/api/accounts/oauth/token":
            form = parse_qs(request.data.decode())
            if form["grant_type"] == ["refresh_token"]:
                self.refresh_count += 1
            return Response(self.tokens())
        if path == "/api/accounts/oauth/revoke":
            return Response(b"")
        if path == "/v1/models":
            return Response({"models": [
                {"visibility": "list", "display_name": "Model B", "slug": "model-b"},
                {"visibility": "hidden", "display_name": "Hidden", "slug": "hidden"},
                {"visibility": "list", "display_name": "Model A", "slug": "model-a"}]})
        if path == "/v1/responses":
            return Response(self.stream if self.stream is not None else event("response.completed", response=self.final), stream=True)
        raise AssertionError("unexpected route")

    def browser(self, url):
        query = parse_qs(urlsplit(url).query)
        self.authorization = query
        self.nonce = query["nonce"][0]
        callback = {"code": secrets.token_urlsafe(32), "state": query["state"][0], "client_id": self.issued}
        callback.update(self.callback_override)
        def send():
            with urlopen(query["redirect_uri"][0] + "?" + urlencode(callback), timeout=3) as response:
                response.read()
        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        return True

    def connect(self):
        return self.auth.connect(threading.Event(), timeout=2)

    def expire(self):
        with self.store.locked():
            value = self.store.load()
            value["accounts"][value["active"]]["expires_at"] = time.time() - 1
            self.store.save(value)

    def test_research_transport_returns_open_page_sources_and_action_receipt(self):
        self.connect()
        url = 'https://example.test/opened'
        self.final['output'].insert(0, {'type': 'web_search_call', 'status': 'completed',
            'action': {'type': 'open_page', 'url': url}})
        reply = self.client.research('model-b', 'research prompt')
        self.assertEqual(({'url': url, 'title': ''},), reply.sources)
        self.assertEqual(({'type': 'open_page', 'urls': [url]},), reply.web_actions)
        self.assertEqual(1, reply.search_calls)
        posts = [r for r in self.calls if r.full_url.endswith('/responses')]
        self.assertEqual(1, len(posts))
        self.assertEqual(['web_search_call.action.sources'], json.loads(posts[0].data)['include'])

    def test_dynamic_registration_exact_redirect_pkce_and_protected_storage(self):
        status = self.connect()
        self.assertEqual(status["active"], self.issued)
        self.assertEqual(self.authorization["client_id"], ["dynamic_agent_client"])
        form = next(parse_qs(req.data.decode()) for req in self.calls if req.full_url.endswith("/oauth/token"))
        self.assertEqual(form["client_id"], [self.issued])
        self.assertEqual(form["redirect_uri"], self.authorization["redirect_uri"])
        self.assertIn("127.0.0.1:", form["redirect_uri"][0])
        import hashlib
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).decode().rstrip("=")
        self.assertEqual(self.authorization["code_challenge"], [challenge])
        self.assertNotIn(self.secret.encode(), self.store.path.read_bytes())
        self.assertNotIn(self.secret, repr(status))
        self.assertNotIn(self.secret, repr(self.store.load()))
        with self.auth.session() as account:
            self.assertNotIn(self.secret, repr(account))

    def test_reauthorization_reuses_host_and_issued_client_with_fresh_attempt(self):
        self.connect()
        first = copy.deepcopy(self.authorization)
        self.auth.connect(threading.Event(), account_id=self.issued, timeout=2)
        self.assertEqual(self.authorization["client_id"], [self.issued])
        self.assertEqual(self.authorization["ext_agent_host_id"], first["ext_agent_host_id"])
        self.assertNotIn("agent_name_hint", self.authorization)
        self.assertNotIn("id_token_hint", self.authorization)
        for key in ("state", "nonce", "code_challenge"):
            self.assertNotEqual(self.authorization[key], first[key])

    def test_callback_failures_never_exchange_or_activate(self):
        for changes, code in (({"state": "wrong"}, "callback_invalid"),
                              ({"error": "access_denied"}, "access_denied"),
                              ({"client_id": ""}, "client_mismatch"),
                              ({"client_id": "dynamic_agent_client"}, "client_mismatch")):
            with self.subTest(code=code):
                self.callback_override = changes
                self.calls.clear()
                with self.assertRaises(ChatGPTPlanError) as error:
                    self.connect()
                self.assertEqual(error.exception.code, code)
                self.assertFalse(any(req.full_url.endswith("/oauth/token") for req in self.calls))
                self.assertEqual(self.auth.status()["active"], "")

    def test_returning_client_mismatch_preserves_existing_account(self):
        self.connect()
        self.callback_override = {"client_id": "different"}
        with self.assertRaises(ChatGPTPlanError) as error:
            self.auth.connect(threading.Event(), account_id=self.issued, timeout=2)
        self.assertEqual(error.exception.code, "client_mismatch")
        self.assertTrue(self.auth.status()["accounts"][0]["connected"])

    def test_id_token_signature_identity_claim_and_expiry_rejections(self):
        self.nonce = secrets.token_urlsafe(32)
        for changes in ({"iss": "https://wrong.invalid"}, {"aud": "wrong"}, {"nonce": "wrong"},
                        {"exp": int(time.time()) - 60}, {"sub": ""}, {"iat": int(time.time()) + 60}):
            with self.subTest(claims=list(changes)):
                with self.assertRaises(ChatGPTPlanError):
                    validate_identity(self.identity(**changes), self.issued, self.nonce, self.jwks)
        token = self.identity()
        pieces = token.split(".")
        pieces[2] = base64.urlsafe_b64encode(secrets.token_bytes(256)).decode().rstrip("=")
        with self.assertRaises(ChatGPTPlanError):
            validate_identity(".".join(pieces), self.issued, self.nonce, self.jwks)
        with self.assertRaises(ChatGPTPlanError):
            validate_identity(token, self.issued, self.nonce, {"keys": []})

    def test_identity_diagnostics_never_disclose_rejected_claim_values(self):
        self.nonce = secrets.token_urlsafe(32)
        for changes, category in (({"iss": self.secret}, "issuer"),
                                  ({"nonce": self.secret}, "nonce")):
            with self.assertRaises(ChatGPTPlanError) as error:
                validate_identity(self.identity(**changes), self.issued, self.nonce, self.jwks)
            self.assertEqual(error.exception.identity_failure, category)
            self.assertNotIn(self.secret, repr(error.exception.__dict__))

    def test_verified_oidc_audience_arrays_require_issued_client_and_authorized_party(self):
        self.nonce = secrets.token_urlsafe(32)
        for audience, extra in ((self.issued, {}), ([self.issued], {}),
                                ([self.issued, "other-resource"], {"azp": self.issued})):
            claims = validate_identity(self.identity(aud=audience, **extra), self.issued, self.nonce, self.jwks)
            self.assertEqual(claims["sub"], "test-subject")
        for changes in ({"aud": ["other-client"]}, {"aud": []}, {"aud": [self.issued, 1]},
                        {"aud": [self.issued, "other-resource"]},
                        {"aud": [self.issued], "azp": "other-client"}):
            with self.assertRaises(ChatGPTPlanError):
                validate_identity(self.identity(**changes), self.issued, self.nonce, self.jwks)

    def test_account_mismatch_does_not_replace_tokens(self):
        self.connect()
        self.claim_changes = {"sub": "different-account"}
        with self.assertRaises(ChatGPTPlanError) as error:
            self.auth.connect(threading.Event(), account_id=self.issued, timeout=2)
        self.assertEqual(error.exception.code, "account_mismatch")
        self.assertEqual(self.store.load()["accounts"][self.issued]["subject"], "test-subject")

    def test_partial_permission_is_login_without_inference(self):
        self.scope = "openid offline_access"
        status = self.connect()
        self.assertTrue(status["accounts"][0]["connected"])
        self.assertFalse(status["accounts"][0]["permitted"])
        with self.assertRaises(ChatGPTPlanError) as error:
            self.client.models()
        self.assertEqual(error.exception.code, "permission_required")
        self.assertFalse(any(req.full_url.endswith("/models") for req in self.calls))

    def test_identity_only_grant_without_refresh_is_retained_but_cannot_infer(self):
        self.scope = "openid profile email"
        original = self.tokens
        def identity_only():
            tokens = original(); tokens.pop("refresh_token")
            return tokens
        self.tokens = identity_only
        self.assertTrue(self.connect()["accounts"][0]["connected"])
        with self.assertRaises(ChatGPTPlanError) as error: self.client.models()
        self.assertEqual(error.exception.code, "permission_required")

    def test_code_invalid_grant_retains_pending_issued_id_without_activating(self):
        original = self.auth.opener
        def invalid_code(request):
            if request.full_url.endswith("/oauth/token"):
                raise HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b'{"error":"invalid_grant"}'))
            return original(request)
        self.auth.opener = invalid_code
        with self.assertRaises(ChatGPTPlanError): self.connect()
        self.assertEqual(self.auth.status()["active"], "")
        self.assertEqual(self.store.load()["pending_client_id"], self.issued)
        self.auth.opener = original
        self.connect()
        self.assertEqual(self.authorization["client_id"], [self.issued])
        self.assertNotIn("agent_name_hint", self.authorization)
        self.assertNotIn("pending_client_id", self.store.load())

    def test_timeout_and_cancel_close_listener_without_exchange(self):
        self.auth.browser = lambda url: True
        with self.assertRaises(ChatGPTPlanError) as error:
            self.auth.connect(threading.Event(), timeout=0.01)
        self.assertEqual(error.exception.code, "timeout")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ChatGPTPlanError) as error:
            self.auth.connect(cancel)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertFalse(any(req.full_url.endswith("/oauth/token") for req in self.calls))

    def test_refresh_rotation_serializes_threads_and_reloads_record(self):
        self.connect()
        original = self.store.load()["accounts"][self.issued]["refresh_token"]
        self.expire()
        results = []
        def run():
            with ChatGPTAuth(ChatGPTCredentialStore(self.store.path), opener=self.open).session() as account:
                results.append(account["refresh_token"])
        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertEqual(len(results), 4)
        self.assertEqual(len(set(results)), 1)
        self.assertNotEqual(results[0], original)
        self.assertEqual(self.refresh_count, 1)

    def test_failed_rotation_publication_fences_same_process(self):
        self.connect()
        self.expire()
        original_save = self.store.save
        attempts = []
        def fail_publication(value):
            attempts.append(1)
            if len(attempts) == 1:
                return original_save(value)
            raise ChatGPTPlanError("storage_unavailable")
        with patch.object(self.store, "save", side_effect=fail_publication):
            with self.assertRaises(ChatGPTPlanError) as error:
                self.client.models()
        self.assertEqual(error.exception.code, "rotation_unsaved")
        with self.assertRaises(ChatGPTPlanError) as error:
            self.client.models()
        self.assertEqual(error.exception.code, "rotation_unsaved")
        self.assertEqual(self.refresh_count, 1)
        _ROTATION_UNSAVED.discard(self.store._key)
        # A new owner after restart also rejects the durable pending record.
        with self.assertRaises(ChatGPTPlanError) as error:
            ChatGPTPlanClient(ChatGPTAuth(ChatGPTCredentialStore(self.store.path), opener=self.open)).models()
        self.assertEqual(error.exception.code, "rotation_unsaved")

    def test_terminal_refresh_error_clears_tokens_retains_registration(self):
        self.connect()
        self.expire()
        original_open = self.auth.opener
        def failed(request):
            if request.full_url.endswith("/oauth/token"):
                raise HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b'{"error":"invalid_grant"}'))
            return original_open(request)
        self.auth.opener = failed
        with self.assertRaises(ChatGPTPlanError) as error:
            self.client.models()
        self.assertEqual(error.exception.code, "invalid_grant")
        account = self.store.load()["accounts"][self.issued]
        self.assertEqual(account["refresh_token"], "")
        self.assertEqual(account["client_id"], self.issued)

    def test_catalog_order_visibility_and_no_general_api_schema_assumption(self):
        self.connect()
        self.assertEqual(self.client.models(), (("Model B", "model-b"), ("Model A", "model-a")))
        self.auth.opener = lambda req: Response({"data": [{"id": "model-b"}]})
        with self.assertRaises(ChatGPTPlanError): self.client.models()

    def test_supported_request_completed_final_and_usage(self):
        self.connect()
        self.stream = event("response.output_text.delta", delta="wrong intermediate") + event("response.completed", response=self.final)
        text, usage = self.client.respond("model-b", "test prompt")
        self.assertEqual(json.loads(text)["summary"], "요약")
        self.assertEqual(usage["total_tokens"], 60)
        request = next(req for req in self.calls if req.full_url.endswith("/responses"))
        self.assertEqual(json.loads(request.data), {"model": "model-b", "store": False, "stream": True,
            "input": [{"role": "user", "content": "test prompt"}]})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.secret)

    def test_usage_keeps_only_existing_counter_contract(self):
        from kiwoom_monitor.infrastructure.chatgpt_plan import _usage
        usage = _usage({"usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3,
                                   "attribution": {"unexpected": self.secret}}})
        self.assertEqual(usage, {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
        self.assertNotIn(self.secret, repr(usage))

    def test_stream_failures_do_not_accept_delta_or_missing_final_output(self):
        self.connect()
        for stream, code in (
            (event("response.output_text.delta", delta="{}"), "stream_interrupted"),
            (event("response.incomplete", response={"status": "incomplete"}), "response_incomplete"),
            (event("response.failed", response={"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": self.secret}}), "subscription_sharing_usage_limit_exceeded"),
            (event("error", code="subscription_sharing_usage_unavailable"), "subscription_sharing_usage_unavailable"),
            (b'data: {"type":"response.completed"', "stream_interrupted"),
            (event("response.completed", response={"status": "completed", "output": []}), "response_invalid"),
            (event("response.completed", response={**self.final, "status": "incomplete"}), "response_invalid")):
            with self.subTest(code=code):
                self.stream = stream
                with self.assertRaises(ChatGPTPlanError) as error:
                    self.client.respond("model-b", "prompt")
                self.assertEqual(error.exception.code, code)
                self.assertNotIn(self.secret, str(error.exception))
                self.assertTrue(error.exception.inference_attempted)

    def test_http_status_body_shape_and_secret_safe_error_no_retries(self):
        self.connect()
        for status, body in ((401, {"detail": self.secret}), (429, {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": self.secret}})):
            count = []
            def failed(request):
                if request.full_url.endswith("/responses"):
                    count.append(1)
                    raise HTTPError(request.full_url, status, self.secret, {"x-request-id": "req_mock123"}, io.BytesIO(json.dumps(body).encode()))
                return self.open(request)
            self.auth.opener = failed
            with self.assertRaises(ChatGPTPlanError) as error:
                self.client.respond("model-b", "prompt")
            self.assertEqual(error.exception.status_code, status)
            self.assertEqual(error.exception.body_shape, "detail" if status == 401 else "error")
            self.assertEqual(error.exception.request_id, "req_mock123")
            self.assertNotIn(self.secret, str(error.exception))
            self.assertEqual(len(count), 1)

    def test_unknown_model_cancelled_request_and_no_paid_fallback(self):
        self.connect()
        with self.assertRaises(ChatGPTPlanError) as error: self.client.respond("hidden", "prompt")
        self.assertEqual(error.exception.code, "model_unavailable")
        self.assertFalse(error.exception.inference_attempted)
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(ChatGPTPlanError) as error: self.client.respond("model-b", "prompt", cancel=cancel)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertFalse(any(req.full_url.endswith("/responses") for req in self.calls))

    def test_unexpected_response_diagnostics_return_only_shape_not_body(self):
        self.connect()
        original = self.auth.opener
        def json_response(request):
            if request.full_url.endswith("/responses"):
                return Response({"access_token": self.secret, "status": "completed", "output": []})
            return original(request)
        self.auth.opener = json_response
        with self.assertRaises(ChatGPTPlanError) as error:
            self.client.respond("model-b", "prompt")
        self.assertEqual(error.exception.status_code, 200)
        self.assertEqual(error.exception.response_failure, "stream_content_type")
        self.assertTrue(error.exception.response_diagnostics["body_is_json_object"])
        self.assertNotIn(self.secret, repr(error.exception.response_diagnostics))
        self.assertNotIn("access_token", repr(error.exception.response_diagnostics))

    def test_missing_header_requires_valid_completed_sse_not_json_or_deltas(self):
        self.connect()
        original = self.auth.opener
        def without_header(request):
            response = original(request)
            if request.full_url.endswith("/responses"):
                response.headers = {}
            return response
        self.auth.opener = without_header
        text, usage = self.client.respond("model-b", "prompt")
        self.assertEqual(json.loads(text)["summary"], "요약")
        self.assertEqual(usage["total_tokens"], 60)
        for body in (json.dumps(self.final).encode(), event("response.output_text.delta", delta="{}")):
            self.stream = body
            with self.assertRaises(ChatGPTPlanError):
                self.client.respond("model-b", "prompt")

    def test_completed_items_reconstruct_metadata_only_terminal_without_using_delta(self):
        self.connect()
        item = {**self.final["output"][0], "id": "msg_test"}
        created = event("response.created", response={"id": "resp_test", "status": "in_progress"})
        added = event("response.output_item.added", output_index=0, item={**item, "status": "in_progress", "content": []})
        done = event("response.output_item.done", output_index=0, item=item)
        terminal = event("response.completed", response={**self.final, "id": "resp_test", "output": []})
        self.stream = created + added + event("response.output_text.delta", delta="wrong partial text") + done + terminal
        text, usage = self.client.respond("model-b", "prompt")
        self.assertEqual(json.loads(text)["summary"], "요약")
        self.assertEqual(usage["total_tokens"], 60)
        for stream in (
            created + added + terminal,
            created + added + done,
            created + added + done + event("response.failed", response={"error": {"code": "subscription_sharing_usage_limit_exceeded"}}),
            created + added + event("response.output_item.done", output_index=0, item={**item, "status": "incomplete"}) + terminal,
            created + added + event("response.output_item.done", output_index=0, item={**item, "id": "wrong"}) + terminal,
            created + added + done + event("response.completed", response={**self.final, "id": "wrong", "output": []}),
        ):
            self.stream = stream
            with self.assertRaises(ChatGPTPlanError): self.client.respond("model-b", "prompt")

    def test_cancel_during_terminal_read_never_accepts_completed_response(self):
        self.connect()
        cancel = threading.Event()
        original = self.auth.opener
        def cancelled_read(request):
            if request.full_url.endswith("/responses"):
                response = Response(event("response.completed", response=self.final), stream=True)
                read = response.readline
                def interrupted(*args):
                    value = read(*args); cancel.set(); return value
                response.readline = interrupted
                return response
            return original(request)
        self.auth.opener = cancelled_read
        with self.assertRaises(ChatGPTPlanError) as error:
            self.client.respond("model-b", "prompt", cancel=cancel)
        self.assertEqual(error.exception.code, "cancelled")

    def test_disconnect_revokes_and_preserves_account_mapping(self):
        self.connect()
        result = self.auth.disconnect()
        self.assertTrue(result["revocation_confirmed"])
        self.assertFalse(result["accounts"][0]["connected"])
        self.assertEqual(result["active"], self.issued)
        request = next(req for req in self.calls if req.full_url.endswith("/oauth/revoke"))
        self.assertEqual(parse_qs(request.data.decode())["client_id"], [self.issued])
        self.assertEqual(self.store.load()["accounts"][self.issued]["id_token"], "")

    def test_disconnect_network_failure_reports_unconfirmed_and_clears_locally(self):
        self.connect()
        self.auth.opener = lambda req: (_ for _ in ()).throw(OSError(self.secret))
        result = self.auth.disconnect()
        self.assertFalse(result["revocation_confirmed"])
        self.assertFalse(result["accounts"][0]["connected"])
        self.assertNotIn(self.secret, repr(result))

    def test_protection_failure_never_writes_plaintext(self):
        with patch("kiwoom_monitor.infrastructure.chatgpt_auth._protect", side_effect=OSError(self.secret)):
            with self.assertRaises(ChatGPTPlanError) as error:
                self.store.save({"access_token": self.secret})
        self.assertFalse(self.store.path.exists())
        self.assertNotIn(self.secret, str(error.exception))

    def test_news_parser_and_existing_writer_in_isolated_db(self):
        from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository
        from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
        from kiwoom_monitor.application.news_analysis import assess_stock_news
        from kiwoom_monitor.infrastructure.news_ai import AIRequestUsage
        self.connect()
        config = LocalNaverNewsConfig(Path(self.temp.name) / "naver.dat", chatgpt_storage_path=self.store.path)
        config.save(NaverNewsCredentials(), ai=NewsAISettings(provider="chatgpt_plan", model="model-b"))
        with patch("kiwoom_monitor.infrastructure.chatgpt_auth.open_request", self.open):
            # Constructors bind default opener at definition; supply transport explicitly.
            with patch("kiwoom_monitor.infrastructure.chatgpt_auth.ChatGPTAuth", return_value=self.auth):
                results, usage = analyze_articles(config.load_ai(), "시험기업", (("제목", "본문"),))
        self.assertEqual(usage, AIRequestUsage(40, 20, 60))
        item = StockNewsItem("제목", "요약", "https://example.invalid/news", "", None, assess_stock_news("시험기업", "제목", "요약"))
        repo = NewsAIRepository(Path(self.temp.name) / "news.sqlite3")
        repo.save("000001", item, "chatgpt_plan", "model-b", analysis_body_hash("시험기업", "본문"), results[0])
        repo.log_request("chatgpt_plan", "model-b", "single", 1, 1, usage)
        loaded = repo.load("000001", item)
        self.assertEqual(loaded.provider, "chatgpt_plan")
        self.assertEqual(loaded.analysis.theme_candidates, results[0].theme_candidates)
        self.assertEqual(repo.daily_usage(), (1, 40, 20, 60))
        for path in (repo._database_path, config._path):
            self.assertNotIn(self.secret.encode(), path.read_bytes())

    def test_news_missing_fields_duplicate_batch_and_valid_small_batch(self):
        from kiwoom_monitor.infrastructure.news_ai import _validate_plan_json
        for text, count in (("{}", 1), (json.dumps([{"id": 1, **news_result()}, {"id": 1, **news_result()}]), 2),
                            ('{"summary":', 1)):
            with self.assertRaises(ValueError): _validate_plan_json(text, count)
        values = [{"id": 2, **news_result("둘째")}, {"id": 1, **news_result("첫째")}]
        with patch("kiwoom_monitor.infrastructure.chatgpt_plan.ChatGPTPlanClient.respond", return_value=(json.dumps(values), {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})):
            results, usage = analyze_articles(NewsAISettings(provider="chatgpt_plan", model="model-b", chatgpt_storage_path=self.store.path),
                "시험기업", (("첫째", "본문"), ("둘째", "본문")))
        self.assertEqual([item.summary for item in results], ["첫째", "둘째"])
        self.assertEqual(usage.total_tokens, 3)

    def test_central_plan_sends_only_news_and_model_not_oauth(self):
        sent = []
        def opener(request, **kwargs):
            sent.append(json.loads(request.data))
            return Response({"results": [], "usage": {}, "provider": "chatgpt_plan", "model": "model-b"})
        client = CentralAIClient("https://example.invalid", "dummy", opener=opener)
        client.analyze("000001", "시험기업", "chatgpt_plan", "model-b", [], 0)
        self.assertEqual(set(sent[0]), {"stock_code", "stock_name", "provider", "model", "events", "article_count"})
        self.assertEqual(sent[0]["provider"], "chatgpt_plan")

    def test_cross_process_refresh_owner_is_exclusive(self):
        import subprocess
        self.connect()
        with self.store.locked():
            code = "from pathlib import Path; from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTCredentialStore,ChatGPTPlanError; import sys\ntry:\n with ChatGPTCredentialStore(Path(sys.argv[1])).locked(): print('unexpected')\nexcept ChatGPTPlanError as e: print(e.code)"
            result = subprocess.run([__import__("sys").executable, "-c", code, str(self.store.path)],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "storage_busy")

    def test_multiple_registrations_same_email_remain_separate(self):
        self.connect()
        first = self.issued
        self.issued = "oaiapp_" + secrets.token_hex(8)
        second = self.connect()
        self.assertEqual(len(second["accounts"]), 2)
        self.assertNotEqual(second["accounts"][0]["label"], second["accounts"][1]["label"])
        self.assertEqual(self.auth.select(first)["active"], first)
        self.assertEqual(self.store.load()["accounts"][first]["client_id"], first)

    def test_ambiguous_refresh_disconnect_blocks_restart_without_erasing_identity(self):
        self.connect(); self.expire()
        self.auth.opener = lambda req: (_ for _ in ()).throw(OSError(self.secret))
        with self.assertRaises(ChatGPTPlanError) as error: self.client.models()
        self.assertEqual(error.exception.code, "network_error")
        self.assertTrue(self.store.load()["accounts"][self.issued]["refresh_pending"])
        with self.assertRaises(ChatGPTPlanError) as error:
            ChatGPTPlanClient(ChatGPTAuth(ChatGPTCredentialStore(self.store.path), opener=self.open)).models()
        self.assertEqual(error.exception.code, "rotation_unsaved")

    def test_settings_and_news_backup_preserve_selection_without_oauth_credentials(self):
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService
        from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
        root = Path(self.temp.name)
        database = root / "monitor.sqlite3"
        Database(database).initialize()
        config = LocalNaverNewsConfig(root / "naver_news.dat", chatgpt_storage_path=self.store.path)
        config.save(NaverNewsCredentials(), ai=NewsAISettings(provider="chatgpt_plan", model="model-b"))
        self.connect()
        backup = root / "settings.json"
        service = SettingsBackupService(database)
        service.export_to(backup, include_themes=False)
        raw = backup.read_text(encoding="utf-8")
        self.assertNotIn(self.secret, raw)
        self.assertNotIn("access_token", raw)
        self.assertNotIn("credentials.dpapi", raw)
        self.assertEqual(json.loads(raw)["news_settings"]["ai"]["provider"], "chatgpt_plan")
        config.save(NaverNewsCredentials(), ai=NewsAISettings(provider="none"))
        service.import_from(backup, include_themes=False)
        self.assertEqual(config.load_ai().provider, "chatgpt_plan")
        self.assertEqual(config.load_ai().model, "model-b")
        news_backup = root / "news-ai.json"
        NewsAIBackupService(root / "news.sqlite3").export_to(news_backup)
        self.assertNotIn(self.secret, news_backup.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
