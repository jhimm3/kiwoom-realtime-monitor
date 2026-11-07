import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox

from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from kiwoom_monitor.presentation.nas_credentials_dialog import NasCredentialsDialog
from qt_settings_test_support import dispose_dialogs, wait_until


class TrConnectionTransportTests(unittest.TestCase):
    def test_tr_connection_client_uses_verified_refs_and_cas_without_key_fields(self):
        client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.example.test", "access"))
        scope = {"broker": "kiwoom", "environment": "mock", "account_ref": str(uuid.uuid4())}
        target = {"scope": scope, "credential_profile_id": "mock-one", "binding_revision": 4}
        values = {"mode": "integrated", "execution_target": target, "data_target": target}
        applied = {"settings": {**values, "revision": 5}, "applied_revision": 5, "status": "applied",
                   "effective_mode": "integrated", "execution_ready": True,
                   "realtime_market": {"environment": "real", "credential_profile_id": "nas-real-default"}}
        responses = iter([applied, {"accounts": [{**scope, "credential_profile_id": "mock-one", "binding_revision": 4,
                                                 "display_label": "모의 계좌", "extra": "ignored"}]}, applied])
        requests = []

        def open_response(request, timeout):
            self.assertEqual(10, timeout)
            requests.append(request)
            response = io.BytesIO(json.dumps(next(responses)).encode())
            response.geturl = lambda: request.full_url
            return response

        with patch("kiwoom_monitor.infrastructure.central_credentials_client.build_opener") as build:
            build.return_value.open.side_effect = open_response
            self.assertEqual([(target, "모의 계좌")], client.load_tr_connections()["targets"])
            self.assertEqual(applied, client.update_tr_connections(values, 4))
            with self.assertRaisesRegex(ValueError, "TR_CONNECTION_SETTINGS_INVALID"):
                client.update_tr_connections({**values, "app_key": "never-send"}, 5)
        self.assertEqual(["GET", "GET", "PUT"], [request.method for request in requests])
        self.assertEqual({**values, "expected_revision": 4}, json.loads(requests[-1].data))
        self.assertTrue(requests[-1].full_url.endswith("/api/v1/settings/tr-connections"))


class NasCredentialsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.client = CentralCredentialsClient(DataSourceSettings("personal_server", "https://nas.example.test", "access"))
        self.profile = {"provider": "kiwoom_mock", "profile_id": "nas-mock-default", "label": "모의계좌",
            "supported": True, "revision": 2, "account_ref": str(uuid.uuid4()), "runtime": "ACTIVE"}
        self.metadata = {"providers": [{"provider": "kiwoom_mock", "supported": True}], "profiles": [self.profile]}
        self.settings = {"settings": {"scope": {"broker": "kiwoom", "environment": "mock",
            "account_ref": self.profile["account_ref"]}, "active_profile_id": self.profile["profile_id"],
            "revision": 4, "monitor_enabled": True, "mock_order_enabled": True}, "applied_revision": 4}
        self.operation = {"operation_id": str(uuid.uuid4()), "provider": "kiwoom_mock", "profile_id": self.profile["profile_id"],
            "expected_revision": 2, "target_account_ref": self.profile["account_ref"], "state": "READY"}
        self.load_patch = patch.object(self.client, "load", return_value=self.metadata)
        self.load = self.load_patch.start()
        self.settings_patch = patch.object(self.client, "account_settings", return_value=self.settings)
        self.settings_get = self.settings_patch.start()
        self.addCleanup(self.load_patch.stop); self.addCleanup(self.settings_patch.stop)

    def tearDown(self):
        dispose_dialogs()

    def open(self):
        dialog = NasCredentialsDialog(self.client)
        dialog.show()
        wait_until(lambda: dialog._worker is None and dialog._profiles.count() > 0)
        return dialog

    def accept_ready(self, dialog):
        self.client.operation_id = self.operation["operation_id"]
        self.client.operation_profile = self.profile["profile_id"]
        self.client.operation_state = "READY"
        dialog._operation_result(self.operation)

    def test_inputs_are_blank_and_old_server_is_disabled(self):
        self.metadata["providers"][0]["supported"] = False
        dialog = self.open()
        self.assertEqual(dialog._app_key.text(), "")
        self.assertEqual(dialog._secret_key.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse(dialog._prepare_button.isEnabled())
        self.assertFalse(dialog._create_button.isEnabled())
        self.assertFalse(dialog._settings_button.isEnabled())

    def test_tr_settings_selects_shared_account_and_separately_displays_realtime_source(self):
        dialog = self.open()
        execution = {"scope": self.settings["settings"]["scope"], "credential_profile_id": self.profile["profile_id"],
                     "binding_revision": 1}
        realtime = {"scope": {"broker": "kiwoom", "environment": "real", "account_ref": str(uuid.uuid4())},
                    "credential_profile_id": "market-realtime", "binding_revision": 2}
        legacy = {"settings": {"mode": "separated", "execution_target": None, "data_target": None, "revision": 0},
                  "status": "legacy", "applied_revision": None, "effective_mode": None, "execution_ready": None,
                  "realtime_market": {"environment": "real", "credential_profile_id": "market-realtime"},
                  "targets": [(execution, "모의 매매"), (realtime, "실전 시세")]}
        with patch.object(self.client, "load_tr_connections", return_value=legacy), \
             patch.object(self.client, "update_tr_connections", side_effect=RuntimeError("응답 유실")) as update:
            dialog._tr_reload.click()
            wait_until(lambda: dialog._worker is None and dialog._tr_settings is not None)
            self.assertFalse(dialog._tr_apply.isEnabled())
            self.assertIn("실시간 0B 공통 입력: 실전 시세", dialog._tr_realtime.text())
            dialog._tr_execution.setCurrentIndex(0)
            self.assertFalse(dialog._tr_apply.isEnabled())
            dialog._tr_mode.setCurrentIndex(dialog._tr_mode.findData("integrated"))
            self.assertFalse(dialog._tr_data.isEnabled())
            dialog._tr_apply.click()
            wait_until(lambda: dialog._worker is None)
            update.assert_called_once_with({"mode": "integrated", "execution_target": execution, "data_target": execution}, 0)
            self.assertFalse(dialog._tr_apply.isEnabled())
            self.assertIn("다시 불러오세요", dialog._tr_status.text())

    def test_prepare_clears_keys_and_prevents_duplicate_clicks(self):
        dialog = self.open()
        started, release = Event(), Event()
        def prepare(*args):
            started.set(); release.wait(2)
            return self.operation
        with patch.object(self.client, "prepare_mock", side_effect=prepare) as request:
            dialog._app_key.setText("ephemeral-key"); dialog._secret_key.setText("ephemeral-secret")
            dialog._prepare_button.click()
            worker = dialog._worker
            try:
                wait_until(started.is_set)
                self.assertEqual(dialog._app_key.text(), "")
                self.assertEqual(dialog._secret_key.text(), "")
                dialog._prepare_button.click()
                self.assertEqual(request.call_count, 1)
            finally:
                release.set(); worker.wait(3000)
            wait_until(lambda: dialog._worker is None)
        self.assertTrue(dialog._apply_button.isEnabled())
        self.assertIn(self.profile["account_ref"], dialog._preview.text())

    def test_slow_request_keeps_gui_responsive_and_survives_close(self):
        started, release = Event(), Event()
        def delayed():
            started.set(); release.wait(2)
            return self.metadata
        self.load.side_effect = delayed
        dialog = NasCredentialsDialog(self.client); dialog.show()
        worker = dialog._worker
        try:
            wait_until(started.is_set)
            ticks = []; QTimer.singleShot(0, lambda: ticks.append(True))
            wait_until(lambda: bool(ticks))
            dialog._app_key.setText("ephemeral-key")
            dialog.reject()
            self.assertEqual(dialog._app_key.text(), "")
            dialog.deleteLater(); dispose_dialogs()
        finally:
            release.set(); worker.wait(3000)
            self.assertFalse(worker.isRunning())
            self.app.processEvents()

    def test_new_profile_is_selected_and_market_profile_is_hidden(self):
        self.metadata["profiles"].append({**self.profile, "profile_id": "nas-main-mock-default"})
        dialog = self.open()
        self.assertEqual(dialog._profiles.count(), 1)
        def create(label):
            self.metadata["profiles"].append({**self.profile, "profile_id": "new-account", "account_ref": None})
            return {"profile_id": "new-account"}
        with patch.object(self.client, "create_mock_profile", side_effect=create):
            dialog._new_label.setText("새 계좌"); dialog._create_button.click()
            wait_until(lambda: dialog._worker is None and (dialog._profile() or {}).get("profile_id") == "new-account")

    def test_disable_requires_explicit_apply_and_preserves_history_message(self):
        dialog = self.open()
        operation = {**self.operation, "disabled": True}
        with patch.object(self.client, "prepare_mock", return_value=operation) as prepare, patch.object(self.client, "apply") as apply:
            dialog._disable_button.click()
            wait_until(lambda: dialog._worker is None)
            prepare.assert_called_once_with("nas-mock-default", 2, disabled=True)
            apply.assert_not_called()
            self.assertIn("매매 이력은 유지", dialog._preview.text())
            self.assertTrue(dialog._apply_button.isEnabled())

    def test_disconnected_account_is_hidden_by_default_and_can_be_shown(self):
        old = {**self.profile, "profile_id": "nas-mock-default", "label": "", "disabled": True,
               "configured": False, "runtime": "ACTIVE"}
        active = {**self.profile, "profile_id": "new-mock", "label": "새 모의계좌",
                  "account_ref": str(uuid.uuid4())}
        self.metadata["profiles"] = [old, active]
        dialog = self.open()

        self.assertEqual(dialog._profiles.count(), 1)
        self.assertEqual(dialog._profiles.itemData(0)["profile_id"], "new-mock")

        dialog._show_disconnected.setChecked(True)
        wait_until(lambda: dialog._worker is None and dialog._profiles.count() == 2)
        labels = [dialog._profiles.itemText(index) for index in range(dialog._profiles.count())]
        self.assertTrue(any("이전 모의계좌" in label and "연결 해제됨" in label for label in labels))

    def test_delete_requires_disconnected_profile_and_preserves_history_message(self):
        dialog = self.open()
        self.assertFalse(dialog._delete_button.isEnabled())
        disconnected = {**self.profile, "disabled": True, "configured": False}
        dialog._profiles.setItemData(0, disconnected)
        dialog._refresh()
        self.assertTrue(dialog._delete_button.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question, patch.object(self.client, "delete_account_profile") as delete:
            dialog._delete_button.click()
            delete.assert_not_called()
            self.assertIn("이력", question.call_args.args[2])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.client, "delete_account_profile", return_value={
                 "provider": "kiwoom_mock", "profile_id": self.profile["profile_id"],
                 "lifecycle_state": "archived",
             }) as delete:
            dialog._delete_button.click()
            wait_until(lambda: delete.call_count == 1)
            delete.assert_called_once_with(self.profile["profile_id"], 2)

    def test_existing_account_name_can_be_changed_without_reconnecting(self):
        dialog = self.open()

        def rename(profile_id, revision, label):
            self.profile["label"] = label
            return {"provider": "kiwoom_mock", "profile_id": profile_id, "label": label}

        with patch.object(QInputDialog, "getText", return_value=("단타 모의", True)), \
             patch.object(self.client, "rename_account_profile", side_effect=rename) as request, \
             patch.object(self.client, "prepare_mock") as prepare, patch.object(self.client, "apply") as apply:
            dialog._rename_button.click()
            wait_until(lambda: dialog._worker is None and "단타 모의" in dialog._profiles.currentText())
            prepare.assert_not_called()
            apply.assert_not_called()

        request.assert_called_once_with("nas-mock-default", 2, "단타 모의")
        self.assertTrue(dialog._monitor.isChecked())
        self.assertTrue(dialog._orders.isChecked())

    def test_different_account_is_not_applied_and_failure_stays_visible(self):
        dialog = self.open()
        with patch.object(self.client, "apply") as apply:
            dialog._operation_result({**self.operation, "state": "FAILED", "error_code": "ACCOUNT_CHANGED",
                "target_account_ref": None})
            wait_until(lambda: dialog._worker is None)
            self.assertIn("새 계좌 추가", dialog._preview.text())
            self.assertEqual(dialog._status.text(), "적용 실패")
            self.assertFalse(dialog._apply_button.isEnabled())
            apply.assert_not_called()

    def test_apply_timeout_polls_same_operation_and_blocks_second_apply(self):
        dialog = self.open(); self.accept_ready(dialog)
        def timeout(revision, ref):
            self.client.operation_state = "DRAINING"
            raise RuntimeError("상태를 확인하세요.")
        with patch.object(self.client, "apply", side_effect=timeout) as apply, patch.object(self.client, "status",
                return_value={**self.operation, "state": "ACTIVE"}) as status:
            dialog._apply_button.click()
            wait_until(lambda: status.call_count == 1 and dialog._worker is None)
            apply.assert_called_once_with(2, self.profile["account_ref"])
            self.assertEqual(dialog._operation["state"], "ACTIVE")
            self.assertFalse(dialog._apply_button.isEnabled())

    def test_reopening_restores_safe_operation_but_not_keys(self):
        first = self.open(); self.accept_ready(first)
        first._app_key.setText("ephemeral-key"); first.reject()
        with patch.object(self.client, "status", return_value=self.operation) as status:
            second = self.open()
            status.assert_called_once()
            self.assertTrue(second._apply_button.isEnabled())
            self.assertEqual(second._app_key.text(), "")

    def test_settings_timeout_requires_reload_and_monitor_off_clears_orders(self):
        dialog = self.open()
        dialog._monitor.setChecked(False)
        self.assertFalse(dialog._orders.isChecked())
        with patch.object(self.client, "update_account", side_effect=RuntimeError("응답 확인 필요")) as update:
            dialog._settings_button.click()
            wait_until(lambda: dialog._worker is None)
            update.assert_called_once_with(self.profile["account_ref"], "nas-mock-default", 4,
                monitor_enabled=False, mock_order_enabled=False)
            self.assertFalse(dialog._settings_button.isEnabled())
            dialog._reload_button.click()
            wait_until(lambda: dialog._worker is None and dialog._settings_button.isEnabled())

    def test_parent_reuses_client_and_never_writes_pc_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); api = root / "api.env"
            api.write_text("KIWOOM_APP_KEY=existing-pc-key\n")
            config = DataSourceConfig(root / "data_source.json"); config.save(self.client.source)
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            parent = ApiSettingsDialog(api, section="nas")
            with patch("kiwoom_monitor.presentation.nas_credentials_dialog.NasCredentialsDialog") as child:
                parent._open_nas_credentials(); parent._open_nas_credentials()
                self.assertIs(child.call_args_list[0].args[0], child.call_args_list[1].args[0])
                parent._local_fallback.setChecked(True)
                parent._open_nas_credentials()
                self.assertIs(child.call_args_list[0].args[0], child.call_args_list[2].args[0])
            self.assertEqual(original, {p.name: p.read_bytes() for p in root.iterdir()})


class NasRealCredentialsDialogTests(unittest.TestCase):
    setUpClass = NasCredentialsDialogTests.__dict__["setUpClass"]
    tearDown = NasCredentialsDialogTests.tearDown
    open = NasCredentialsDialogTests.open

    def setUp(self):
        NasCredentialsDialogTests.setUp(self)
        self.client.provider = "kiwoom_real"
        self.profile.update(provider="kiwoom_real", profile_id="nas-real-default", label="실전계좌")
        self.metadata["providers"][0]["provider"] = "kiwoom_real"
        self.settings["settings"]["scope"]["environment"] = "real"
        self.settings["settings"].update(active_profile_id="nas-real-default", mock_order_enabled=False)
        self.operation.update(provider="kiwoom_real", profile_id="nas-real-default")
        self.settings["monitor_status"] = {"state": "waiting", "realtime": {"state": "waiting"}}

    def test_real_labels_hide_orders_and_wait_for_upstream_registration(self):
        dialog = self.open()
        self.assertIn("실전", dialog.windowTitle())
        self.assertFalse(dialog._global)
        self.assertTrue(dialog._orders.isHidden())
        self.assertFalse(dialog._orders.isEnabled())
        self.assertIn("등록 승인 대기", dialog._connection_status.text())
        self.assertNotIn("승인됨", dialog._connection_status.text())
        self.settings["monitor_status"]["realtime"]["state"] = "ready"
        dialog._settings_loaded(self.settings)
        self.assertIn("실시간 등록 승인됨", dialog._connection_status.text())
        self.settings["monitor_status"]["realtime"]["error_code"] = "REAL_ACCOUNT_EVENTS_PENDING"
        dialog._settings_loaded(self.settings)
        self.assertIn("수신·저장 오류 확인 필요", dialog._connection_status.text())
        self.settings["monitor_status"] = {"realtime": "invalid"}
        dialog._settings_loaded(self.settings)
        self.assertIn("실시간 상태 확인 필요", dialog._connection_status.text())
        self.assertNotIn("승인됨", dialog._connection_status.text())

    def test_real_prepare_runs_in_worker_and_clears_secrets(self):
        dialog = self.open()
        started, release = Event(), Event()
        def prepare(*args):
            started.set(); release.wait(2)
            return self.operation
        with patch.object(self.client, "prepare_account", side_effect=prepare) as request:
            dialog._app_key.setText("ephemeral-real"); dialog._secret_key.setText("ephemeral-secret")
            dialog._prepare_button.click()
            worker = dialog._worker
            try:
                wait_until(started.is_set)
                self.assertEqual(dialog._app_key.text(), "")
                self.assertEqual(dialog._secret_key.text(), "")
                self.assertFalse(dialog._prepare_button.isEnabled())
            finally:
                release.set(); worker.wait(5000)
            wait_until(lambda: dialog._worker is None)
            request.assert_called_once_with("nas-real-default", 2, "ephemeral-real", "ephemeral-secret")
            self.assertTrue(dialog._apply_button.isEnabled())

    def test_real_settings_off_sends_no_order_permission_and_requires_reload_after_timeout(self):
        dialog = self.open()
        dialog._monitor.setChecked(False)
        with patch.object(self.client, "update_account", side_effect=RuntimeError("응답 확인 필요")) as update:
            dialog._settings_button.click()
            wait_until(lambda: dialog._worker is None)
            update.assert_called_once_with(self.profile["account_ref"], "nas-real-default", 4,
                monitor_enabled=False, mock_order_enabled=False)
            self.assertFalse(dialog._settings_button.isEnabled())
            dialog._reload_button.click()
            wait_until(lambda: dialog._worker is None and dialog._settings_button.isEnabled())

    def test_parent_reuses_separate_real_client_without_saving_pc_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); api = root / "api.env"
            api.write_text("KIWOOM_APP_KEY=existing-pc-key\n")
            DataSourceConfig(root / "data_source.json").save(self.client.source)
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            parent = ApiSettingsDialog(api, section="nas")
            with patch("kiwoom_monitor.presentation.nas_credentials_dialog.NasCredentialsDialog") as child:
                parent._open_nas_provider_credentials("kiwoom_real")
                parent._open_nas_provider_credentials("kiwoom_real")
                parent._open_nas_credentials()
                self.assertIs(child.call_args_list[0].args[0], child.call_args_list[1].args[0])
                self.assertEqual(child.call_args_list[0].args[0].provider, "kiwoom_real")
                self.assertIsNot(child.call_args_list[0].args[0], child.call_args_list[2].args[0])
            self.assertEqual(original, {p.name: p.read_bytes() for p in root.iterdir()})
