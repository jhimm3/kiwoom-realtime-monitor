"""Create an immutable chronological split plan for historical cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_research_split import (
    build_historical_evaluation_plan,
    load_historical_evaluation_plan,
    write_historical_evaluation_plan,
)
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export


def main() -> int:
    parser = argparse.ArgumentParser(
        description="다기간 역사 사례를 시간순 TRAIN/VALIDATION/OOS 계획으로 동결합니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--train-cases", required=True, type=int)
    parser.add_argument("--validation-cases", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    dataset = load_frozen_research_export(args.source)
    plan = build_historical_evaluation_plan(
        dataset, train_cases=args.train_cases, validation_cases=args.validation_cases,
    )
    write_historical_evaluation_plan(plan, args.output)
    verified = load_historical_evaluation_plan(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "plan_id": verified["plan_id"],
        "case_assignments": verified["case_assignments"],
        "oos_status": verified["oos_status"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
