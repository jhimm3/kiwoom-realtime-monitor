from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings


class _Response:
    def __init__(self, document):
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return json.dumps(self.document).encode()


class OperationalSettingsClientTests(unittest.TestCase):
    def test_ram_selection_uses_separate_revision_and_requires_loaded_selection(self):
        client = CentralOperationalSettingsClient(DataSourceSettings("personal_server", "https://nas.test", "token"))
        calls = []
        def open_request(request, **kwargs):
            calls.append(request)
            self.assertEqual(10, kwargs["timeout"])
            return _Response({"revision": 7 if request.full_url.endswith("/selection") else 3})
        with patch("kiwoom_monitor.infrastructure.central_operational_settings.urlopen", open_request):
            client.load()
            with self.assertRaises(ValueError):
                client.save_ram_shadow_selection({"enabled": True})
            client.load_ram_shadow_selection()
            client.save_ram_shadow_selection({"enabled": False})
            client.save({"dart_enabled": True})
        self.assertEqual(7, json.loads(calls[2].data)["expected_revision"])
        self.assertEqual(3, json.loads(calls[3].data)["expected_revision"])
        self.assertTrue(calls[2].full_url.endswith("/api/v1/research/ram-shadow/selection"))
        self.assertEqual("Bearer token", calls[2].get_header("Authorization"))

    def test_update_sends_only_changes_with_previously_loaded_revision(self):
        client = CentralOperationalSettingsClient(DataSourceSettings(
            "personal_server", "https://nas.example.test", "token",
        ))
        calls = []

        def open_request(request, **_kwargs):
            calls.append(request)
            return _Response({"revision": len(calls), "shadow_candidate_enabled": True})

        with patch("kiwoom_monitor.infrastructure.central_operational_settings.urlopen", open_request):
            client.load()
            client.update({"ai_daily_limit": 42})

        self.assertEqual(["GET", "PUT"], [request.method for request in calls])
        self.assertEqual({"ai_daily_limit": 42, "expected_revision": 1},
                         json.loads(calls[1].data))

    def test_older_server_without_revision_accepts_partial_save(self):
        client = CentralOperationalSettingsClient(DataSourceSettings(
            "personal_server", "https://nas.example.test", "token",
        ))
        with patch("kiwoom_monitor.infrastructure.central_operational_settings.urlopen",
                   return_value=_Response({})) as opened:
            client.load()
            client.save({"dart_enabled": True})
        self.assertEqual({"dart_enabled": True}, json.loads(opened.call_args.args[0].data))
