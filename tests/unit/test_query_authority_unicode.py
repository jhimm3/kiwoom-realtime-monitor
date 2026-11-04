"""Exact non-ASCII bearer comparison over actual HTTP and WS handshakes."""
from __future__ import annotations

import base64
import json
import socket
import struct
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx

from tests.unit.test_query_authority import settings, query_app, wire_server


class CloseRecorder:
    def __init__(self, app, *, values):
        self.app, self.values = app, values

    async def __call__(self, scope, receive, send):
        async def recorded(message):
            if scope["type"] == "websocket" and message["type"] == "websocket.close":
                self.values.append(message.get("code"))
            await send(message)
        await self.app(scope, receive, recorded)


class RawHandshake:
    """Send literal HTTP header octets, including ISO-8859-1 obs-text."""
    def __init__(self, base, token):
        address = urlsplit(base)
        self.socket = socket.create_connection((address.hostname, address.port), timeout=5)
        self.reader = self.socket.makefile("rb")
        key = base64.b64encode(b"query-auth-wskey")
        self.socket.sendall(b"GET /api/v1/realtime HTTP/1.1\r\nHost: " +
            f"{address.hostname}:{address.port}".encode("ascii") +
            b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key +
            b"\r\nAuthorization: Bearer " + token.encode("latin-1") + b"\r\n\r\n")
        self.status = int(self.reader.readline().split()[1])
        while True:
            line = self.reader.readline()
            if line == b"\r\n": break
            if not line: raise AssertionError("incomplete handshake headers")

    def receive_json(self):
        first, second = self.reader.read(2)
        length = second & 127
        if length == 126: length = struct.unpack("!H", self.reader.read(2))[0]
        elif length == 127: length = struct.unpack("!Q", self.reader.read(8))[0]
        if second & 128:
            raise AssertionError("server frame must not be masked")
        data = self.reader.read(length)
        if first & 15 != 1:
            raise AssertionError("expected text frame")
        return json.loads(data)

    def close(self):
        if self.status == 101:
            mask, payload = b"QAUTH"[:4], struct.pack("!H", 1000)
            self.socket.sendall(b"\x88\x82" + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))
        self.reader.close()
        self.socket.close()


class QueryAuthorityUnicodeTests(unittest.TestCase):
    def test_unknown_non_ascii_header_fails_closed_http401_and_ws4401(self):
        app = query_app(settings())
        closes = []
        app.add_middleware(CloseRecorder, values=closes)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/v1/settings/credentials")
        spy = AsyncMock(wraps=route.dependant.call)
        with wire_server(app) as (base, _loop), patch.object(route.dependant, "call", spy), \
             httpx.Client(base_url=base, timeout=5) as client:
            with self.subTest(protocol="HTTP"):
                response = client.get("/api/v1/settings/credentials",
                                      headers=[(b"authorization", b"Bearer invalid-\xe9")])
                self.assertEqual(401, response.status_code)
            with self.subTest(protocol="WebSocket"):
                wire = RawHandshake(base, "invalid-\u00e9")
                try:
                    self.assertEqual(403, wire.status, "preaccept ASGI4401 must deny the handshake")
                    self.assertEqual([4401], closes)
                finally:
                    wire.close()
            spy.assert_not_called()

    def test_configured_non_ascii_tokens_preserve_query_and_admin_authority(self):
        configured = replace(settings(query="query-\u00e9"), access_token="admin-\u00e9")
        app = query_app(configured)
        closes = []
        app.add_middleware(CloseRecorder, values=closes)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/v1/settings/credentials")
        spy = AsyncMock(wraps=route.dependant.call)
        with wire_server(app) as (base, _loop), patch.object(route.dependant, "call", spy), \
             httpx.Client(base_url=base, timeout=5) as client:
            for token, expected_scope in ((b"query-\xe9", "query"), (b"admin-\xe9", None)):
                with self.subTest(protocol="HTTP", scope=expected_scope):
                    response = client.get("/api/v1/capabilities", headers=[(b"authorization", b"Bearer " + token)])
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(expected_scope, response.json().get("access_scope"))
            with self.subTest(protocol="known query denied command"):
                self.assertEqual(403, client.get("/api/v1/settings/credentials",
                    headers=[(b"authorization", b"Bearer query-\xe9")]).status_code)
                spy.assert_not_called()
            with self.subTest(protocol="different exact token"):
                self.assertEqual(401, client.get("/api/v1/capabilities",
                    headers={"Authorization": "Bearer query-e"}).status_code)
            for token in ("query-\u00e9", "admin-\u00e9"):
                with self.subTest(protocol="WebSocket", scope="query" if token.startswith("query") else "admin"):
                    wire = RawHandshake(base, token)
                    try:
                        self.assertEqual(101, wire.status)
                        self.assertEqual("ready", wire.receive_json()["type"])
                    finally:
                        wire.close()
            self.assertEqual([], closes)


if __name__ == "__main__":
    unittest.main()
