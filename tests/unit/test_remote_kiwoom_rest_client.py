from __future__ import annotations

import io
import json
import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient


class Response:
    def __init__(self, document: dict[str, object]) -> None:
        self._body = json.dumps(document).encode("utf-8")

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self) -> bytes: return self._body


class RemoteKiwoomRestClientTests(unittest.TestCase):
    def test_matches_local_client_request_contract(self) -> None:
        captured = {}

        def opener(request, **kwargs):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = kwargs["timeout"]
            return Response({"payload": {"return_code": 0}, "has_next": True, "next_key": "page2"})

        client = RemoteKiwoomRestClient("https://nas.example.test/", "secret", opener=opener)
        result = client.request_with_continuation("ka10080", "/api/dostk/chart", {"stk_cd": "005930"})
        self.assertEqual(({"return_code": 0}, True, "page2"), result)
        self.assertEqual("https://nas.example.test/api/v1/kiwoom/query", captured["url"])
        self.assertEqual("Bearer secret", captured["authorization"])
        self.assertEqual("ka10080", captured["body"]["api_id"])


if __name__ == "__main__":
    unittest.main()
