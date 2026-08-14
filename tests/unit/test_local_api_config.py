from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig


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
