import os
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.presentation.nas_credentials_dialog import NasCredentialsDialog
from qt_settings_test_support import dispose_dialogs, wait_until
from credential_owner_test_support import FakeClient


class _HTTPResponse(io.BytesIO):
    def __init__(self, result, url):
        super().__init__(json.dumps(result).encode())
        self.url = url

    def geturl(self):
        return self.url


class NasCredentialsUIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_create_prepare_apply_disable_against_real_routes_without_network(self):
        self._exercise_account_routes("kiwoom_mock")

    def test_real_create_verify_apply_monitor_off_and_disable_without_network(self):
        self._exercise_account_routes("kiwoom_real")

    def _exercise_account_routes(self, provider):
        with tempfile.TemporaryDirectory() as directory, \
             patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient), \
             patch("kiwoom_monitor.central_server.mock_account_monitor.RealAccountRealtimeCollector",
                 side_effect=lambda **kwargs: AsyncMock(ready=False, error_code=None)), \
             patch("kiwoom_monitor.central_server.mock_account_monitor.MockAccountRealtimeCollector.start", new=AsyncMock()):
            root = Path(directory)
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
                credential_directory=str(root / "secrets"), account_identity_registry_enabled=True,
                account_identity_hmac_key="x" * 32, autonomous_top20_enabled=False, market_event_collection_enabled=False))
            with TestClient(app, base_url="https://nas.test") as server:
                requests = []
                def open_request(request, timeout):
                    requests.append(request)
                    response = server.request(request.method, request.full_url, content=request.data,
                        headers=dict(request.header_items()))
                    if response.status_code >= 400:
                        raise HTTPError(request.full_url, response.status_code, "fake error", {}, None)
                    return _HTTPResponse(response.json(), request.full_url)
                with patch("kiwoom_monitor.infrastructure.central_credentials_client.build_opener") as factory:
                    factory.return_value.open.side_effect = open_request
                    client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.test", "private-token"), provider=provider)
                    dialog = NasCredentialsDialog(client)
                    try:
                        dialog.show()
                        wait_until(lambda: dialog._worker is None and dialog._create_button.isEnabled(), timeout=5)
                        label = "새 실전계좌" if provider == "kiwoom_real" else "새 모의계좌"
                        dialog._new_label.setText(label); dialog._create_button.click()
                        wait_until(lambda: dialog._worker is None and (dialog._profile() or {}).get("label") == label, timeout=5)
                        profile = dialog._profile()["profile_id"]
                        dialog._app_key.setText("a-integration"); dialog._secret_key.setText("fake-secret")
                        dialog._prepare_button.click()
                        wait_until(lambda: dialog._worker is None and dialog._apply_button.isEnabled(), timeout=5)
                        ref = dialog._operation["target_account_ref"]
                        self.assertEqual(dialog._app_key.text(), "")
                        dialog._apply_button.click()
                        wait_until(lambda: dialog._worker is None and dialog._operation.get("state") == "ACTIVE"
                            and dialog._settings is not None, timeout=8)
                        self.assertEqual(dialog._profile()["account_ref"], ref)
                        self.assertFalse(dialog._orders.isChecked())
                        self.assertEqual(client.account_settings(ref)["settings"]["active_profile_id"], profile)
                        self.assertEqual(client.account_settings(ref)["settings"]["scope"]["environment"],
                            "real" if provider == "kiwoom_real" else "mock")
                        if provider == "kiwoom_real":
                            dialog._monitor.setChecked(True); dialog._settings_button.click()
                            wait_until(lambda: dialog._worker is None and dialog._settings is not None
                                and dialog._settings["monitor_enabled"], timeout=5)
                            dialog._monitor.setChecked(False); dialog._settings_button.click()
                            wait_until(lambda: dialog._worker is None and dialog._settings is not None
                                and not dialog._settings["monitor_enabled"], timeout=5)
                            self.assertTrue(dialog._orders.isHidden())
                        dialog._disable_button.click()
                        wait_until(lambda: dialog._worker is None and dialog._apply_button.isEnabled(), timeout=5)
                        self.assertTrue(dialog._operation["disabled"])
                        dialog._apply_button.click()
                        wait_until(lambda: dialog._worker is None
                            and dialog._operation.get("state") == "ACTIVE", timeout=8)
                        visible_profile_ids = {
                            item.get("profile_id") for index in range(dialog._profiles.count())
                            if isinstance((item := dialog._profiles.itemData(index)), dict)
                        }
                        self.assertNotIn(profile, visible_profile_ids)
                        dialog._show_disconnected.setChecked(True)
                        wait_until(lambda: dialog._worker is None and any(
                            isinstance(dialog._profiles.itemData(index), dict)
                            and dialog._profiles.itemData(index).get("profile_id") == profile
                            for index in range(dialog._profiles.count())), timeout=8)
                        disconnected = next(
                            (dialog._profiles.itemData(index), dialog._profiles.itemText(index))
                            for index in range(dialog._profiles.count())
                            if isinstance(dialog._profiles.itemData(index), dict)
                            and dialog._profiles.itemData(index).get("profile_id") == profile)
                        self.assertTrue(disconnected[0]["disabled"])
                        self.assertIn("연결 해제됨", disconnected[1])
                        document = client.account_settings(ref)["settings"]
                        self.assertIsNone(document["active_profile_id"])
                        self.assertFalse(document["mock_order_enabled"])
                        self.assertEqual(sum(r.method == "POST" and r.full_url.endswith("/apply") for r in requests), 2)
                        root_entries = {p.name for p in root.iterdir()}
                        self.assertIn("central.sqlite", root_entries)
                        self.assertIn("secrets", root_entries)
                        self.assertLessEqual(root_entries, {
                            "central.sqlite", "central.sqlite-wal", "central.sqlite-shm", "secrets"})
                        secret_files = {p.name for p in (root / "secrets").iterdir()}
                        self.assertEqual(secret_files, {
                            "vault.lock", "master.key", f"{provider}--{profile}.json"})
                        encrypted_credentials = (root / "secrets" / f"{provider}--{profile}.json").read_bytes()
                        self.assertNotIn(b"a-integration", encrypted_credentials)
                        self.assertNotIn(b"fake-secret", encrypted_credentials)
                    finally:
                        dialog.reject()
                        if dialog._worker is not None: dialog._worker.wait(5000)
                        dispose_dialogs()
