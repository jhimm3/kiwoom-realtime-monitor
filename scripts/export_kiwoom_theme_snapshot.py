from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = KiwoomRestClient(LocalApiConfig(args.api_config).load())
    request = {"qry_tp": "0", "stk_cd": "", "date_tp": "10", "thema_nm": "", "flu_pl_amt_tp": "0", "stex_tp": "3"}
    groups: list[dict[str, object]] = []
    cont_yn, next_key = "N", ""
    while True:
        payload, has_next, next_key = client.request_with_continuation(
            "ka90001", "/api/dostk/thme", request, cont_yn=cont_yn, next_key=next_key,
        )
        groups.extend(item for item in payload.get("thema_grp", []) if isinstance(item, dict))
        if not has_next:
            break
        cont_yn = "Y"
    themes: list[dict[str, object]] = []
    for index, group in enumerate(groups, start=1):
        code = str(group.get("thema_grp_cd", "")).strip()
        name = str(group.get("thema_nm", "")).strip()
        if not code or not name:
            continue
        detail = client.request(
            "ka90002", "/api/dostk/thme",
            {"date_tp": "1", "thema_grp_cd": code, "stex_tp": "3"},
        )
        stocks = [item for item in detail.get("thema_comp_stk", []) if isinstance(item, dict)]
        themes.append({"theme_code": code, "theme_name": name, "group": group, "stocks": stocks})
        if index % 20 == 0:
            print(f"loaded {index}/{len(groups)}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "captured_at": datetime.now().isoformat(timespec="seconds"), "themes": themes,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved themes={len(themes)} stocks={sum(len(v['stocks']) for v in themes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
