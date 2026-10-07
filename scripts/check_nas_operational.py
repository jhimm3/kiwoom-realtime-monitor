"""NAS API/DB/WebSocket 운영 경계를 다른 PC에서도 같은 방식으로 점검한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def _request(base_url: str, token: str, path: str) -> dict[str, object]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid_response:{path}")
    return value


async def _websocket_check(base_url: str, token: str) -> dict[str, object]:
    import websockets

    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    url = urlunsplit((scheme, parsed.netloc, "/api/v1/realtime", "", ""))
    async with websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=15, close_timeout=5,
    ) as socket:
        ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
        await socket.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
    return {
        "ready": ready.get("type") == "ready",
        "pong": pong.get("type") == "pong",
    }


def _latest_snapshot(base_url: str, token: str, kind: str) -> dict[str, object]:
    values = _request(base_url, token, f"/api/v1/market/snapshots/{kind}?limit=1")
    snapshots = values.get("snapshots", [])
    if not isinstance(snapshots, list) or not snapshots:
        return {"count": 0, "snapshot_key": ""}
    latest = snapshots[0] if isinstance(snapshots[0], dict) else {}
    return {"count": len(snapshots), "snapshot_key": str(latest.get("snapshot_key", ""))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("KIWOOM_MONITOR_SERVER_URL", ""))
    parser.add_argument("--token", default=os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", ""))
    parser.add_argument("--observe-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    base_url, token = args.url.strip().rstrip("/"), args.token.strip()
    if not base_url or not token:
        print(json.dumps({"status": "error", "reason": "url_and_token_required"}))
        return 2

    try:
        health = _request(base_url, token, "/health")
        capabilities = _request(base_url, token, "/api/v1/capabilities")
        resources = _request(base_url, token, "/api/v1/diagnostics/resources")
        websocket = asyncio.run(_websocket_check(base_url, token))
        initial = {
            kind: _latest_snapshot(base_url, token, kind)
            for kind in ("ranking", "top20_index", "market_state")
        }
        observations: list[dict[str, object]] = []
        deadline = monotonic() + max(0, args.observe_seconds)
        while monotonic() < deadline:
            sleep(min(max(1, args.poll_seconds), max(0.0, deadline - monotonic())))
            sample_resources = _request(base_url, token, "/api/v1/diagnostics/resources")
            observations.append({
                "at": datetime.now(UTC).isoformat(),
                "process_memory_bytes": sample_resources.get("process_memory_bytes"),
                "database_size_bytes": sample_resources.get("database_size_bytes"),
                "ranking": _latest_snapshot(base_url, token, "ranking"),
                "top20_index": _latest_snapshot(base_url, token, "top20_index"),
            })
        result = {
            "status": "ok",
            "server_build": health.get("server_build"),
            "api_version": health.get("api_version"),
            "schema_version": health.get("schema_version"),
            "capabilities": capabilities.get("capabilities", {}),
            "resources": resources,
            "websocket": websocket,
            "initial_snapshots": initial,
            "observations": observations,
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "reason": type(error).__name__, "detail": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
