"""ka00198 실시간종목조회순위의 응답 구조를 확인하는 검증 도구.

인증 토큰은 메모리에서만 사용하며 출력·저장하지 않는다.
"""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from verify_rest_connection import BASE_URLS, ENV_PATH, load_env


def post_json(url: str, headers: dict[str, str], body: dict[str, str]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if not ENV_PATH.exists():
        print("실패: .env 파일을 찾을 수 없습니다.")
        return 2

    config = load_env(ENV_PATH)
    environment = config.get("KIWOOM_ENVIRONMENT", "").lower()
    app_key = config.get("KIWOOM_APP_KEY", "")
    secret_key = config.get("KIWOOM_SECRET_KEY", "")
    if environment not in BASE_URLS or not app_key or not secret_key:
        print("실패: .env의 키 또는 환경 설정을 확인하세요.")
        return 2

    try:
        auth = post_json(
            f"{BASE_URLS[environment]}/oauth2/token",
            {"Content-Type": "application/json;charset=UTF-8"},
            {
                "grant_type": "client_credentials",
                "appkey": app_key,
                "secretkey": secret_key,
            },
        )
        token = auth.get("token")
        if auth.get("return_code") not in (None, 0, "0") or not isinstance(token, str):
            print("실패: 순위 조회용 토큰 발급에 실패했습니다.")
            return 1

        response = post_json(
            f"{BASE_URLS[environment]}/api/dostk/stkinfo",
            {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka00198",
            },
            {"qry_tp": "1"},
        )
    except HTTPError as error:
        print(f"실패: 서버가 HTTP {error.code}을 반환했습니다.")
        return 1
    except URLError as error:
        print(f"실패: 서버에 연결할 수 없습니다 ({error.reason}).")
        return 1

    return_code = response.get("return_code")
    if return_code not in (None, 0, "0"):
        print(f"실패: 순위 조회 오류 ({return_code}: {response.get('return_msg', '')}).")
        return 1

    records = response.get("item_inq_rank", [])
    if not isinstance(records, list):
        print("실패: item_inq_rank가 목록 형식이 아닙니다.")
        return 1

    field_names = sorted(
        {
            field_name
            for record in records
            if isinstance(record, dict)
            for field_name in record
        }
    )
    print(f"성공: ka00198 순위 조회 응답을 확인했습니다 ({environment}, qry_tp=1).")
    print(f"수신 종목 수: {len(records)}")
    print("확인된 필드: " + ", ".join(field_names))
    if records and isinstance(records[0], dict):
        print("첫 번째 종목의 필드 구조: " + ", ".join(sorted(records[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

