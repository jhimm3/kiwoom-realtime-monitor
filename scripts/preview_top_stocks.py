"""모의/실전 REST API에서 Top 20과 신고가 상태 결합을 확인한다."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kiwoom_monitor.application.ranking_service import RankingService
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig


def main() -> int:
    try:
        settings = LocalApiConfig(PROJECT_ROOT / "data" / "api.env").load()
        if not settings.app_key or not settings.secret_key:
            raise ValueError("선택된 환경의 API 키가 설정되지 않았습니다.")
        stocks = RankingService(KiwoomRestClient(settings)).load_top_stocks()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"실패: {error}")
        return 1

    print(f"성공: Top 20 {len(stocks)}개 종목과 신고가 상태를 결합했습니다 ({settings.environment}).")
    for stock in stocks[:3]:
        print(f"{stock.rank}위 {stock.name}({stock.code}) · 등락률 {stock.change_rate} · 신고가 {stock.new_high_label}")
    print("인증정보와 토큰은 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
