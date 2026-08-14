"""키움 REST API의 인증 설정만 확인하는 일회성 검증 도구.

액세스 토큰·App Key·Secret Key는 출력하거나 파일에 저장하지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "data" / "api.env"
BASE_URLS = {
    "mock": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}


def main() -> int:
    if not CONFIG_PATH.exists():
        print("실패: API 설정 파일을 찾을 수 없습니다. 앱에서 API 설정을 먼저 저장하세요.")
        return 2

    from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig

    settings = LocalApiConfig(CONFIG_PATH).load()
    app_key = settings.app_key
    secret_key = settings.secret_key
    environment = settings.environment.lower()

    if not app_key or not secret_key:
        print("실패: 선택된 환경의 App Key 또는 Secret Key가 비어 있습니다.")
        return 2
    if environment not in BASE_URLS:
        print("실패: KIWOOM_ENVIRONMENT는 mock 또는 real이어야 합니다.")
        return 2

    payload = json.dumps(
        {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": secret_key,
        }
    ).encode("utf-8")
    request = Request(
        f"{BASE_URLS[environment]}/oauth2/token",
        data=payload,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        print(f"실패: 인증 서버가 HTTP {error.code}을 반환했습니다.")
        return 1
    except URLError as error:
        print(f"실패: 인증 서버에 연결할 수 없습니다 ({error.reason}).")
        return 1
    except TimeoutError:
        print("실패: 인증 서버 응답 시간이 초과되었습니다.")
        return 1

    return_code = body.get("return_code")
    is_success_code = return_code in (None, 0, "0")
    if not is_success_code or not body.get("token"):
        message = str(body.get("return_msg", "응답 메시지 없음")).replace("\n", " ")
        print(
            "실패: 토큰이 발급되지 않았습니다 "
            f"(코드: {return_code!s}, 메시지: {message})."
        )
        return 1

    print(f"성공: 키움 REST API {environment} 환경에서 토큰 발급을 확인했습니다.")
    print("토큰 값은 출력하거나 저장하지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
