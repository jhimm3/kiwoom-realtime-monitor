"""실제 NAS 중단과 복구 사이에서 같은 조회 클라이언트의 장애전환을 검증한다."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient


def _server_is_ready(url: str, token: str) -> bool:
    request = Request(
        f"{url.rstrip('/')}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=2) as response:
            value = json.loads(response.read().decode("utf-8"))
        return isinstance(value, dict) and value.get("status") == "ok"
    except (URLError, TimeoutError, OSError, ValueError):
        return False


def _wait_for_state(url: str, token: str, expected: bool, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _server_is_ready(url, token) is expected:
            return True
        time.sleep(1)
    return False


def _ranking_request(client: object) -> dict[str, object]:
    value = client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
    if not isinstance(value, dict):
        raise RuntimeError("ranking_response_not_mapping")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--outage-timeout", type=float, default=300)
    parser.add_argument("--recovery-timeout", type=float, default=300)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    source = DataSourceConfig(data_dir / "data_source.json").load()
    local_settings = LocalApiConfig(data_dir / "api.env").load()
    if source.mode != "personal_server" or not source.local_fallback_enabled:
        print(json.dumps({"status": "error", "reason": "nas_failover_not_enabled"}))
        return 2
    if not local_settings.app_key or not local_settings.secret_key:
        print(json.dumps({"status": "error", "reason": "local_kiwoom_credentials_missing"}))
        return 2

    primary = RemoteKiwoomRestClient(source.server_url, source.access_token, timeout_seconds=5)
    fallback = KiwoomRestClient(local_settings)
    client = FailoverKiwoomRestClient(primary, fallback, retry_primary_seconds=15)

    _ranking_request(client)
    print(json.dumps({"status": "waiting_for_nas_stop"}), flush=True)
    if not _wait_for_state(source.server_url, source.access_token, False, args.outage_timeout):
        print(json.dumps({"status": "error", "reason": "nas_did_not_stop"}))
        return 3

    fallback_payload = _ranking_request(client)
    if not getattr(client, "_using_fallback", False):
        print(json.dumps({"status": "error", "reason": "fallback_route_not_selected"}))
        return 4
    print(json.dumps({
        "status": "local_fallback_ok",
        "ranking_rows": len(fallback_payload.get("item_inq_rank", [])),
        "next": "start_nas",
    }), flush=True)

    if not _wait_for_state(source.server_url, source.access_token, True, args.recovery_timeout):
        print(json.dumps({"status": "error", "reason": "nas_did_not_recover"}))
        return 5
    retry_at = float(getattr(client, "_primary_retry_at", 0.0))
    if retry_at > time.monotonic():
        time.sleep(retry_at - time.monotonic() + 0.2)
    recovered_payload = _ranking_request(client)
    recovered = not getattr(client, "_using_fallback", True)
    print(json.dumps({
        "status": "ok" if recovered else "error",
        "central_initial": True,
        "local_fallback": True,
        "central_recovered": recovered,
        "ranking_rows_after_recovery": len(recovered_payload.get("item_inq_rank", [])),
    }))
    return 0 if recovered else 6


if __name__ == "__main__":
    sys.exit(main())
