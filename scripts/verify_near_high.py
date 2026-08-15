"""고저가근접(ka10018)의 당일 고가 근접 필드를 확인한다."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from verify_rest_connection import BASE_URLS, ENV_PATH, load_env


def post_json(url: str, body: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if not ENV_PATH.exists():
        print("실패: .env 파일을 찾을 수 없습니다.")
        return 2
    config = load_env(ENV_PATH)
    environment = config.get("KIWOOM_ENVIRONMENT", "").lower()
    if environment not in BASE_URLS or not config.get("KIWOOM_APP_KEY") or not config.get("KIWOOM_SECRET_KEY"):
        print("실패: .env의 키 또는 환경 설정을 확인하세요.")
        return 2
    try:
        auth = post_json(
            f"{BASE_URLS[environment]}/oauth2/token",
            {"grant_type": "client_credentials", "appkey": config["KIWOOM_APP_KEY"], "secretkey": config["KIWOOM_SECRET_KEY"]},
            {"Content-Type": "application/json;charset=UTF-8"},
        )
        token = auth.get("token")
        if auth.get("return_code") not in (None, 0, "0") or not isinstance(token, str):
            print("실패: 고가근접 조회용 토큰 발급에 실패했습니다.")
            return 1
        response = post_json(
            f"{BASE_URLS[environment]}/api/dostk/stkinfo",
            {"high_low_tp": "1", "alacc_rt": "05", "mrkt_tp": "000", "trde_qty_tp": "00000", "stk_cnd": "0", "crd_cnd": "0", "stex_tp": "1"},
            {"Content-Type": "application/json;charset=UTF-8", "authorization": f"Bearer {token}", "api-id": "ka10018"},
        )
    except HTTPError as error:
        print(f"실패: 서버가 HTTP {error.code}을 반환했습니다.")
        return 1
    except URLError as error:
        print(f"실패: 서버에 연결할 수 없습니다 ({error.reason}).")
        return 1
    if response.get("return_code") not in (None, 0, "0"):
        print(f"실패: 고가근접 조회 오류 ({response.get('return_msg', '')}).")
        return 1
    records = response.get("high_low_pric_alacc", [])
    if not isinstance(records, list):
        print("실패: 고저가근접 목록 형식을 확인할 수 없습니다.")
        return 1
    fields = sorted({key for row in records if isinstance(row, dict) for key in row})
    print(f"성공: ka10018 당일 고가 근접 응답을 확인했습니다 ({environment}).")
    print(f"수신 종목 수: {len(records)}")
    print("확인된 필드: " + ", ".join(fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())

