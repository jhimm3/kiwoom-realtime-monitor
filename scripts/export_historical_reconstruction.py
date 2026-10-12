"""Export selected post-hoc candidate days as immutable reconstruction inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.infrastructure.historical_reconstruction import (
    export_historical_reconstruction,
    load_historical_reconstruction,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 후보·대신 봉·검증 상태가 있는 뉴스를 복원 연구 입력으로 동결합니다."
    )
    parser.add_argument("--candidate-database", required=True, type=Path)
    parser.add_argument("--intelligence-database", required=True, type=Path)
    parser.add_argument("--dates", required=True, help="쉼표로 구분한 YYYY-MM-DD")
    parser.add_argument(
        "--outcome-end-date", default="",
        help="후보일 뒤 결과 봉을 포함할 마지막 날짜. 생략하면 후보일 자료만 동결합니다.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--intelligence-immutable", action="store_true",
                        help="WAL/SHM 없는 게시된 불변 스냅샷만 사용합니다. 수집 중인 DB에는 지정하지 않습니다.")
    parser.add_argument("--strategy-outcomes-only", action="store_true",
                        help="후보와 후보일 뒤 결과 봉만 복원합니다. 뉴스와 선정일 봉은 읽지 않습니다.")
    args = parser.parse_args()
    selected = [value.strip() for value in args.dates.split(",") if value.strip()]
    manifest = export_historical_reconstruction(
        args.candidate_database,
        args.intelligence_database,
        args.output,
        dates=selected,
        outcome_end_date=args.outcome_end_date,
        intelligence_immutable=args.intelligence_immutable,
        strategy_outcomes_only=args.strategy_outcomes_only,
    )
    dataset = load_historical_reconstruction(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "record_count": len(dataset.records),
        "resolution_by_candidate_day": manifest["resolution_by_candidate_day"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
