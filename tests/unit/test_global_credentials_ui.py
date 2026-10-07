import json
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit

from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from kiwoom_monitor.presentation.nas_credentials_dialog import NasCredentialsDialog, PROVIDER_LABELS
from qt_settings_test_support import dispose_dialogs, wait_until
from test_central_credentials_client import Response


def operation(provider, **changes):
    return {"operation_id": str(uuid.uuid4()), "provider": provider,
        "profile_id": f"nas-{provider}-default", "expected_revision": 0,
        "target_account_ref": None, "state": "READY", "validation":
        "VERIFIED" if provider in {"naver", "dart"} else "UNVERIFIED", **changes}


class GlobalCredentialsClientTests(unittest.TestCase):
    def setUp(self):
        self.requests, self.responses = [], []
        self.factory = patch("kiwoom_monitor.infrastructure.central_credentials_client.build_opener")
        self.factory.start().return_value.open.side_effect = self.open_request
        self.addCleanup(self.factory.stop)

    def open_request(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return Response(response, request.full_url)

    def client(self, provider="gemini"):
        return CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "token"), provider=provider)

    def test_five_providers_have_exact_fields_and_null_account_apply(self):
        for provider, fields in CentralCredentialsClient.GLOBAL_FIELDS.items():
            with self.subTest(provider=provider):
                client = self.client(provider)
                ready = operation(provider)
                self.responses = [ready, {**ready, "state": "ACTIVE"}]
                client.prepare_global(0, {key: " ephemeral-" + key + " " for key in fields})
                self.assertNotIn("ephemeral", repr(client.__dict__))
                request = self.requests[-1]
                self.assertTrue(request.full_url.endswith(f"/{provider}/profiles/nas-{provider}-default/prepare"))
                self.assertEqual(set(fields), set(json.loads(request.data)["replacement"]))
                self.assertEqual("ACTIVE", client.apply(0, None)["state"])
                self.assertEqual({"expected_revision": 0, "target_account_ref": None}, json.loads(self.requests[-1].data))

    def test_timeout_reuses_same_prepare_id_without_retaining_secrets(self):
        client = self.client()
        ready = operation("gemini")
        self.responses = [TimeoutError("ephemeral-key"), ready]
        with self.assertRaises(RuntimeError) as error: client.prepare_global(0, {"api_key": "ephemeral-key"})
        self.assertNotIn("ephemeral", str(error.exception))
        self.assertNotIn("ephemeral", repr(client.__dict__))
        client.prepare_global(0, {"api_key": "ephemeral-key"})
        self.assertEqual(*[json.loads(r.data)["request_id"] for r in self.requests])
        self.assertIsNone(self.client("claude").pending_profile)

    def test_apply_timeout_only_checks_same_operation_and_restart_receipt(self):
        client = self.client()
        ready = operation("gemini")
        receipt = {**ready, "state": "ACTIVE", "committed": True, "revision": 1}
        del receipt["expected_revision"]
        self.responses = [ready, TimeoutError(), receipt]
        client.prepare_global(0, {"api_key": "key"})
        with self.assertRaises(RuntimeError): client.apply(0, None)
        with self.assertRaises(ValueError): client.apply(0, None)
        self.assertEqual("ACTIVE", client.status()["state"])
        self.assertEqual(["POST", "POST", "GET"], [r.method for r in self.requests])

    def test_wrong_context_or_account_cannot_apply(self):
        client = self.client()
        ready = operation("gemini")
        self.responses = [ready]
        client.prepare_global(0, {"api_key": "key"})
        with self.assertRaises(ValueError): client.apply(0, str(uuid.uuid4()))
        for changes in ({"provider": "claude"}, {"profile_id": "other"}, {"expected_revision": True},
                        {"target_account_ref": str(uuid.uuid4())}, {"operation_id": str(uuid.uuid4())}):
            self.responses = [{**ready, **changes}]
            with self.assertRaises(RuntimeError): client.status()
        self.assertEqual(1, sum(r.method == "POST" for r in self.requests))

    def test_invalid_inputs_and_account_methods_do_not_send(self):
        client = self.client()
        for replacement in ({}, {"api_key": ""}, {"api_key": "x", "extra": "y"}, {"api_key": 123}):
            with self.assertRaises(ValueError): client.prepare_global(0, replacement)
        with self.assertRaises(ValueError): client.prepare_global(True, {"api_key": "key"})
        with self.assertRaises(ValueError): client.prepare_global(0, {"api_key": "key"}, disabled=True)
        with self.assertRaises(ValueError): client.create_mock_profile("계좌")
        with self.assertRaises(ValueError): client.account_settings(str(uuid.uuid4()))
        self.assertEqual([], self.requests)

    def test_disable_has_no_secret_and_unverified_naver_is_rejected(self):
        client = self.client("naver")
        self.responses = [operation("naver", validation="UNVERIFIED")]
        with self.assertRaises(RuntimeError): client.prepare_global(0, {"client_id": "id", "client_secret": "secret"})
        self.responses = [operation("naver", disabled=True)]
        client = self.client("naver")
        client.prepare_global(0, disabled=True)
        body = json.loads(self.requests[-1].data)
        self.assertTrue(body["disable"])
        self.assertNotIn("replacement", body)


class GlobalCredentialsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_keyless_global_form_never_reads_account_settings(self):
        for provider in PROVIDER_LABELS:
            with self.subTest(provider=provider):
                client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "token"), provider=provider)
                profile = {"provider": provider, "profile_id": f"nas-{provider}-default", "supported": True,
                    "revision": 0, "configured": False, "runtime": "UNCONFIGURED"}
                metadata = {"providers": [{"provider": provider, "supported": True}],
                    "profiles": [profile, {**profile, "profile_id": "other"}]}
                with patch.object(client, "load", return_value=metadata), patch.object(client, "account_settings") as accounts:
                    dialog = NasCredentialsDialog(client)
                    dialog.show()
                    wait_until(lambda: dialog._worker is None and dialog._profiles.count() == 1)
                    self.assertFalse(dialog._monitor.isVisible())
                    self.assertFalse(dialog._create_button.isEnabled())
                    self.assertEqual(provider == "naver", dialog._secret_key.isVisible())
                    self.assertEqual(QLineEdit.EchoMode.Password, dialog._app_key.echoMode())
                    self.assertFalse(dialog._disable_button.isEnabled())
                    ready = operation(provider)
                    with patch.object(client, "prepare_global", return_value=ready) as prepare:
                        dialog._app_key.setText("ephemeral-key"); dialog._secret_key.setText("ephemeral-secret")
                        dialog._prepare_button.click()
                        wait_until(lambda: dialog._worker is None and dialog._apply_button.isEnabled())
                        expected = {"client_id": "ephemeral-key", "client_secret": "ephemeral-secret"} if provider == "naver" else {"api_key": "ephemeral-key"}
                        prepare.assert_called_once_with(0, expected)
                        self.assertEqual("", dialog._app_key.text())
                        self.assertEqual("", dialog._secret_key.text())
                    self.assertNotIn("계좌 확인", dialog._status.text())
                    accounts.assert_not_called()
                    dialog.reject()

    def test_ai_active_receipt_without_validation_never_claims_authentication(self):
        client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "token"), provider="gemini")
        dialog = NasCredentialsDialog(client)
        with patch.object(dialog, "_load"):
            receipt = operation("gemini", state="ACTIVE")
            del receipt["validation"]
            dialog._operation_result(receipt)
        self.assertIn("실제 분석이 성공하면", dialog._preview.text())
        self.assertNotIn("새 키를 확인했습니다", dialog._preview.text())
        dialog.reject()

    def test_slow_prepare_keeps_gui_alive_and_close_clears_keys(self):
        client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "token"), provider="gemini")
        entered, release = Event(), Event()
        def delayed(revision, replacement):
            entered.set(); release.wait(3)
            return operation("gemini")
        dialog = NasCredentialsDialog(client)
        dialog._profiles.addItem("Gemini", {"profile_id": "nas-gemini-default", "revision": 0, "supported": True})
        dialog._supported = True
        with patch.object(client, "prepare_global", side_effect=delayed):
            dialog._app_key.setText("ephemeral-key"); dialog._prepare()
            worker = dialog._worker
            try:
                wait_until(entered.is_set)
                ticks = []; QTimer.singleShot(0, lambda: ticks.append(True))
                wait_until(lambda: bool(ticks))
                self.assertFalse(dialog._apply_button.isEnabled())
                dialog.reject()
                self.assertEqual("", dialog._app_key.text())
                dialog.deleteLater(); dispose_dialogs()
            finally:
                release.set(); worker.wait(4000); self.qt.processEvents()

    def test_provider_menu_keeps_pending_clients_separate_and_reuses_same_nas(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._data_source_mode.setCurrentIndex(dialog._data_source_mode.findData("personal_server"))
            dialog._server_url.setText("https://nas.test"); dialog._server_token.setText("token")
            self.assertEqual(list(PROVIDER_LABELS.values()), [a.text() for a in dialog._nas_provider_button.menu().actions()])
            with patch("kiwoom_monitor.presentation.api_settings_dialog.NasCredentialsDialog") as form:
                dialog._open_nas_provider_credentials("gemini")
                client = form.call_args.args[0]
                client.operation_id = str(uuid.uuid4()); client.operation_profile = "nas-gemini-default"; client.operation_state = "READY"
                dialog._open_nas_provider_credentials("claude")
                self.assertIsNot(client, form.call_args.args[0])
                dialog._open_nas_provider_credentials("gemini")
                self.assertIs(client, form.call_args.args[0])
                self.assertEqual("READY", client.operation_state)
                dialog._server_url.setText("https://other.test")
                dialog._open_nas_provider_credentials("gemini")
                self.assertIsNot(client, form.call_args.args[0])
            dialog.reject()


class GlobalCredentialsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_five_provider_forms_prepare_apply_disable_against_real_routes(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from test_ai_credential_owner import FakeAI
        from test_naver_credential_owner import FakeNaver
        from test_dart_credential_owner import FakeDart

        with tempfile.TemporaryDirectory() as directory, patch(
                "kiwoom_monitor.central_server.news_credentials.NaverNewsClient", FakeNaver), patch(
                "kiwoom_monitor.central_server.news_credentials.DartDisclosureClient", FakeDart), patch(
                "kiwoom_monitor.central_server.ai_service.analyze_articles", FakeAI()) as ai:
            root = Path(directory)
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
                credential_directory=str(root / "secrets"), news_query_set_enabled=False))
            with TestClient(app, base_url="https://nas.test") as server:
                requests = []
                def send(request, timeout):
                    requests.append(request)
                    response = server.request(request.method, request.full_url, content=request.data,
                        headers=dict(request.header_items()))
                    if response.status_code >= 400:
                        raise HTTPError(request.full_url, response.status_code, "fake error", {}, None)
                    return Response(response.json(), request.full_url)
                with patch("kiwoom_monitor.infrastructure.central_credentials_client.build_opener") as factory:
                    factory.return_value.open.side_effect = send
                    for provider in PROVIDER_LABELS:
                        with self.subTest(provider=provider):
                            client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "private-token"), provider=provider)
                            dialog = NasCredentialsDialog(client)
                            try:
                                dialog.show()
                                wait_until(lambda: dialog._worker is None and dialog._prepare_button.isEnabled(), timeout=5)
                                dialog._app_key.setText("fake-provider-key")
                                if provider == "naver": dialog._secret_key.setText("fake-provider-secret")
                                dialog._prepare_button.click()
                                wait_until(lambda: dialog._worker is None and dialog._apply_button.isEnabled(), timeout=6)
                                self.assertIsNone(dialog._operation["target_account_ref"])
                                self.assertEqual("VERIFIED" if provider in {"naver", "dart"} else "UNVERIFIED", dialog._operation["validation"])
                                self.assertEqual("", dialog._app_key.text())
                                self.assertEqual([], ai.calls)
                                dialog._apply_button.click()
                                wait_until(lambda: dialog._worker is None and (dialog._profile() or {}).get("revision") == 1, timeout=6)
                                self.assertTrue(dialog._disable_button.isEnabled())
                                self.assertIsNone(dialog._settings)
                                dialog._disable_button.click()
                                wait_until(lambda: dialog._worker is None and dialog._apply_button.isEnabled(), timeout=6)
                                self.assertTrue(dialog._operation["disabled"])
                                dialog._apply_button.click()
                                wait_until(lambda: dialog._worker is None and (dialog._profile() or {}).get("disabled") is True, timeout=6)
                                self.assertEqual(2, dialog._profile()["revision"])
                            finally:
                                dialog.reject()
                                if dialog._worker is not None: dialog._worker.wait(5000)
                                dispose_dialogs()
                    self.assertFalse(any("/settings/accounts/" in r.full_url for r in requests))
                    self.assertEqual(10, sum(r.method == "POST" and r.full_url.endswith("/apply") for r in requests))
                    self.assertEqual([], ai.calls)
                    self.assertEqual({"central.sqlite", "secrets"}, {p.name for p in root.iterdir()})
