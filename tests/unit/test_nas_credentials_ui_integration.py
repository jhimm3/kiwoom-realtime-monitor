import os
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
from test_central_credentials_client import Response
from test_mock_credential_owner import FakeClient


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
                    return Response(response.json(), request.full_url)
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
                        wait_until(lambda: dialog._worker is None and dialog._operation.get("state") == "ACTIVE"
                            and dialog._profile().get("disabled") is True, timeout=8)
                        document = client.account_settings(ref)["settings"]
                        self.assertIsNone(document["active_profile_id"])
                        self.assertFalse(document["mock_order_enabled"])
                        self.assertEqual(sum(r.method == "POST" and r.full_url.endswith("/apply") for r in requests), 2)
                        self.assertEqual({p.name for p in root.iterdir()}, {"central.sqlite", "secrets"})
                    finally:
                        dialog.reject()
                        if dialog._worker is not None: dialog._worker.wait(5000)
                        dispose_dialogs()
