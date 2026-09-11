from __future__ import annotations

import json
import unittest

from kiwoom_monitor.central_server.deployment_check import run_check


class Response:
    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._value).encode("utf-8")


class CentralDeploymentCheckTests(unittest.TestCase):
    def test_checks_health_auth_database_and_realtime(self) -> None:
        requested: list[str] = []

        def opener(request, **kwargs):
            requested.append(request.full_url)
            if request.full_url.endswith("/health"):
                return Response({"status": "ok", "api_version": "v1", "schema_version": 1})
            if "/capabilities" in request.full_url:
                self.assertEqual("Bearer token", request.headers["Authorization"])
                return Response({"api_version": "v1", "schema_version": 1, "capabilities": {}})
            return Response({"collection": "app_settings", "documents": []})

        async def realtime_checker(url: str, token: str) -> bool:
            return url == "https://nas.example" and token == "token"

        result = run_check(
            "https://nas.example/", "token", opener=opener, realtime_checker=realtime_checker,
        )
        self.assertTrue(result.ok)
        self.assertEqual(3, len(requested))


if __name__ == "__main__":
    unittest.main()
