import io
import json
import ssl
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError

from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient, _NoRedirect
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


class Response(io.BytesIO):
    def __init__(self, result, url):
        super().__init__(json.dumps(result).encode())
        self.url = url

    def geturl(self):
        return self.url


class CredentialsClientTests(unittest.TestCase):
    def setUp(self):
        self.client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.example.test", "access"))
        self.operation = {"operation_id": str(uuid.uuid4()), "provider": "kiwoom_mock",
            "profile_id": "nas-mock-default", "expected_revision": 3, "state": "READY",
            "target_account_ref": str(uuid.uuid4())}
        self.requests = []
        self.responses = []
        self.factory = patch("kiwoom_monitor.infrastructure.central_credentials_client.build_opener")
        self.opener = self.factory.start().return_value
        self.opener.open.side_effect = self.open
        self.addCleanup(self.factory.stop)

    def open(self, request, timeout):
        self.assertEqual(timeout, 10)
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return Response(result, request.full_url)

    def ready(self):
        self.responses.append(self.operation)
        self.client.prepare_mock("nas-mock-default", 3, "ephemeral-key", "ephemeral-secret")

    def test_unsafe_destination_sends_nothing(self):
        for url in ["http://nas.test", "https://user:pass@nas.test", "https://nas.test?key=x", "https://nas.test/#x", "https://nas.test:bad"]:
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                CentralCredentialsClient(DataSourceSettings("personal_server", url, "access")).prepare_mock(
                    "nas-mock-default", 0, "ephemeral-key", "ephemeral-secret")
        self.assertEqual(self.requests, [])

    def test_system_tls_requires_certificate_and_hostname(self):
        context = system_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_old_server_and_secret_echo_filter(self):
        self.responses = [{"capabilities": {"runtime_credentials_v1": False}}]
        with self.assertRaisesRegex(RuntimeError, "지원하지"):
            self.client.load()
        self.assertEqual(len(self.requests), 1)
        self.responses = [{"capabilities": {"runtime_credentials_v1": True}},
            {"providers": [{"provider": "kiwoom_mock", "supported": True, "secret_key": "echo"}],
             "profiles": [{"profile_id": "nas-mock-default", "app_key": "echo", "secret_key": "echo"}]}]
        self.assertNotIn("echo", repr(self.client.load()))

    def test_prepare_timeout_reuses_uuid_without_retaining_keys(self):
        self.responses = [TimeoutError("ephemeral-secret"), self.operation]
        with self.assertRaises(RuntimeError) as error:
            self.client.prepare_mock("nas-mock-default", 3, "ephemeral-key", "ephemeral-secret")
        self.assertNotIn("ephemeral", str(error.exception))
        self.assertNotIn("ephemeral", repr(self.client.__dict__))
        self.client.prepare_mock("nas-mock-default", 3, "ephemeral-key", "ephemeral-secret")
        first, second = [json.loads(request.data) for request in self.requests]
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(self.client.operation_id, self.operation["operation_id"])

    def test_apply_timeout_checks_same_operation_without_reapplying(self):
        self.ready()
        self.responses = [TimeoutError(), {**self.operation, "state": "ACTIVE"}]
        with self.assertRaises(RuntimeError):
            self.client.apply(3, self.operation["target_account_ref"])
        self.assertEqual(self.client.pending_profile, "nas-mock-default")
        with self.assertRaises(ValueError):
            self.client.apply(3, self.operation["target_account_ref"])
        self.client.status()
        self.assertIsNone(self.client.pending_profile)
        self.assertEqual([r.method for r in self.requests], ["POST", "POST", "GET"])
        self.assertTrue(self.requests[-1].full_url.endswith(self.operation["operation_id"]))

    def test_wrong_profile_revision_target_or_operation_cannot_apply(self):
        self.ready()
        with self.assertRaises(RuntimeError): self.client.prepare_mock("nas-mock-default", 3, "k", "s")
        for revision, ref in [(4, self.operation["target_account_ref"]), (3, str(uuid.uuid4()))]:
            with self.assertRaises(ValueError): self.client.apply(revision, ref)
        for change in [{"profile_id": "other"}, {"expected_revision": True},
                       {"operation_id": str(uuid.uuid4())}, {"target_account_ref": str(uuid.uuid4())}]:
            self.responses = [{**self.operation, **change}]
            with self.assertRaises(RuntimeError): self.client.status()
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)

    def test_committed_receipt_after_restart_is_accepted(self):
        self.ready()
        receipt = {**self.operation, "state": "ACTIVE", "committed": True, "revision": 4}
        del receipt["expected_revision"]
        self.responses = [receipt]
        self.assertEqual(self.client.status()["state"], "ACTIVE")

    def test_redirect_and_transport_details_never_escape(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 307, "", {}, "http://other"))
        self.responses = [HTTPError("https://nas.test", 307, "ephemeral-secret", {}, io.BytesIO(b"ephemeral-secret"))]
        with self.assertRaises(RuntimeError) as error: self.client.load()
        self.assertNotIn("ephemeral", str(error.exception))
        self.assertEqual(len(self.requests), 1)

    def test_create_timeout_reuses_request_and_boolean_revision_is_invalid(self):
        self.responses = [TimeoutError(), {"profile_id": "new-profile"}]
        with self.assertRaises(RuntimeError): self.client.create_mock_profile("새 계좌")
        self.client.create_mock_profile("새 계좌")
        self.assertEqual(*[json.loads(r.data)["request_id"] for r in self.requests])
        with self.assertRaises(ValueError): self.client.prepare_mock("nas-mock-default", True, "k", "s")
        self.assertEqual(len(self.requests), 2)

    def test_settings_write_uses_explicit_mock_scope_and_cas(self):
        self.responses = [{"settings": {"scope": {"broker": "kiwoom", "environment": "mock",
            "account_ref": self.operation["target_account_ref"]}, "revision": 9,
            "monitor_enabled": True, "mock_order_enabled": False}}]
        self.client.update_account(self.operation["target_account_ref"], "nas-mock-default", 8,
            monitor_enabled=True, mock_order_enabled=False)
        request = self.requests[0]
        self.assertTrue(request.full_url.endswith("?environment=mock"))
        self.assertEqual(json.loads(request.data), {"expected_revision": 8, "active_profile_id": "nas-mock-default",
            "monitor_enabled": True, "mock_order_enabled": False})

    def test_wrong_account_settings_response_is_rejected(self):
        self.responses = [{"settings": {"scope": {"broker": "kiwoom", "environment": "real",
            "account_ref": self.operation["target_account_ref"]}}}]
        with self.assertRaises(RuntimeError): self.client.account_settings(self.operation["target_account_ref"])

    def test_changed_response_url_and_oversized_body_are_rejected(self):
        for response in [Response({}, "https://other.test"), Response({"padding": "x" * 1_048_577}, "https://nas.example.test/api/v1/capabilities")]:
            self.opener.open.side_effect = None
            self.opener.open.return_value = response
            with self.assertRaises(RuntimeError): self.client.load()

    def test_invalid_prepare_response_cannot_enable_apply(self):
        self.responses = [{**self.operation, "profile_id": "wrong"}]
        with self.assertRaises(RuntimeError):
            self.client.prepare_mock("nas-mock-default", 3, "ephemeral-key", "ephemeral-secret")
        self.assertIsNone(self.client.operation_id)
        self.assertEqual(self.client.pending_profile, "nas-mock-default")

    def test_real_profile_prepare_apply_keeps_verified_target_and_provider(self):
        self.client = CentralCredentialsClient(self.client.source, provider="kiwoom_real")
        self.operation = {**self.operation, "provider": "kiwoom_real", "profile_id": str(uuid.uuid4())}
        self.responses = [{"profile_id": self.operation["profile_id"]}, self.operation,
            {**self.operation, "state": "ACTIVE"}]
        self.client.create_account_profile("실전 두 번째")
        self.client.prepare_account(self.operation["profile_id"], 3, "ephemeral-real", "ephemeral-secret")
        with self.assertRaises(ValueError): self.client.apply(3, str(uuid.uuid4()))
        self.client.apply(3, self.operation["target_account_ref"])
        self.assertTrue(self.requests[0].full_url.endswith("/kiwoom_real/profiles"))
        self.assertIn("/kiwoom_real/profiles/", self.requests[1].full_url)
        self.assertEqual(json.loads(self.requests[2].data)["target_account_ref"], self.operation["target_account_ref"])
        self.assertNotIn("ephemeral", repr(self.client.__dict__))

    def test_real_account_settings_use_real_scope_and_reject_mock_orders(self):
        self.client = CentralCredentialsClient(self.client.source, provider="kiwoom_real")
        ref = self.operation["target_account_ref"]
        document = {"scope": {"broker": "kiwoom", "environment": "real", "account_ref": ref},
            "revision": 9, "monitor_enabled": True, "mock_order_enabled": False}
        self.responses = [{"settings": document}, {"settings": document}]
        self.client.account_settings(ref)
        self.client.update_account(ref, "nas-real-default", 8, monitor_enabled=True, mock_order_enabled=False)
        self.assertTrue(all(r.full_url.endswith("?environment=real") for r in self.requests))
        with self.assertRaises(ValueError):
            self.client.update_account(ref, "nas-real-default", 9, monitor_enabled=True, mock_order_enabled=True)
        self.assertEqual(len(self.requests), 2)
        for invalid in ({**document, "scope": {**document["scope"], "environment": "mock"}},
                        {**document, "mock_order_enabled": True}):
            self.responses = [{"settings": invalid}]
            with self.assertRaises(RuntimeError): self.client.account_settings(ref)

    def test_real_ready_without_target_and_mock_named_methods_fail_closed(self):
        self.client = CentralCredentialsClient(self.client.source, provider="kiwoom_real")
        self.responses = [{**self.operation, "provider": "kiwoom_real", "profile_id": "nas-real-default",
            "target_account_ref": None}]
        with self.assertRaises(RuntimeError):
            self.client.prepare_account("nas-real-default", 3, "ephemeral", "ephemeral")
        self.assertIsNone(self.client.operation_id)
        with self.assertRaises(ValueError): self.client.prepare_mock("nas-real-default", 3, "k", "s")
        with self.assertRaises(ValueError): self.client.create_mock_profile("mock")

    def test_real_prepare_timeout_reuses_request_and_filters_secrets(self):
        self.client = CentralCredentialsClient(self.client.source, provider="kiwoom_real")
        self.operation = {**self.operation, "provider": "kiwoom_real", "profile_id": "nas-real-default"}
        self.responses = [TimeoutError("ephemeral-secret"), self.operation]
        with self.assertRaises(RuntimeError) as error:
            self.client.prepare_account("nas-real-default", 3, "ephemeral-real", "ephemeral-secret")
        self.assertNotIn("ephemeral", str(error.exception))
        self.assertNotIn("ephemeral", repr(self.client.__dict__))
        self.client.prepare_account("nas-real-default", 3, "ephemeral-real", "ephemeral-secret")
        self.assertEqual(*[json.loads(r.data)["request_id"] for r in self.requests])
