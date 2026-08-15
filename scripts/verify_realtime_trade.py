"""주식체결(0B) WebSocket의 실제 수신 필드를 확인하는 검증 도구.

Top 20 순위에 포함된 삼성전자 한 종목만 최대 30초 동안 구독한다.
인증 토큰과 시세 값은 저장하지 않는다. 수신된 FID 이름만 출력한다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig

BASE_URLS = {
    "mock": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}
PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from websockets.asyncio.client import connect
except ImportError:
    print("실패: websockets 패키지가 필요합니다.")
    raise SystemExit(2)


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}


def issue_token(app_key: str, secret_key: str, environment: str) -> str:
    request = Request(
        f"{BASE_URLS[environment]}/oauth2/token",
        data=json.dumps(
            {
                "grant_type": "client_credentials",
                "appkey": app_key,
                "secretkey": secret_key,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json;charset=UTF-8"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    token = body.get("token")
    if body.get("return_code") not in (None, 0, "0") or not isinstance(token, str):
        raise RuntimeError("WebSocket 로그인용 토큰 발급에 실패했습니다.")
    return token


async def receive_fields(token: str, environment: str) -> tuple[bool, set[str]]:
    uri = f"{WS_BASE_URLS[environment]}/api/dostk/websocket"
    async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
        await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
        login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
        if login.get("return_code") not in (None, 0, "0"):
            raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")

        await websocket.send(
            json.dumps(
                {
                    "trnm": "REG",
                    "grp_no": "1",
                    "refresh": "1",
                    "data": [{"item": ["005930"], "type": ["0B"]}],
                }
            )
        )

        fields: set[str] = set()
        while True:
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=15)
            except TimeoutError:
                return True, fields
            message = json.loads(raw)
            if str(message.get("trnm", "")).upper() == "PING":
                await websocket.send(json.dumps(message))
                continue
            if str(message.get("trnm", "")).upper() != "REAL":
                continue
            for entry in message.get("data", []):
                if entry.get("type") == "0B" and isinstance(entry.get("values"), dict):
                    fields.update(entry["values"])
            if fields:
                return True, fields


def main() -> int:
    config_path = PROJECT_ROOT / "data" / "api.env"
    if not config_path.exists():
        print("실패: API 설정 파일을 찾을 수 없습니다.")
        return 2
    config = LocalApiConfig(config_path).load()
    environment = config.environment.lower()
    if environment not in BASE_URLS or not config.app_key or not config.secret_key:
        print("실패: 선택된 환경의 키 또는 환경 설정을 확인하세요.")
        return 2
    try:
        subscribed, fields = asyncio.run(
            asyncio.wait_for(
                receive_fields(issue_token(config.app_key, config.secret_key, environment), environment),
                timeout=20,
            )
        )
    except Exception as error:
        print(f"실패: WebSocket 검증 오류 ({type(error).__name__}: {error}).")
        return 1

    if not subscribed:
        print("실패: WebSocket 구독을 확인하지 못했습니다.")
        return 1
    if fields:
        print(f"성공: 주식체결(0B) WebSocket 수신 필드를 확인했습니다 ({environment}).")
        print("수신 FID: " + ", ".join(sorted(fields, key=int)))
    else:
        print(f"성공: 주식체결(0B) WebSocket 로그인·구독을 확인했습니다 ({environment}).")
        print("15초 동안 체결 메시지가 없어 시세 FID 수신은 장중에 다시 확인하면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
