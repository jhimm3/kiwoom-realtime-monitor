from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


@dataclass(frozen=True)
class DeploymentCheckResult:
    health: bool
    authentication: bool
    database_read: bool
    realtime: bool
    api_version: str = ""
    schema_version: int = 0

    @property
    def ok(self) -> bool:
        return self.health and self.authentication and self.database_read and self.realtime


def check_http(
    server_url: str, token: str, *, opener: Callable[..., Any] = urlopen,
) -> tuple[dict[str, object], dict[str, object]]:
    base = server_url.rstrip("/")
    health = _get_json(opener, Request(f"{base}/health"))
    capabilities = _get_json(opener, Request(
        f"{base}/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"},
    ))
    # 빈 조회도 실제 중앙 저장소를 거치므로 PostgreSQL 읽기 가능 여부를 확인한다.
    _get_json(opener, Request(
        f"{base}/api/v1/content/app_settings?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    ))
    return health, capabilities


async def check_realtime(server_url: str, token: str) -> bool:
    parsed = urlsplit(server_url)
    url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/api/v1/realtime", "", ""))
    async with connect(
        url, additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=10, close_timeout=3,
    ) as websocket:
        document = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        return isinstance(document, dict) and document.get("type") == "ready"


def run_check(server_url: str, token: str, *, opener: Callable[..., Any] = urlopen,
              realtime_checker: Callable[[str, str], Any] | None = None) -> DeploymentCheckResult:
    server_url = server_url.rstrip("/")
    health, capabilities = check_http(server_url, token, opener=opener)
    checker = realtime_checker or check_realtime
    realtime = bool(asyncio.run(checker(server_url, token)))
    return DeploymentCheckResult(
        health=health.get("status") == "ok",
        authentication="capabilities" in capabilities,
        database_read=True,
        realtime=realtime,
        api_version=str(capabilities.get("api_version", "")),
        schema_version=int(capabilities.get("schema_version", 0)),
    )


def _get_json(opener: Callable[..., Any], request: Request) -> dict[str, object]:
    with opener(request, timeout=10, context=system_ssl_context()) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("중앙 서버 응답 형식이 올바르지 않습니다.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="키움 개인 중앙 서버 배포 상태 확인")
    parser.add_argument("--url", default=os.environ.get("KIWOOM_SERVER_URL", ""))
    parser.add_argument(
        "--token",
        default=(
            os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", "")
            or os.environ.get("KIWOOM_SERVER_ACCESS_TOKEN", "")
        ),
    )
    args = parser.parse_args()
    if not args.url or not args.token:
        raise SystemExit("--url과 --token 또는 대응 환경 변수가 필요합니다.")
    try:
        result = run_check(args.url, args.token)
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"배포 점검 실패: {error}") from error
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
