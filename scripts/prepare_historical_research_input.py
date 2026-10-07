"""Adapt immutable reconstruction cases to the existing research runner input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.infrastructure.historical_reconstruction import (
    adapt_historical_reconstruction_for_research,
    load_historical_reconstruction,
    write_historical_research_input,
)
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 복원 사례를 기존 1분 전략 runner용 동결 입력으로 변환합니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--date")
    selection.add_argument("--dates", help="쉼표로 구분한 YYYY-MM-DD")
    parser.add_argument("--individual-stocks-only", action="store_true",
                        help="동결된 후보의 market_code 0/10만 연구 모집단에 포함합니다.")
    parser.add_argument("--exclude-noncontinuous-minute-candidates", action="store_true",
                        help="결과일에 연속 1분봉 한 쌍도 없는 종목은 연구 모집단에서 제외하고 원장에 기록합니다.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = load_historical_reconstruction(args.source)
    selected_dates = [value.strip() for value in (args.dates or "").split(",") if value.strip()]
    dataset = adapt_historical_reconstruction_for_research(
        source, selected_date=args.date, selected_dates=selected_dates,
        individual_stocks_only=args.individual_stocks_only,
        exclude_noncontinuous_minute_candidates=args.exclude_noncontinuous_minute_candidates,
    )
    manifest = write_historical_research_input(dataset, args.output)
    verified = load_frozen_research_export(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "revision_count": len(verified.observations),
        "source_dataset_id": manifest["source_dataset_id"],
        "rank_factor_supported": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
