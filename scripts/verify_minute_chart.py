"""ka10080 1분봉 응답 형식을 확인한다. 시세 값은 출력하지 않는다."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings


def main() -> int:
    try:
        settings = KiwoomSettings.from_env_file(PROJECT_ROOT / ".env")
        response = KiwoomRestClient(settings).request(
            "ka10080",
            "/api/dostk/chart",
            {"stk_cd": "005930", "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": datetime.now().strftime("%Y%m%d")},
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"실패: {error}")
        return 1
    records = response.get("stk_min_pole_chart_qry", [])
    if not isinstance(records, list):
        print("실패: 분봉 목록 형식이 올바르지 않습니다.")
        return 1
    fields = sorted({key for row in records if isinstance(row, dict) for key in row})
    print(f"성공: ka10080 1분봉 응답을 확인했습니다 ({settings.environment}).")
    print(f"수신 봉 수: {len(records)}")
    print("필드: " + ", ".join(fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
