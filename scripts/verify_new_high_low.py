"""Verify the REST response shape for ka10016 new-high/new-low screening."""

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
        print("Failure: .env file was not found.")
        return 2

    config = load_env(ENV_PATH)
    environment = config.get("KIWOOM_ENVIRONMENT", "").lower()
    if environment not in BASE_URLS or not config.get("KIWOOM_APP_KEY") or not config.get("KIWOOM_SECRET_KEY"):
        print("Failure: check .env credentials and environment.")
        return 2

    try:
        auth = post_json(
            f"{BASE_URLS[environment]}/oauth2/token",
            {"grant_type": "client_credentials", "appkey": config["KIWOOM_APP_KEY"], "secretkey": config["KIWOOM_SECRET_KEY"]},
            {"Content-Type": "application/json;charset=UTF-8"},
        )
        token = auth.get("token")
        if auth.get("return_code") not in (None, 0, "0") or not isinstance(token, str):
            print("Failure: could not issue a token.")
            return 1

        response = post_json(
            f"{BASE_URLS[environment]}/api/dostk/stkinfo",
            {
                "mrkt_tp": "000",
                "ntl_tp": "1",
                "high_low_close_tp": "1",
                "stk_cnd": "0",
                "trde_qty_tp": "00000",
                "crd_cnd": "0",
                "updown_incls": "0",
                "dt": "20",
                "stex_tp": "1",
            },
            {"Content-Type": "application/json;charset=UTF-8", "authorization": f"Bearer {token}", "api-id": "ka10016"},
        )
    except HTTPError as error:
        print(f"Failure: server returned HTTP {error.code}.")
        return 1
    except URLError as error:
        print(f"Failure: could not connect to server ({error.reason}).")
        return 1

    if response.get("return_code") not in (None, 0, "0"):
        print(f"Failure: ka10016 error ({response.get('return_msg', '')}).")
        return 1
    records = response.get("ntl_pric", [])
    if not isinstance(records, list):
        print("Failure: unexpected ntl_pric response format.")
        return 1

    fields = sorted({key for row in records if isinstance(row, dict) for key in row})
    print(f"Success: ka10016 new-high response verified ({environment}).")
    print(f"Screen: market=all, high basis, new high, period=20 trading days")
    print(f"Received stocks: {len(records)}")
    print("Fields: " + ", ".join(fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())
