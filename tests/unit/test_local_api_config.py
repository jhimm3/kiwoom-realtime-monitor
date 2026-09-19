from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.local_account_binding import LocalAccountBindingConfig


class LocalApiConfigTests(unittest.TestCase):
    def test_returns_empty_profiles_when_configuration_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalApiConfig(Path(directory) / "data" / "api.env")

            self.assertEqual(ApiProfiles(), config.load_profiles())
            self.assertEqual("", config.load().app_key)

    def test_encrypts_and_restores_api_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "api.env"
            config = LocalApiConfig(path)
            config.save_profiles(ApiProfiles("mock-app", "mock-secret", "real-app", "real-secret", "real"))

            stored = path.read_text(encoding="utf-8")
            self.assertIn("KIWOOM_CONFIG_ENCRYPTED=", stored)
            self.assertNotIn("mock-app", stored)
            self.assertNotIn("real-secret", stored)

            loaded = config.load()
            self.assertEqual("real-app", loaded.app_key)
            self.assertEqual("real-secret", loaded.secret_key)
            self.assertEqual("real", loaded.environment)

    def test_account_binding_mirror_is_protected_and_keeps_latest_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "account-bindings.dat"
            config = LocalAccountBindingConfig(path)
            account_ref = str(uuid.uuid4())
            first = AccountBinding(
                "mock-default", AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
                1, datetime(2026, 9, 13, tzinfo=UTC),
            )
            latest = AccountBinding(
                "mock-default", first.scope, 2, datetime(2026, 9, 13, 1, tzinfo=UTC),
            )
            config.save_binding(first)
            config.save_binding(latest)

            raw = path.read_text(encoding="utf-8")
            loaded = config.load_bindings()
            self.assertIn("KIWOOM_ACCOUNT_BINDINGS_ENCRYPTED=", raw)
            self.assertNotIn(account_ref, raw)
            self.assertEqual((latest,), loaded)
            with self.assertRaisesRegex(ValueError, "older revision"):
                config.save_binding(first)
