from __future__ import annotations

import asyncio
import json
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore


class CentralServerDbApiConnectionTests(unittest.TestCase):
    def test_market_coverage_api_reads_from_composed_sqlite_store(self) -> None:
        store = SQLiteQueryStore(":memory:")
        settings = CentralServerSettings(
            "sqlite:///:memory:", "private-token", autonomous_top20_enabled=False,
        )
        query = urlencode({
            "kind": "minute_bar",
            "subject": "005930:KRX",
            "start": "2026-09-10T00:00:00+09:00",
            "end": "2026-09-11T00:00:00+09:00",
        }).encode("ascii")

        async def request(app):
            sent = []
            received = False
            response_finished = asyncio.Event()

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await response_finished.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)
                if (message["type"] == "http.response.body"
                        and not message.get("more_body", False)):
                    response_finished.set()

            await app({
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/v1/market/coverage",
                "raw_path": b"/api/v1/market/coverage",
                "query_string": query,
                "root_path": "",
                "headers": [(b"authorization", b"Bearer private-token")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "state": {},
            }, receive, send)
            start = next(message for message in sent if message["type"] == "http.response.start")
            body = b"".join(
                message.get("body", b"")
                for message in sent if message["type"] == "http.response.body"
            )
            return start["status"], json.loads(body)

        try:
            with patch(
                "kiwoom_monitor.central_server.app.create_query_store", return_value=store,
            ):
                app = create_app(settings)
            status, body = asyncio.run(request(app))
            self.assertEqual(200, status)
            self.assertEqual("missing", body["state"])
            self.assertEqual("no_trade_or_no_observation", body["absence_meaning"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
